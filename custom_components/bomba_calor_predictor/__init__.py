"""
IntegraciÃ³n bomba_calor_predictor para Home Assistant.

Registra:
  - Coordinator (SGD cada 5 min)
  - Plataforma sensor (6 entidades)
  - Servicios: reset_modelo, set_learning_rate
"""
from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import CONF_LEARNING_RATE, DOMAIN
from .coordinator import BombaCalorCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]

SERVICE_RESET = "reset_modelo"
SERVICE_SET_LR = "set_learning_rate"

SERVICE_SET_LR_SCHEMA = vol.Schema({
    vol.Required("learning_rate"): vol.All(
        vol.Coerce(float), vol.Range(min=0.000001, max=0.1)
    )
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Inicializa la integraciÃ³n."""
    _LOGGER.info("Bomba Calor Predictor: prueba de actualización vía HACS OK (marca updatetest-01)")
    hass.data.setdefault(DOMAIN, {})

    merged_config = {**entry.data, **entry.options}
    coordinator = BombaCalorCoordinator(hass, merged_config)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_reset(call: ServiceCall) -> None:
        """Resetea el modelo a los priors del fabricante SC984."""
        await coordinator.async_reset_model()
        _LOGGER.info("Servicio reset_modelo ejecutado")

    async def handle_set_lr(call: ServiceCall) -> None:
        """Cambia la learning rate en caliente y la persiste en entry.options."""
        lr = call.data["learning_rate"]
        await coordinator.async_set_learning_rate(lr)
        new_options = dict(entry.options)
        new_options[CONF_LEARNING_RATE] = lr
        hass.config_entries.async_update_entry(entry, options=new_options)
        _LOGGER.info("Learning rate cambiada a %s", lr)

    hass.services.async_register(DOMAIN, SERVICE_RESET, handle_reset)
    hass.services.async_register(
        DOMAIN, SERVICE_SET_LR, handle_set_lr, schema=SERVICE_SET_LR_SCHEMA
    )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Descarga la integraciÃ³n limpiamente."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_RESET)
            hass.services.async_remove(DOMAIN, SERVICE_SET_LR)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Recarga si cambian las opciones."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)