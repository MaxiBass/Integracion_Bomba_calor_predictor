"""Config Flow - configuracion visual desde la UI de Home Assistant."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

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
    MAX_LEARNING_RATE,
    MIN_LEARNING_RATE,
)

# Entidades que tienen que existir, y de qué dominios pueden ser.
ENTIDADES = {
    CONF_SENSOR_T_AGUA: ["sensor", "input_number"],
    CONF_SENSOR_CONSUMO: ["sensor"],
    CONF_SENSOR_MODO: ["select", "input_select"],
    CONF_SWITCH_BOMBA: ["switch", "input_boolean"],
    CONF_NUMBER_SETPOINT: ["number", "input_number", "sensor"],
}

VALORES_POR_DEFECTO = {
    CONF_SENSOR_T_AGUA: DEFAULT_T_AGUA,
    CONF_SENSOR_CONSUMO: DEFAULT_CONSUMO,
    CONF_SENSOR_MODO: DEFAULT_MODO,
    CONF_SWITCH_BOMBA: DEFAULT_BOMBA,
    CONF_NUMBER_SETPOINT: DEFAULT_SETPOINT,
    CONF_MODO_SILENT: DEFAULT_SILENT_V,
    CONF_MODO_BOOST: DEFAULT_BOOST_V,
    CONF_LEARNING_RATE: DEFAULT_LEARNING_RATE,
}


def _esquema(valores: dict[str, Any]) -> vol.Schema:
    """El mismo formulario para el alta y las opciones, relleno con `valores`."""
    campos: dict[Any, Any] = {
        vol.Required(clave, default=valores[clave]): EntitySelector(
            EntitySelectorConfig(domain=dominios)
        )
        for clave, dominios in ENTIDADES.items()
    }
    campos[vol.Required(CONF_MODO_SILENT, default=valores[CONF_MODO_SILENT])] = str
    campos[vol.Required(CONF_MODO_BOOST, default=valores[CONF_MODO_BOOST])] = str
    # El mismo rango que el servicio set_learning_rate.
    campos[vol.Required(CONF_LEARNING_RATE, default=valores[CONF_LEARNING_RATE])] = vol.All(
        vol.Coerce(float), vol.Range(min=MIN_LEARNING_RATE, max=MAX_LEARNING_RATE)
    )
    return vol.Schema(campos)


def _entidades_que_faltan(hass: HomeAssistant, datos: dict[str, Any]) -> dict[str, str]:
    """Una entidad que no existe deja el modelo sin datos sin avisar: se rechaza."""
    return {
        clave: "entity_not_found"
        for clave in ENTIDADES
        if not datos.get(clave) or hass.states.get(datos[clave]) is None
    }


class BombaCalorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Flujo de configuracion inicial.

    Solo se admite una entrada (`single_config_entry` en el manifest): todas
    guardan el modelo en el mismo fichero y se pisarían los pesos.
    """

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        valores = dict(VALORES_POR_DEFECTO)
        if user_input is not None:
            errors = _entidades_que_faltan(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title="Bomba Calor Predictor SC984",
                    data=user_input,
                )
            valores |= user_input

        return self.async_show_form(
            step_id="user",
            data_schema=_esquema(valores),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> BombaCalorOptionsFlow:
        return BombaCalorOptionsFlow()


class BombaCalorOptionsFlow(config_entries.OptionsFlow):
    """Permite cambiar cualquier entidad, los valores del modo y la learning
    rate sin reinstalar (antes solo tres campos y sin comprobar nada)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        # Combinar data + options: options sobreescribe si existe (valor mas reciente)
        valores = dict(VALORES_POR_DEFECTO) | {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            errors = _entidades_que_faltan(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title="", data=user_input)
            valores |= user_input

        return self.async_show_form(
            step_id="init", data_schema=_esquema(valores), errors=errors
        )
