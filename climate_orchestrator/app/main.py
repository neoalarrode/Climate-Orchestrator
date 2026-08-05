from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.serving import make_server

import climate_exec
import config_store
import ha_client
import history_store
import mqtt_client
import scheduler
import thermal_model_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("climate_orchestrator")

app = Flask(__name__, static_folder="static", template_folder="templates")

# Igual que en Battery Orchestrator: puerto adicional de solo lectura para
# dejar el panel fijo en una tablet de pared sin pasar por el login de HA.
WALLPANEL_PORT = int(os.environ.get("WALLPANEL_PORT", 8198))
WALLPANEL_ALLOWED_GET = {"/", "/api/status", "/api/live"}


@app.before_request
def _restrict_wallpanel_port():
    if request.environ.get("SERVER_PORT") != str(WALLPANEL_PORT):
        return None
    if request.method == "GET" and request.path in WALLPANEL_ALLOWED_GET:
        return None
    return jsonify({
        "error": "No disponible desde el puerto de solo lectura (wallpanel). "
                 "Configura el add-on desde el panel lateral de Home Assistant.",
    }), 403


_state_lock = threading.Lock()
_last_status = {"last_run": None, "zones": [], "mqtt_connected": False, "error": None}


def _presence_now(zone: dict) -> bool | None:
    entities = zone.get("presence_entities") or []
    if not entities:
        return None
    results = [ha_client.is_on(e) for e in entities]
    known = [r for r in results if r is not None]
    if not known:
        return None
    return any(known)


def _outdoor_forecast(cfg: dict, zone: dict, horizon: int) -> list[float]:
    """Prioriza el sensor exterior propio de la zona (si lo tiene) sobre el
    tiempo global: mas preciso para esa habitacion/orientacion concreta."""
    outdoor_sensor = zone.get("outdoor_temp_sensor")
    weather_entity = cfg.get("weather_entity")

    if weather_entity:
        forecast = ha_client.weather_forecast(weather_entity, horizon)
        if forecast:
            if outdoor_sensor:
                # corrige la hora actual con la lectura real del sensor propio
                now_real = ha_client.get_numeric_state(outdoor_sensor, default=None)
                if now_real is not None:
                    forecast[0] = now_real
            return forecast

    if outdoor_sensor:
        return ha_client.hourly_average_forecast(outdoor_sensor, horizon, days=14, default=15.0)

    current = ha_client.current_outdoor_temp(weather_entity, outdoor_sensor)
    default = current if current is not None else (5.0 if zone.get("hvac_capability") != "cool" else 28.0)
    return [default] * horizon


def _run_zone_cycle(cfg: dict, zone: dict, now: datetime, dry_run: bool) -> dict:
    zone_id = zone["id"]
    horizon = int(cfg["general"]["horizon_hours"])

    current_temp = ha_client.get_numeric_state(zone["current_temp_sensor"], default=None) \
        if zone.get("current_temp_sensor") else None
    if current_temp is None:
        mqtt_client.publish_discovery(zone)
        return {
            "id": zone_id, "name": zone["name"], "error": "Sensor de temperatura no disponible.",
            "current_temp": None, "target_temp": None, "action": "idle", "reason": "sin datos",
            "level": None, "plan": [], "manual_override": None,
        }

    presence_now = _presence_now(zone)
    outdoor_forecast = _outdoor_forecast(cfg, zone, horizon)
    model = thermal_model_store.get_model(zone, days=cfg["general"]["history_days_for_inertia"])

    levels = scheduler.build_target_levels(
        now, horizon, zone.get("schedule", []), presence_now,
        zone.get("presence_overrides_schedule", True), zone.get("priority", "confort"),
    )
    plan = scheduler.build_plan(
        now=now, levels=levels, outdoor_forecast=outdoor_forecast, current_temp=current_temp,
        comfort_temp=float(zone["comfort_temp"]), eco_temp=float(zone["eco_temp"]), away_temp=float(zone["away_temp"]),
        hvac_capability=zone.get("hvac_capability", "heat"), priority=zone.get("priority", "confort"),
        deadband=float(zone.get("deadband", 0.3)),
        heating_rate_deg_h=model["heating_rate_deg_h"], cooling_rate_deg_h=model["cooling_rate_deg_h"],
        idle_loss_coeff=model["idle_loss_coeff"], thermal_model_reliable=model["reliable"],
    )
    now_hp = plan[0]

    override = mqtt_client.get_manual_override(zone_id)
    if override:
        ov_mode = override.get("mode")
        ov_temp = override.get("temperature") if override.get("temperature") is not None else now_hp.target_temp
        deadband = float(zone.get("deadband", 0.3))
        if ov_mode == "off":
            action = "idle"
        elif ov_mode in ("heat", "heat_cool") and current_temp < ov_temp - deadband:
            action = "heat"
        elif ov_mode in ("cool", "heat_cool") and current_temp > ov_temp + deadband:
            action = "cool"
        else:
            action = "idle"
        target_temp = ov_temp
        until_hm = override["until"][11:16]
        reason = f"anulado a mano hasta las {until_hm} (objetivo {ov_temp:.1f}°C)"
        mode_reported = ov_mode or "off"
    else:
        action = now_hp.action
        target_temp = now_hp.target_temp
        reason = now_hp.reason
        mode_reported = "off" if not zone.get("enabled", True) else zone.get("hvac_capability", "heat")

    hvac_action_real, log_lines = climate_exec.execute(zone, action, target_temp, dry_run, now)
    for line in log_lines:
        log.info(f"[{zone['name']}] {line}")
    log.info(f"[{zone['name']}] {now_hp.level} objetivo {target_temp:.1f}°C - {reason}")

    history_store.record(zone_id, now, {
        "dt": now.replace(minute=0, second=0, microsecond=0).isoformat(),
        "level": now_hp.level, "target_temp": round(target_temp, 1),
        "current_temp": round(current_temp, 1), "outdoor_temp": round(now_hp.outdoor_temp, 1),
        "action": action, "reason": reason,
    })

    mqtt_client.publish_discovery(zone)
    mqtt_client.publish_state(
        zone_id, mode=mode_reported, action=hvac_action_real,
        current_temp=current_temp, target_temp=target_temp,
        attributes={
            "reason": reason, "level": now_hp.level, "priority": zone.get("priority"),
            "thermal_model_reliable": model["reliable"],
            "heating_rate_deg_h": round(model["heating_rate_deg_h"], 2),
            "cooling_rate_deg_h": round(model["cooling_rate_deg_h"], 2),
            "dry_run": dry_run, "outdoor_temp": round(now_hp.outdoor_temp, 1),
        },
    )

    today_history = [{**e, "historical": True} for e in history_store.get_today(zone_id, now)]
    future_plan = [
        {
            "dt": hp.dt.isoformat(), "level": hp.level, "target_temp": round(hp.target_temp, 1),
            "predicted_temp": round(hp.predicted_temp, 1), "outdoor_temp": round(hp.outdoor_temp, 1),
            "action": hp.action, "reason": hp.reason, "historical": False,
        }
        for hp in plan
    ]

    return {
        "id": zone_id, "name": zone["name"], "error": None,
        "current_temp": round(current_temp, 1), "target_temp": round(target_temp, 1),
        "outdoor_temp": round(now_hp.outdoor_temp, 1), "level": now_hp.level,
        "action": action, "hvac_action_real": hvac_action_real, "mode": mode_reported, "reason": reason,
        "presence_now": presence_now, "thermal_model": model,
        "manual_override": override, "plan": today_history + future_plan,
    }


def run_cycle() -> None:
    cfg = config_store.load_config()
    dry_run = bool(cfg["general"]["dry_run"])
    now = datetime.now()

    zones = [z for z in cfg["zones"] if z.get("enabled", True)]
    if not zones:
        with _state_lock:
            _last_status.update(last_run=now.isoformat(), zones=[], mqtt_connected=mqtt_client.is_connected(),
                                 error="No hay zonas configuradas todavia.")
        return

    zone_statuses = []
    for zone in zones:
        try:
            zone_statuses.append(_run_zone_cycle(cfg, zone, now, dry_run))
        except Exception:
            log.exception(f"Fallo planificando la zona {zone.get('name')}")
            zone_statuses.append({"id": zone["id"], "name": zone["name"], "error": "Fallo en el ciclo, revisa el log del addon."})

    with _state_lock:
        _last_status.update(last_run=now.isoformat(), zones=zone_statuses,
                             mqtt_connected=mqtt_client.is_connected(), error=None)


def _on_mqtt_command(zone_id: str, kind: str, value) -> None:
    log.info(f"Comando manual recibido para zona {zone_id}: {kind}={value}")


def background_loop() -> None:
    while True:
        try:
            run_cycle()
        except Exception:
            log.exception("Fallo en el ciclo de planificacion")
            with _state_lock:
                _last_status["error"] = "Error en el ultimo ciclo, revisa los logs del addon."
        cfg = config_store.load_config()
        time.sleep(max(15, int(cfg["general"]["cycle_seconds"])))


# ---------------------------------------------------------------- API ----

@app.get("/api/config")
def api_get_config():
    return jsonify(config_store.load_config())


@app.post("/api/config")
def api_save_config():
    cfg = request.get_json(force=True)
    config_store.save_config(cfg)
    return jsonify(cfg)


@app.get("/api/config/export")
def api_export_config():
    cfg = config_store.load_config()
    body = json.dumps(cfg, indent=2, ensure_ascii=False)
    return Response(
        body, mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=climate_orchestrator_config.json"},
    )


@app.post("/api/config/import")
def api_import_config():
    cfg = request.get_json(force=True)
    required_keys = {"zones", "weather_entity", "mqtt", "general"}
    if not isinstance(cfg, dict) or not required_keys.issubset(cfg.keys()):
        return jsonify({"error": "El archivo no tiene el formato esperado de configuración."}), 400
    config_store.save_config(cfg)
    return jsonify(cfg)


@app.post("/api/zones")
def api_add_zone():
    cfg = config_store.load_config()
    zone = config_store.add_zone(cfg, request.get_json(force=True))
    mqtt_client.publish_discovery(zone)
    return jsonify(zone), 201


@app.put("/api/zones/<zone_id>")
def api_update_zone(zone_id):
    cfg = config_store.load_config()
    updated = config_store.update_zone(cfg, zone_id, request.get_json(force=True))
    if updated is None:
        return jsonify({"error": "no encontrada"}), 404
    mqtt_client.publish_discovery(updated)
    return jsonify(updated)


@app.delete("/api/zones/<zone_id>")
def api_delete_zone(zone_id):
    cfg = config_store.load_config()
    ok = config_store.delete_zone(cfg, zone_id)
    if ok:
        mqtt_client.remove_discovery(zone_id)
        mqtt_client.clear_manual_override(zone_id)
        history_store.clear_zone(zone_id)
    return jsonify({"deleted": ok})


@app.post("/api/zones/<zone_id>/clear_override")
def api_clear_override(zone_id):
    mqtt_client.clear_manual_override(zone_id)
    return jsonify({"cleared": True})


@app.get("/api/status")
def api_status():
    with _state_lock:
        return jsonify(_last_status)


@app.get("/api/live")
def api_live():
    """Lectura rapida de solo lectura, sin replanificar — pensada para
    refrescar el dashboard cada pocos segundos entre ciclos completos."""
    cfg = config_store.load_config()
    zones_live = []
    for zone in cfg["zones"]:
        current_temp = ha_client.get_numeric_state(zone.get("current_temp_sensor"), default=None) \
            if zone.get("current_temp_sensor") else None
        humidity = ha_client.get_numeric_state(zone.get("humidity_sensor"), default=None) \
            if zone.get("humidity_sensor") else None
        zones_live.append({
            "id": zone["id"], "name": zone["name"], "current_temp": current_temp, "humidity": humidity,
            "presence_now": _presence_now(zone),
        })
    return jsonify({"now": datetime.now().isoformat(), "zones": zones_live, "mqtt_connected": mqtt_client.is_connected()})


@app.post("/api/run_now")
def api_run_now():
    try:
        run_cycle()
    except Exception:
        log.exception("Fallo al forzar ciclo")
        return jsonify({"error": "No se pudo forzar el ciclo, revisa el log del addon"}), 500
    with _state_lock:
        return jsonify(_last_status)


@app.get("/")
def index():
    return send_from_directory("templates", "index.html")


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory("static", path)


def _run_wallpanel_server():
    try:
        server = make_server("0.0.0.0", WALLPANEL_PORT, app, threaded=True)
        log.info(f"Panel de solo lectura (wallpanel) escuchando en el puerto {WALLPANEL_PORT}")
        server.serve_forever()
    except OSError as e:
        log.warning(f"No se pudo abrir el puerto wallpanel ({WALLPANEL_PORT}): {e}")


def _start_mqtt():
    cfg = config_store.load_config()
    mqtt_cfg = cfg.get("mqtt", {})
    mqtt_client.start(
        host=mqtt_cfg.get("host", "core-mosquitto"), port=int(mqtt_cfg.get("port", 1883)),
        username=mqtt_cfg.get("username", ""), password=mqtt_cfg.get("password", ""),
        on_command=_on_mqtt_command,
    )
    for zone in cfg.get("zones", []):
        mqtt_client.publish_discovery(zone)


if __name__ == "__main__":
    _start_mqtt()
    t = threading.Thread(target=background_loop, daemon=True)
    t.start()
    wp = threading.Thread(target=_run_wallpanel_server, daemon=True)
    wp.start()
    app.run(host="0.0.0.0", port=8199, threaded=True)
