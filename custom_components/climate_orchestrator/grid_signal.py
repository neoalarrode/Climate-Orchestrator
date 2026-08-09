"""
Lectura de la señal de red publicada por Battery Orchestrator (si esta
instalado) — ver scheduler.py, seccion "Señal de red" en su docstring, y
el diseño de la integracion entre los dos proyectos.

Entity_id FIJO, no configurable en ningun lado: Battery Orchestrator
siempre publica en "sensor.battery_orchestrator_grid_signal" (una unica
instancia por Home Assistant, igual que esta integracion). Se detecta
sola: si la entidad no existe (addon no instalado, o sin haber corrido su
primer ciclo todavia), `read()` devuelve todo a None y quien lo consuma
(scheduler.decide_action) ya sabe caer al comportamiento de siempre.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

GRID_SIGNAL_ENTITY_ID = "sensor.battery_orchestrator_grid_signal"


def read(hass: HomeAssistant) -> dict:
    """Devuelve {"tier", "solar_surplus_now_w", "home_power_sensor"} —
    todo None si Battery Orchestrator no esta instalado o no ha publicado
    nunca. "home_power_sensor" es el sensor general de consumo de la casa
    que Battery Orchestrator YA tiene declarado (ver CONF_HOME_POWER_SENSOR
    en const.py: aqui sirve para aprender solo el consumo real de cada
    actuador sin que el usuario tenga que declarar el mismo sensor otra
    vez en esta integracion)."""
    state = hass.states.get(GRID_SIGNAL_ENTITY_ID)
    if state is None or state.state in ("unknown", "unavailable"):
        return {"tier": None, "solar_surplus_now_w": None, "home_power_sensor": None}
    attrs = state.attributes or {}
    surplus = attrs.get("solar_surplus_now_w")
    try:
        surplus = float(surplus) if surplus is not None else None
    except (TypeError, ValueError):
        surplus = None
    return {
        "tier": attrs.get("tier"),
        "solar_surplus_now_w": surplus,
        "home_power_sensor": attrs.get("home_power_sensor") or None,
    }
