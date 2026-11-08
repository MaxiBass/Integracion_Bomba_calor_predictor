"""
Coordinator: logica SGD y persistencia del modelo.
Se ejecuta cada UPDATE_INTERVAL_MINUTES minutos.

Modelo: consumo = w0 + w2*T_agua  (2 parametros)
T_ext eliminada: la bomba opera solo en verano (25-35 C exterior),
rango donde el COP es estable y T_ext no aporta poder predictivo real.
"""
from __future__ import annotations

import logging
import math
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfPower,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
    UPDATE_INTERVAL_MINUTES,
    PRIORS,
    CLAMP,
    WEIGHT_LIMITS,
    MIN_CONSUMO_VALIDO,
    MARGEN_MODULACION,
    MIN_TIEMPO_ESTABLE_MINUTOS,
    CONF_SENSOR_T_AGUA,
    CONF_SENSOR_CONSUMO,
    CONF_SENSOR_MODO,
    CONF_SWITCH_BOMBA,
    CONF_NUMBER_SETPOINT,
    CONF_LEARNING_RATE,
    CONF_MODO_SILENT,
    CONF_MODO_BOOST,
    DEFAULT_LEARNING_RATE,
    MAX_LEARNING_RATE,
    MIN_LEARNING_RATE,
    RETRASO_GUARDADO,
)

MODOS = ("silent", "boost")

_LOGGER = logging.getLogger(__name__)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _get_float(hass: HomeAssistant, entity_id: str, default: float | None = None) -> float | None:
    state = hass.states.get(entity_id)
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
        return default
    try:
        valor = float(state.state)
    except (ValueError, TypeError):
        return default
    return valor if math.isfinite(valor) else default


def _get_potencia_w(hass: HomeAssistant, entity_id: str) -> float | None:
    """Potencia en W, aunque el sensor la dé en kW (si no, 1,3 kW quedaba
    por debajo de MIN_CONSUMO_VALIDO y el modelo no aprendía nunca)."""
    valor = _get_float(hass, entity_id)
    if valor is None:
        return None
    estado = hass.states.get(entity_id)
    if estado.attributes.get(ATTR_UNIT_OF_MEASUREMENT) == UnitOfPower.KILO_WATT:
        valor *= 1000
    return valor


def _minutos_desde_cambio(estado: State | None) -> float:
    """Minutos desde que la entidad cambió de estado por última vez.

    Se mira el `last_changed` de HA y no la primera lectura que lo vio: con
    lecturas cada 5 minutos, un Silent→Boost→Silent o un apagado y encendido
    entre dos lecturas pasaba desapercibido y se entrenaba con el consumo
    aún en transición. Tras reiniciar HA los estados se crean de nuevo, así
    que se vuelve a esperar MIN_TIEMPO_ESTABLE_MINUTOS (como antes).
    """
    if estado is None:
        return 0.0
    return max(0.0, (dt_util.utcnow() - estado.last_changed).total_seconds() / 60)


class BombaCalorCoordinator(DataUpdateCoordinator):
    """Gestiona el modelo SGD y actualiza los sensores."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, config: dict) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=UPDATE_INTERVAL_MINUTES),
        )
        self._config = config
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._weights: dict = {}
        self._samples: dict = {"silent": 0, "boost": 0}
        self._predictions: dict = {"silent": None, "boost": None}
        self._errors: dict = {"silent": None, "boost": None}
        self._last_trained: dict = {"silent": None, "boost": None}
        # Hay muestras aprendidas que aún no están en disco.
        self._sin_guardar = False
        # Por qué aprendió o no en la última lectura (para el diagnóstico).
        self.entrenamiento: dict = {}

    @property
    def config(self) -> dict:
        return self._config

    async def async_load(self) -> None:
        """Carga pesos desde .storage o inicializa con priors."""
        data = await self._store.async_load()
        if data and "weights" in data:
            # Un modo que falte en el fichero (guardado a mano o de otra
            # versión) arranca con los priores y 0 muestras, en vez de dar
            # KeyError en cada lectura.
            guardados = data.get("weights") or {}
            muestras = data.get("samples") or {}
            self._weights = {
                modo: {**PRIORS[modo], **(guardados.get(modo) or {})} for modo in MODOS
            }
            self._samples = {modo: int(muestras.get(modo, 0)) for modo in MODOS}
            _LOGGER.info("Modelo cargado desde storage: %s muestras", self._samples)
        else:
            self._weights = {
                "silent": dict(PRIORS["silent"]),
                "boost":  dict(PRIORS["boost"]),
            }
            _LOGGER.info("Modelo inicializado con priors SC984 (2 parametros: w0, w2)")

    def _datos(self) -> dict:
        """Lo que se guarda en disco (lo llama el Store al escribir)."""
        self._sin_guardar = False
        return {"weights": self._weights, "samples": self._samples}

    async def async_guardar_pendiente(self) -> None:
        """Al descargar la entrada: escribe ya lo que esté pendiente.

        Sin esto, una recarga leería el fichero antes de que el guardado
        retrasado lo actualizase y se perderían las últimas muestras.
        """
        if self._sin_guardar:
            await self._store.async_save(self._datos())

    def _calcular_predicciones(self, t_agua: float) -> None:
        """Predicciones para AMBOS modos: consumo = w0 + w2*T_agua."""
        for modo_key in MODOS:
            w = self._weights[modo_key]
            pred = w["w0"] + w["w2"] * t_agua
            pred = _clamp(pred, CLAMP[modo_key]["min"], CLAMP[modo_key]["max"])
            self._predictions[modo_key] = round(pred, 1)

    def _snapshot(self) -> dict:
        return {
            "predictions": dict(self._predictions),
            "errors":      dict(self._errors),
            "samples":     dict(self._samples),
            "weights":     {k: dict(v) for k, v in self._weights.items()},
        }

    @callback
    def async_t_agua_cambiada(self, event: Event[EventStateChangedData]) -> None:
        """Al arrancar HA, la temperatura del agua suele llegar después de la
        primera lectura: sin esto las predicciones se quedaban en «unknown»
        hasta la siguiente, 5 minutos más tarde, y las plantillas de umbrales
        usaban mientras tanto su valor de reserva. Solo calcula predicciones
        (no entrena) y solo mientras falten."""
        if None not in self._predictions.values():
            return
        t_agua = _get_float(self.hass, self._config[CONF_SENSOR_T_AGUA])
        if t_agua is None:
            return
        self._calcular_predicciones(t_agua)
        self.async_set_updated_data(self._snapshot())

    async def _async_update_data(self) -> dict:
        """Llamado cada UPDATE_INTERVAL_MINUTES. Lee sensores, entrena si hay muestra valida."""
        cfg = self._config

        t_agua   = _get_float(self.hass, cfg[CONF_SENSOR_T_AGUA])
        consumo  = _get_potencia_w(self.hass, cfg[CONF_SENSOR_CONSUMO])
        # Sin valor por defecto: con la consigna desconocida no se sabe si
        # la bomba está modulando cerca de ella, y no se entrena (antes se
        # suponían 28 °C y se aprendía igualmente).
        setpoint = _get_float(self.hass, cfg[CONF_NUMBER_SETPOINT])

        estado_bomba = self.hass.states.get(cfg[CONF_SWITCH_BOMBA])
        estado_modo  = self.hass.states.get(cfg[CONF_SENSOR_MODO])

        bomba_on   = bool(estado_bomba and estado_bomba.state == "on")
        modo_val   = estado_modo.state if estado_modo else ""
        silent_val = cfg.get(CONF_MODO_SILENT, "Silent_heat")
        boost_val  = cfg.get(CONF_MODO_BOOST,  "Boost_heat")

        if bomba_on and modo_val == silent_val:
            modo_activo = "silent"
        elif bomba_on and modo_val == boost_val:
            modo_activo = "boost"
        else:
            modo_activo = None

        minutos_bomba = _minutos_desde_cambio(estado_bomba) if bomba_on else 0.0
        minutos_modo = _minutos_desde_cambio(estado_modo) if modo_activo else 0.0

        if t_agua is not None:
            self._calcular_predicciones(t_agua)

        # El error solo tiene sentido en el modo que está funcionando: el del
        # otro se quedaba con el valor de la última vez que estuvo activo.
        for modo_key in MODOS:
            prediccion = self._predictions[modo_key]
            self._errors[modo_key] = (
                round(consumo - prediccion, 1)
                if modo_key == modo_activo and consumo is not None and prediccion is not None
                else None
            )

        # ENTRENAMIENTO SGD
        condiciones = {
            "bomba_encendida": bomba_on,
            "modo_reconocido": modo_activo is not None,
            "t_agua_valida": t_agua is not None,
            "consumo_valido": consumo is not None and consumo > MIN_CONSUMO_VALIDO,
            "consigna_conocida": setpoint is not None,
            "lejos_de_la_consigna": (
                t_agua is not None and setpoint is not None
                and t_agua < (setpoint - MARGEN_MODULACION)
            ),
            "bomba_estable": minutos_bomba >= MIN_TIEMPO_ESTABLE_MINUTOS,
            "modo_estable": minutos_modo >= MIN_TIEMPO_ESTABLE_MINUTOS,
        }
        can_train = all(condiciones.values())
        self.entrenamiento = {
            "can_train": can_train,
            "modo": modo_activo,
            "minutos_bomba": round(minutos_bomba, 1),
            "minutos_modo": round(minutos_modo, 1),
            "condiciones": condiciones,
        }

        if can_train:
            # Acotada también aquí: una entrada guardada con una tasa de antes
            # de bajar el máximo (§R.7) no debe hacer diverger el modelo.
            lr = _clamp(
                float(cfg.get(CONF_LEARNING_RATE, DEFAULT_LEARNING_RATE)),
                MIN_LEARNING_RATE,
                MAX_LEARNING_RATE,
            )
            w  = self._weights[modo_activo]
            y_pred = w["w0"] + w["w2"] * t_agua
            error  = consumo - y_pred

            wl = WEIGHT_LIMITS
            w["w0"] = round(_clamp(w["w0"] + lr * error * 1.0,    *wl["w0"]), 4)
            w["w2"] = round(_clamp(w["w2"] + lr * error * t_agua, *wl["w2"]), 6)

            self._samples[modo_activo] = min(self._samples[modo_activo] + 1, 999999)
            self._last_trained[modo_activo] = dt_util.utcnow()

            _LOGGER.debug(
                "SGD %s muestra #%d | T_agua=%.1f | "
                "Real=%.0fW Pred=%.0fW Error=%.0fW | "
                "estable_bomba=%.1fmin estable_modo=%.1fmin | W=%s",
                modo_activo, self._samples[modo_activo],
                t_agua, consumo, y_pred, error,
                minutos_bomba, minutos_modo, w,
            )

            # Retrasado: no hace falta escribir en disco cada 5 minutos.
            self._sin_guardar = True
            self._store.async_delay_save(self._datos, RETRASO_GUARDADO)
        elif bomba_on and modo_activo is not None:
            _LOGGER.debug(
                "SGD %s: muestra descartada (%s)",
                modo_activo, [k for k, v in condiciones.items() if not v],
            )

        return self._snapshot()

    async def async_reset_model(self) -> None:
        """Resetea pesos a los priors SC984."""
        self._weights = {
            "silent": dict(PRIORS["silent"]),
            "boost":  dict(PRIORS["boost"]),
        }
        self._samples = {"silent": 0, "boost": 0}
        self._errors = {"silent": None, "boost": None}
        self._last_trained = {"silent": None, "boost": None}
        await self._store.async_save(self._datos())
        await self.async_refresh()
        _LOGGER.info("Modelo reseteado a priors SC984 (2 parametros)")

    async def async_set_learning_rate(self, lr: float) -> None:
        """Cambia la learning rate en caliente."""
        self._config[CONF_LEARNING_RATE] = lr
        _LOGGER.info("Learning rate actualizada a %s", lr)

    @property
    def predictions(self) -> dict:
        return self._predictions

    @property
    def samples(self) -> dict:
        return self._samples

    @property
    def weights(self) -> dict:
        return self._weights

    @property
    def last_trained(self) -> dict:
        return self._last_trained
