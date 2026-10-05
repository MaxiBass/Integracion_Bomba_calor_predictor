"""
Sensores expuestos por bomba_calor_predictor (entity_id que genera HA a
partir del nombre):
  - sensor.bdc_prediccion_consumo_silent
  - sensor.bdc_prediccion_consumo_boost
  - sensor.bdc_error_prediccion_silent
  - sensor.bdc_error_prediccion_boost
  - sensor.bdc_muestras_silent
  - sensor.bdc_muestras_boost
"""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BombaCalorCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: BombaCalorCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        BdcPredictionSensor(coordinator, "silent", entry),
        BdcPredictionSensor(coordinator, "boost",  entry),
        BdcErrorSensor(coordinator, "silent", entry),
        BdcErrorSensor(coordinator, "boost",  entry),
        BdcSamplesSensor(coordinator, "silent", entry),
        BdcSamplesSensor(coordinator, "boost",  entry),
    ])


class _BdcSensor(CoordinatorEntity, SensorEntity):
    """Base de los seis sensores.

    Sin dispositivo a propósito: en HA 2026.9, colgarlos de uno hace que una
    instalación nueva genere los entity_id con su nombre delante
    (`sensor.bomba_calor_predictor_sc984_bdc_…`), y las plantillas de la
    piscina dependen de `sensor.bdc_*` (DECISIONES §R.3).
    """

    def __init__(
        self, coordinator: BombaCalorCoordinator, modo: str, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._modo  = modo
        self._entry = entry


class BdcPredictionSensor(_BdcSensor):
    """Consumo predicho (W) para un modo dado."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self, coordinator: BombaCalorCoordinator, modo: str, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, modo, entry)
        self._attr_unique_id = f"{entry.entry_id}_prediccion_{modo}"
        self._attr_name = f"BDC Prediccion Consumo {modo.capitalize()}"
        self._attr_icon = "mdi:lightning-bolt" if modo == "boost" else "mdi:volume-off"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data["predictions"].get(self._modo)

    @property
    def extra_state_attributes(self) -> dict:
        w = self.coordinator.data["weights"].get(self._modo, {})
        return {
            "w0_base":   w.get("w0"),
            "w2_t_agua": w.get("w2"),
            "muestras":  self.coordinator.data["samples"].get(self._modo, 0),
            "modelo":    "consumo = w0 + w2 * T_agua",
        }


class BdcErrorSensor(_BdcSensor):
    """Error de prediccion actual (W) = real - predicho."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:delta"

    def __init__(
        self, coordinator: BombaCalorCoordinator, modo: str, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, modo, entry)
        self._attr_unique_id = f"{entry.entry_id}_error_{modo}"
        self._attr_name = f"BDC Error Prediccion {modo.capitalize()}"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data["errors"].get(self._modo)


class BdcSamplesSensor(_BdcSensor):
    """Numero de muestras validas acumuladas para el modo."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "muestras"
    _attr_icon = "mdi:counter"

    def __init__(
        self, coordinator: BombaCalorCoordinator, modo: str, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, modo, entry)
        self._attr_unique_id = f"{entry.entry_id}_muestras_{modo}"
        self._attr_name = f"BDC Muestras {modo.capitalize()}"

    @property
    def native_value(self) -> int:
        return self.coordinator.data["samples"].get(self._modo, 0)
