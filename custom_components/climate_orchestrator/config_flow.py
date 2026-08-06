"""
Asistente de configuracion: UNA entrada de integracion = UNA zona (mismo
patron que versatile_thermostat — se repite "+ Añadir integración" por
cada habitacion). Nada de config.json propio ni de interfaz web aparte:
todo lo declara el usuario aqui, con los formularios nativos de HA.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import presets as presets_module
from .const import (
    CONF_AUTO_DRY_HUMIDITY,
    CONF_AUTO_FAN_IDLE,
    CONF_AWAY_PRESET,
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
    DOMAIN,
)

PRIORITY_OPTIONS = [
    selector.SelectOptionDict(value="confort", label="Confort: actúa en cuanto hace falta"),
    selector.SelectOptionDict(value="ahorro", label="Ahorro: margen más ancho, se estrecha si empeora la previsión exterior"),
    selector.SelectOptionDict(value="manual", label="Manual: nunca decide sola"),
]

PRESETS_TEXT_DESCRIPTION = (
    'Un preset por cada situación que quieras distinguir, separados por comas: '
    '"Nombre: calor/frío" (consignas de invierno y verano por separado) o '
    '"Nombre: temperatura" si la zona es de un solo sentido. Ejemplo: '
    '"Confort: 21/25, Ausente: 17/28, Fiesta: 23/24". Esto solo siembra el '
    'valor inicial: luego cada consigna es su propia entidad number.*, '
    'ajustable desde Lovelace.'
)


def _entity(domain, device_class=None, multiple=False):
    # OJO: NO pasar "device_class=None" explicito al selector cuando no
    # aplica — un EntitySelectorConfig con esa clave puesta a None (en vez
    # de omitida del todo) deja el picker de entidades roto en el
    # frontend: aparece vacio y no filtra nada al escribir, sin ningun
    # error visible (asi se detecto: "el desplegable no muestra nada").
    config: dict = {"domain": domain, "multiple": multiple}
    if device_class is not None:
        config["device_class"] = device_class
    return selector.EntitySelector(selector.EntitySelectorConfig(**config))


def _temp_number():
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=-20, max=45, step=0.5, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="°C",
    ))


def _seconds_number():
    return selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=3600, step=30, mode=selector.NumberSelectorMode.BOX))


def _percent_number():
    return selector.NumberSelector(selector.NumberSelectorConfig(min=30, max=90, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="%"))


def _preset_names_selector(names: list[str]):
    return selector.SelectSelector(selector.SelectSelectorConfig(
        options=[selector.SelectOptionDict(value=n, label=n) for n in names], mode=selector.SelectSelectorMode.DROPDOWN,
    ))


def _actuator_fields() -> dict:
    """Nada de "actuator_mode" ni de elegir capacidad de calor/frio a
    mano: se listan los actuadores que de verdad tiene la zona (tantos
    como se quiera de cada tipo) y climate.py deduce solo, a partir de
    ellos, que puede hacer la zona y que hvac_modes expone al final —
    ver const.py y climate.py (`_compute_capability`)."""
    return {
        vol.Optional(CONF_CLIMATE_ENTITIES, default=[]): _entity("climate", multiple=True),
        vol.Optional(CONF_HEAT_SWITCHES, default=[]): _entity("switch", multiple=True),
        vol.Optional(CONF_COOL_SWITCHES, default=[]): _entity("switch", multiple=True),
    }


class ClimateOrchestratorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Asistente de alta de una zona nueva, paso a paso."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict = {}
        self._preset_names: list[str] = []

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_sensors()

        schema = vol.Schema({
            vol.Required("name"): str,
            vol.Required(CONF_PRIORITY, default="confort"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=PRIORITY_OPTIONS, mode=selector.SelectSelectorMode.LIST)),
        })
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_sensors(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_actuator()

        schema = vol.Schema({
            vol.Required(CONF_CURRENT_TEMP_SENSOR): _entity("sensor"),
            vol.Optional(CONF_HUMIDITY_SENSOR): _entity("sensor"),
            vol.Optional(CONF_OUTDOOR_TEMP_SENSOR): _entity("sensor"),
            vol.Optional(CONF_WEATHER_ENTITY): _entity("weather"),
        })
        return self.async_show_form(step_id="sensors", data_schema=schema)

    async def async_step_actuator(self, user_input=None):
        """Actuadores de la zona: cuantos climate.* delegados quieras
        (cada uno gobernado por SUS PROPIOS hvac_modes nativos — si
        soporta "heat", se activa en "heat" cuando toca; si soporta
        "cool", en "cool"; si soporta los dos de verdad -bomba de calor
        reversible-, se le manda el que corresponda cada vez, una unica
        orden) mas los switches de calor/frio que tengas (un switch, a
        diferencia de un climate.*, no puede autodeclarar para que sirve,
        asi que esos si van en su lista correspondiente)."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_presets()

        return self.async_show_form(step_id="actuator", data_schema=vol.Schema(_actuator_fields()))

    async def async_step_presets(self, user_input=None):
        """Presets con nombre en vez de horario — ver presets.py. Se valida
        el texto aqui mismo (formato "Nombre: temperatura, ...") antes de
        avanzar, para poder construir el desplegable del siguiente paso con
        los nombres ya validados."""
        errors: dict = {}
        if user_input is not None:
            try:
                parsed = presets_module.parse_presets(user_input[CONF_PRESETS_TEXT])
            except ValueError as e:
                errors["base"] = "invalid_presets"
                self._preset_error = str(e)
            else:
                self._data[CONF_PRESETS_TEXT] = user_input[CONF_PRESETS_TEXT]
                self._preset_names = [p["name"] for p in parsed]
                return await self.async_step_preset_roles()

        schema = vol.Schema({vol.Required(CONF_PRESETS_TEXT, default=self._data.get(CONF_PRESETS_TEXT, "Confort: 21/25, Ausente: 17/28")): str})
        description_placeholders = {"error": getattr(self, "_preset_error", ""), "format": PRESETS_TEXT_DESCRIPTION}
        return self.async_show_form(step_id="presets", data_schema=schema, errors=errors, description_placeholders=description_placeholders)

    async def async_step_preset_roles(self, user_input=None):
        """Cuál de los presets declarados se activa en modo automático
        según la presencia FÍSICA real de la zona (ver
        CONF_PRESENCE_ENTITIES) — el que se usa cuando el preset activo es
        "Automático", el valor por defecto."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_limits()

        names = self._preset_names or ["Confort", "Ausente"]
        default_presence = names[0]
        default_away = names[1] if len(names) > 1 else names[0]
        schema = vol.Schema({
            vol.Required(CONF_PRESENCE_PRESET, default=default_presence): _preset_names_selector(names),
            vol.Required(CONF_AWAY_PRESET, default=default_away): _preset_names_selector(names),
        })
        return self.async_show_form(step_id="preset_roles", data_schema=schema)

    async def async_step_limits(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_options()

        schema = vol.Schema({
            vol.Required(CONF_DEADBAND, default=DEFAULT_DEADBAND): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.1, max=3, step=0.1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="°C")),
            vol.Required(CONF_MIN_TEMP, default=DEFAULT_MIN_TEMP): _temp_number(),
            vol.Required(CONF_MAX_TEMP, default=DEFAULT_MAX_TEMP): _temp_number(),
            vol.Optional(CONF_MIN_ON_SECONDS, default=DEFAULT_MIN_ON_SECONDS): _seconds_number(),
            vol.Optional(CONF_MIN_OFF_SECONDS, default=DEFAULT_MIN_OFF_SECONDS): _seconds_number(),
        })
        return self.async_show_form(step_id="limits", data_schema=schema, description_placeholders={
            "limits_note": "Techo/suelo de seguridad: se respetan SIEMPRE, haya o no presencia, sea cual sea el preset activo."
        })

    async def async_step_options(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title=self._data["name"], data=self._data)

        schema = vol.Schema({
            vol.Optional(CONF_PRESENCE_ENTITIES, default=[]): _entity(
                ["binary_sensor", "person", "device_tracker"], multiple=True),
            vol.Optional(CONF_DOOR_WINDOW_ENTITIES, default=[]): _entity("binary_sensor", multiple=True),
            vol.Optional(CONF_HISTORY_DAYS_FOR_INERTIA, default=DEFAULT_HISTORY_DAYS_FOR_INERTIA): selector.NumberSelector(
                selector.NumberSelectorConfig(min=3, max=30, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="días")),
            vol.Optional(CONF_FORECAST_REFRESH_MINUTES, default=DEFAULT_FORECAST_REFRESH_MINUTES): selector.NumberSelector(
                selector.NumberSelectorConfig(min=2, max=60, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="min")),
            vol.Optional(CONF_AUTO_FAN_IDLE, default=False): selector.BooleanSelector(),
            vol.Optional(CONF_AUTO_DRY_HUMIDITY, default=False): selector.BooleanSelector(),
            vol.Optional(CONF_DRY_HUMIDITY_THRESHOLD, default=DEFAULT_DRY_HUMIDITY_THRESHOLD): _percent_number(),
            vol.Optional(CONF_SIMULATE, default=True): selector.BooleanSelector(),
        })
        return self.async_show_form(step_id="options", data_schema=schema, description_placeholders={
            "presence_note": "Pensado para sensores de presencia FÍSICA de esta habitación (PIR, mmWave, radar de presencia...), "
                              "no solo \"en casa\": binary_sensor de ocupación/movimiento es la señal principal. "
                              "person./device_tracker. también se aceptan, como señal adicional.",
            "smart_idle_note": "Reposo inteligente (opcional, desactivado por defecto): en vez de apagar del todo cuando ya no "
                                "hace falta calor ni frío, un climate.* delegado que también sepa ventilar o deshumidificar puede "
                                "usarse — solo si el equipo lo soporta de verdad. Deshumidificar solo se activa si además hay un "
                                "sensor de humedad configurado (paso Sensores) y su lectura supera el umbral elegido."
        })

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ClimateOrchestratorOptionsFlow(config_entry)


class ClimateOrchestratorOptionsFlow(config_entries.OptionsFlow):
    """Edicion de una zona ya creada: un unico formulario con todo,
    precargado con los valores actuales. Guardar aqui actualiza
    directamente `entry.data` y recarga la entrada (ver __init__.py).

    Los presets se editan como el mismo texto libre del asistente inicial
    (no un desplegable ya validado) — es una edicion, no un alta guiada, y
    asi no hay problema de "el desplegable ya no coincide con lo que
    acabas de escribir" a mitad de guardar."""

    def __init__(self, config_entry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        current = {**self.config_entry.data}
        errors: dict = {}

        if user_input is not None:
            try:
                presets_module.parse_presets(user_input[CONF_PRESETS_TEXT])
            except ValueError:
                errors["base"] = "invalid_presets"
            else:
                merged = {**current, **user_input}
                self.hass.config_entries.async_update_entry(self.config_entry, data=merged, title=merged.get("name", self.config_entry.title))
                return self.async_create_entry(title="", data={})

        fields: dict = {
            vol.Required("name", default=current.get("name", "")): str,
            vol.Required(CONF_PRIORITY, default=current.get(CONF_PRIORITY, "confort")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=PRIORITY_OPTIONS, mode=selector.SelectSelectorMode.LIST)),
            vol.Required(CONF_CURRENT_TEMP_SENSOR, default=current.get(CONF_CURRENT_TEMP_SENSOR, "")): _entity("sensor"),
            vol.Optional(CONF_HUMIDITY_SENSOR, default=current.get(CONF_HUMIDITY_SENSOR, "")): _entity("sensor"),
            vol.Optional(CONF_OUTDOOR_TEMP_SENSOR, default=current.get(CONF_OUTDOOR_TEMP_SENSOR, "")): _entity("sensor"),
            vol.Optional(CONF_WEATHER_ENTITY, default=current.get(CONF_WEATHER_ENTITY, "")): _entity("weather"),
            vol.Optional(CONF_CLIMATE_ENTITIES, default=current.get(CONF_CLIMATE_ENTITIES, [])): _entity("climate", multiple=True),
            vol.Optional(CONF_HEAT_SWITCHES, default=current.get(CONF_HEAT_SWITCHES, [])): _entity("switch", multiple=True),
            vol.Optional(CONF_COOL_SWITCHES, default=current.get(CONF_COOL_SWITCHES, [])): _entity("switch", multiple=True),
            vol.Required(CONF_PRESETS_TEXT, default=current.get(CONF_PRESETS_TEXT, "Confort: 21/25, Ausente: 17/28")): str,
            vol.Required(CONF_PRESENCE_PRESET, default=current.get(CONF_PRESENCE_PRESET, "Confort")): str,
            vol.Required(CONF_AWAY_PRESET, default=current.get(CONF_AWAY_PRESET, "Ausente")): str,
            vol.Required(CONF_DEADBAND, default=current.get(CONF_DEADBAND, DEFAULT_DEADBAND)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.1, max=3, step=0.1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="°C")),
            vol.Required(CONF_MIN_TEMP, default=current.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)): _temp_number(),
            vol.Required(CONF_MAX_TEMP, default=current.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)): _temp_number(),
            vol.Optional(CONF_MIN_ON_SECONDS, default=current.get(CONF_MIN_ON_SECONDS, DEFAULT_MIN_ON_SECONDS)): _seconds_number(),
            vol.Optional(CONF_MIN_OFF_SECONDS, default=current.get(CONF_MIN_OFF_SECONDS, DEFAULT_MIN_OFF_SECONDS)): _seconds_number(),
            vol.Optional(CONF_PRESENCE_ENTITIES, default=current.get(CONF_PRESENCE_ENTITIES, [])): _entity(
                ["binary_sensor", "person", "device_tracker"], multiple=True),
            vol.Optional(CONF_DOOR_WINDOW_ENTITIES, default=current.get(CONF_DOOR_WINDOW_ENTITIES, [])): _entity("binary_sensor", multiple=True),
            vol.Optional(CONF_HISTORY_DAYS_FOR_INERTIA, default=current.get(CONF_HISTORY_DAYS_FOR_INERTIA, DEFAULT_HISTORY_DAYS_FOR_INERTIA)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=3, max=30, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="días")),
            vol.Optional(CONF_FORECAST_REFRESH_MINUTES, default=current.get(CONF_FORECAST_REFRESH_MINUTES, DEFAULT_FORECAST_REFRESH_MINUTES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=2, max=60, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="min")),
            vol.Optional(CONF_AUTO_FAN_IDLE, default=current.get(CONF_AUTO_FAN_IDLE, False)): selector.BooleanSelector(),
            vol.Optional(CONF_AUTO_DRY_HUMIDITY, default=current.get(CONF_AUTO_DRY_HUMIDITY, False)): selector.BooleanSelector(),
            vol.Optional(CONF_DRY_HUMIDITY_THRESHOLD, default=current.get(CONF_DRY_HUMIDITY_THRESHOLD, DEFAULT_DRY_HUMIDITY_THRESHOLD)): _percent_number(),
            vol.Optional(CONF_SIMULATE, default=current.get(CONF_SIMULATE, True)): selector.BooleanSelector(),
        }
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields), errors=errors)
