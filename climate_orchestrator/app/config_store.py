"""
Persistencia de la configuracion del usuario (zonas de clima, tiempo,
broker MQTT). Todo editable desde la interfaz, nada hardcodeado — misma
filosofia que Battery Orchestrator (ver su config_store.py). Se guarda en
un JSON dentro del directorio persistente del addon.
"""

from __future__ import annotations

import json
import os
import threading
import uuid

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/data/config.json")

_lock = threading.RLock()  # reentrante: load_config() llama a save_config() en el primer arranque

DEFAULT_CONFIG = {
    "zones": [],
    "weather_entity": "",  # weather.* con previsión horaria (AEMET, Met.no...), opcional pero recomendado
    "mqtt": {
        "host": "core-mosquitto",  # nombre del addon oficial "Mosquitto broker" dentro de la red interna de HA
        "port": 1883,
        "username": "",
        "password": "",
    },
    "general": {
        "cycle_seconds": 60,          # frecuencia del control fino (histeresis del actuador)
        "plan_refresh_seconds": 300,  # frecuencia de recalculo del plan del dia completo (mas caro: historico + previsión)
        "horizon_hours": 36,          # >24h para que el preaviso de "ahorro" vea ya la franja de manana de madrugada
        "dry_run": True,
        "history_days_for_inertia": 14,
        "language": "auto",  # "auto" | "es" | "en"
    },
}

DEFAULT_ZONE = {
    "name": "",
    "enabled": True,
    "hvac_capability": "heat",       # "heat" | "cool" | "heat_cool"
    "current_temp_sensor": "",
    "humidity_sensor": "",
    "actuator_mode": "switch",       # "switch" (enciende/apaga un calefactor/AC) | "climate" (delega en un climate.* ya existente)
    "heat_switch": "",
    "cool_switch": "",
    "underlying_climate": "",
    "min_temp": 15.0,
    "max_temp": 30.0,
    "deadband": 0.3,                 # histeresis en grados: evita ciclar el actuador por decimas
    "min_on_seconds": 300,
    "min_off_seconds": 300,
    "comfort_temp": 21.0,
    "eco_temp": 18.0,
    "away_temp": 16.0,
    "schedule": [],                  # [{"days": [0..6] (vacio = todos), "start": "07:00", "end": "23:00"}] — dentro: comfort; fuera: eco (salvo presencia, ver abajo)
    "presence_entities": [],         # person.*/device_tracker.*/binary_sensor.* — CUALQUIERA en "on"/"home" cuenta como zona ocupada
    "presence_overrides_schedule": True,
    "outdoor_temp_sensor": "",       # opcional; si no, se usa weather_entity global
    "priority": "confort",           # "confort" (calienta ya en cuanto hace falta) | "ahorro" (usa inercia termica: arranca lo mas tarde posible para llegar justo) | "manual" (nunca decide sola)
    "manual_override_hours": 2.0,    # cuanto dura un cambio manual desde HA antes de volver al plan automatico
}


def load_config() -> dict:
    with _lock:
        if not os.path.exists(CONFIG_PATH):
            save_config(DEFAULT_CONFIG)
            return json.loads(json.dumps(DEFAULT_CONFIG))
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        _deep_merge(merged, cfg)
        # cada zona tambien se completa con las claves nuevas que falten,
        # por si se actualiza el esquema entre versiones
        merged["zones"] = [_merge_zone(z) for z in merged.get("zones", [])]
        return merged


def _merge_zone(zone: dict) -> dict:
    merged = json.loads(json.dumps(DEFAULT_ZONE))
    _deep_merge(merged, zone)
    merged["id"] = zone.get("id") or str(uuid.uuid4())[:8]
    return merged


def save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with _lock:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)


def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def add_zone(cfg: dict, zone: dict) -> dict:
    merged = _merge_zone(zone)
    cfg["zones"].append(merged)
    save_config(cfg)
    return merged


def update_zone(cfg: dict, zone_id: str, updates: dict) -> dict | None:
    for z in cfg["zones"]:
        if z["id"] == zone_id:
            z.update(updates)
            save_config(cfg)
            return z
    return None


def delete_zone(cfg: dict, zone_id: str) -> bool:
    before = len(cfg["zones"])
    cfg["zones"] = [z for z in cfg["zones"] if z["id"] != zone_id]
    save_config(cfg)
    return len(cfg["zones"]) < before
