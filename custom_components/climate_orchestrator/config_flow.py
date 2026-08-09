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
    CONF_ACTUATOR_POWER,
    CONF_AUTO_WINDOW_DETECTION,
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
    CONF_HOME_POWER_SENSOR,
    CONF_HUMIDIFIER_ENTITIES,
    CONF_HUMIDITY_SENSOR,
    CONF_MAX_POWER_W,
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
    CONF_TARGET_HUMIDITY,
    CONF_TPI_CYCLE_MINUTES,
    CONF_WEATHER_ENTITY,
    DEFAULT_DEADBAND,
    DEFAULT_DRY_HUMIDITY_THRESHOLD,
    DEFAULT_FORECAST_REFRESH_MINUTES,
    DEFAULT_HISTORY_DAYS_FOR_INERTIA,
    DEFAULT_MAX_POWER_W,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_OFF_SECONDS,
    DEFAULT_MIN_ON_SECONDS,
    DEFAULT_MIN_TEMP,
    DEFAULT_TARGET_HUMIDITY,
    DEFAULT_TPI_CYCLE_MINUTES,
    DOMAIN,
)

# Prefijos de las claves DINAMICAS del paso "consumo por actuador" (ver
# `_actuator_power_fields`/`_parse_actuator_power_input`) — una por cada
# entidad ya declarada como actuador, generadas en tiempo de ejecucion
# (no se conocen los entity_id de antemano, asi que no pueden ser claves
# fijas de const.py).
POWER_SENSOR_KEY_PREFIX = "power_sensor::"
POWER_ESTIMATE_KEY_PREFIX = "power_estimate::"

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


def _watts_number():
    return selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=20000, step=50, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="W"))


def _percent_number():
    return selector.NumberSelector(selector.NumberSelectorConfig(min=30, max=90, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="%"))


def _target_humidity_number():
    return selector.NumberSelector(selector.NumberSelectorConfig(min=20, max=80, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="%"))


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
        vol.Optional(CONF_HUMIDIFIER_ENTITIES, default=[]): _entity("humidifier", multiple=True),
    }


def _all_actuator_entities(data: dict) -> list[str]:
    """Todos los actuadores YA declarados (de cualquier tipo) — es sobre
    esta lista sobre la que se construye el paso dinamico de consumo
    electrico POR ACTUADOR (ver `_actuator_power_fields`): una misma zona
    puede tener un equipo sin forma de medir su consumo real (un aire
    acondicionado con maquina exterior compartida) y otro con su propio
    sensor, cada uno necesita su propia fuente."""
    return (
        (data.get(CONF_HEAT_SWITCHES) or [])
        + (data.get(CONF_COOL_SWITCHES) or [])
        + (data.get(CONF_CLIMATE_ENTITIES) or [])
        + (data.get(CONF_HUMIDIFIER_ENTITIES) or [])
    )


def _actuator_power_fields(entities: list[str], existing: dict, editing: bool) -> dict:
    """Campos DINAMICOS, uno por cada actuador ya declarado — claves
    generadas con el propio entity_id (ver POWER_SENSOR_KEY_PREFIX/
    POWER_ESTIMATE_KEY_PREFIX), asi que no pueden ser constantes fijas.
    `editing=True` (options flow) da un default explicito (incluido ""/0
    para "nada puesto"); en el alta nueva se deja sin default cuando no
    hay nada que precargar, siguiendo el mismo patron ya usado en el
    resto del asistente para selectores de entidad opcionales."""
    fields: dict = {}
    for entity_id in entities:
        current_entity = existing.get(entity_id) or {}
        sensor_key = f"{POWER_SENSOR_KEY_PREFIX}{entity_id}"
        estimate_key = f"{POWER_ESTIMATE_KEY_PREFIX}{entity_id}"
        if editing:
            fields[vol.Optional(sensor_key, default=current_entity.get("sensor") or "")] = _entity("sensor", device_class="power")
            fields[vol.Optional(estimate_key, default=current_entity.get("estimated_w") or 0)] = _watts_number()
        else:
            fields[vol.Optional(sensor_key)] = _entity("sensor", device_class="power")
            fields[vol.Optional(estimate_key, default=0)] = _watts_number()
    return fields


def _parse_actuator_power_input(entities: list[str], user_input: dict) -> dict:
    """Reconstruye el dict CONF_ACTUATOR_POWER a partir de las claves
    dinamicas del formulario — solo se guarda entrada por actuador si de
    verdad se puso algo (sensor o potencia estimada), para no llenar la
    entrada de la zona de ceros/vacios inutiles."""
    power: dict = {}
    for entity_id in entities:
        sensor = user_input.get(f"{POWER_SENSOR_KEY_PREFIX}{entity_id}") or None
        estimate = user_input.get(f"{POWER_ESTIMATE_KEY_PREFIX}{entity_id}") or 0
        if sensor or estimate:
            power[entity_id] = {"sensor": sensor, "estimated_w": estimate}
    return power


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
            return await self.async_step_actuator_power()

        return self.async_show_form(step_id="actuator", data_schema=vol.Schema(_actuator_fields()))

    async def async_step_actuator_power(self, user_input=None):
        """Consumo electrico POR ACTUADOR (opcional) — un sensor de
        potencia propio si lo tiene, o una potencia estimada fija (de su
        ficha tecnica) si no se puede medir, p.ej. un aire acondicionado
        con maquina exterior compartida entre varias interiores. Se
        salta sin mas si esta zona todavia no tiene ningun actuador
        declarado."""
        entities = _all_actuator_entities(self._data)
        if not entities:
            return await self.async_step_presets()

        if user_input is not None:
            self._data[CONF_ACTUATOR_POWER] = _parse_actuator_power_input(entities, user_input)
            return await self.async_step_presets()

        fields = _actuator_power_fields(entities, {}, editing=False)
        return self.async_show_form(step_id="actuator_power", data_schema=vol.Schema(fields), description_placeholders={
            "power_note": "Opcional, por cada actuador de arriba: un sensor de potencia (W) propio si lo tiene, o una potencia "
                          "estimada fija (de su ficha técnica) si no se puede medir de verdad. Si lo dejas en blanco y más "
                          "adelante declaras un sensor de potencia GENERAL de la vivienda (paso Avanzado), Climate Orchestrator "
                          "intenta aprender solo su consumo típico."
        })

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
            vol.Optional(CONF_TARGET_HUMIDITY, default=DEFAULT_TARGET_HUMIDITY): _target_humidity_number(),
        })
        return self.async_show_form(step_id="limits", data_schema=schema, description_placeholders={
            "limits_note": "Techo/suelo de seguridad: se respetan SIEMPRE, haya o no presencia, sea cual sea el preset activo.",
            "humidity_note": "Consigna de humedad (solo si declaraste humidifier_entities en Actuadores): un único valor por "
                              "zona, no por preset — también ajustable al vuelo desde la tarjeta del termostato."
        })

    async def async_step_options(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title=self._data["name"], data=self._data)

        schema = vol.Schema({
            vol.Optional(CONF_PRESENCE_ENTITIES, default=[]): _entity(
                ["binary_sensor", "person", "device_tracker"], multiple=True),
            vol.Optional(CONF_DOOR_WINDOW_ENTITIES, default=[]): _entity("binary_sensor", multiple=True),
            vol.Optional(CONF_AUTO_WINDOW_DETECTION, default=False): selector.BooleanSelector(),
            vol.Optional(CONF_HISTORY_DAYS_FOR_INERTIA, default=DEFAULT_HISTORY_DAYS_FOR_INERTIA): selector.NumberSelector(
                selector.NumberSelectorConfig(min=3, max=30, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="días")),
            vol.Optional(CONF_FORECAST_REFRESH_MINUTES, default=DEFAULT_FORECAST_REFRESH_MINUTES): selector.NumberSelector(
                selector.NumberSelectorConfig(min=2, max=60, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="min")),
            vol.Optional(CONF_DRY_HUMIDITY_THRESHOLD, default=DEFAULT_DRY_HUMIDITY_THRESHOLD): _percent_number(),
            vol.Optional(CONF_HOME_POWER_SENSOR): _entity("sensor", device_class="power"),
            vol.Optional(CONF_MAX_POWER_W, default=DEFAULT_MAX_POWER_W): _watts_number(),
            vol.Optional(CONF_TPI_CYCLE_MINUTES, default=DEFAULT_TPI_CYCLE_MINUTES): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=60, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="min")),
            vol.Optional(CONF_SIMULATE, default=True): selector.BooleanSelector(),
        })
        return self.async_show_form(step_id="options", data_schema=schema, description_placeholders={
            "presence_note": "Pensado para sensores de presencia FÍSICA de esta habitación (PIR, mmWave, radar de presencia...), "
                              "no solo \"en casa\": binary_sensor de ocupación/movimiento es la señal principal. "
                              "person./device_tracker. también se aceptan, como señal adicional.",
            "smart_idle_note": "Reposo inteligente: en cuanto la zona ya no necesita calor ni frío (y sigue en el modo más "
                                "automático que tenga, Auto o su único modo), un climate.* delegado que también sepa ventilar o "
                                "deshumidificar se usa solo — solo si el equipo lo soporta de verdad. Deshumidificar solo se "
                                "activa si además hay un sensor de humedad configurado (paso Sensores) y su lectura supera el "
                                "umbral elegido.",
            "window_note": "Detección automática de ventana abierta (opcional, desactivada por defecto): respaldo por caída/subida "
                            "anómala de temperatura para ventanas sin sensor propio — nunca sustituye a un sensor real declarado arriba.",
            "power_note": "Sensor de potencia GENERAL de la vivienda (opcional): con él, Climate Orchestrator intenta aprender el "
                          "consumo típico de los actuadores que no tengan ni sensor propio ni potencia estimada (paso Actuadores) "
                          "— descartando muestras contaminadas por otras zonas activas a la vez. Si además pones una potencia "
                          "máxima, no se arrancan nuevos actuadores mientras la zona ya esté al límite."
        })

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ClimateOrchestratorOptionsFlow()


class ClimateOrchestratorOptionsFlow(config_entries.OptionsFlow):
    """Edicion de una zona ya creada: un unico formulario con todo,
    precargado con los valores actuales. Guardar aqui actualiza
    directamente `entry.data` y recarga la entrada (ver __init__.py).

    Los presets se editan como el mismo texto libre del asistente inicial
    (no un desplegable ya validado) — es una edicion, no un alta guiada, y
    asi no hay problema de "el desplegable ya no coincide con lo que
    acabas de escribir" a mitad de guardar.

    OJO: nada de `__init__(self, config_entry)` fijando `self.config_entry`
    a mano — las versiones recientes de HA ya gestionan `config_entry`
    ellas solas como propiedad de la clase base; sobreescribirla a mano
    rompe el flujo entero con un 500 en cuanto se abre "Configurar" (asi
    se detecto: "no se pudo cargar el flujo de configuracion"). Se usa tal
    cual, sin constructor propio.

    Un MENU por categorias en vez de un unico formulario gigante con los
    ~25 campos a la vez (como era antes) — entras, editas SOLO la
    categoria que te interesa (Actuadores, Presets, Limites...) y listo,
    sin tener que revisar/volver a rellenar todo lo demas cada vez. Mismo
    espiritu que versatile_thermostat. Cada categoria guarda y cierra el
    flujo por su cuenta (`_save_and_close`) — para tocar otra categoria,
    se vuelve a abrir "Configurar" desde cero."""

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "actuators", "presets", "limits", "presence_window", "power", "advanced"],
        )

    def _save_and_close(self, user_input: dict):
        current = {**self.config_entry.data}
        merged = {**current, **user_input}
        self.hass.config_entries.async_update_entry(
            self.config_entry, data=merged, title=merged.get("name", self.config_entry.title))
        return self.async_create_entry(title="", data={})

    async def async_step_general(self, user_input=None):
        current = {**self.config_entry.data}
        if user_input is not None:
            return self._save_and_close(user_input)
        fields = {
            vol.Required("name", default=current.get("name", "")): str,
            vol.Required(CONF_PRIORITY, default=current.get(CONF_PRIORITY, "confort")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=PRIORITY_OPTIONS, mode=selector.SelectSelectorMode.LIST)),
            vol.Required(CONF_CURRENT_TEMP_SENSOR, default=current.get(CONF_CURRENT_TEMP_SENSOR, "")): _entity("sensor"),
            vol.Optional(CONF_HUMIDITY_SENSOR, default=current.get(CONF_HUMIDITY_SENSOR, "")): _entity("sensor"),
            vol.Optional(CONF_OUTDOOR_TEMP_SENSOR, default=current.get(CONF_OUTDOOR_TEMP_SENSOR, "")): _entity("sensor"),
            vol.Optional(CONF_WEATHER_ENTITY, default=current.get(CONF_WEATHER_ENTITY, "")): _entity("weather"),
        }
        return self.async_show_form(step_id="general", data_schema=vol.Schema(fields))

    async def async_step_actuators(self, user_input=None):
        current = {**self.config_entry.data}
        if user_input is not None:
            return self._save_and_close(user_input)
        fields = {
            vol.Optional(CONF_CLIMATE_ENTITIES, default=current.get(CONF_CLIMATE_ENTITIES, [])): _entity("climate", multiple=True),
            vol.Optional(CONF_HEAT_SWITCHES, default=current.get(CONF_HEAT_SWITCHES, [])): _entity("switch", multiple=True),
            vol.Optional(CONF_COOL_SWITCHES, default=current.get(CONF_COOL_SWITCHES, [])): _entity("switch", multiple=True),
            vol.Optional(CONF_HUMIDIFIER_ENTITIES, default=current.get(CONF_HUMIDIFIER_ENTITIES, [])): _entity("humidifier", multiple=True),
        }
        return self.async_show_form(step_id="actuators", data_schema=vol.Schema(fields))

    async def async_step_power(self, user_input=None):
        """Consumo electrico POR ACTUADOR (ver `_actuator_power_fields` —
        no por zona: un aire acondicionado sin forma de medir su consumo
        real y un radiador con su propio sensor, en la misma zona,
        necesitan cada uno su propia fuente)."""
        current = {**self.config_entry.data}
        entities = _all_actuator_entities(current)
        existing_power = current.get(CONF_ACTUATOR_POWER) or {}
        if user_input is not None:
            return self._save_and_close({CONF_ACTUATOR_POWER: _parse_actuator_power_input(entities, user_input)})
        if not entities:
            return self.async_show_form(step_id="power", data_schema=vol.Schema({}), description_placeholders={
                "power_note": "Esta zona todavía no tiene ningún actuador declarado — añade uno primero en \"Actuadores\"."
            })
        fields = _actuator_power_fields(entities, existing_power, editing=True)
        return self.async_show_form(step_id="power", data_schema=vol.Schema(fields), description_placeholders={
            "power_note": "Por cada actuador: un sensor de potencia (W) propio si lo tiene, o una potencia estimada fija (de su "
                          "ficha técnica) si no se puede medir de verdad — p.ej. un aire acondicionado con máquina exterior "
                          "compartida. El sensor de potencia GENERAL de la vivienda (para aprender el resto solos) se declara en "
                          "\"Avanzado\"."
        })

    async def async_step_presets(self, user_input=None):
        current = {**self.config_entry.data}
        errors: dict = {}
        if user_input is not None:
            try:
                presets_module.parse_presets(user_input[CONF_PRESETS_TEXT])
            except ValueError:
                errors["base"] = "invalid_presets"
            else:
                return self._save_and_close(user_input)
        fields = {
            vol.Required(CONF_PRESETS_TEXT, default=current.get(CONF_PRESETS_TEXT, "Confort: 21/25, Ausente: 17/28")): str,
            vol.Required(CONF_PRESENCE_PRESET, default=current.get(CONF_PRESENCE_PRESET, "Confort")): str,
            vol.Required(CONF_AWAY_PRESET, default=current.get(CONF_AWAY_PRESET, "Ausente")): str,
        }
        return self.async_show_form(step_id="presets", data_schema=vol.Schema(fields), errors=errors)

    async def async_step_limits(self, user_input=None):
        current = {**self.config_entry.data}
        if user_input is not None:
            return self._save_and_close(user_input)
        fields = {
            vol.Required(CONF_DEADBAND, default=current.get(CONF_DEADBAND, DEFAULT_DEADBAND)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.1, max=3, step=0.1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="°C")),
            vol.Required(CONF_MIN_TEMP, default=current.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)): _temp_number(),
            vol.Required(CONF_MAX_TEMP, default=current.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)): _temp_number(),
            vol.Optional(CONF_MIN_ON_SECONDS, default=current.get(CONF_MIN_ON_SECONDS, DEFAULT_MIN_ON_SECONDS)): _seconds_number(),
            vol.Optional(CONF_MIN_OFF_SECONDS, default=current.get(CONF_MIN_OFF_SECONDS, DEFAULT_MIN_OFF_SECONDS)): _seconds_number(),
            vol.Optional(CONF_TARGET_HUMIDITY, default=current.get(CONF_TARGET_HUMIDITY, DEFAULT_TARGET_HUMIDITY)): _target_humidity_number(),
        }
        return self.async_show_form(step_id="limits", data_schema=vol.Schema(fields))

    async def async_step_presence_window(self, user_input=None):
        current = {**self.config_entry.data}
        if user_input is not None:
            return self._save_and_close(user_input)
        fields = {
            vol.Optional(CONF_PRESENCE_ENTITIES, default=current.get(CONF_PRESENCE_ENTITIES, [])): _entity(
                ["binary_sensor", "person", "device_tracker"], multiple=True),
            vol.Optional(CONF_DOOR_WINDOW_ENTITIES, default=current.get(CONF_DOOR_WINDOW_ENTITIES, [])): _entity("binary_sensor", multiple=True),
            vol.Optional(CONF_AUTO_WINDOW_DETECTION, default=current.get(CONF_AUTO_WINDOW_DETECTION, False)): selector.BooleanSelector(),
        }
        return self.async_show_form(step_id="presence_window", data_schema=vol.Schema(fields))

    async def async_step_advanced(self, user_input=None):
        current = {**self.config_entry.data}
        if user_input is not None:
            return self._save_and_close(user_input)
        fields = {
            vol.Optional(CONF_HISTORY_DAYS_FOR_INERTIA, default=current.get(CONF_HISTORY_DAYS_FOR_INERTIA, DEFAULT_HISTORY_DAYS_FOR_INERTIA)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=3, max=30, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="días")),
            vol.Optional(CONF_FORECAST_REFRESH_MINUTES, default=current.get(CONF_FORECAST_REFRESH_MINUTES, DEFAULT_FORECAST_REFRESH_MINUTES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=2, max=60, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="min")),
            vol.Optional(CONF_DRY_HUMIDITY_THRESHOLD, default=current.get(CONF_DRY_HUMIDITY_THRESHOLD, DEFAULT_DRY_HUMIDITY_THRESHOLD)): _percent_number(),
            vol.Optional(CONF_HOME_POWER_SENSOR, default=current.get(CONF_HOME_POWER_SENSOR, "")): _entity("sensor", device_class="power"),
            vol.Optional(CONF_MAX_POWER_W, default=current.get(CONF_MAX_POWER_W, DEFAULT_MAX_POWER_W)): _watts_number(),
            vol.Optional(CONF_TPI_CYCLE_MINUTES, default=current.get(CONF_TPI_CYCLE_MINUTES, DEFAULT_TPI_CYCLE_MINUTES)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=60, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="min")),
            vol.Optional(CONF_SIMULATE, default=current.get(CONF_SIMULATE, True)): selector.BooleanSelector(),
        }
        return self.async_show_form(step_id="advanced", data_schema=vol.Schema(fields))
