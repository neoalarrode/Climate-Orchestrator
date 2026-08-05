"""
Aprende la inercia termica REAL de una zona a partir del propio historico
de Home Assistant (via el recorder): cuantos grados por hora sube/baja la
temperatura con el actuador encendido, y cuantos pierde/gana por hora con
el actuador apagado en funcion de la diferencia con el exterior.

Nada de machine learning ni de un solver: se buscan tramos continuos en el
historico donde el actuador estuvo en un mismo estado (encendido/apagado)
al menos `MIN_RUN_MINUTES`, se calcula la pendiente real de temperatura de
cada tramo, y se toma la MEDIANA de todos los tramos validos (robusta
frente a un tramo suelto, p.ej. una ventana abierta o una visita que
toquetea el termostato a mano). El resultado es un numero que se puede
explicar en una frase: "de media, esta zona sube 0.9°C por hora
calentando".

Funciona con los DOS tipos de actuador (ver const.py: cada lado, calor y
frio, puede ser distinto):

  - "switch": se usa directamente su estado on/off del historico — se
    sabe con certeza cuando estuvo actuando, porque lo enciende/apaga
    esta misma integracion.
  - "climate" (delegado en un climate.* ya existente, p.ej. una valvula
    termostatica): tambien se puede aprender, a partir del atributo
    `hvac_action` de SU PROPIO historico (heating/cooling frente a
    idle/off/otro) — la mayoria de integraciones de climate lo publican.
    Si esa entidad en concreto nunca lo reporta (queda siempre en blanco),
    sencillamente no se encuentran tramos validos y esa zona se queda con
    los valores por defecto, marcados `reliable=False` — nunca se inventa
    una cifra.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance, history
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_COOLING_RATE_DEG_H,
    DEFAULT_HEATING_RATE_DEG_H,
    DEFAULT_IDLE_LOSS_COEFF,
)

_LOGGER = logging.getLogger(__name__)

MIN_RUN_MINUTES = 20
MIN_VALID_RUNS = 3


class _SyntheticState:
    """Un estado on/off minimo (mismo shape que usa `_state_runs`), para
    poder tratar un climate.* delegado exactamente igual que un switch
    propio una vez traducido su `hvac_action` — ver `_climate_actuator_states`."""

    __slots__ = ("state", "last_changed")

    def __init__(self, state: str, last_changed: datetime) -> None:
        self.state = state
        self.last_changed = last_changed


def _state_runs(states: list) -> list[tuple[datetime, datetime, str]]:
    """Tramos continuos (inicio, fin, estado) de una entidad on/off, a
    partir de la lista de estados que devuelve el recorder (ordenada por
    tiempo, una entrada por cambio de estado real)."""
    runs = []
    for i, s in enumerate(states):
        if s.state not in ("on", "off"):
            continue
        start = s.last_changed
        end = states[i + 1].last_changed if i + 1 < len(states) else dt_util.utcnow()
        runs.append((start, end, s.state))
    return runs


def _value_at_or_before(states: list, ts: datetime) -> float | None:
    best = None
    for s in states:
        try:
            val = float(s.state)
        except (ValueError, TypeError):
            continue
        if s.last_changed <= ts:
            best = val
        else:
            break
    return best


def _learn_rate(temp_states: list, actuator_states: list) -> tuple[float | None, int]:
    slopes = []
    for start, end, state in _state_runs(actuator_states):
        if state != "on":
            continue
        duration_h = (end - start).total_seconds() / 3600
        if duration_h * 60 < MIN_RUN_MINUTES:
            continue
        t0 = _value_at_or_before(temp_states, start)
        t1 = _value_at_or_before(temp_states, end)
        if t0 is None or t1 is None:
            continue
        slope = (t1 - t0) / duration_h
        if 0.05 <= abs(slope) <= 5.0:
            slopes.append(abs(slope))
    if len(slopes) < MIN_VALID_RUNS:
        return None, len(slopes)
    return statistics.median(slopes), len(slopes)


def _learn_idle_loss_coeff(temp_states: list, actuator_states: list, outdoor_states: list) -> tuple[float | None, int]:
    coeffs = []
    for start, end, state in _state_runs(actuator_states):
        if state != "off":
            continue
        duration_h = (end - start).total_seconds() / 3600
        if duration_h * 60 < MIN_RUN_MINUTES:
            continue
        t0, t1 = _value_at_or_before(temp_states, start), _value_at_or_before(temp_states, end)
        out0, out1 = _value_at_or_before(outdoor_states, start), _value_at_or_before(outdoor_states, end)
        if None in (t0, t1, out0, out1):
            continue
        avg_delta = ((out0 - t0) + (out1 - t1)) / 2
        if abs(avg_delta) < 1.0:
            continue
        slope = (t1 - t0) / duration_h
        coeff = slope / avg_delta
        if 0.0 <= coeff <= 0.6:
            coeffs.append(coeff)
    if len(coeffs) < MIN_VALID_RUNS:
        return None, len(coeffs)
    return statistics.median(coeffs), len(coeffs)


def _history_for(hass: HomeAssistant, entity_id: str, start: datetime, end: datetime) -> list:
    """Historico de una entidad simple (switch, sensor): una entrada por
    cambio de SU `state`, sin atributos (mas barato)."""
    result = history.state_changes_during_period(hass, start, end, entity_id, no_attributes=True)
    return result.get(entity_id, [])


def _climate_actuator_states(hass: HomeAssistant, entity_id: str, wanted_action: str,
                              start: datetime, end: datetime) -> list:
    """Traduce el historico de un climate.* delegado a la misma forma
    on/off que un switch, usando su atributo `hvac_action` (heating/
    cooling/idle/off/fan/drying): "on" mientras coincide con
    `wanted_action` ("heating" o "cooling"), "off" el resto del tiempo.
    Hace falta el historico CON atributos (mas caro que `_history_for`,
    por eso es una funcion aparte) porque `hvac_action` es un atributo,
    no el `state` de la entidad (que es el hvac_mode: heat/cool/off/...)."""
    result = history.get_significant_states(
        hass, start, end, [entity_id], significant_changes_only=False, minimal_response=False, no_attributes=False,
    )
    raw = result.get(entity_id, [])
    synthetic = []
    for s in raw:
        action = (s.attributes or {}).get("hvac_action")
        synthetic.append(_SyntheticState("on" if action == wanted_action else "off", s.last_changed))
    return synthetic


def _actuator_states_for_side(hass: HomeAssistant, zone: dict, side: str, wanted_action: str,
                               start: datetime, end: datetime) -> list | None:
    """`side` es "heat" o "cool". Devuelve el historico on/off de ese lado
    (switch propio, o climate.* delegado traducido), o None si ese lado no
    tiene actuador configurado."""
    mode = zone.get(f"{side}_actuator_mode", "switch")
    if mode == "switch":
        switch = zone.get(f"{side}_switch")
        return _history_for(hass, switch, start, end) if switch else None
    if mode == "climate":
        entity = zone.get(f"{side}_climate_entity")
        return _climate_actuator_states(hass, entity, wanted_action, start, end) if entity else None
    return None


def _compute_model_sync(hass: HomeAssistant, zone: dict, days: int) -> dict:
    model = {
        "heating_rate_deg_h": DEFAULT_HEATING_RATE_DEG_H,
        "cooling_rate_deg_h": DEFAULT_COOLING_RATE_DEG_H,
        "idle_loss_coeff": DEFAULT_IDLE_LOSS_COEFF,
        "reliable": False,
        "runs_used": 0,
    }

    if not zone.get("current_temp_sensor"):
        return model

    end = dt_util.utcnow()
    start = end - timedelta(days=days)
    temp_states = _history_for(hass, zone["current_temp_sensor"], start, end)
    if not temp_states:
        return model

    runs_used = 0
    capability = zone.get("hvac_capability", "heat")

    heat_states = cool_states = None

    if capability in ("heat", "heat_cool"):
        heat_states = _actuator_states_for_side(hass, zone, "heat", "heating", start, end)
        if heat_states:
            rate, n = _learn_rate(temp_states, heat_states)
            if rate is not None:
                model["heating_rate_deg_h"] = rate
                runs_used += n

    if capability in ("cool", "heat_cool"):
        cool_states = _actuator_states_for_side(hass, zone, "cool", "cooling", start, end)
        if cool_states:
            rate, n = _learn_rate(temp_states, cool_states)
            if rate is not None:
                model["cooling_rate_deg_h"] = rate
                runs_used += n

    outdoor_sensor = zone.get("outdoor_temp_sensor")
    actuator_states_for_idle = heat_states or cool_states
    if outdoor_sensor and actuator_states_for_idle:
        outdoor_states = _history_for(hass, outdoor_sensor, start, end)
        coeff, n = _learn_idle_loss_coeff(temp_states, actuator_states_for_idle, outdoor_states)
        if coeff is not None:
            model["idle_loss_coeff"] = coeff
            runs_used += n

    model["runs_used"] = runs_used
    model["reliable"] = runs_used >= MIN_VALID_RUNS
    return model


async def async_get_model(hass: HomeAssistant, zone: dict, days: int) -> dict:
    """Consulta el recorder en su propio hilo (nunca en el loop de eventos
    de HA — una consulta de historico de varios dias puede tardar)."""
    try:
        return await get_instance(hass).async_add_executor_job(_compute_model_sync, hass, zone, days)
    except Exception:  # el recorder puede no estar listo, o la entidad no tener historico todavia
        _LOGGER.debug("No se pudo calcular la inercia termica de %s todavia", zone.get("name"), exc_info=True)
        return {
            "heating_rate_deg_h": DEFAULT_HEATING_RATE_DEG_H,
            "cooling_rate_deg_h": DEFAULT_COOLING_RATE_DEG_H,
            "idle_loss_coeff": DEFAULT_IDLE_LOSS_COEFF,
            "reliable": False,
            "runs_used": 0,
        }
