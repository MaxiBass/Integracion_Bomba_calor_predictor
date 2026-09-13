"""Config Flow - configuracion visual desde la UI de Home Assistant."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    DOMAIN,
    CONF_SENSOR_T_AGUA,
    CONF_SENSOR_CONSUMO,
    CONF_SENSOR_MODO,
    CONF_SWITCH_BOMBA,
    CONF_NUMBER_SETPOINT,
    CONF_LEARNING_RATE,
    CONF_MODO_SILENT,
    CONF_MODO_BOOST,
    DEFAULT_T_AGUA,
    DEFAULT_CONSUMO,
    DEFAULT_MODO,
    DEFAULT_BOMBA,
    DEFAULT_SETPOINT,
    DEFAULT_SILENT_V,
    DEFAULT_BOOST_V,
    DEFAULT_LEARNING_RATE,
)

STEP_USER_SCHEMA = vol.Schema({
    vol.Required(CONF_SENSOR_T_AGUA,   default=DEFAULT_T_AGUA):   str,
    vol.Required(CONF_SENSOR_CONSUMO,  default=DEFAULT_CONSUMO):  str,
    vol.Required(CONF_SENSOR_MODO,     default=DEFAULT_MODO):     str,
    vol.Required(CONF_SWITCH_BOMBA,    default=DEFAULT_BOMBA):    str,
    vol.Required(CONF_NUMBER_SETPOINT, default=DEFAULT_SETPOINT): str,
    vol.Required(CONF_MODO_SILENT,     default=DEFAULT_SILENT_V): str,
    vol.Required(CONF_MODO_BOOST,      default=DEFAULT_BOOST_V):  str,
    vol.Optional(CONF_LEARNING_RATE,   default=DEFAULT_LEARNING_RATE): vol.Coerce(float),
})


class BombaCalorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Flujo de configuracion inicial."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            for key in [
                CONF_SENSOR_T_AGUA, CONF_SENSOR_CONSUMO,
                CONF_SENSOR_MODO, CONF_SWITCH_BOMBA, CONF_NUMBER_SETPOINT,
            ]:
                eid = user_input.get(key, "")
                if not eid or self.hass.states.get(eid) is None:
                    errors[key] = "entity_not_found"

            if not errors:
                return self.async_create_entry(
                    title="Bomba Calor Predictor SC984",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return BombaCalorOptionsFlow(config_entry)


class BombaCalorOptionsFlow(config_entries.OptionsFlow):
    """Permite cambiar la learning rate y entity_ids sin reinstalar."""

    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Combinar data + options: options sobreescribe si existe (valor mas reciente)
        current = {**self._config_entry.data, **self._config_entry.options}

        schema = vol.Schema({
            vol.Optional(
                CONF_LEARNING_RATE,
                default=current.get(CONF_LEARNING_RATE, DEFAULT_LEARNING_RATE)
            ): vol.Coerce(float),
            vol.Optional(
                CONF_SENSOR_T_AGUA,
                default=current.get(CONF_SENSOR_T_AGUA, DEFAULT_T_AGUA)
            ): str,
            vol.Optional(
                CONF_SENSOR_CONSUMO,
                default=current.get(CONF_SENSOR_CONSUMO, DEFAULT_CONSUMO)
            ): str,
        })
        return self.async_show_form(step_id="init", data_schema=schema)