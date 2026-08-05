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
    CONF_HUMIDITY_SENSOR,
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
    DOMAIN,
)

CAPABILITY_OPTIONS = [
    selector.SelectOptionDict(value="heat", label="Solo calor"),
    selector.SelectOptionDict(value="cool", label="Solo frío"),
    selector.SelectOptionDict(value="heat_cool", label="Calor y frío (dos equipos, o uno reversible)"),
]
ACTUATOR_MODE_OPTIONS = [
    selector.SelectOptionDict(value="switch", label="Encender/apagar un switch directamente"),
    selector.SelectOptionDict(value="climate", label="Delegar en un climate.* ya existente"),
]
PRIORITY_OPTIONS = [
    selector.SelectOptionDict(value="confort", label="Confort: actúa en cuanto hace falta"),
    selector.SelectOptionDict(value="ahorro", label="Ahorro: margen más ancho, se estrecha si empeora la previsión exterior"),
    selector.SelectOptionDict(value="manual", label="Manual: nunca decide sola"),
]

PRESETS_TEXT_DESCRIPTION = (
    'Un preset por cada situación que quieras distinguir, separados por comas: '
    '"Nombre: temperatura". Ejemplo: "Confort: 21, Ausente: 17, Fiesta: 23"'
)


def _entity(domain, device_class=None, multiple=False):
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain, device_class=device_class, multiple=multiple))


def _temp_number():
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=-20, max=45, step=0.5, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="°C",
    ))


def _seconds_number():
    return selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=3600, step=30, mode=selector.NumberSelectorMode.BOX))


def _actuator_mode_selector():
    return selector.SelectSelector(selector.SelectSelectorConfig(options=ACTUATOR_MODE_OPTIONS, mode=selector.SelectSelectorMode.LIST))


def _preset_names_selector(names: list[str]):
    return selector.SelectSelector(selector.SelectSelectorConfig(
        options=[selector.SelectOptionDict(value=n, label=n) for n in names], mode=selector.SelectSelectorMode.DROPDOWN,
    ))


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
            vol.Required(CONF_HVAC_CAPABILITY, default="heat"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=CAPABILITY_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)),
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
        """Calor y frío tienen CADA UNO su propio actuador, independiente,
        y SIN asumir que uno es switch y el otro climate.* — cualquiera de
        los dos puede ser lo uno o lo otro (un radiador puede tener su
        propia entidad climate.*, p.ej. una válvula termostática, igual
        que un aire acondicionado; y cualquiera puede ser tambien un
        switch simple). Pensado sobre todo para el caso real mas comun con
        "heat_cool": DOS equipos DISTINTOS, no uno reversible. Si son el
        mismo climate.* de verdad (un equipo reversible), basta con poner
        la misma entidad en los dos campos "climate" — climate.py lo
        detecta y le manda una unica orden con el modo correcto cada vez,
        nunca dos ordenes que se pisen."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_presets()

        capability = self._data.get(CONF_HVAC_CAPABILITY, "heat")
        fields: dict = {}
        if capability in ("heat", "heat_cool"):
            fields[vol.Required(CONF_HEAT_ACTUATOR_MODE, default="switch")] = _actuator_mode_selector()
            fields[vol.Optional(CONF_HEAT_SWITCH)] = _entity("switch")
            fields[vol.Optional(CONF_HEAT_CLIMATE)] = _entity("climate")
        if capability in ("cool", "heat_cool"):
            fields[vol.Required(CONF_COOL_ACTUATOR_MODE, default="switch")] = _actuator_mode_selector()
            fields[vol.Optional(CONF_COOL_SWITCH)] = _entity("switch")
            fields[vol.Optional(CONF_COOL_CLIMATE)] = _entity("climate")

        return self.async_show_form(step_id="actuator", data_schema=vol.Schema(fields))

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

        schema = vol.Schema({vol.Required(CONF_PRESETS_TEXT, default=self._data.get(CONF_PRESETS_TEXT, "Confort: 21, Ausente: 17")): str})
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
            vol.Optional(CONF_MANUAL_OVERRIDE_HOURS, default=DEFAULT_MANUAL_OVERRIDE_HOURS): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.5, max=12, step=0.5, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="h")),
            vol.Optional(CONF_HISTORY_DAYS_FOR_INERTIA, default=DEFAULT_HISTORY_DAYS_FOR_INERTIA): selector.NumberSelector(
                selector.NumberSelectorConfig(min=3, max=30, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="días")),
            vol.Optional(CONF_FORECAST_REFRESH_MINUTES, default=DEFAULT_FORECAST_REFRESH_MINUTES): selector.NumberSelector(
                selector.NumberSelectorConfig(min=2, max=60, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="min")),
            vol.Optional(CONF_SIMULATE, default=True): selector.BooleanSelector(),
        })
        return self.async_show_form(step_id="options", data_schema=schema, description_placeholders={
            "presence_note": "Pensado para sensores de presencia FÍSICA de esta habitación (PIR, mmWave, radar de presencia...), "
                              "no solo \"en casa\": binary_sensor de ocupación/movimiento es la señal principal. "
                              "person./device_tracker. también se aceptan, como señal adicional."
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
            except ValueError as e:
                errors["base"] = "invalid_presets"
            else:
                merged = {**current, **user_input}
                self.hass.config_entries.async_update_entry(self.config_entry, data=merged, title=merged.get("name", self.config_entry.title))
                return self.async_create_entry(title="", data={})

        capability = current.get(CONF_HVAC_CAPABILITY, "heat")

        fields: dict = {
            vol.Required("name", default=current.get("name", "")): str,
            vol.Required(CONF_HVAC_CAPABILITY, default=capability): selector.SelectSelector(
                selector.SelectSelectorConfig(options=CAPABILITY_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Required(CONF_PRIORITY, default=current.get(CONF_PRIORITY, "confort")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=PRIORITY_OPTIONS, mode=selector.SelectSelectorMode.LIST)),
            vol.Required(CONF_CURRENT_TEMP_SENSOR, default=current.get(CONF_CURRENT_TEMP_SENSOR, "")): _entity("sensor"),
            vol.Optional(CONF_HUMIDITY_SENSOR, default=current.get(CONF_HUMIDITY_SENSOR, "")): _entity("sensor"),
            vol.Optional(CONF_OUTDOOR_TEMP_SENSOR, default=current.get(CONF_OUTDOOR_TEMP_SENSOR, "")): _entity("sensor"),
            vol.Optional(CONF_WEATHER_ENTITY, default=current.get(CONF_WEATHER_ENTITY, "")): _entity("weather"),
        }
        if capability in ("heat", "heat_cool"):
            fields[vol.Required(CONF_HEAT_ACTUATOR_MODE, default=current.get(CONF_HEAT_ACTUATOR_MODE, "switch"))] = _actuator_mode_selector()
            fields[vol.Optional(CONF_HEAT_SWITCH, default=current.get(CONF_HEAT_SWITCH, ""))] = _entity("switch")
            fields[vol.Optional(CONF_HEAT_CLIMATE, default=current.get(CONF_HEAT_CLIMATE, ""))] = _entity("climate")
        if capability in ("cool", "heat_cool"):
            fields[vol.Required(CONF_COOL_ACTUATOR_MODE, default=current.get(CONF_COOL_ACTUATOR_MODE, "switch"))] = _actuator_mode_selector()
            fields[vol.Optional(CONF_COOL_SWITCH, default=current.get(CONF_COOL_SWITCH, ""))] = _entity("switch")
            fields[vol.Optional(CONF_COOL_CLIMATE, default=current.get(CONF_COOL_CLIMATE, ""))] = _entity("climate")

        fields.update({
            vol.Required(CONF_PRESETS_TEXT, default=current.get(CONF_PRESETS_TEXT, "Confort: 21, Ausente: 17")): str,
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
            vol.Optional(CONF_MANUAL_OVERRIDE_HOURS, default=current.get(CONF_MANUAL_OVERRIDE_HOURS, DEFAULT_MANUAL_OVERRIDE_HOURS)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.5, max=12, step=0.5, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="h")),
            vol.Optional(CONF_HISTORY_DAYS_FOR_INERTIA, default=current.get(CONF_HISTORY_DAYS_FOR_INERTIA, DEFAULT_HISTORY_DAYS_FOR_INERTIA)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=3, max=30, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="días")),
            vol.Optional(CONF_FORECAST_REFRESH_MINUTES, default=current.get(CONF_FORECAST_REFRESH_MINUTES, DEFAULT_FORECAST_REFRESH_MINUTES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=2, max=60, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="min")),
            vol.Optional(CONF_SIMULATE, default=current.get(CONF_SIMULATE, True)): selector.BooleanSelector(),
        })
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields), errors=errors)
