"""
Aprende la inercia termica REAL de cada zona a partir de su propio
historico: cuantos grados por hora sube (o baja) su temperatura con el
actuador encendido, y cuantos pierde/gana por hora con el actuador apagado
en funcion de la diferencia con el exterior.

Nada de machine learning ni de un solver: se buscan tramos continuos en el
historico donde el actuador estuvo en un mismo estado (encendido/apagado)
al menos `MIN_RUN_MINUTES`, se calcula la pendiente real de temperatura de
cada tramo, y se toma la MEDIANA de todos los tramos validos (robusta
frente a un tramo suelto con una ventana abierta o una visita que dispara
el termostato a mano). El resultado es un numero que se puede explicar en
una frase: "de media, esta zona sube 0.9°C por hora calentando".

Solo funciona con actuador de tipo "switch" (el addon controla el
encendido/apagado el mismo, asi que sabe con certeza cuando estuvo
actuando). Con actuador de tipo "climate" (delega en un climate.* ya
existente) no hay forma fiable de reconstruir desde el historico cuando
ese climate decidio calentar por su cuenta, asi que esas zonas se quedan
con los valores por defecto (conservadores) y `reliable=False` — se avisa
en la interfaz, nunca se inventa una cifra.
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime

import ha_client

MIN_RUN_MINUTES = 20
MIN_VALID_RUNS = 3
CACHE_SECONDS = 1800  # recalcular como mucho cada 30 min: es una consulta de historico cara

DEFAULT_HEATING_RATE = 0.9   # °C/h, conservador (radiador domestico tipico)
DEFAULT_COOLING_RATE = 1.2   # °C/h, conservador (split domestico tipico)
DEFAULT_IDLE_LOSS_COEFF = 0.08  # fraccion de la diferencia con el exterior que se pierde/gana por hora

_cache: dict[str, dict] = {}  # zone_id -> {"fetched_at": epoch, "model": {...}}


def _state_runs(history: list[dict]) -> list[tuple[datetime, datetime, str]]:
    """Tramos continuos (inicio, fin, estado) a partir del historico de una
    entidad on/off, tal como lo devuelve la API de HA (una entrada por
    cambio de estado)."""
    runs = []
    for i, point in enumerate(history):
        state = point.get("state")
        if state not in ("on", "off"):
            continue
        start = datetime.fromisoformat(point["last_changed"].replace("Z", "+00:00"))
        end = (
            datetime.fromisoformat(history[i + 1]["last_changed"].replace("Z", "+00:00"))
            if i + 1 < len(history) else datetime.now(start.tzinfo)
        )
        runs.append((start, end, state))
    return runs


def _temp_at_or_before(temp_history: list[dict], ts: datetime) -> float | None:
    """Ultima temperatura conocida en o antes de `ts` (interpolacion simple
    por el punto mas cercano, suficiente para una pendiente hora a hora)."""
    best = None
    for point in temp_history:
        try:
            pts = datetime.fromisoformat(point["last_changed"].replace("Z", "+00:00"))
            val = float(point["state"])
        except (KeyError, ValueError):
            continue
        if pts <= ts:
            best = val
        else:
            break
    return best


def _learn_rate(temp_history: list[dict], actuator_history: list[dict]) -> tuple[float | None, int]:
    """Mediana de °C/h de los tramos con el actuador en 'on', y cuantos
    tramos validos se usaron."""
    slopes = []
    for start, end, state in _state_runs(actuator_history):
        if state != "on":
            continue
        duration_h = (end - start).total_seconds() / 3600
        if duration_h * 60 < MIN_RUN_MINUTES:
            continue
        t0 = _temp_at_or_before(temp_history, start)
        t1 = _temp_at_or_before(temp_history, end)
        if t0 is None or t1 is None:
            continue
        slope = (t1 - t0) / duration_h
        if 0.05 <= abs(slope) <= 5.0:
            slopes.append(abs(slope))
    if len(slopes) < MIN_VALID_RUNS:
        return None, len(slopes)
    return statistics.median(slopes), len(slopes)


def _learn_idle_loss_coeff(temp_history: list[dict], actuator_history: list[dict],
                            outdoor_history: list[dict]) -> tuple[float | None, int]:
    coeffs = []
    for start, end, state in _state_runs(actuator_history):
        if state != "off":
            continue
        duration_h = (end - start).total_seconds() / 3600
        if duration_h * 60 < MIN_RUN_MINUTES:
            continue
        t0 = _temp_at_or_before(temp_history, start)
        t1 = _temp_at_or_before(temp_history, end)
        out0 = _temp_at_or_before(outdoor_history, start)
        out1 = _temp_at_or_before(outdoor_history, end)
        if None in (t0, t1, out0, out1):
            continue
        avg_delta = ((out0 - t0) + (out1 - t1)) / 2
        if abs(avg_delta) < 1.0:
            continue  # sin diferencia apreciable con el exterior, la pendiente no es fiable para este calculo
        slope = (t1 - t0) / duration_h
        coeff = slope / avg_delta
        if 0.0 <= coeff <= 0.6:
            coeffs.append(coeff)
    if len(coeffs) < MIN_VALID_RUNS:
        return None, len(coeffs)
    return statistics.median(coeffs), len(coeffs)


def get_model(zone: dict, days: int = 14, force_refresh: bool = False) -> dict:
    """
    Devuelve {"heating_rate_deg_h", "cooling_rate_deg_h", "idle_loss_coeff",
    "reliable", "runs_used"} para la zona. Cacheado `CACHE_SECONDS` — el
    historico no cambia lo bastante rapido como para recalcularlo en cada
    ciclo corto de control.
    """
    zone_id = zone["id"]
    cached = _cache.get(zone_id)
    if cached and not force_refresh and time.time() - cached["fetched_at"] < CACHE_SECONDS:
        return cached["model"]

    model = {
        "heating_rate_deg_h": DEFAULT_HEATING_RATE,
        "cooling_rate_deg_h": DEFAULT_COOLING_RATE,
        "idle_loss_coeff": DEFAULT_IDLE_LOSS_COEFF,
        "reliable": False,
        "runs_used": 0,
    }

    if zone.get("actuator_mode") != "switch" or not zone.get("current_temp_sensor"):
        _cache[zone_id] = {"fetched_at": time.time(), "model": model}
        return model

    try:
        temp_history = ha_client.get_history(zone["current_temp_sensor"], days)
    except Exception:
        _cache[zone_id] = {"fetched_at": time.time(), "model": model}
        return model

    runs_used = 0
    capability = zone.get("hvac_capability", "heat")

    if capability in ("heat", "heat_cool") and zone.get("heat_switch"):
        try:
            heat_history = ha_client.get_history(zone["heat_switch"], days)
            rate, n = _learn_rate(temp_history, heat_history)
            if rate is not None:
                model["heating_rate_deg_h"] = rate
                runs_used += n
        except Exception:
            pass

    if capability in ("cool", "heat_cool") and zone.get("cool_switch"):
        try:
            cool_history = ha_client.get_history(zone["cool_switch"], days)
            rate, n = _learn_rate(temp_history, cool_history)
            if rate is not None:
                model["cooling_rate_deg_h"] = rate
                runs_used += n
        except Exception:
            pass

    outdoor_sensor = zone.get("outdoor_temp_sensor")
    switch_for_idle = zone.get("heat_switch") or zone.get("cool_switch")
    if outdoor_sensor and switch_for_idle:
        try:
            outdoor_history = ha_client.get_history(outdoor_sensor, days)
            actuator_history = ha_client.get_history(switch_for_idle, days)
            coeff, n = _learn_idle_loss_coeff(temp_history, actuator_history, outdoor_history)
            if coeff is not None:
                model["idle_loss_coeff"] = coeff
                runs_used += n
        except Exception:
            pass

    model["runs_used"] = runs_used
    model["reliable"] = runs_used >= MIN_VALID_RUNS
    _cache[zone_id] = {"fetched_at": time.time(), "model": model}
    return model
