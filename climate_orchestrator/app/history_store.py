"""
Historico ligero de decisiones ya ejecutadas por zona (no previstas), para
poder mostrar la tabla completa del dia (00:00 a la hora actual) mezclando
lo que ya paso con lo que queda por delante en el plan. Mismo patron que
Battery Orchestrator (una entrada por hora de reloj y por zona; cada ciclo
dentro de esa hora sobreescribe con la ultima decision real tomada).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta

HISTORY_PATH = os.environ.get("HISTORY_PATH", "/data/history.json")
MAX_AGE_HOURS = 24 * 3  # 3 dias es de sobra para la tabla de "hoy"; no hace falta mas

_lock = threading.RLock()


def _hour_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H")


def _load() -> dict:
    with _lock:
        if not os.path.exists(HISTORY_PATH):
            return {}
        try:
            with open(HISTORY_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with _lock:
        with open(HISTORY_PATH, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def record(zone_id: str, now: datetime, entry: dict) -> None:
    data = _load()
    zone_hist = data.setdefault(zone_id, {})
    zone_hist[_hour_key(now)] = entry

    cutoff_key = _hour_key(now - timedelta(hours=MAX_AGE_HOURS))
    data[zone_id] = {k: v for k, v in zone_hist.items() if k >= cutoff_key}

    _save(data)


def get_today(zone_id: str, now: datetime) -> list[dict]:
    data = _load()
    zone_hist = data.get(zone_id, {})
    today_prefix = now.strftime("%Y-%m-%d")
    return [v for k, v in sorted(zone_hist.items()) if k.startswith(today_prefix) and k < _hour_key(now)]


def clear_zone(zone_id: str) -> None:
    data = _load()
    data.pop(zone_id, None)
    _save(data)
