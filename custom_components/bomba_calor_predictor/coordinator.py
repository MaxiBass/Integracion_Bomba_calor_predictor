"""
Coordinator: logica SGD y persistencia del modelo.
Se ejecuta cada UPDATE_INTERVAL_MINUTES minutos.

Modelo: consumo = w0 + w2*T_agua  (2 parametros)
T_ext eliminada: la bomba opera solo en verano (25-35 C exterior),
rango donde el COP es estable y T_ext no aporta poder predictivo real.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
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
)

_LOGGER = logging.getLogger(__name__)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _get_float(hass: HomeAssistant, entity_id: str, default: float | None = None) -> float | None:
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown", ""):
        return default
    try:
        return float(state.state)
    except (ValueError, TypeError):
        return default


class BombaCalorCoordinator(DataUpdateCoordinator):
    """Gestiona el modelo SGD y actualiza los sensores."""

    def __init__(self, hass: HomeAssistant, config: dict) -> None:
        super().__init__(
            hass,
            _LOGGER,
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

        # Tracking de estabilidad temporal (no persiste entre reinicios a proposito)
        self._bomba_on_desde = None
        self._modo_actual_tracked: str | None = None
        self._modo_desde = None

    async def async_load(self) -> None:
        """Carga pesos desde .storage o inicializa con priors."""
        data = await self._store.async_load()
        if data and "weights" in data:
            self._weights = data["weights"]
            self._samples = data.get("samples", {"silent": 0, "boost": 0})
            _LOGGER.info("Modelo cargado desde storage: %s muestras", self._samples)
        else:
            self._weights = {
                "silent": dict(PRIORS["silent"]),
                "boost":  dict(PRIORS["boost"]),
            }
            _LOGGER.info("Modelo inicializado con priors SC984 (2 parametros: w0, w2)")

    def _actualizar_estabilidad(
        self, bomba_on: bool, modo_activo: str | None
    ) -> tuple[float, float]:
        """Actualiza timestamps de encendido/cambio de modo.
        Devuelve (minutos_bomba_encendida, minutos_en_modo_actual)."""
        ahora = dt_util.utcnow()

        if bomba_on:
            if self._bomba_on_desde is None:
                self._bomba_on_desde = ahora
        else:
            self._bomba_on_desde = None

        if modo_activo != self._modo_actual_tracked:
            self._modo_actual_tracked = modo_activo
            self._modo_desde = ahora if modo_activo is not None else None

        minutos_bomba = (
            (ahora - self._bomba_on_desde).total_seconds() / 60
            if self._bomba_on_desde else 0.0
        )
        minutos_modo = (
            (ahora - self._modo_desde).total_seconds() / 60
            if self._modo_desde else 0.0
        )
        return minutos_bomba, minutos_modo

    async def _async_update_data(self) -> dict:
        """Llamado cada UPDATE_INTERVAL_MINUTES. Lee sensores, entrena si hay muestra valida."""
        cfg = self._config

        t_agua   = _get_float(self.hass, cfg[CONF_SENSOR_T_AGUA])
        consumo  = _get_float(self.hass, cfg[CONF_SENSOR_CONSUMO])
        setpoint = _get_float(self.hass, cfg[CONF_NUMBER_SETPOINT], default=28.0)

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

        minutos_bomba, minutos_modo = self._actualizar_estabilidad(bomba_on, modo_activo)

        # Predicciones para AMBOS modos: consumo = w0 + w2*T_agua
        if t_agua is not None:
            for modo_key in ("silent", "boost"):
                w = self._weights[modo_key]
                pred = w["w0"] + w["w2"] * t_agua
                pred = _clamp(pred, CLAMP[modo_key]["min"], CLAMP[modo_key]["max"])
                self._predictions[modo_key] = round(pred, 1)

        if modo_activo and consumo is not None and self._predictions[modo_activo] is not None:
            self._errors[modo_activo] = round(consumo - self._predictions[modo_activo], 1)

        # ENTRENAMIENTO SGD
        can_train = (
            bomba_on
            and modo_activo is not None
            and t_agua is not None
            and consumo is not None
            and consumo > MIN_CONSUMO_VALIDO
            and setpoint is not None
            and t_agua < (setpoint - MARGEN_MODULACION)
            and minutos_bomba >= MIN_TIEMPO_ESTABLE_MINUTOS
            and minutos_modo  >= MIN_TIEMPO_ESTABLE_MINUTOS
        )

        if can_train:
            lr = cfg.get(CONF_LEARNING_RATE, DEFAULT_LEARNING_RATE)
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

            await self._store.async_save({
                "weights": self._weights,
                "samples": self._samples,
            })
        elif bomba_on and modo_activo is not None:
            _LOGGER.debug(
                "SGD %s: muestra descartada (estable_bomba=%.1fmin estable_modo=%.1fmin, "
                "se requieren %d min)",
                modo_activo, minutos_bomba, minutos_modo, MIN_TIEMPO_ESTABLE_MINUTOS,
            )

        return {
            "predictions": dict(self._predictions),
            "errors":      dict(self._errors),
            "samples":     dict(self._samples),
            "weights":     {k: dict(v) for k, v in self._weights.items()},
        }

    async def async_reset_model(self) -> None:
        """Resetea pesos a los priors SC984."""
        self._weights = {
            "silent": dict(PRIORS["silent"]),
            "boost":  dict(PRIORS["boost"]),
        }
        self._samples = {"silent": 0, "boost": 0}
        await self._store.async_save({
            "weights": self._weights,
            "samples": self._samples,
        })
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