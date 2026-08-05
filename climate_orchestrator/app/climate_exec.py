"""
Ejecucion real de la decision de cada zona sobre su actuador.

Dos modos, declarados por zona (`actuator_mode`):

  "switch": el addon enciende/apaga directamente un switch de
  calefactor/AC. Aplica histeresis con la temperatura REAL leida ahora
  mismo (no la del plan horario, que es una proyeccion) mas un anti-ciclado
  por tiempo minimo encendido/apagado, para no destrozar el rele a
  base de encendidos y apagados seguidos por una decima de grado.

  "climate": el addon delega en un climate.* que ya existe en HA (p.ej. un
  termostato de valvula termostatica con su propia electronica) — solo le
  manda el modo y la temperatura objetivo que decidio el plan; el
  encendido/apagado fino lo gestiona esa entidad sola.

El estado de anti-ciclado vive en memoria (se resetea si el addon
reinicia) — con el ciclo de control por defecto a 60s el impacto de perder
esa memoria en un reinicio puntual es minimo.
"""

from __future__ import annotations

import logging
from datetime import datetime

import ha_client

log = logging.getLogger("climate_orchestrator.exec")

_last_change: dict[str, dict] = {}  # entity_id -> {"state": "on"/"off", "since": datetime}


def _record_change(entity_id: str, state: str, now: datetime) -> None:
    _last_change[entity_id] = {"state": state, "since": now}


def _can_switch(entity_id: str, now: datetime, min_on_s: float, min_off_s: float) -> bool:
    last = _last_change.get(entity_id)
    if not last:
        return True
    elapsed = (now - last["since"]).total_seconds()
    needed = min_on_s if last["state"] == "on" else min_off_s
    return elapsed >= needed


def _drive_switch(entity_id: str, desired_on: bool, min_on_s: float, min_off_s: float,
                   dry_run: bool, now: datetime) -> str | None:
    if not entity_id:
        return None
    current = ha_client.is_on(entity_id)
    desired_state = "on" if desired_on else "off"
    if current is None:
        return f"{entity_id}: sensor no disponible, se omite este ciclo"
    if (current and desired_on) or (not current and not desired_on):
        # ya esta en el estado deseado; si no habia registro previo (p.ej.
        # arranque del addon), se registra ahora para que el anti-ciclado
        # cuente desde un punto real, no desde "siempre permitido"
        if entity_id not in _last_change:
            _record_change(entity_id, "on" if current else "off", now)
        return None
    if not _can_switch(entity_id, now, min_on_s, min_off_s):
        last = _last_change[entity_id]
        remaining = (min_on_s if last["state"] == "on" else min_off_s) - (now - last["since"]).total_seconds()
        return f"{entity_id}: esperando anti-ciclado ({round(remaining)}s mas antes de poder {'apagar' if desired_on else 'encender'})"
    if not dry_run:
        (ha_client.turn_on if desired_on else ha_client.turn_off)(entity_id)
    _record_change(entity_id, desired_state, now)
    verb = "encendido" if desired_on else "apagado"
    prefix = "[SIMULACION] " if dry_run else ""
    return f"{prefix}{entity_id}: {verb}"


def execute(zone: dict, action: str, target_temp: float, dry_run: bool, now: datetime) -> tuple[str, list[str]]:
    """
    Ejecuta la decision de esta zona para el ciclo actual.
    Devuelve (hvac_action_real, log_lines): hvac_action_real es lo que HA
    debe mostrar como accion real del termostato ("heating"/"cooling"/"idle"/"off"),
    que en modo switch puede no coincidir exactamente con `action` del plan
    (p.ej. plan dice "heat" pero el anti-ciclado todavia no deja encender:
    la accion real ese instante sigue siendo "idle").
    """
    if not zone.get("enabled", True):
        return "off", []

    capability = zone.get("hvac_capability", "heat")
    mode = zone.get("actuator_mode", "switch")

    if mode == "climate":
        entity = zone.get("underlying_climate")
        if not entity:
            return "idle", ["zona sin climate.* delegado configurado"]
        hvac_mode = {"heat": "heat", "cool": "cool", "idle": "off"}.get(action, "off")
        if dry_run:
            return action, [f"[SIMULACION] {entity}: hvac_mode={hvac_mode}, temperatura={target_temp:.1f}°C"]
        try:
            ha_client.call_service("climate", "set_hvac_mode", entity, {"hvac_mode": hvac_mode})
            if hvac_mode != "off":
                ha_client.call_service("climate", "set_temperature", entity, {"temperature": target_temp})
        except Exception as e:
            return "idle", [f"{entity}: fallo al mandar la orden ({e})"]
        return action, [f"{entity}: hvac_mode={hvac_mode}, temperatura={target_temp:.1f}°C"]

    # modo "switch"
    log_lines: list[str] = []
    heat_line = cool_line = None

    if capability in ("heat", "heat_cool") and zone.get("heat_switch"):
        heat_line = _drive_switch(
            zone["heat_switch"], desired_on=(action == "heat"),
            min_on_s=zone.get("min_on_seconds", 300), min_off_s=zone.get("min_off_seconds", 300),
            dry_run=dry_run, now=now,
        )
    if capability in ("cool", "heat_cool") and zone.get("cool_switch"):
        cool_line = _drive_switch(
            zone["cool_switch"], desired_on=(action == "cool"),
            min_on_s=zone.get("min_on_seconds", 300), min_off_s=zone.get("min_off_seconds", 300),
            dry_run=dry_run, now=now,
        )
    log_lines = [line for line in (heat_line, cool_line) if line]

    real_heat_on = ha_client.is_on(zone.get("heat_switch")) if zone.get("heat_switch") else False
    real_cool_on = ha_client.is_on(zone.get("cool_switch")) if zone.get("cool_switch") else False
    if real_heat_on:
        hvac_action_real = "heating"
    elif real_cool_on:
        hvac_action_real = "cooling"
    else:
        hvac_action_real = "idle"

    return hvac_action_real, log_lines
