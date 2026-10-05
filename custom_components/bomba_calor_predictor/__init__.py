"""
Integración bomba_calor_predictor para Home Assistant.

Registra:
  - Coordinator (SGD cada 5 min)
  - Plataforma sensor (6 entidades, agrupadas en un dispositivo de servicio)
  - Servicios: reset_modelo, set_learning_rate
"""
from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_LEARNING_RATE,
    CONF_SENSOR_T_AGUA,
    DEFAULT_LEARNING_RATE,
    DOMAIN,
    MAX_LEARNING_RATE,
    MIN_LEARNING_RATE,
)
from .coordinator import BombaCalorCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]

SERVICE_RESET = "reset_modelo"
SERVICE_SET_LR = "set_learning_rate"

SERVICE_SET_LR_SCHEMA = vol.Schema({
    vol.Required("learning_rate"): vol.All(
        vol.Coerce(float), vol.Range(min=MIN_LEARNING_RATE, max=MAX_LEARNING_RATE)
    )
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Inicializa la integración."""
    hass.data.setdefault(DOMAIN, {})

    merged_config = {**entry.data, **entry.options}
    coordinator = BombaCalorCoordinator(hass, entry, merged_config)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Si al arrancar HA aún no había temperatura del agua, se predice en
    # cuanto llegue en vez de esperar a la siguiente lectura (5 min).
    entry.async_on_unload(
        async_track_state_change_event(
            hass, [merged_config[CONF_SENSOR_T_AGUA]], coordinator.async_t_agua_cambiada
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_reset(call: ServiceCall) -> None:
        """Resetea el modelo a los priors del fabricante SC984."""
        await coordinator.async_reset_model()
        _LOGGER.info("Servicio reset_modelo ejecutado")

    async def handle_set_lr(call: ServiceCall) -> None:
        """Cambia la learning rate y la persiste en entry.options.

        El listener de abajo ve que solo ha cambiado la tasa y la aplica en
        caliente, sin recargar la integración.
        """
        lr = call.data["learning_rate"]
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_LEARNING_RATE: lr}
        )
        _LOGGER.info("Learning rate cambiada a %s", lr)

    hass.services.async_register(DOMAIN, SERVICE_RESET, handle_reset)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_LR, handle_set_lr, schema=SERVICE_SET_LR_SCHEMA
    )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Descarga la integración limpiamente."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_RESET)
            hass.services.async_remove(DOMAIN, SERVICE_SET_LR)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Aplica un cambio de opciones.

    Si solo cambia la learning rate, se aplica en caliente: recargar
    reiniciaría el modelo en memoria y, con él, la espera de estabilidad.
    Cualquier otro cambio (una entidad, el valor de un modo) recarga con el
    mecanismo oficial de HA: llamar a async_setup_entry a mano (como se hacía
    antes) rompía async_config_entry_first_refresh() y podía dejar la entrada
    en FAILED_UNLOAD hasta reiniciar HA.
    """
    coordinator: BombaCalorCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    nueva = {**entry.data, **entry.options}
    if coordinator is not None:
        actual = coordinator.config
        if {k: v for k, v in nueva.items() if k != CONF_LEARNING_RATE} == {
            k: v for k, v in actual.items() if k != CONF_LEARNING_RATE
        }:
            lr = nueva.get(CONF_LEARNING_RATE, DEFAULT_LEARNING_RATE)
            if lr != actual.get(CONF_LEARNING_RATE, DEFAULT_LEARNING_RATE):
                await coordinator.async_set_learning_rate(lr)
            return
    await hass.config_entries.async_reload(entry.entry_id)
