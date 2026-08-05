"""
Previsión de temperatura exterior para el precalentamiento con antelacion
(prioridad "ahorro"). Prioriza, en este orden:

  1. Previsión horaria de una entidad `weather.*` ya existente en tu HA
     (AEMET, Met.no, OpenWeatherMap...), corregida en la hora actual con el
     sensor exterior propio de la zona si lo tiene declarado.
  2. Si no hay `weather.*` global, o no da previsión horaria: la media
     historica real de esa MISMA hora del dia en los ultimos dias, a partir
     del sensor exterior propio de la zona (nada de aprendizaje automatico
     opaco).
  3. Si tampoco hay sensor propio: temperatura constante (la actual, o un
     valor por defecto seguro) — mejor una previsión plana y honesta que
     inventar una curva.
"""

from __future__ import annotations

import logging
import statistics
from datetime import timedelta

from homeassistant.components.recorder import get_instance, history
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

MIN_SAMPLES_PER_HOUR = 3


async def async_weather_hourly_forecast(hass: HomeAssistant, weather_entity: str, horizon_hours: int) -> list[float] | None:
    if not weather_entity:
        return None
    try:
        response = await hass.services.async_call(
            "weather", "get_forecasts", {"entity_id": weather_entity, "type": "hourly"},
            blocking=True, return_response=True,
        )
    except Exception:
        _LOGGER.debug("weather.get_forecasts no disponible para %s", weather_entity, exc_info=True)
        return None
    forecasts = (response or {}).get(weather_entity, {}).get("forecast", [])
    if not forecasts:
        return None
    temps = [f.get("temperature") for f in forecasts[:horizon_hours] if f.get("temperature") is not None]
    if not temps:
        return None
    if len(temps) < horizon_hours:
        temps += [temps[-1]] * (horizon_hours - len(temps))
    return temps[:horizon_hours]


def _hourly_average_sync(hass: HomeAssistant, entity_id: str, horizon_hours: int, days: int, default: float) -> list[float]:
    end = dt_util.utcnow()
    start = end - timedelta(days=days)
    result = history.state_changes_during_period(hass, start, end, entity_id, no_attributes=True)
    raw = result.get(entity_id, [])
    if not raw:
        return [default] * horizon_hours

    buckets: dict[int, list[float]] = {h: [] for h in range(24)}
    for point in raw:
        try:
            val = float(point.state)
        except (ValueError, TypeError):
            continue
        buckets[dt_util.as_local(point.last_changed).hour].append(val)

    hourly_avg: dict[int, float | None] = {}
    for h, vals in buckets.items():
        hourly_avg[h] = statistics.mean(vals) if len(vals) >= MIN_SAMPLES_PER_HOUR else None
    known = [v for v in hourly_avg.values() if v is not None]
    fallback = statistics.mean(known) if known else default
    for h in range(24):
        if hourly_avg[h] is None:
            hourly_avg[h] = fallback

    now_local = dt_util.now()
    return [hourly_avg[(now_local.hour + i) % 24] for i in range(horizon_hours)]


async def async_get_outdoor_forecast(hass: HomeAssistant, zone: dict, weather_entity: str, horizon_hours: int) -> list[float]:
    outdoor_sensor = zone.get("outdoor_temp_sensor")
    default = 5.0 if zone.get("hvac_capability") != "cool" else 28.0

    forecast = await async_weather_hourly_forecast(hass, weather_entity, horizon_hours)
    if forecast:
        if outdoor_sensor:
            state = hass.states.get(outdoor_sensor)
            if state is not None:
                try:
                    forecast[0] = float(state.state)
                except (ValueError, TypeError):
                    pass
        return forecast

    if outdoor_sensor:
        try:
            return await get_instance(hass).async_add_executor_job(
                _hourly_average_sync, hass, outdoor_sensor, horizon_hours, 14, default
            )
        except Exception:
            _LOGGER.debug("No se pudo calcular la previsión exterior por historico para %s", outdoor_sensor, exc_info=True)

    state = hass.states.get(outdoor_sensor) if outdoor_sensor else None
    try:
        current = float(state.state) if state else default
    except (ValueError, TypeError):
        current = default
    return [current] * horizon_hours
