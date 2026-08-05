"""
Expone cada zona como una entidad `climate.*` REAL de Home Assistant, via
MQTT Discovery — el mismo mecanismo que usan ESPHome, Zigbee2MQTT, etc.

Por que MQTT y no un custom_component: Climate Orchestrator sigue siendo un
addon independiente con su propia web (igual que Battery Orchestrator), no
un componente que vive dentro del arranque de HA Core. MQTT Discovery es la
forma soportada de que un proceso EXTERNO cree una entidad de primera clase
en HA (aparece en Lovelace, Google Home, Alexa... como cualquier termostato)
sin tener que escribir una integracion de HA Core aparte.

Requiere que el addon oficial "Mosquitto broker" (u otro broker MQTT) este
instalado y la integracion MQTT de HA configurada — se avisa en la interfaz
si no se puede conectar, nunca falla en silencio.

Cada zona publica:
  - estado (hvac_mode: off/heat/cool/heat_cool)
  - accion real (hvac_action: heating/cooling/idle/off) — lo que EN ESTE
    INSTANTE esta haciendo el actuador, no el modo elegido
  - temperatura actual (del sensor declarado) y objetivo
  - un atributo JSON con el MOTIVO de la decision de este ciclo, en texto
    plano — el mismo principio de "nada de cajas negras" de Battery
    Orchestrator, visible en el propio dialogo de mas informacion del
    termostato en HA (atributo `reason`)

Y escucha los comandos que el usuario mande desde HA (tarjeta de
termostato, Google Home, Alexa...): cambiar modo o temperatura objetivo
pasa a "anulacion manual" durante `manual_override_hours` (configurable),
tras las cuales la zona vuelve sola al horario/plan automatico — igual de
predecible que versatile_thermostat, pero con el motivo explicito en todo
momento (ver `reason` en la interfaz y en el propio atributo).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt

log = logging.getLogger("climate_orchestrator.mqtt")

DISCOVERY_PREFIX = "homeassistant"
BASE_TOPIC = "climate_orchestrator"

_lock = threading.Lock()
_client: mqtt.Client | None = None
_connected = False
_manual_overrides: dict[str, dict] = {}  # zone_id -> {"until": iso, "mode": str|None, "temperature": float|None}
_on_command = None  # callback(zone_id, kind, value) inyectado desde main.py


def _topics(zone_id: str) -> dict:
    base = f"{BASE_TOPIC}/{zone_id}"
    return {
        "config": f"{DISCOVERY_PREFIX}/climate/climate_orchestrator_{zone_id}/config",
        "mode_state": f"{base}/mode/state",
        "mode_command": f"{base}/mode/set",
        "temperature_state": f"{base}/temperature/state",
        "temperature_command": f"{base}/temperature/set",
        "current_temperature": f"{base}/current_temperature/state",
        "action": f"{base}/action/state",
        "availability": f"{base}/availability",
        "attributes": f"{base}/attributes/state",
    }


def is_connected() -> bool:
    with _lock:
        return _connected


def start(host: str, port: int, username: str, password: str, on_command) -> None:
    """Conecta al broker y arranca el loop en un hilo aparte. Reintenta solo
    (paho ya trae reconexion automatica) — nunca tumba el addon si el
    broker no esta disponible todavia al arrancar."""
    global _client, _on_command
    _on_command = on_command

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="climate_orchestrator")
    if username:
        client.username_pw_set(username, password or None)
    client.will_set(f"{BASE_TOPIC}/bridge/availability", payload="offline", retain=True)

    def _on_connect(c, userdata, flags, reason_code, properties=None):
        global _connected
        with _lock:
            _connected = reason_code == 0
        if reason_code == 0:
            log.info(f"Conectado al broker MQTT en {host}:{port}")
            c.publish(f"{BASE_TOPIC}/bridge/availability", "online", retain=True)
            c.subscribe(f"{BASE_TOPIC}/+/mode/set")
            c.subscribe(f"{BASE_TOPIC}/+/temperature/set")
        else:
            log.warning(f"Fallo al conectar al broker MQTT: reason_code={reason_code}")

    def _on_disconnect(c, userdata, flags, reason_code, properties=None):
        global _connected
        with _lock:
            _connected = False
        log.warning("Desconectado del broker MQTT")

    def _on_message(c, userdata, msg):
        try:
            parts = msg.topic.split("/")
            zone_id, kind = parts[1], parts[2]  # climate_orchestrator/<zone_id>/<mode|temperature>/set
            value = msg.payload.decode().strip()
        except (IndexError, UnicodeDecodeError):
            return
        if kind == "temperature":
            try:
                value = float(value)
            except ValueError:
                return
        _register_manual_override(zone_id, kind, value)
        if _on_command:
            try:
                _on_command(zone_id, kind, value)
            except Exception:
                log.exception(f"Fallo procesando comando manual de {zone_id}")

    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message

    try:
        client.connect(host, port, keepalive=30)
    except OSError as e:
        log.warning(f"No se pudo conectar al broker MQTT ({host}:{port}): {e}")
    client.loop_start()
    _client = client


def stop() -> None:
    global _client
    if _client:
        _client.publish(f"{BASE_TOPIC}/bridge/availability", "offline", retain=True)
        _client.loop_stop()
        _client.disconnect()
        _client = None


def _register_manual_override(zone_id: str, kind: str, value, hours: float = 2.0) -> None:
    with _lock:
        entry = _manual_overrides.setdefault(zone_id, {"until": None, "mode": None, "temperature": None})
        entry["until"] = (datetime.now() + timedelta(hours=hours)).isoformat()
        entry[{"mode": "mode", "temperature": "temperature"}.get(kind, kind)] = value


def set_manual_override_hours(zone_id: str, hours: float) -> None:
    """Permite a main.py ajustar la duracion de la anulacion en curso, por
    si la zona tiene un `manual_override_hours` propio distinto del general."""
    with _lock:
        entry = _manual_overrides.get(zone_id)
        if entry:
            entry["until"] = (datetime.now() + timedelta(hours=hours)).isoformat()


def get_manual_override(zone_id: str) -> dict | None:
    """Devuelve la anulacion manual vigente para esta zona (mode/temperature
    y hasta cuando), o None si no hay ninguna activa o ya caduco."""
    with _lock:
        entry = _manual_overrides.get(zone_id)
        if not entry or not entry.get("until"):
            return None
        if datetime.fromisoformat(entry["until"]) <= datetime.now():
            del _manual_overrides[zone_id]
            return None
        return dict(entry)


def clear_manual_override(zone_id: str) -> None:
    with _lock:
        _manual_overrides.pop(zone_id, None)


def publish_discovery(zone: dict) -> None:
    """Publica (retained) la configuracion de discovery de una zona. Se
    reenvia en cada arranque y cada vez que cambia la config de la zona —
    si se borra la zona, `remove_discovery` limpia el retained para que
    desaparezca tambien de HA."""
    if not _client:
        return
    t = _topics(zone["id"])
    modes = ["off"] + (["heat_cool"] if zone.get("hvac_capability") == "heat_cool"
                        else [zone.get("hvac_capability", "heat")])
    payload = {
        "name": zone["name"],
        "unique_id": f"climate_orchestrator_{zone['id']}",
        "modes": modes,
        "mode_state_topic": t["mode_state"],
        "mode_command_topic": t["mode_command"],
        "temperature_state_topic": t["temperature_state"],
        "temperature_command_topic": t["temperature_command"],
        "current_temperature_topic": t["current_temperature"],
        "action_topic": t["action"],
        "json_attributes_topic": t["attributes"],
        "availability_topic": t["availability"],
        "min_temp": zone.get("min_temp", 15),
        "max_temp": zone.get("max_temp", 30),
        "temp_step": 0.5,
        "precision": 0.1,
        "device": {
            "identifiers": ["climate_orchestrator"],
            "name": "Climate Orchestrator",
            "manufacturer": "Climate Orchestrator",
            "model": "Orquestador de zonas",
        },
    }
    _client.publish(t["config"], json.dumps(payload), retain=True)
    _client.publish(t["availability"], "online", retain=True)


def remove_discovery(zone_id: str) -> None:
    if not _client:
        return
    _client.publish(_topics(zone_id)["config"], "", retain=True)


def publish_state(zone_id: str, mode: str, action: str, current_temp: float | None,
                   target_temp: float | None, attributes: dict) -> None:
    """Publica el estado de una zona tras un ciclo: modo activo, accion real
    del actuador (heating/cooling/idle/off), temperaturas y el motivo de la
    decision (dentro de `attributes`, incluye siempre `reason`)."""
    if not _client:
        return
    t = _topics(zone_id)
    _client.publish(t["mode_state"], mode, retain=True)
    _client.publish(t["action"], action, retain=True)
    if current_temp is not None:
        _client.publish(t["current_temperature"], current_temp, retain=True)
    if target_temp is not None:
        _client.publish(t["temperature_state"], target_temp, retain=True)
    _client.publish(t["attributes"], json.dumps(attributes, ensure_ascii=False), retain=True)
