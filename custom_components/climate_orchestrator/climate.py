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
Y no se limita a calor/frio: si un climate.* delegado declara TAMBIEN
"dry" o "fan_only" en sus propios hvac_modes (p.ej. un aire acondicionado
con deshumidificacion o solo ventilador), esta zona los hereda igual — un
radiador que solo sepa "off"/"heat" simplemente no los aporta. Elegirlos a
mano desde el termostato los relega directos al equipo, sin pasar por
ninguna consigna de temperatura (ver `_PASSTHROUGH_MODES`); ademas, un
"reposo inteligente" opcional (desactivado por defecto, ver
CONF_AUTO_FAN_IDLE/CONF_AUTO_DRY_HUMIDITY en const.py) puede usarlos EL
SOLO en vez de apagar del todo cuando la zona ya esta dentro de margen —
ventilar por comodidad, o deshumidificar si la humedad medida supera un
umbral configurable — sin que sustituyan nunca a calor/frio cuando de
verdad hacen falta (ver `_smart_idle_action`).
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
    CONF_AUTO_DRY_HUMIDITY,
    CONF_AUTO_FAN_IDLE,
    CONF_CLIMATE_ENTITIES,
    CONF_COOL_SWITCHES,
    CONF_CURRENT_TEMP_SENSOR,
    CONF_DEADBAND,
    CONF_DOOR_WINDOW_ENTITIES,
    CONF_DRY_HUMIDITY_THRESHOLD,
    CONF_FORECAST_REFRESH_MINUTES,
    CONF_HEAT_SWITCHES,
    CONF_HISTORY_DAYS_FOR_INERTIA,
    CONF_HUMIDITY_SENSOR,
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
    DEFAULT_DRY_HUMIDITY_THRESHOLD,
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

_ACTION_MAP = {
    "heat": HVACAction.HEATING, "cool": HVACAction.COOLING, "idle": HVACAction.IDLE,
    "dry": HVACAction.DRYING, "fan_only": HVACAction.FAN,
}

# Modos que NO se deciden solos por temperatura (no hay "consigna" que
# perseguir): el usuario los elige a mano desde el termostato y se relegan
# directos a quien de verdad los soporte (ver `_async_execute_passthrough_mode`).
# Nunca los propone el motor de decision ni son el modo por defecto al
# arrancar — a diferencia de calor/frio/Auto, que si se auto-seleccionan.
_PASSTHROUGH_MODES = {HVACMode.DRY: "dry", HVACMode.FAN_ONLY: "fan_only"}


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
        self._last_full_capability: set[str] = set()  # ver _refresh_hvac_modes / _smart_idle_action
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
        self._attr_hvac_action = HVACAction.OFF if self._attr_hvac_mode == HVACMode.OFF else HVACAction.IDLE
        self._attr_current_temperature = None
        self._attr_current_humidity = None  # ver CONF_HUMIDITY_SENSOR / _read_humidity_now — solo informativa, no hay control
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

        # Ultimo modo hvac activo (no "apagado") — lo usa async_turn_on
        # (ver TURN_ON/TURN_OFF en _refresh_hvac_modes) para volver
        # exactamente a donde estaba la zona, no a un generico "Auto": si
        # se habia bloqueado a mano a "solo calor", encender debe volver a
        # "solo calor", no saltar a otra cosa.
        self._last_active_hvac_mode: HVACMode | None = (
            self._attr_hvac_mode if self._attr_hvac_mode != HVACMode.OFF else None
        )

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
        de este modulo). Un switch solo entiende encendido/apagado, asi que
        solo puede aportar heat/cool (segun en que lista este); un
        climate.* delegado aporta el conjunto COMPLETO de SUS PROPIOS
        `hvac_modes`, leidos en vivo del estado actual de esa entidad — no
        solo heat/cool: si un aire acondicionado declara tambien "dry" o
        "fan_only", esta zona los hereda igual (ver `_refresh_hvac_modes` y
        `_PASSTHROUGH_MODES`); un radiador que solo declare "off"/"heat" no
        aporta nada mas que eso."""
        capability: set[str] = set()
        if self.zone.get(CONF_HEAT_SWITCHES):
            capability.add("heat")
        if self.zone.get(CONF_COOL_SWITCHES):
            capability.add("cool")
        for entity_id in self.zone.get(CONF_CLIMATE_ENTITIES) or []:
            state = self.hass.states.get(entity_id)
            supported = (state.attributes.get("hvac_modes") if state else None) or []
            for mode in ("heat", "cool", *_PASSTHROUGH_MODES.values()):
                if mode in supported:
                    capability.add(mode)
        return capability

    def _refresh_hvac_modes(self) -> set[str]:
        """Recalcula `_attr_hvac_modes` (y las features soportadas) a
        partir de la capacidad real actual. Una zona con calor Y frio
        expone los CUATRO modos estandar de temperatura: apagado, "Auto"
        (HVACMode.HEAT_COOL, el System Mode Auto de Matter — doble
        consigna, baja de calor y alta de frio, decide sola cual toca), y
        tambien calor y frio por separado por si quieres bloquear la zona
        a mano a uno solo. Una zona de un solo sentido expone solo ese modo
        (mas apagado).

        Ademas — y esto es lo que generaliza el reconocimiento mas alla de
        calor/frio — si algun climate.* delegado declara tambien "dry" o
        "fan_only" en sus PROPIOS hvac_modes, esta zona los añade tal
        cual: no son modos que el motor de decision persiga con una
        temperatura, son eleccion directa del usuario (ver
        `_PASSTHROUGH_MODES` y el reposo inteligente opcional en
        `_smart_idle_action`). Un switch nunca aporta dry/fan_only (no
        tiene forma de hacerlo); un radiador con solo "off"/"heat" nunca
        los expone tampoco.

        Se llama al crear la entidad y en cada refresco de previsión — por
        si un climate.* delegado todavia no estaba disponible al arrancar
        HA, o cambia de capacidad tras un reload de su propia integracion
        (ver tambien `_capability_pending`, mas abajo, para el caso de que
        ESTA entidad se cree antes de que el actuador delegado este
        listo)."""
        capability = self._compute_capability()
        self._last_full_capability = capability

        modes = [HVACMode.OFF]
        if {"heat", "cool"} <= capability:
            modes.append(HVACMode.HEAT_COOL)
        if "heat" in capability:
            modes.append(HVACMode.HEAT)
        if "cool" in capability:
            modes.append(HVACMode.COOL)
        for hvac_mode, name in _PASSTHROUGH_MODES.items():
            if name in capability:
                modes.append(hvac_mode)
        self._attr_hvac_modes = modes

        features = ClimateEntityFeature.PRESET_MODE
        if {"heat", "cool"} <= capability:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        if ("heat" in capability) or ("cool" in capability):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if len(modes) > 1:
            # Hay algo mas que "apagado" que ofrecer — declarar TURN_ON/
            # TURN_OFF es lo que hace que HA (y cualquier puente Matter/
            # HomeKit/Google Home montado encima) trate el apagado como un
            # boton de encendido/apagado de verdad, no solo un hvac_mode
            # mas escondido en un desplegable (ver async_turn_on/off, mas
            # abajo). Sin esto una zona sigue funcionando, pero HA avisa de
            # que el comportamiento esta obsoleto y algunos puentes no
            # exponen el interruptor de encendido.
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        self._attr_supported_features = features
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
            **_PASSTHROUGH_MODES,
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
        if self._attr_hvac_mode != HVACMode.OFF:
            self._last_active_hvac_mode = self._attr_hvac_mode

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

    def _read_humidity_now(self) -> float | None:
        sensor = self.zone.get(CONF_HUMIDITY_SENSOR)
        if not sensor:
            return None
        state = self.hass.states.get(sensor)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _smart_idle_action(self) -> tuple[str, str | None]:
        """Reposo INTELIGENTE, opcional y desactivado por defecto (ver
        CONF_AUTO_FAN_IDLE/CONF_AUTO_DRY_HUMIDITY en const.py — no cambia
        nada en una zona existente hasta que el usuario lo activa a
        proposito). Solo se llama cuando el motor YA decidio "idle" (dentro
        de margen, ni calor ni frio hacen falta): en vez de apagar del
        todo, un climate.* delegado que TAMBIEN sepa deshumidificar o solo
        ventilar (segun `_last_full_capability`, detectado en vivo — nunca
        declarado a mano) puede aprovecharse. Deshumidificar tiene
        prioridad sobre ventilar: responde a un problema medido (humedad
        por encima del umbral configurado), no solo a comodidad. Ninguno
        de los dos persigue nunca una temperatura ni sustituye a calor/frio
        cuando de verdad hacen falta."""
        if self.zone.get(CONF_AUTO_DRY_HUMIDITY) and "dry" in self._last_full_capability:
            humidity = self._read_humidity_now()
            threshold = float(self.zone.get(CONF_DRY_HUMIDITY_THRESHOLD, DEFAULT_DRY_HUMIDITY_THRESHOLD))
            if humidity is not None and humidity >= threshold:
                return "dry", f"humedad {humidity:.0f}% ≥ {threshold:.0f}%: deshumidificando en vez de apagar"
        if self.zone.get(CONF_AUTO_FAN_IDLE) and "fan_only" in self._last_full_capability:
            return "fan_only", "dentro de margen: ventilando en vez de apagar del todo"
        return "idle", None

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
        humidity = self._read_humidity_now()
        self._attr_current_humidity = round(humidity) if humidity is not None else None
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

        force_off = False
        if self._attr_hvac_mode == HVACMode.OFF:
            action = "idle"
            heat_target, cool_target = preset_heat, preset_cool
            self._reason = "apagado desde el termostato"
        elif self._door_window_open():
            action = "idle"
            heat_target, cool_target = preset_heat, preset_cool
            self._reason = "puerta/ventana abierta: en pausa"
            # A diferencia de un "idle" normal (que si respeta el
            # anti-ciclado: no urge, solo esta dentro de margen), una
            # puerta/ventana abierta SI es urgente: cortar de verdad ya,
            # sin esperar al tiempo minimo encendido (`min_on_seconds`) —
            # si no, un radiador que se acababa de encender se quedaba
            # calentando con la ventana abierta hasta agotar ese margen.
            # Al cerrarse, la propia entidad esta en la lista de sensores
            # escuchados (ver async_added_to_hass): dispara una reevaluacion
            # inmediata y la zona retoma el calculo normal ella sola, sin
            # nada especial que "reactivar".
            force_off = True
        elif self._attr_hvac_mode in _PASSTHROUGH_MODES:
            # Modos que no persiguen ninguna temperatura (dry/fan_only,
            # ver cabecera del modulo y _PASSTHROUGH_MODES): eleccion
            # directa del usuario desde el termostato, nunca algo que el
            # scheduler decida. Se relegan tal cual a quien de verdad los
            # soporte en `_async_execute`/`_drive_climate_actuator`.
            action = _PASSTHROUGH_MODES[self._attr_hvac_mode]
            heat_target = cool_target = None
            self._reason = f"modo {action} fijado a mano desde el termostato"
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
            if action == "idle":
                # Ya esta dentro de margen: en vez de apagar sin mas, ver
                # si el reposo inteligente (opcional) tiene algo mejor que
                # hacer con lo que de verdad hay conectado.
                smart_action, smart_reason = self._smart_idle_action()
                if smart_reason:
                    action = smart_action
                    self._reason += f" — {smart_reason}"

        self._update_target_attrs(heat_target, cool_target)
        target_for_actuator = heat_target if action == "heat" else cool_target if action == "cool" else (heat_target or cool_target)
        real_action = await self._async_execute(action, target_for_actuator, capability, force_off=force_off)
        # HVACAction.OFF (apagado de verdad, el modo elegido es "apagado")
        # es DISTINTO de HVACAction.IDLE (encendida, dentro de margen, sin
        # nada que hacer ahora mismo) — HA y cualquier puente Matter/
        # HomeKit distinguen los dos; confundirlos hace que un termostato
        # "apagado" se vea como "en espera" en la UI.
        self._attr_hvac_action = HVACAction.OFF if self._attr_hvac_mode == HVACMode.OFF \
            else _ACTION_MAP.get(real_action, HVACAction.IDLE)
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

    async def _async_execute(self, action: str, target_temp: float | None, capability: str, force_off: bool = False) -> str:
        """Ejecuta la decision sobre TODOS los actuadores declarados —
        tantos como se quiera de cada tipo (ver const.py). `action` ya
        viene decidido como uno solo ("heat"/"cool"/"dry"/"fan_only"/
        "idle") por scheduler.py, el reposo inteligente opcional, o el
        modo fijado a mano, asi que nunca se manda mas de una cosa a la
        vez.

        `force_off`: salta el anti-ciclado de los switches (ver
        `_drive_switch`) — solo lo usa el aviso de puerta/ventana abierta,
        que es urgente de verdad; un "idle" normal (dentro de margen) sigue
        respetando el tiempo minimo encendido.

        Los switches SOLO entienden calor/frio (un switch no tiene forma
        de ventilar o deshumidificar) — con "dry"/"fan_only" simplemente
        se apagan. Cada climate.* delegado se gobierna por SUS PROPIOS
        `hvac_modes` (consultados en vivo, ver `_drive_climate_actuator`)
        — recibe una unica orden con el modo que toque cada vez; el que no
        lo soporte se ignora sin mas.

        Devuelve la accion REAL resultante — en modo switch puede no
        coincidir con `action` si el anti-ciclado todavia no deja cambiar
        de estado."""
        simulate = bool(self.zone.get(CONF_SIMULATE, True))
        real_heat = real_cool = False
        real_other: str | None = None
        target_temp = target_temp if target_temp is not None else self._attr_current_temperature or 20.0

        if capability in ("heat", "heat_cool"):
            for sw in self.zone.get(CONF_HEAT_SWITCHES) or []:
                if await self._drive_switch(sw, action == "heat", simulate, force=force_off):
                    real_heat = True

        if capability in ("cool", "heat_cool"):
            for sw in self.zone.get(CONF_COOL_SWITCHES) or []:
                if await self._drive_switch(sw, action == "cool", simulate, force=force_off):
                    real_cool = True

        for entity_id in self.zone.get(CONF_CLIMATE_ENTITIES) or []:
            result = await self._drive_climate_actuator(entity_id, action, target_temp, simulate)
            if result == "heat":
                real_heat = True
            elif result == "cool":
                real_cool = True
            elif result in ("dry", "fan_only"):
                real_other = result

        if real_heat:
            return "heat"
        if real_cool:
            return "cool"
        if real_other:
            return real_other
        return "idle"

    async def _drive_climate_actuator(self, entity_id: str, action: str, target_temp: float, simulate: bool) -> str:
        """Consulta los `hvac_modes` NATIVOS de este climate.* delegado
        (nunca una declaracion nuestra) para saber si puede hacer lo que
        hace falta ahora ("heat"/"cool"/"dry"/"fan_only"). Si puede, se le
        manda ese modo — con temperatura solo para heat/cool, ya que
        dry/fan_only no persiguen ninguna consigna —; si no, se le manda
        "off" (si lo soporta) y se deja en paz — asi un equipo con varias
        capacidades se activa solo en la que corresponda sin ninguna
        deteccion especial, y el que no soporte el modo que toca se ignora
        sin mas."""
        state = self.hass.states.get(entity_id)
        supported = list((state.attributes.get("hvac_modes") if state else None) or [])
        can_do = action in ("heat", "cool", "dry", "fan_only") and action in supported

        if can_do:
            if not simulate:
                await self.hass.services.async_call(
                    "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": action}, blocking=False)
                if action in ("heat", "cool"):
                    await self.hass.services.async_call(
                        "climate", "set_temperature", {"entity_id": entity_id, "temperature": target_temp}, blocking=False)
            return action

        if not simulate and "off" in supported and state is not None and state.state != "off":
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "off"}, blocking=False)
        return "idle"

    async def _drive_switch(self, entity_id: str, desired_on: bool, simulate: bool, force: bool = False) -> bool:
        """Aplica anti-ciclado (tiempo minimo encendido/apagado) y, si
        procede, enciende/apaga de verdad. Devuelve si el switch queda
        REALMENTE encendido tras esta llamada.

        `force=True` (solo lo usa el aviso de puerta/ventana abierta, ver
        `_async_decide_and_act`) salta el anti-ciclado por completo: es
        una parada urgente, no un simple "esta dentro de margen, no
        merece la pena tocarlo" — sin esto, un radiador que se acababa de
        encender se quedaba calentando con la ventana abierta hasta
        agotar el tiempo minimo encendido configurado."""
        state = self.hass.states.get(entity_id)
        current_on = state is not None and state.state == "on"
        now = dt_util.utcnow()

        if current_on == desired_on:
            self._switch_last_change.setdefault(entity_id, ("on" if current_on else "off", now))
            return current_on

        last_state, last_change = self._switch_last_change.get(entity_id, (None, None))
        if not force and last_change is not None:
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
        if hvac_mode != HVACMode.OFF:
            self._last_active_hvac_mode = hvac_mode
        # Un cambio de modo puede cambiar la capacidad EFECTIVA (p.ej. de
        # "auto" a "solo frio", ver _effective_capability) — recalcular ya
        # mismo la decision, no esperar al proximo evento reactivo.
        await self._async_decide_and_act()

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        """El interruptor de encendido (ver ClimateEntityFeature.TURN_ON/
        TURN_OFF en `_refresh_hvac_modes` — necesario para que HA, y
        cualquier puente Matter/HomeKit montado encima, trate el apagado
        como un boton real). Vuelve al ultimo modo activo que tenia la
        zona, no a un generico "Auto": si estaba bloqueada a mano a "solo
        calor", encender debe devolverla a "solo calor"."""
        target = self._last_active_hvac_mode
        if target is None or target not in (self._attr_hvac_modes or []):
            # La capacidad pudo cambiar mientras tanto (un actuador se
            # quito/añadio) y ese modo ya no es valido — cae al modo por
            # defecto actual en vez de mandar algo que la zona ya no puede
            # cumplir.
            target = self._default_hvac_mode(self._last_full_capability)
        await self.async_set_hvac_mode(target)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Fijar CUALQUIER preset a mano (incluido "Manual", o
        "Automático") es una eleccion PERSISTENTE — no caduca sola, se
        restaura tras un reinicio, igual que el modo hvac."""
        if preset_mode not in (self._attr_preset_modes or []):
            return
        self._attr_preset_mode = preset_mode
        await self._async_decide_and_act()
