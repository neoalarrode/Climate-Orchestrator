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
fijado a mano. Cada preset lleva DOS consignas (calor/"invierno" y
frio/"verano"), expuestas como entidades number.* propias (ver
number.py) para poder ajustarlas en caliente sin volver a "Configurar".

El modo hvac de una zona con calor Y frio de verdad es SIEMPRE "Auto"
(HVACMode.HEAT_COOL) — nunca se ofrece bloquear a mano "solo calor" o
"solo frio": eso es exactamente el System Mode Auto estandar de Matter
(consigna baja de calor + consigna alta de frio, el equipo decide solo
cual aplica cada momento), asi que esta entidad ya sale lista para
cualquier puente Matter/HomeKit sin traduccion. Una zona de un solo
sentido (declarada solo con actuadores de calor, o solo de frio) sigue
ofreciendo unicamente ese modo — "Auto" no tendria sentido ahi.

El PRESET activo es una eleccion PERSISTENTE del usuario — se restaura
sola tras un reinicio via RestoreEntity, igual que cualquier otro
termostato de HA. Ajustar la TEMPERATURA directamente desde la tarjeta
del termostato (en vez de elegir un preset) NO es una anulacion temporal
con caducidad: cambia el preset activo a "Manual" — tan persistente como
cualquier otro preset, se queda con esa temperatura hasta que tu mismo
elijas otro preset o vuelvas a "Automático" (ver presets.py).

Tampoco hay "capacidad" (calor/frio/ambos) declarada a mano: se DEDUCE en
vivo de los actuadores de verdad configurados (ver `_refresh_hvac_modes`).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.components.climate.const import ATTR_TARGET_TEMP_HIGH, ATTR_TARGET_TEMP_LOW
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util, slugify

from . import outdoor, presets as presets_module, scheduler, thermal_model
from .const import (
    CONF_CLIMATE_ENTITIES,
    CONF_COOL_SWITCHES,
    CONF_CURRENT_TEMP_SENSOR,
    CONF_DEADBAND,
    CONF_DOOR_WINDOW_ENTITIES,
    CONF_FORECAST_REFRESH_MINUTES,
    CONF_HEAT_SWITCHES,
    CONF_HISTORY_DAYS_FOR_INERTIA,
    CONF_MAX_TEMP,
    CONF_MIN_OFF_SECONDS,
    CONF_MIN_ON_SECONDS,
    CONF_MIN_TEMP,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_PRESENCE_ENTITIES,
    CONF_PRESENCE_PRESET,
    CONF_AWAY_PRESET,
    CONF_PRESETS_TEXT,
    CONF_PRIORITY,
    CONF_SIMULATE,
    CONF_WEATHER_ENTITY,
    DEFAULT_DEADBAND,
    DEFAULT_FORECAST_REFRESH_MINUTES,
    DEFAULT_HISTORY_DAYS_FOR_INERTIA,
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
    _attr_icon = "mdi:home-thermometer"

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

        self._attr_hvac_modes = [HVACMode.OFF]  # se recalcula de verdad justo abajo y en cada refresco
        capability = self._refresh_hvac_modes()
        # Si en ESTE instante (construccion de la entidad) no se detecta
        # ningun actuador, lo mas probable es que sea una carrera de
        # arranque (el climate.* delegado todavia no cargo) y no que la
        # zona de verdad no tenga nada configurado — ver `_reconcile_hvac_mode`.
        self._capability_pending = not capability

        try:
            self._presets = presets_module.parse_presets(self.zone.get(CONF_PRESETS_TEXT, ""))
        except ValueError:
            self._presets = []
        self._attr_preset_modes = [presets_module.PRESET_AUTO, presets_module.PRESET_MANUAL] + [p["name"] for p in self._presets]
        self._attr_preset_mode = presets_module.PRESET_AUTO

        self._attr_min_temp = float(self.zone.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP))
        self._attr_max_temp = float(self.zone.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP))
        self._attr_hvac_mode = self._default_hvac_mode(capability)
        self._attr_hvac_action = HVACAction.IDLE
        self._attr_current_temperature = None
        self._attr_target_temperature = None
        self._attr_target_temperature_low = None
        self._attr_target_temperature_high = None
        self._attr_available = True

        self._outdoor_forecast: list[float] = []
        self._outdoor_now: float | None = None
        self._thermal_model: dict = {}
        self._reason = "sin calcular todavia"
        self._active_preset_name: str | None = None

        # Consignas del preset "Manual" (ver presets.py) — a diferencia de
        # los demas presets, no viven en una entidad number.*: se ponen
        # directamente desde la tarjeta del termostato (async_set_temperature)
        # y se guardan aqui mismo, persistentes, restauradas tras un
        # reinicio igual que el resto (ver async_added_to_hass).
        self._manual_heat: float | None = None
        self._manual_cool: float | None = None

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
        }

    # ---------------------------------------------------- capacidad real ----

    def _compute_capability(self) -> set[str]:
        """Que puede hacer esta zona DE VERDAD, a partir de los actuadores
        declarados — nunca una eleccion manual (ver const.py y la cabecera
        de este modulo). Un switch aporta lo que se le haya asignado
        (heat_switches/cool_switches); un climate.* delegado aporta lo que
        digan SUS PROPIOS `hvac_modes`, leidos en vivo del estado actual de
        esa entidad."""
        capability: set[str] = set()
        if self.zone.get(CONF_HEAT_SWITCHES):
            capability.add("heat")
        if self.zone.get(CONF_COOL_SWITCHES):
            capability.add("cool")
        for entity_id in self.zone.get(CONF_CLIMATE_ENTITIES) or []:
            state = self.hass.states.get(entity_id)
            supported = (state.attributes.get("hvac_modes") if state else None) or []
            if "heat" in supported:
                capability.add("heat")
            if "cool" in supported:
                capability.add("cool")
        return capability

    def _refresh_hvac_modes(self) -> set[str]:
        """Recalcula `_attr_hvac_modes` (y las features soportadas) a
        partir de la capacidad real actual. Una zona con calor Y frio
        expone los CUATRO modos estandar: apagado, "Auto" (HVACMode.
        HEAT_COOL, el System Mode Auto de Matter — doble consigna, baja de
        calor y alta de frio, decide sola cual toca), y tambien calor y
        frio por separado por si quieres bloquear la zona a mano a uno
        solo. Una zona de un solo sentido expone solo ese modo (mas
        apagado). Se llama al crear la entidad y en cada refresco de
        previsión — por si un climate.* delegado todavia no estaba
        disponible al arrancar HA, o cambia de capacidad tras un reload de
        su propia integracion (ver tambien `_capability_pending`, mas
        abajo, para el caso de que ESTA entidad se cree antes de que el
        actuador delegado este listo)."""
        capability = self._compute_capability()
        if {"heat", "cool"} <= capability:
            self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL, HVACMode.HEAT, HVACMode.COOL]
            self._attr_supported_features = (
                ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
                | ClimateEntityFeature.TARGET_TEMPERATURE
                | ClimateEntityFeature.PRESET_MODE
            )
        elif "cool" in capability:
            self._attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL]
            self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
        elif "heat" in capability:
            self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
            self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
        else:
            self._attr_hvac_modes = [HVACMode.OFF]
            self._attr_supported_features = ClimateEntityFeature.PRESET_MODE
        return capability

    def _reconcile_hvac_mode(self, capability: set[str]) -> None:
        """Corrige la carrera de arranque: si al CREAR la entidad ningun
        actuador estaba disponible todavia (p.ej. el climate.* delegado
        tarda mas en cargar que esta integracion), la zona se quedaba
        forzada a "apagado" — y nada la reevaluaba despues aunque el
        actuador apareciera mas tarde, porque "apagado" es indistinguible
        de una eleccion real del usuario. `_capability_pending` marca ese
        arranque a ciegas; en cuanto se detecta capacidad real por primera
        vez, se propone un modo sensato una unica vez."""
        if self._capability_pending and capability:
            self._capability_pending = False
            self._attr_hvac_mode = self._default_hvac_mode(capability)

    @staticmethod
    def _default_hvac_mode(capability: set[str]) -> HVACMode:
        if {"heat", "cool"} <= capability:
            return HVACMode.HEAT_COOL
        if "cool" in capability:
            return HVACMode.COOL
        if "heat" in capability:
            return HVACMode.HEAT
        return HVACMode.OFF

    def _effective_capability(self) -> str:
        """La capacidad que rige AHORA MISMO, directamente del modo hvac
        activo: "heat_cool" en Auto (doble consigna), "heat"/"cool" si se
        bloqueo la zona a mano a uno solo. Un modo solo aparece en
        `_attr_hvac_modes` (y por tanto solo se puede seleccionar) si la
        capacidad real lo soporta, asi que esto nunca pide mas de lo que
        los actuadores de verdad pueden dar."""
        return {
            HVACMode.HEAT: "heat", HVACMode.COOL: "cool", HVACMode.HEAT_COOL: "heat_cool",
        }.get(self._attr_hvac_mode, "none")

    # ------------------------------------------------------- ciclo vida ----

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Los actuadores declarados pueden no haber estado disponibles
        # todavia al construir la entidad (orden de arranque de HA) —
        # recalcular la capacidad real ahora que el resto de entidades ya
        # deberia estar cargado.
        capability = self._refresh_hvac_modes()
        self._reconcile_hvac_mode(capability)

        last_state = await self.async_get_last_state()
        valid_modes = {m.value for m in self._attr_hvac_modes}
        if self._capability_pending:
            # Todavia sin capacidad detectada (ver `_async_decide_and_act`,
            # que marca la entidad "no disponible" mientras tanto en vez de
            # escribir un modo resuelto) — no hay nada fiable que restaurar
            # ni que proponer todavia. `_reconcile_hvac_mode` (mas arriba)
            # ya se habria encargado si `capability` hubiera aparecido.
            pass
        elif last_state is not None and last_state.state in valid_modes:
            self._attr_hvac_mode = HVACMode(last_state.state)
        else:
            self._attr_hvac_mode = self._default_hvac_mode(capability)

        if last_state is not None:
            last_preset = last_state.attributes.get("preset_mode")
            if last_preset in (self._attr_preset_modes or []):
                self._attr_preset_mode = last_preset
            if last_preset == presets_module.PRESET_MANUAL:
                # Restaura las consignas que el usuario habia puesto a
                # mano, del propio estado grabado (attr_target_temperature_
                # low/high en Auto, o attr_temperature en calor/frio solo).
                for attr_key, field in ((ATTR_TARGET_TEMP_LOW, "_manual_heat"), (ATTR_TARGET_TEMP_HIGH, "_manual_cool")):
                    val = last_state.attributes.get(attr_key)
                    if val is not None:
                        try:
                            setattr(self, field, float(val))
                        except (TypeError, ValueError):
                            pass
                single = last_state.attributes.get(ATTR_TEMPERATURE)
                if single is not None:
                    try:
                        single_f = float(single)
                        if self._attr_hvac_mode == HVACMode.HEAT:
                            self._manual_heat = single_f
                        elif self._attr_hvac_mode == HVACMode.COOL:
                            self._manual_cool = single_f
                    except (TypeError, ValueError):
                        pass

        watched = [e for e in [
            self.zone.get(CONF_CURRENT_TEMP_SENSOR),
            self.zone.get(CONF_OUTDOOR_TEMP_SENSOR),
            *(self.zone.get(CONF_PRESENCE_ENTITIES) or []),
            *(self.zone.get(CONF_DOOR_WINDOW_ENTITIES) or []),
            *(self.zone.get(CONF_CLIMATE_ENTITIES) or []),
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
        # Tambien se escuchan los climate.* delegados (ver `watched` arriba)
        # justamente para esto: en cuanto uno de ellos aparece/actualiza su
        # estado por primera vez, reevaluar la capacidad AL INSTANTE en vez
        # de esperar al proximo refresco periodico (hasta
        # `forecast_refresh_minutes`, 10 min por defecto) — asi la carrera
        # de arranque se resuelve en segundos, no en minutos.
        if self._capability_pending:
            capability = self._refresh_hvac_modes()
            self._reconcile_hvac_mode(capability)
        await self._async_decide_and_act()

    async def _handle_forecast_refresh(self, now) -> None:
        await self._async_refresh_forecast()

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

    def _preset_value(self, preset_name: str, side: str) -> float | None:
        """Consigna VIVA (calor o frio) de un preset — se busca en su
        entidad number.* propia (ver number.py), no en el texto estatico
        de la configuracion: asi ajustarla desde Lovelace/una
        automatizacion se nota al instante, sin tener que volver a
        "Configurar" la zona."""
        unique_id = f"{self.entry.entry_id}_preset_{slugify(preset_name)}_{side}"
        entity_id = er.async_get(self.hass).async_get_entity_id("number", DOMAIN, unique_id)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    # ---------------------------------------------------- previsión cara ----

    async def _async_refresh_forecast(self) -> None:
        capability = self._refresh_hvac_modes()
        # Ultima red de seguridad contra la carrera de arranque (ver
        # `_reconcile_hvac_mode`): si el actuador delegado tardo mas en
        # cargar que incluso el primer refresco tras `async_added_to_hass`,
        # este ciclo periodico (cada `forecast_refresh_minutes`) acaba
        # curandolo solo, en vez de dejar la zona en "apagado" para siempre.
        self._reconcile_hvac_mode(capability)

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
        if self._capability_pending:
            # Todavia no se ha detectado NINGUN actuador (lo mas probable:
            # un climate.* delegado que arranca mas despacio que esta
            # integracion). A proposito NO se escribe un hvac_mode
            # resuelto ("apagado") aqui: si se grabase, quedaria como el
            # "ultimo estado conocido" y un reinicio futuro lo restauraria
            # como si fuera una eleccion real, incluso en un arranque SIN
            # carrera — perpetuando el bug para siempre. Mejor mostrar la
            # entidad "no disponible" mientras tanto (un estado que HA
            # nunca restaura como si fuera real) hasta que `_reconcile_hvac_mode`
            # detecte capacidad real por primera vez, ya sea por el evento
            # reactivo del propio actuador o por el refresco periodico.
            self._attr_available = False
            self.async_write_ha_state()
            return

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
        wants_heat = capability in ("heat", "heat_cool")
        wants_cool = capability in ("cool", "heat_cool")

        preset_name, preset_reason = presets_module.resolve_active_preset_name(
            self._attr_preset_mode, [p["name"] for p in self._presets],
            self.zone.get(CONF_PRESENCE_PRESET, ""), self.zone.get(CONF_AWAY_PRESET, ""),
            self._presence_now(),
        )
        self._active_preset_name = preset_name
        if preset_name == presets_module.PRESET_MANUAL:
            # Consignas puestas a mano desde la propia tarjeta del
            # termostato — persistentes, no una anulacion con caducidad
            # (ver async_set_temperature). Se tratan igual que cualquier
            # otro preset de aqui en adelante: mismo scheduler.decide_action,
            # con su reactivo/anticipacion/limites de seguridad normales.
            preset_heat = self._manual_heat if wants_heat else None
            preset_cool = self._manual_cool if wants_cool else None
        else:
            preset_heat = self._preset_value(preset_name, "heat") if wants_heat else None
            preset_cool = self._preset_value(preset_name, "cool") if wants_cool else None

        if self._attr_hvac_mode == HVACMode.OFF:
            action = "idle"
            heat_target, cool_target = preset_heat, preset_cool
            self._reason = "apagado desde el termostato"
        elif self._door_window_open():
            action = "idle"
            heat_target, cool_target = preset_heat, preset_cool
            self._reason = "puerta/ventana abierta: en pausa"
        else:
            heat_target, cool_target = preset_heat, preset_cool
            action, decide_reason = scheduler.decide_action(
                current_temp=current_temp, heat_target=heat_target, cool_target=cool_target,
                priority=self.zone.get(CONF_PRIORITY, "confort"), deadband=deadband,
                min_temp=min_temp, max_temp=max_temp,
                outdoor_now=self._outdoor_now, outdoor_forecast=self._outdoor_forecast,
                heating_rate_deg_h=self._thermal_model.get("heating_rate_deg_h", 0.0),
                cooling_rate_deg_h=self._thermal_model.get("cooling_rate_deg_h", 0.0),
                idle_loss_coeff=self._thermal_model.get("idle_loss_coeff", 0.0),
            )
            self._reason = f"{preset_reason} — {decide_reason}"

        self._update_target_attrs(heat_target, cool_target)
        target_for_actuator = heat_target if action == "heat" else cool_target if action == "cool" else (heat_target or cool_target)
        real_action = await self._async_execute(action, target_for_actuator, capability)
        self._attr_hvac_action = _ACTION_MAP.get(real_action, HVACAction.IDLE)
        self.async_write_ha_state()

    def _update_target_attrs(self, heat_target: float | None, cool_target: float | None) -> None:
        if self._attr_hvac_mode == HVACMode.HEAT_COOL:
            self._attr_target_temperature = None
            self._attr_target_temperature_low = heat_target
            self._attr_target_temperature_high = cool_target
        else:
            self._attr_target_temperature = heat_target if self._attr_hvac_mode == HVACMode.HEAT else cool_target
            self._attr_target_temperature_low = None
            self._attr_target_temperature_high = None

    # ------------------------------------------------------ actuadores ----

    async def _async_execute(self, action: str, target_temp: float | None, capability: str) -> str:
        """Ejecuta la decision sobre TODOS los actuadores declarados —
        tantos como se quiera de cada tipo (ver const.py). `action` ya
        viene decidido como uno solo ("heat"/"cool"/"idle") por
        scheduler.py, asi que nunca se manda calor y frio a la vez.

        Cada climate.* delegado se gobierna por SUS PROPIOS `hvac_modes`
        (consultados en vivo, ver `_drive_climate_actuator`) — un equipo
        reversible (soporta heat Y cool) recibe una unica orden con el
        modo que toque cada vez; uno de un solo sentido simplemente se
        ignora cuando toca el otro.

        Devuelve la accion REAL resultante — en modo switch puede no
        coincidir con `action` si el anti-ciclado todavia no deja cambiar
        de estado."""
        simulate = bool(self.zone.get(CONF_SIMULATE, True))
        real_heat = real_cool = False
        target_temp = target_temp if target_temp is not None else self._attr_current_temperature or 20.0

        if capability in ("heat", "heat_cool"):
            for sw in self.zone.get(CONF_HEAT_SWITCHES) or []:
                if await self._drive_switch(sw, action == "heat", simulate):
                    real_heat = True

        if capability in ("cool", "heat_cool"):
            for sw in self.zone.get(CONF_COOL_SWITCHES) or []:
                if await self._drive_switch(sw, action == "cool", simulate):
                    real_cool = True

        for entity_id in self.zone.get(CONF_CLIMATE_ENTITIES) or []:
            result = await self._drive_climate_actuator(entity_id, action, target_temp, simulate)
            if result == "heat":
                real_heat = True
            elif result == "cool":
                real_cool = True

        if real_heat:
            return "heat"
        if real_cool:
            return "cool"
        return "idle"

    async def _drive_climate_actuator(self, entity_id: str, action: str, target_temp: float, simulate: bool) -> str:
        """Consulta los `hvac_modes` NATIVOS de este climate.* delegado
        (nunca una declaracion nuestra) para saber si puede hacer lo que
        hace falta ahora ("heat"/"cool"). Si puede, se le manda ese modo +
        la temperatura; si no, se le manda "off" (si lo soporta) y se deja
        en paz — asi un equipo reversible se activa solo en su modo
        correcto sin ninguna deteccion especial, y uno de un solo sentido
        se ignora sin mas cuando toca el otro."""
        state = self.hass.states.get(entity_id)
        supported = list((state.attributes.get("hvac_modes") if state else None) or [])
        can_do = action in ("heat", "cool") and action in supported

        if can_do:
            if not simulate:
                await self.hass.services.async_call(
                    "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": action}, blocking=False)
                await self.hass.services.async_call(
                    "climate", "set_temperature", {"entity_id": entity_id, "temperature": target_temp}, blocking=False)
            return action

        if not simulate and "off" in supported and state is not None and state.state != "off":
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "off"}, blocking=False)
        return "idle"

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
        """Ajustar la temperatura directamente desde la tarjeta del
        termostato pasa la zona al preset "Manual" — PERSISTENTE, no una
        anulacion con caducidad (ver presets.py y la cabecera del modulo):
        se queda con esta consigna hasta que tu mismo elijas otro preset."""
        single = kwargs.get(ATTR_TEMPERATURE)
        low = kwargs.get(ATTR_TARGET_TEMP_LOW)
        high = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        if single is None and low is None and high is None:
            return

        if self._attr_preset_mode != presets_module.PRESET_MANUAL:
            # Semilla desde lo que estuviera activo justo antes (el preset
            # saliente), para no dejar sin valor el lado que esta llamada
            # no toca — p.ej. tocar solo la consigna de calor en Auto no
            # debe perder la de frio que ya tenia el preset anterior.
            self._manual_heat = self._attr_target_temperature_low if self._attr_hvac_mode == HVACMode.HEAT_COOL \
                else (self._attr_target_temperature if self._attr_hvac_mode == HVACMode.HEAT else None)
            self._manual_cool = self._attr_target_temperature_high if self._attr_hvac_mode == HVACMode.HEAT_COOL \
                else (self._attr_target_temperature if self._attr_hvac_mode == HVACMode.COOL else None)

        if self._attr_hvac_mode == HVACMode.HEAT_COOL:
            if low is not None:
                self._manual_heat = float(low)
            if high is not None:
                self._manual_cool = float(high)
        elif self._attr_hvac_mode == HVACMode.HEAT and single is not None:
            self._manual_heat = float(single)
        elif self._attr_hvac_mode == HVACMode.COOL and single is not None:
            self._manual_cool = float(single)
        else:
            return

        self._attr_preset_mode = presets_module.PRESET_MANUAL
        await self._async_decide_and_act()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._attr_hvac_mode = hvac_mode
        # Un cambio de modo puede cambiar la capacidad EFECTIVA (p.ej. de
        # "auto" a "solo frio", ver _effective_capability) — recalcular ya
        # mismo la decision, no esperar al proximo evento reactivo.
        await self._async_decide_and_act()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Fijar CUALQUIER preset a mano (incluido "Manual", o
        "Automático") es una eleccion PERSISTENTE — no caduca sola, se
        restaura tras un reinicio, igual que el modo hvac."""
        if preset_mode not in (self._attr_preset_modes or []):
            return
        self._attr_preset_mode = preset_mode
        await self._async_decide_and_act()
