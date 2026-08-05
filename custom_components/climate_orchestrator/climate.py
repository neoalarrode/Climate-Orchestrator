"""
La entidad climate.* de una zona.

Dos velocidades, a proposito (mismo espiritu que Battery Orchestrator, ver
const.py): la PREVISIÓN (previsión exterior, inercia térmica aprendida
del historico) es la parte cara y se recalcula cada
`forecast_refresh_minutes`. La DECISION de cada instante (temperatura
real, presencia real, puerta/ventana, y la ejecucion sobre el actuador) es
la parte barata y reacciona AL INSTANTE a los cambios de esos sensores via
`async_track_state_change_event` — el bus de eventos de HA, no un sondeo
periodico ni un websocket aparte. Esto es lo que un custom_component tiene
gratis y un addon externo no.

Nada de horario: el objetivo de la zona en cada momento lo decide un
PRESET (ver presets.py) — activado automaticamente segun la presencia
FISICA real de la habitacion (sensores PIR/mmWave, no "en casa"), o
fijado a mano. El MODO hvac (apagado/calor/frio/auto) y el PRESET activo
son elecciones PERSISTENTES del usuario — se restauran solas tras un
reinicio via RestoreEntity, igual que cualquier otro termostato de HA. La
TEMPERATURA objetivo, en cambio, es una anulacion TEMPORAL con caducidad
(`manual_override_hours`): pasado ese tiempo, la zona vuelve sola al
preset activo.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_time, async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import outdoor, presets as presets_module, scheduler, thermal_model
from .const import (
    CONF_AWAY_PRESET,
    CONF_COOL_ACTUATOR_MODE,
    CONF_COOL_CLIMATE,
    CONF_COOL_SWITCH,
    CONF_CURRENT_TEMP_SENSOR,
    CONF_DEADBAND,
    CONF_DOOR_WINDOW_ENTITIES,
    CONF_FORECAST_REFRESH_MINUTES,
    CONF_HEAT_ACTUATOR_MODE,
    CONF_HEAT_CLIMATE,
    CONF_HEAT_SWITCH,
    CONF_HISTORY_DAYS_FOR_INERTIA,
    CONF_HVAC_CAPABILITY,
    CONF_MANUAL_OVERRIDE_HOURS,
    CONF_MAX_TEMP,
    CONF_MIN_OFF_SECONDS,
    CONF_MIN_ON_SECONDS,
    CONF_MIN_TEMP,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_PRESENCE_ENTITIES,
    CONF_PRESENCE_PRESET,
    CONF_PRESETS_TEXT,
    CONF_PRIORITY,
    CONF_SIMULATE,
    CONF_WEATHER_ENTITY,
    DEFAULT_DEADBAND,
    DEFAULT_FORECAST_REFRESH_MINUTES,
    DEFAULT_HISTORY_DAYS_FOR_INERTIA,
    DEFAULT_MANUAL_OVERRIDE_HOURS,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_OFF_SECONDS,
    DEFAULT_MIN_ON_SECONDS,
    DEFAULT_MIN_TEMP,
    DEFAULT_OUTDOOR_HORIZON_HOURS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_ACTION_MAP = {"heat": HVACAction.HEATING, "cool": HVACAction.COOLING, "idle": HVACAction.IDLE}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([ClimateOrchestratorZone(hass, entry)])


class ClimateOrchestratorZone(ClimateEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_target_temperature_step = 0.5
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.zone: dict = dict(entry.data)

        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=self.zone.get("name", "Zona"),
            manufacturer="Climate Orchestrator",
            model="Zona de clima",
        )

        capability = self.zone.get(CONF_HVAC_CAPABILITY, "heat")
        if capability == "heat_cool":
            self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]
        elif capability == "cool":
            self._attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL]
        else:
            self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]

        try:
            self._presets = presets_module.parse_presets(self.zone.get(CONF_PRESETS_TEXT, ""))
        except ValueError:
            self._presets = []
        self._attr_preset_modes = [presets_module.PRESET_AUTO] + [p["name"] for p in self._presets]
        self._attr_preset_mode = presets_module.PRESET_AUTO

        self._attr_min_temp = float(self.zone.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP))
        self._attr_max_temp = float(self.zone.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP))
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_hvac_action = HVACAction.IDLE
        self._attr_current_temperature = None
        self._attr_target_temperature = self._presets[0]["target_temp"] if self._presets else 21.0
        self._attr_available = True

        self._outdoor_forecast: list[float] = []
        self._outdoor_now: float | None = None
        self._thermal_model: dict = {}
        self._reason = "sin calcular todavia"
        self._active_preset_name: str | None = None

        self._temp_override_value: float | None = None
        self._temp_override_until = None
        self._unsub_override_expiry = None

        self._switch_last_change: dict[str, tuple[str, object]] = {}

    # ---------------------------------------------------------- estado ----

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "reason": self._reason,
            "active_preset": self._active_preset_name,
            "priority": self.zone.get(CONF_PRIORITY),
            "simulate": self.zone.get(CONF_SIMULATE, True),
            "thermal_model_reliable": self._thermal_model.get("reliable", False),
            "heating_rate_deg_h": round(self._thermal_model.get("heating_rate_deg_h", 0) or 0, 2),
            "cooling_rate_deg_h": round(self._thermal_model.get("cooling_rate_deg_h", 0) or 0, 2),
            "outdoor_now": self._outdoor_now,
            "manual_override_until": self._temp_override_until.isoformat() if self._temp_override_until else None,
        }

    # ------------------------------------------------------- ciclo vida ----

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        valid_modes = {m.value for m in self._attr_hvac_modes}
        if last_state is not None and last_state.state in valid_modes:
            self._attr_hvac_mode = HVACMode(last_state.state)
        else:
            capability = self.zone.get(CONF_HVAC_CAPABILITY, "heat")
            self._attr_hvac_mode = {
                "heat": HVACMode.HEAT, "cool": HVACMode.COOL, "heat_cool": HVACMode.HEAT_COOL,
            }.get(capability, HVACMode.HEAT)

        if last_state is not None:
            last_preset = last_state.attributes.get("preset_mode")
            if last_preset in (self._attr_preset_modes or []):
                self._attr_preset_mode = last_preset
            if last_state.attributes.get(ATTR_TEMPERATURE) is not None:
                try:
                    self._attr_target_temperature = float(last_state.attributes[ATTR_TEMPERATURE])
                except (TypeError, ValueError):
                    pass

        watched = [e for e in [
            self.zone.get(CONF_CURRENT_TEMP_SENSOR),
            self.zone.get(CONF_OUTDOOR_TEMP_SENSOR),
            *(self.zone.get(CONF_PRESENCE_ENTITIES) or []),
            *(self.zone.get(CONF_DOOR_WINDOW_ENTITIES) or []),
        ] if e]
        if watched:
            self.async_on_remove(async_track_state_change_event(self.hass, watched, self._handle_reactive_event))

        refresh_minutes = self.zone.get(CONF_FORECAST_REFRESH_MINUTES, DEFAULT_FORECAST_REFRESH_MINUTES)
        self.async_on_remove(
            async_track_time_interval(self.hass, self._handle_forecast_refresh, timedelta(minutes=refresh_minutes))
        )

        await self._async_refresh_forecast()

    # -------------------------------------------------------- reactivo ----

    async def _handle_reactive_event(self, event) -> None:
        await self._async_decide_and_act()

    async def _handle_forecast_refresh(self, now) -> None:
        await self._async_refresh_forecast()

    async def _handle_override_expiry(self, now) -> None:
        self._temp_override_value = None
        self._temp_override_until = None
        self._unsub_override_expiry = None
        await self._async_decide_and_act()

    # ----------------------------------------------------- lecturas HA ----

    def _read_current_temp(self) -> float | None:
        sensor = self.zone.get(CONF_CURRENT_TEMP_SENSOR)
        if not sensor:
            return None
        state = self.hass.states.get(sensor)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _presence_now(self) -> bool | None:
        """Presencia FISICA de la zona AHORA MISMO — pensado sobre todo
        para sensores propios de la habitacion (PIR, mmWave, radar de
        presencia: binary_sensor de ocupacion/movimiento), no solo "en
        casa". person./device_tracker. tambien cuentan si se declaran,
        como señal adicional. Nunca se predice: solo el dato medido ahora."""
        entities = self.zone.get(CONF_PRESENCE_ENTITIES) or []
        if not entities:
            return None
        known = []
        for e in entities:
            state = self.hass.states.get(e)
            if state is None or state.state in ("unknown", "unavailable"):
                continue
            known.append(state.state in ("on", "home"))
        return any(known) if known else None

    def _door_window_open(self) -> bool:
        for e in self.zone.get(CONF_DOOR_WINDOW_ENTITIES) or []:
            state = self.hass.states.get(e)
            if state is not None and state.state == "on":
                return True
        return False

    def _effective_capability(self) -> str:
        """La capacidad que rige AHORA MISMO: si el usuario bloqueo el modo
        a "solo calor" o "solo frio" desde el termostato (HA/HomeKit/
        Matter), manda eso; en "auto" (heat_cool) o con capacidad simple,
        la declarada al configurar la zona."""
        if self._attr_hvac_mode == HVACMode.HEAT:
            return "heat"
        if self._attr_hvac_mode == HVACMode.COOL:
            return "cool"
        return self.zone.get(CONF_HVAC_CAPABILITY, "heat")

    # ---------------------------------------------------- previsión cara ----

    async def _async_refresh_forecast(self) -> None:
        weather_entity = self.zone.get(CONF_WEATHER_ENTITY, "")
        self._outdoor_forecast = await outdoor.async_get_outdoor_forecast(
            self.hass, self.zone, weather_entity, DEFAULT_OUTDOOR_HORIZON_HOURS
        )
        self._outdoor_now = self._outdoor_forecast[0] if self._outdoor_forecast else None
        self._thermal_model = await thermal_model.async_get_model(
            self.hass, self.zone, int(self.zone.get(CONF_HISTORY_DAYS_FOR_INERTIA, DEFAULT_HISTORY_DAYS_FOR_INERTIA))
        )
        await self._async_decide_and_act()

    # ---------------------------------------------------- decision barata --

    async def _async_decide_and_act(self) -> None:
        current_temp = self._read_current_temp()
        self._attr_current_temperature = current_temp
        if current_temp is None:
            self._attr_available = False
            self.async_write_ha_state()
            return
        self._attr_available = True

        deadband = float(self.zone.get(CONF_DEADBAND, DEFAULT_DEADBAND))
        min_temp = float(self.zone.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP))
        max_temp = float(self.zone.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP))
        capability = self._effective_capability()

        preset_name, preset_target, preset_reason = presets_module.resolve_active_preset(
            self._attr_preset_mode, self._presets,
            self.zone.get(CONF_PRESENCE_PRESET, ""), self.zone.get(CONF_AWAY_PRESET, ""),
            self._presence_now(),
        )
        self._active_preset_name = preset_name
        base_target = preset_target if preset_target is not None else self._attr_target_temperature

        now = dt_util.now()
        override_active = (
            self._temp_override_value is not None and self._temp_override_until is not None and now < self._temp_override_until
        )

        if self._attr_hvac_mode == HVACMode.OFF:
            action, target_temp = "idle", base_target
            self._reason = "apagado desde el termostato"
        elif self._door_window_open():
            action, target_temp = "idle", base_target
            self._reason = "puerta/ventana abierta: en pausa"
        elif override_active:
            target_temp = self._temp_override_value
            if capability in ("heat", "heat_cool") and current_temp < target_temp - deadband:
                action = "heat"
            elif capability in ("cool", "heat_cool") and current_temp > target_temp + deadband:
                action = "cool"
            else:
                action = "idle"
            self._reason = f"objetivo anulado a mano hasta las {self._temp_override_until.strftime('%H:%M')} ({target_temp:.1f}°C)"
        else:
            target_temp = base_target
            self._attr_target_temperature = target_temp
            action, decide_reason = scheduler.decide_action(
                current_temp=current_temp, target_temp=target_temp, hvac_capability=capability,
                priority=self.zone.get(CONF_PRIORITY, "confort"), deadband=deadband,
                min_temp=min_temp, max_temp=max_temp,
                outdoor_now=self._outdoor_now, outdoor_forecast=self._outdoor_forecast,
                heating_rate_deg_h=self._thermal_model.get("heating_rate_deg_h", 0.0),
                cooling_rate_deg_h=self._thermal_model.get("cooling_rate_deg_h", 0.0),
                idle_loss_coeff=self._thermal_model.get("idle_loss_coeff", 0.0),
            )
            self._reason = f"{preset_reason} — {decide_reason}"

        real_action = await self._async_execute(action, target_temp, capability)
        self._attr_hvac_action = _ACTION_MAP.get(real_action, HVACAction.IDLE)
        self.async_write_ha_state()

    # ------------------------------------------------------ actuadores ----

    async def _async_execute(self, action: str, target_temp: float, capability: str) -> str:
        """Ejecuta la decision sobre el actuador real de calor Y el de
        frio — cada uno el suyo, independiente (ver const.py). Nunca se
        activan los dos a la vez: `action` ya viene decidido como uno solo
        por el planificador (scheduler.py). Caso especial: si el actuador
        de calor y el de frio son el MISMO climate.* (un equipo reversible
        de verdad — muchos aires acondicionados llevan tambien bomba de
        calor), se manda una UNICA orden con el modo que toque en cada
        momento ("heat" en invierno, "cool" en verano, "off" si no hace
        falta nada) en vez de dos ordenes que podrian pisarse.

        Devuelve la accion REAL resultante — en modo switch puede no
        coincidir con `action` si el anti-ciclado todavia no deja cambiar
        de estado."""
        simulate = bool(self.zone.get(CONF_SIMULATE, True))

        heat_mode = self.zone.get(CONF_HEAT_ACTUATOR_MODE, "switch") if capability in ("heat", "heat_cool") else None
        cool_mode = self.zone.get(CONF_COOL_ACTUATOR_MODE, "switch") if capability in ("cool", "heat_cool") else None
        heat_climate = self.zone.get(CONF_HEAT_CLIMATE)
        cool_climate = self.zone.get(CONF_COOL_CLIMATE)
        shared_climate = heat_mode == "climate" and cool_mode == "climate" and heat_climate and heat_climate == cool_climate

        real_heat = real_cool = False

        if shared_climate:
            hvac_mode = {"heat": "heat", "cool": "cool"}.get(action, "off")
            if not simulate:
                await self.hass.services.async_call(
                    "climate", "set_hvac_mode", {"entity_id": heat_climate, "hvac_mode": hvac_mode}, blocking=False)
                if hvac_mode != "off":
                    await self.hass.services.async_call(
                        "climate", "set_temperature", {"entity_id": heat_climate, "temperature": target_temp}, blocking=False)
            real_heat, real_cool = action == "heat", action == "cool"
        else:
            if heat_mode == "switch" and self.zone.get(CONF_HEAT_SWITCH):
                real_heat = await self._drive_switch(self.zone[CONF_HEAT_SWITCH], action == "heat", simulate)
            elif heat_mode == "climate" and heat_climate:
                real_heat = await self._drive_climate_actuator(heat_climate, action == "heat", target_temp, "heat", simulate)

            if cool_mode == "switch" and self.zone.get(CONF_COOL_SWITCH):
                real_cool = await self._drive_switch(self.zone[CONF_COOL_SWITCH], action == "cool", simulate)
            elif cool_mode == "climate" and cool_climate:
                real_cool = await self._drive_climate_actuator(cool_climate, action == "cool", target_temp, "cool", simulate)

        if real_heat:
            return "heat"
        if real_cool:
            return "cool"
        return "idle"

    async def _drive_climate_actuator(self, entity_id: str, desired_active: bool, target_temp: float,
                                       mode_name: str, simulate: bool) -> bool:
        """Delega en un climate.* ya existente (p.ej. una valvula
        termostatica, o un AC que solo se usa para uno de los dos
        sentidos). Se le manda su modo CORRECTO (`mode_name`: "heat" o
        "cool", nunca el contrario) cuando toca actuar, y "off" cuando no."""
        hvac_mode = mode_name if desired_active else "off"
        if not simulate:
            await self.hass.services.async_call(
                "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": hvac_mode}, blocking=False)
            if desired_active:
                await self.hass.services.async_call(
                    "climate", "set_temperature", {"entity_id": entity_id, "temperature": target_temp}, blocking=False)
        return desired_active

    async def _drive_switch(self, entity_id: str, desired_on: bool, simulate: bool) -> bool:
        """Aplica anti-ciclado (tiempo minimo encendido/apagado) y, si
        procede, enciende/apaga de verdad. Devuelve si el switch queda
        REALMENTE encendido tras esta llamada."""
        state = self.hass.states.get(entity_id)
        current_on = state is not None and state.state == "on"
        now = dt_util.utcnow()

        if current_on == desired_on:
            self._switch_last_change.setdefault(entity_id, ("on" if current_on else "off", now))
            return current_on

        last_state, last_change = self._switch_last_change.get(entity_id, (None, None))
        if last_change is not None:
            min_seconds = self.zone.get(CONF_MIN_ON_SECONDS, DEFAULT_MIN_ON_SECONDS) if last_state == "on" \
                else self.zone.get(CONF_MIN_OFF_SECONDS, DEFAULT_MIN_OFF_SECONDS)
            if (now - last_change).total_seconds() < min_seconds:
                return current_on  # anti-ciclado: se queda como esta por ahora

        if not simulate:
            service = "turn_on" if desired_on else "turn_off"
            await self.hass.services.async_call("switch", service, {"entity_id": entity_id}, blocking=False)
        self._switch_last_change[entity_id] = ("on" if desired_on else "off", now)
        return desired_on

    # --------------------------------------------------------- comandos ----

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        hours = float(self.zone.get(CONF_MANUAL_OVERRIDE_HOURS, DEFAULT_MANUAL_OVERRIDE_HOURS))
        self._temp_override_value = float(temperature)
        self._temp_override_until = dt_util.now() + timedelta(hours=hours)
        self._attr_target_temperature = self._temp_override_value

        if self._unsub_override_expiry:
            self._unsub_override_expiry()
        self._unsub_override_expiry = async_track_point_in_time(self.hass, self._handle_override_expiry, self._temp_override_until)

        await self._async_decide_and_act()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._attr_hvac_mode = hvac_mode
        # Un cambio de modo puede cambiar la capacidad EFECTIVA (p.ej. de
        # "auto" a "solo frio", ver _effective_capability) — recalcular ya
        # mismo la decision, no esperar al proximo evento reactivo.
        await self._async_decide_and_act()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Fijar CUALQUIER preset a mano (o "Automático") es una eleccion
        PERSISTENTE — no caduca sola, se restaura tras un reinicio, igual
        que el modo hvac. Solo la temperatura objetivo (async_set_temperature)
        es una anulacion temporal."""
        if preset_mode not in (self._attr_preset_modes or []):
            return
        self._attr_preset_mode = preset_mode
        await self._async_decide_and_act()
