"""
Cliente minimo para hablar con Home Assistant.

Dentro de un addon, HA Supervisor inyecta SUPERVISOR_TOKEN y el proxy
interno en http://supervisor/core/api/. Para desarrollo local fuera del
addon, se puede usar HA_URL + HA_TOKEN (token de larga duracion) en su lugar.

Mismo cliente que usa Battery Orchestrator (misma filosofia: nada de
sensores ni servicios ocultos, solo lo que el usuario declara en la
configuracion), con los metodos propios de clima anadidos al final
(previsión meteorologica y sensores binarios de presencia).
"""

from __future__ import annotations

import os
import statistics
from datetime import datetime, timedelta, timezone

import requests

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
if SUPERVISOR_TOKEN:
    BASE_URL = "http://supervisor/core/api"
    TOKEN = SUPERVISOR_TOKEN
else:
    BASE_URL = os.environ.get("HA_URL", "http://localhost:8123/api")
    TOKEN = os.environ.get("HA_TOKEN", "")

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
TIMEOUT = 10


class HAError(Exception):
    pass


def get_state(entity_id: str):
    r = requests.get(f"{BASE_URL}/states/{entity_id}", headers=HEADERS, timeout=TIMEOUT)
    if r.status_code == 404:
        raise HAError(f"Entidad no encontrada: {entity_id}")
    r.raise_for_status()
    return r.json()


STALE_STATES = {"unavailable", "unknown", "none", ""}


def get_numeric_state(entity_id: str, default: float | None = 0.0) -> float | None:
    """Valor numerico de una entidad, o `default` si no existe / no esta
    disponible (puede ser None para que el llamante decida saltarsela en
    vez de asumir un valor inventado)."""
    try:
        s = get_state(entity_id)["state"]
        if s.strip().lower() in STALE_STATES:
            return default
        return float(s)
    except (HAError, ValueError, KeyError):
        return default


def is_on(entity_id: str) -> bool | None:
    """Para binary_sensor/person/device_tracker/switch: True si el estado es
    'on'/'home', False si es 'off'/'not_home', None si no hay dato fiable."""
    try:
        s = get_state(entity_id)["state"].strip().lower()
    except (HAError, KeyError):
        return None
    if s in STALE_STATES:
        return None
    return s in ("on", "home", "playing")


def call_service(domain: str, service: str, entity_id: str | None = None, extra: dict | None = None):
    payload = {}
    if entity_id:
        payload["entity_id"] = entity_id
    if extra:
        payload.update(extra)
    r = requests.post(f"{BASE_URL}/services/{domain}/{service}", headers=HEADERS, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def turn_on(entity_id: str):
    domain = entity_id.split(".")[0]
    return call_service(domain, "turn_on", entity_id)


def turn_off(entity_id: str):
    domain = entity_id.split(".")[0]
    return call_service(domain, "turn_off", entity_id)


def publish_sensor(entity_id: str, state, attributes: dict | None = None):
    """Publica un sensor propio del orquestador en HA (para dashboards)."""
    payload = {"state": state, "attributes": attributes or {}}
    r = requests.post(f"{BASE_URL}/states/{entity_id}", headers=HEADERS, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_history(entity_id: str, days: int) -> list[dict]:
    # OJO: la marca de tiempo va EMBEBIDA en la ruta (no en query), y tiene
    # que ir "limpia" — igual que en Battery Orchestrator, ver su ha_client.py.
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get(
        f"{BASE_URL}/history/period/{start}",
        headers=HEADERS,
        params={"filter_entity_id": entity_id, "minimal_response": "true"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return data[0] if data else []


MIN_SAMPLES_PER_HOUR = 3


def hourly_average_forecast(entity_id: str, horizon_hours: int, days: int = 21, default: float = 0.0) -> list[float]:
    """Previsión explicable para cualquier sensor numerico: para cada hora
    del horizonte, la media de esa MISMA hora del dia en los ultimos `days`
    dias de historico real. Se usa como respaldo de la temperatura exterior
    cuando no hay `weather.*` con previsión horaria declarado."""
    raw = get_history(entity_id, days)
    if not raw:
        for fallback_days in (10, 7, 3, 1):
            if fallback_days >= days:
                continue
            raw = get_history(entity_id, fallback_days)
            if raw:
                break
    if not raw:
        current = get_numeric_state(entity_id, default=default)
        return [current] * horizon_hours

    buckets: dict[int, list[float]] = {h: [] for h in range(24)}
    for point in raw:
        try:
            val = float(point["state"])
        except (KeyError, ValueError):
            continue
        ts = datetime.fromisoformat(point["last_changed"].replace("Z", "+00:00"))
        buckets[ts.astimezone().hour].append(val)

    hourly_avg: dict[int, float | None] = {}
    for h, vals in buckets.items():
        hourly_avg[h] = statistics.mean(vals) if len(vals) >= MIN_SAMPLES_PER_HOUR else None

    known = [v for v in hourly_avg.values() if v is not None]
    fallback = statistics.mean(known) if known else default
    for h in range(24):
        if hourly_avg[h] is None:
            hourly_avg[h] = fallback

    now = datetime.now()
    return [hourly_avg[(now.hour + i) % 24] for i in range(horizon_hours)]


def weather_forecast(entity_id: str, horizon_hours: int) -> list[float] | None:
    """
    Temperatura exterior prevista, hora a hora, leyendo el servicio
    `weather.get_forecasts` (HA >= 2024.3) de una entidad `weather.*` ya
    existente en tu instalacion (AEMET, OpenWeatherMap, Met.no...). Devuelve
    None si la entidad no tiene previsión horaria disponible (algunas
    integraciones solo dan previsión diaria) — en ese caso el llamante debe
    caer en `hourly_average_forecast` sobre un sensor de temperatura
    exterior, si hay uno declarado.
    """
    try:
        r = requests.post(
            f"{BASE_URL}/services/weather/get_forecasts",
            headers=HEADERS,
            params={"return_response": "true"},
            json={"entity_id": entity_id, "type": "hourly"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        forecasts = data.get("service_response", {}).get(entity_id, {}).get("forecast", [])
    except (requests.RequestException, ValueError, KeyError):
        return None
    if not forecasts:
        return None
    temps = [f.get("temperature") for f in forecasts[:horizon_hours] if f.get("temperature") is not None]
    if not temps:
        return None
    if len(temps) < horizon_hours:
        temps += [temps[-1]] * (horizon_hours - len(temps))
    return temps[:horizon_hours]


def current_outdoor_temp(weather_entity: str | None, outdoor_sensor: str | None) -> float | None:
    """Temperatura exterior AHORA MISMO: prioriza el sensor dedicado (mas
    preciso y local) sobre el atributo de la entidad `weather.*` (a menudo
    la estacion oficial mas cercana, no tu jardin)."""
    if outdoor_sensor:
        v = get_numeric_state(outdoor_sensor, default=None)
        if v is not None:
            return v
    if weather_entity:
        try:
            state = get_state(weather_entity)
            v = state.get("attributes", {}).get("temperature")
            return float(v) if v is not None else None
        except (HAError, TypeError, ValueError):
            return None
    return None
