"""
Aprendizaje de la potencia TIPICA de un actuador CONCRETO (no de toda la
zona: una misma zona puede tener un aire acondicionado sin forma de
medir su consumo real y un radiador con su propio sensor, cada uno con
su propia fuente — ver CONF_ACTUATOR_POWER en const.py), para cuando ese
actuador en concreto no tiene ni sensor propio ni un valor fijo
declarado. Util sobre todo para equipos donde no se puede instrumentar
el consumo real, como un aire acondicionado con maquina exterior
COMPARTIDA entre varias interiores.

Igual que thermal_model.py: nada de aprendizaje automatico opaco. Se
busca en el historico de un sensor de potencia GENERAL de la vivienda
(CONF_HOME_POWER_SENSOR) el salto medido justo antes/despues de cada vez
que este actuador concreto cambia de estado, y se toma la MEDIANA de
esos saltos — un numero que se puede explicar en una frase: "de media,
este actuador sube el consumo de la vivienda en 1200W al encenderse".
Sin historico suficiente, `reliable=False` — climate.py se encarga de
caer entonces a la potencia estimada fija declarada para ese actuador
(ver CONF_ACTUATOR_POWER en const.py) si la hay, o a nada.

MEJORA CLAVE para el caso de maquina exterior compartida: antes de
aceptar una transicion como muestra valida, se comprueba si ALGUNA OTRA
zona Climate Orchestrator (sus propios switches/climate.*/humidifier.*
declarados) ya estaba activa en ese mismo instante — si es asi, esa
transicion se descarta (no se puede aislar limpiamente que parte del
salto es de esta zona y cual de la otra). Solo se cuentan transiciones
"limpias": ningun otro equipo conocido cambiando ni ya encendido en la
ventana de muestreo. Esto es justo lo que hace falta para un split
multi-interior con un unico compresor: si dos interiores comparten
maquina exterior, un salto medido mientras la otra ya estaba en marcha
no dice nada fiable sobre el consumo de la primera.

Sigue siendo una CORRELACION, no una medida directa — `reliable=False`
con pocas muestras limpias, nunca se presenta como un dato medido de
verdad.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance, history
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

MIN_RUN_MINUTES = 3            # la potencia cambia al instante, no hace falta un tramo largo como en thermal_model
MIN_VALID_SAMPLES = 4          # transiciones LIMPIAS minimas antes de fiarse de la mediana
SAMPLE_WINDOW_MINUTES = 2      # cuanto se promedia el sensor de potencia justo antes/despues de la transicion
MIN_DELTA_W = 10               # saltos menores se descartan como ruido de medida, no un cambio real


class _SyntheticState:
    __slots__ = ("state", "last_changed")

    def __init__(self, state: str, last_changed: datetime) -> None:
        self.state = state
        self.last_changed = last_changed


def _history_for(hass: HomeAssistant, entity_id: str, start: datetime, end: datetime) -> list:
    result = history.state_changes_during_period(hass, start, end, entity_id, no_attributes=True)
    return result.get(entity_id, [])


def _state_runs(states: list) -> list[tuple[datetime, datetime, str]]:
    runs = []
    for i, s in enumerate(states):
        if s.state not in ("on", "off"):
            continue
        start = s.last_changed
        end = states[i + 1].last_changed if i + 1 < len(states) else dt_util.utcnow()
        runs.append((start, end, s.state))
    return runs


def _climate_on_off_runs(hass: HomeAssistant, entity_id: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime, str]]:
    """Traduce el historico de un climate.* a on/off via su `hvac_action`
    (heating/cooling frente al resto) — mismo truco que thermal_model.py."""
    result = history.get_significant_states(
        hass, start, end, [entity_id], significant_changes_only=False, minimal_response=False, no_attributes=False,
    )
    raw = result.get(entity_id, [])
    synthetic = [
        _SyntheticState("on" if (s.attributes or {}).get("hvac_action") in ("heating", "cooling") else "off", s.last_changed)
        for s in raw
    ]
    return _state_runs(synthetic)


def _entity_runs(hass: HomeAssistant, entity_id: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime, str]]:
    if entity_id.startswith("climate."):
        return _climate_on_off_runs(hass, entity_id, start, end)
    return _state_runs(_history_for(hass, entity_id, start, end))


def _other_zone_entities(hass: HomeAssistant, this_entry_id: str) -> list[str]:
    """Actuadores declarados en TODAS las demas zonas Climate Orchestrator
    — para poder descartar transiciones contaminadas por otro equipo
    compartiendo la misma maquina exterior."""
    others: list[str] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == this_entry_id:
            continue
        data = entry.data
        others.extend(data.get("heat_switches") or [])
        others.extend(data.get("cool_switches") or [])
        others.extend(data.get("climate_entities") or [])
        others.extend(data.get("humidifier_entities") or [])
    return others


def _active_intervals(hass: HomeAssistant, entities: list[str], start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Tramos (inicio, fin) en los que CUALQUIERA de estas entidades
    estuvo encendida/calentando/enfriando — la union de todas, para
    comprobar solapamiento rapido despues."""
    intervals = []
    for entity_id in entities:
        for run_start, run_end, state in _entity_runs(hass, entity_id, start, end):
            if state == "on":
                intervals.append((run_start, run_end))
    return intervals


def _overlaps(intervals: list[tuple[datetime, datetime]], t0: datetime, t1: datetime) -> bool:
    return any(a < t1 and b > t0 for a, b in intervals)


def _avg_around(power_states: list, center_ts: datetime, before: bool, window_minutes: float) -> float | None:
    vals = []
    for s in power_states:
        try:
            v = float(s.state)
        except (TypeError, ValueError):
            continue
        delta_min = (center_ts - s.last_changed).total_seconds() / 60 if before \
            else (s.last_changed - center_ts).total_seconds() / 60
        if 0 <= delta_min <= window_minutes:
            vals.append(v)
    return statistics.mean(vals) if vals else None


_EMPTY_ENTRY = {"learned_power_w": None, "reliable": False, "samples_used": 0, "samples_discarded_other_zone": 0}


def _learn_one_entity(hass: HomeAssistant, entity_id: str, home_power_sensor: str, other_intervals: list, power_states: list,
                       start: datetime, end: datetime) -> dict:
    model = dict(_EMPTY_ENTRY)
    runs = _entity_runs(hass, entity_id, start, end)
    if not runs:
        return model

    deltas: list[float] = []
    discarded_other_zone = 0
    for run_start, run_end, _state in runs:
        dur_min = (run_end - run_start).total_seconds() / 60
        if dur_min < MIN_RUN_MINUTES:
            continue

        window_start = run_start - timedelta(minutes=SAMPLE_WINDOW_MINUTES)
        window_end = run_start + timedelta(minutes=SAMPLE_WINDOW_MINUTES)
        if other_intervals and _overlaps(other_intervals, window_start, window_end):
            discarded_other_zone += 1
            continue

        before = _avg_around(power_states, run_start, before=True, window_minutes=SAMPLE_WINDOW_MINUTES)
        after = _avg_around(power_states, run_start, before=False, window_minutes=SAMPLE_WINDOW_MINUTES)
        if before is None or after is None:
            continue
        delta = abs(after - before)
        if delta >= MIN_DELTA_W:
            deltas.append(delta)

    model["samples_discarded_other_zone"] = discarded_other_zone
    if len(deltas) < MIN_VALID_SAMPLES:
        return model

    model["learned_power_w"] = round(statistics.median(deltas), 0)
    model["reliable"] = True
    model["samples_used"] = len(deltas)
    return model


def _compute_power_model_sync(hass: HomeAssistant, entities: list[str], entry_id: str, home_power_sensor: str, days: int) -> dict:
    """Aprende, para CADA entidad de `entities` (las de esta zona sin
    sensor propio ni valor fijo declarado), su salto tipico de potencia —
    ver `_learn_one_entity`. Las demas zonas Climate Orchestrator se
    consultan UNA vez y se reutilizan para todas las entidades de esta
    zona (mas barato que repetir la consulta por cada una)."""
    result: dict[str, dict] = {}
    if not entities or not home_power_sensor:
        return result

    end = dt_util.utcnow()
    start = end - timedelta(days=days)

    other_entities = _other_zone_entities(hass, entry_id)
    other_intervals = _active_intervals(hass, other_entities, start, end) if other_entities else []
    power_states = _history_for(hass, home_power_sensor, start, end)

    for entity_id in entities:
        result[entity_id] = _learn_one_entity(hass, entity_id, home_power_sensor, other_intervals, power_states, start, end)
    return result


async def async_get_power_model(hass: HomeAssistant, entities: list[str], entry_id: str, home_power_sensor: str, days: int) -> dict:
    """Consulta el recorder en su propio hilo (nunca en el loop de eventos
    de HA). Devuelve {entity_id: {"learned_power_w", "reliable",
    "samples_used", "samples_discarded_other_zone"}} — solo para las
    entidades pedidas (las que de verdad necesitan aprenderse)."""
    if not entities or not home_power_sensor:
        return {}
    try:
        return await get_instance(hass).async_add_executor_job(
            _compute_power_model_sync, hass, entities, entry_id, home_power_sensor, days
        )
    except Exception:  # el recorder puede no estar listo, o sin historico todavia
        _LOGGER.debug("No se pudo aprender la potencia de %s actuadores todavia", len(entities), exc_info=True)
        return {}
