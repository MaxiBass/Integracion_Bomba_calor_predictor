"""Diagnóstico descargable (Ajustes → Dispositivos y servicios → la entrada →
⋮ → Descargar diagnóstico).

Reúne de una vez lo que en el incidente de «Boost no se activa» hubo que
pedir a mano (DECISIONES, «Valores que se pidió revisar»): la configuración
en uso, el modelo, el estado de cada entidad de origen con cuándo cambió, y
si en la última lectura habría aprendido y qué condición faltaba. No hay
credenciales que tapar: solo entity_id de la instalación.
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import BombaCalorConfigEntry
from .config_flow import ENTIDADES
from .const import CLAMP


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BombaCalorConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    config = coordinator.config
    fuentes = {}
    for clave in ENTIDADES:
        estado = hass.states.get(config.get(clave, ""))
        fuentes[config.get(clave)] = (
            {"state": estado.state, "last_changed": estado.last_changed.isoformat()}
            if estado
            else None
        )
    return {
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        "config": dict(config),
        "model": {
            "weights": coordinator.weights,
            "samples": coordinator.samples,
            "predictions": coordinator.predictions,
            "clamp": CLAMP,
            "last_trained": {
                modo: momento.isoformat() if momento else None
                for modo, momento in coordinator.last_trained.items()
            },
        },
        "sources": fuentes,
        "training": coordinator.entrenamiento,
        "last_update_success": coordinator.last_update_success,
    }
