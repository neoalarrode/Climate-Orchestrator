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
con deshumidificacion o solo ventilador), esta zona los hereda igual como
modos elegibles A MANO — un radiador que solo sepa "off"/"heat"
simplemente no los aporta. Elegirlos a mano SI cambia el hvac_mode de
esta zona (como cualquier otro modo) y se relegan directos al equipo, sin
pasar por ninguna consigna de temperatura (ver `_PASSTHROUGH_MODES`).

Aparte, y esto es DISTINTO, el "reposo inteligente" (ver
`_smart_idle_action` — sin interruptor propio, coexiste solo mientras la
zona sigue en su modo mas automatico, Auto o su unico modo) puede usarlos
EL SOLO en vez de apagar del todo cuando la zona ya esta dentro de margen
— y ahi el hvac_mode de la zona NUNCA cambia: sigue "en Auto" de cara al
usuario/Matter/HomeKit aunque por debajo el equipo este ventilando o
deshumidificando un rato. En ambos casos, si el delegado no soporta lo
que le toca, se apaga sin mas, nunca se fuerza nada que no sepa hacer.

Humidificacion (ver CONF_HUMIDIFIER_ENTITIES en const.py) es otra
funcion PARALELA, esta vez NATIVA del propio termostato de la zona
(ClimateEntityFeature.TARGET_HUMIDITY — ajustable desde la misma tarjeta,
igual que la temperatura): activa siempre que la zona no este apagada ni
en pausa, sea cual sea el hvac_mode concreto (Auto, calor, frio...) —
"integrada en el funcionamiento automatico" en ese sentido, no exclusiva
de un unico modo. Consigna UNICA por zona (no por preset, a diferencia de
calor/frio) — ver `_drive_humidifiers`, que enciende cada humidifier.*
delegado con esa consigna y confia en su propia logica interna para
pararse solo, el mismo espiritu que el reposo mantenido de los climate.*
delegados."""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.components.climate.const import ATTR_HUMIDITY, ATTR_TARGET_TEMP_HIGH, ATTR_TARGET_TEMP_LOW
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util, slugify

from . import ema as ema_module, grid_signal, outdoor, power_model, presets as presets_module, scheduler, thermal_model, window_algorithm
from .const import (
    CONF_AUTO_WINDOW_DETECTION,
    CONF_CLIMATE_ENTITIES,
    CONF_COOL_SWITCHES,
    CONF_CURRENT_TEMP_SENSOR,
    CONF_DEADBAND,
    CONF_DOOR_WINDOW_ENTITIES,
    CONF_DRY_HUMIDITY_THRESHOLD,
    CONF_FORECAST_REFRESH_MINUTES,
    CONF_HEAT_SWITCHES,
    CONF_HISTORY_DAYS_FOR_INERTIA,
    CONF_HUMIDIFIER_ENTITIES,
    CONF_HUMIDITY_SENSOR,
    CONF_ACTUATOR_POWER,
    CONF_HOME_POWER_SENSOR,
    CONF_MAX_POWER_W,
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
    CONF_TARGET_HUMIDITY,
    CONF_TPI_CYCLE_MINUTES,
    CONF_WEATHER_ENTITY,
    DEFAULT_DEADBAND,
    DEFAULT_DRY_HUMIDITY_THRESHOLD,
    DEFAULT_FORECAST_REFRESH_MINUTES,
    DEFAULT_HISTORY_DAYS_FOR_INERTIA,
    DEFAULT_MAX_HUMIDITY,
    DEFAULT_MAX_POWER_W,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_HUMIDITY,
    DEFAULT_MIN_OFF_SECONDS,
    DEFAULT_MIN_ON_SECONDS,
    DEFAULT_MIN_TEMP,
    DEFAULT_OUTDOOR_HORIZON_HOURS,
    DEFAULT_TARGET_HUMIDITY,
    DEFAULT_TPI_CYCLE_MINUTES,
    DOMAIN,
)

# Vida media (segundos) del suavizado EMA del sensor externo (ver ema.py)
# — un pico de ruido puntual no debe hacer que el motor decida algo
# distinto de golpe. Si el sensor deja de dar lecturas NUEVAS de verdad
# (aunque su `state` siga pareciendo valido, "congelado" sin marcarse
# unavailable), se sigue confiando en el ultimo valor suavizado — marcado
# como `sensor_stale` en razon/atributos — hasta
# STALE_SENSOR_HARD_TIMEOUT_SECONDS; pasado ese limite, se da por no
# disponible de verdad (mismo comportamiento que hasta ahora, dejando de
# actuar del todo).
TEMP_EMA_HALFLIFE_SECONDS = 120
STALE_SENSOR_HARD_TIMEOUT_SECONDS = 5400  # 90 min

# `async_write_ha_state()` (ver `_maybe_write_ha_state`) escribe una fila
# NUEVA en el recorder cada vez que se llama, con la mayoria de los
# atributos de la entidad (no solo el estado) — y `current_temperature`
# viene de una EMA con 2 decimales (ver TEMP_EMA_HALFLIFE_SECONDS) que
# practicamente SIEMPRE cambia un poquito en cada lectura nueva del
# sensor, asi que el filtro de "sin cambios" que HA aplica por defecto
# antes de escribir casi nunca actua aqui. Sin limite, una zona con
# varios sensores vigilados (temperatura, humedad, potencia de cada
# actuador...) que reportan cada pocos segundos puede acabar escribiendo
# al recorder varias veces por minuto, multiplicado por cada zona
# declarada — carga de E/S de disco real e innecesaria en un dispositivo
# limitado (visto en produccion, RPi5). Por eso se throttlea: como mucho
# una escritura cada WRITE_MIN_INTERVAL_SECONDS por SOLO jitter numerico,
# pero cualquier cambio que de verdad importe (accion, modo, motivo)
# sigue escribiendo AL INSTANTE, sin esperar — ver `_maybe_write_ha_state`.
WRITE_MIN_INTERVAL_SECONDS = 20

# `_async_refresh_forecast` (disparado cada `forecast_refresh_minutes`,
# 10 min por defecto) recalcula el modelo termico (`thermal_model.
# async_get_model`) y el de potencia (`power_model.async_get_power_model`)
# desde cero en CADA disparo — cada uno escanea hasta `history_days_for_
# inertia` (14 dias por defecto) de historico del recorder, de VARIAS
# entidades (sensor de temperatura, exterior, cada actuador...), y el
# modelo de potencia ADEMAS escanea el historico de los actuadores de
# TODAS las demas zonas Climate Orchestrator para descartar solapamiento
# con una maquina exterior compartida (ver power_model.py). Confirmado en
# produccion (RPi5): un patron de cuelgues intermitentes de HA Core con
# exactamente este periodo (~10 min), que paraba en cuanto se
# desactivaba la integracion.
#
# Las propiedades fisicas que aprenden estos modelos (inercia termica del
# edificio, consumo tipico de un actuador) NO cambian de un ciclo a otro
# — a diferencia de la previsión meteorologica (que SI conviene refrescar
# cada `forecast_refresh_minutes`, eso sigue igual), volver a escanear
# dias de historico cada 10 min una vez el modelo YA es fiable es puro
# derroche. Por eso, una vez fiable (`_models_settled`), el recalculo se
# espacia a como mucho una vez cada MODEL_RECOMPUTE_MIN_INTERVAL_SECONDS
# — mientras el modelo TODAVIA no es fiable (zona recien creada, poco
# historico) se sigue intentando en cada ciclo normal, para converger
# rapido.
MODEL_RECOMPUTE_MIN_INTERVAL_SECONDS = 21600  # 6 h

# `_drive_climate_actuator`/`_drive_climate_idle`/`_drive_humidifiers` se
# llaman en CADA `_async_decide_and_act` (cada evento reactivo, potencialmente
# varias veces por minuto) — antes mandaban `set_hvac_mode`/`set_temperature`/
# `set_humidity` SIN comprobar si el delegado ya estaba puesto asi, repitiendo
# la misma orden real al dispositivo (y a su nube/API, en equipos WiFi) una y
# otra vez sin que nada hubiera cambiado. Ahora se compara contra lo que el
# propio delegado YA reporta en su estado antes de mandar nada — para
# `hvac_mode` la comparacion es exacta (`state.state`), para temperatura y
# humedad (numeros con redondeo/paso propio de cada fabricante) se usa un
# margen pequeño para no reenviar por una diferencia de decimas que no es
# un cambio real.
TEMP_SEND_TOLERANCE_DEG = 0.1
HUMIDITY_SEND_TOLERANCE_PCT = 1


def _safe_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# Deteccion automatica de velocidad de ventilador (ver `_pick_fan_mode`,
# `_available_fan_modes`, `async_set_fan_mode`): sin ninguna eleccion
# manual del usuario, el motor decide sola por palabras clave sobre los
# nombres REALES que cada delegado declara en sus propias `fan_modes` —
# nunca una lista fija propia, cada fabricante nombra distinto. Urgente
# (tramo de seguridad de scheduler.py) empuja hacia la mas potente
# disponible; tranquilo (todo lo demas: reactivo normal, anticipacion,
# banco de confort) hacia la mas silenciosa/eficiente. Si nada coincide,
# se prueba "auto" antes de rendirse; si tampoco hay "auto", no se toca
# nada — mejor no adivinar que mandar una velocidad al azar.
FAN_MODE_URGENT_KEYWORDS = ("high", "max", "turbo", "strong", "fast", "boost")
FAN_MODE_GENTLE_KEYWORDS = ("low", "quiet", "silent", "eco", "min", "sleep")


def _pick_fan_mode(fan_modes: list[str], urgent: bool, manual: str | None) -> str | None:
    if not fan_modes:
        return None
    if manual and manual in fan_modes:
        # Eleccion a mano del usuario (ver async_set_fan_mode): manda
        # siempre que este delegado la soporte, sin mirar la urgencia —
        # es una eleccion consciente, no algo que el motor deba
        # "corregir" por su cuenta.
        return manual
    keywords = FAN_MODE_URGENT_KEYWORDS if urgent else FAN_MODE_GENTLE_KEYWORDS
    for mode in fan_modes:
        if any(k in mode.lower() for k in keywords):
            return mode
    for mode in fan_modes:
        if "auto" in mode.lower():
            return mode
    return None


# Deteccion de fallo del equipo (ver `_check_equipment_failure`): si
# llevamos esto pidiendo calor/frio de verdad sin que la temperatura se
# mueva lo minimo esperado, es sospechoso -- solo informa, nunca actua
# por su cuenta.
EQUIPMENT_FAILURE_DETECTION_MINUTES = 30
EQUIPMENT_FAILURE_MIN_DELTA_DEG = 0.3

_LOGGER = logging.getLogger(__name__)

_ACTION_MAP = {
    "heat": HVACAction.HEATING, "cool": HVACAction.COOLING, "idle": HVACAction.IDLE,
    "dry": HVACAction.DRYING, "fan_only": HVACAction.FAN,
}


def _zone_stagger_seconds(entry_id: str, refresh_minutes: int) -> float:
    """Desfase ESTABLE (el mismo entry_id da siempre el mismo desfase,
    tambien tras un reinicio de HA — a proposito NO es aleatorio, para no
    generar un patron distinto cada vez que reinicia) derivado del propio
    entry_id de la zona, repartido uniformemente dentro de la ventana de
    `refresh_minutes`. Con esto, si hay varias zonas, sus recalculos
    periodicos (ver `_async_refresh_forecast`) quedan repartidos en el
    tiempo en vez de caer todos en el mismo instante — el motivo real, no
    solo cosmetico: SQLite (el recorder de HA por defecto) solo permite
    una escritura a la vez, y una lectura larga (nuestro escaneo de dias
    de historico) puede bloquear esa escritura mientras dura. Varias
    zonas escaneando A LA VEZ se serializan entre si Y con las escrituras
    normales del recorder, sea cual sea la potencia de la maquina — mas
    nucleos no paralelizan un unico fichero SQLite."""
    digest = hashlib.sha1(entry_id.encode()).hexdigest()
    fraction = int(digest[:8], 16) / 0xFFFFFFFF  # 0.0 .. 1.0, estable
    return fraction * refresh_minutes * 60

# "Modos extra" que un climate.* delegado puede declarar ademas de calor/
# frio (dry = deshumidificar, fan_only = solo ventilador). Dos usos BIEN
# distintos, ver `_async_decide_and_act`:
#   1. Elegidos A MANO desde el termostato: hvac_mode de la zona SI pasa a
#      DRY/FAN_ONLY (como cualquier otro modo) y se relegan directos al
#      delegado que los soporte.
#   2. Reposo inteligente, AUTOMATICO (ver `_smart_idle_action`): actua
#      SOLO mientras la zona sigue en su modo mas automatico (Auto, o el
#      unico modo de una de un solo sentido) — el hvac_mode de la zona
#      NUNCA cambia por esto, solo la orden que recibe el delegado.
_PASSTHROUGH_MODES = {HVACMode.DRY: "dry", HVACMode.FAN_ONLY: "fan_only"}

# Cuantas veces SEGUIDAS hace falta detectar que un climate.* delegado
# sigue desviandose de su consigna mientras se le mantiene encendido (ver
# `_check_delegate_overshoot`) antes de aprender que hay que apagarlo de
# verdad — no un pico puntual del sensor, un comportamiento sostenido.
OVERSHOOT_STRIKES_THRESHOLD = 2


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

        # Tiene que estar inicializado ANTES de la primera llamada a
        # `_refresh_hvac_modes()` de aqui abajo — esa ya lee
        # `self._manual_fan_mode` (ver `_available_fan_modes`). Sin esto,
        # AttributeError en cada arranque: confirmado en producción.
        self._manual_fan_mode: str | None = None
        self._attr_fan_mode: str | None = None
        self._attr_fan_modes: list[str] | None = None

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

        # Humidificacion (ver CONF_HUMIDIFIER_ENTITIES en const.py): consigna
        # UNICA por zona (no por preset), seguida por _drive_humidifiers
        # mientras la zona no este apagada ni en pausa. Ajustable al vuelo
        # desde la propia tarjeta (async_set_humidity), restaurada tras un
        # reinicio igual que la temperatura — el valor del config solo
        # siembra el arranque, igual que ocurre con los presets.
        self._attr_min_humidity = DEFAULT_MIN_HUMIDITY
        self._attr_max_humidity = DEFAULT_MAX_HUMIDITY
        self._attr_target_humidity = float(self.zone.get(CONF_TARGET_HUMIDITY, DEFAULT_TARGET_HUMIDITY))

        self._attr_target_temperature = None
        self._attr_target_temperature_low = None
        self._attr_target_temperature_high = None
        self._attr_available = True

        self._outdoor_forecast: list[float] = []
        self._outdoor_now: float | None = None
        self._thermal_model: dict = {}
        self._reason = "sin calcular todavia"
        self._active_preset_name: str | None = None

        # Throttle de `async_write_ha_state()` (ver `_maybe_write_ha_state`,
        # llamado al final de `_async_decide_and_act`) — ver WRITE_MIN_
        # INTERVAL_SECONDS mas abajo para el motivo.
        self._last_state_write_ts = None
        self._last_written_signature: tuple | None = None

        # Throttle del RECALCULO de los modelos termico/de potencia (ver
        # `_models_settled`/`_async_refresh_forecast`) — MODEL_RECOMPUTE_
        # MIN_INTERVAL_SECONDS mas abajo para el motivo (confirmado en
        # produccion: el patron de cuelgues intermitentes de HA Core tenia
        # exactamente el periodo de `forecast_refresh_minutes`).
        self._model_last_computed_ts = None

        # Consignas del preset "Manual" (ver presets.py) — a diferencia de
        # los demas presets, no viven en una entidad number.*: se ponen
        # directamente desde la tarjeta del termostato (async_set_temperature)
        # y se guardan aqui mismo, persistentes, restauradas tras un
        # reinicio igual que el resto (ver async_added_to_hass).
        self._manual_heat: float | None = None
        self._manual_cool: float | None = None

        # (self._manual_fan_mode / _attr_fan_mode / _attr_fan_modes ya se
        # inicializaron arriba del todo, ANTES de la primera llamada a
        # _refresh_hvac_modes() — ver el comentario ahi.)

        self._switch_last_change: dict[str, tuple[str, object]] = {}

        # TPI (ver scheduler.py `tpi_on_percent`/`_tpi_desired_on`):
        # inicio del ciclo actual de cada lado ("heat"/"cool") — dentro de
        # ese ciclo, el switch esta encendido durante el primer
        # `on_percent` % del tiempo, apagado el resto.
        self._tpi_cycle_start: dict[str, object] = {}
        self._last_heat_on_percent: float | None = None
        self._last_cool_on_percent: float | None = None

        # Desviacion medida AHORA MISMO entre el sensor propio de cada
        # climate.* delegado y el sensor externo de la zona — ver
        # `_compensate_delegate_target`. Se expone tal cual en
        # extra_state_attributes para poder revisarla.
        self._delegate_deviations: dict[str, float] = {}

        # Ultimo modo/consigna REAL (calor o frio, sin corregir) que se
        # persiguio de verdad en cada climate.* delegado — lo usa
        # `_drive_climate_idle` para mantenerlo ahi al llegar a la
        # consigna, en vez de apagarlo sin mas. Cuantas veces seguidas se
        # ha detectado que, mantenido asi, se desvia de esa consigna (ver
        # `_check_delegate_overshoot`) y el conjunto de los que ya se ha
        # aprendido que NO se autorregulan solos — este ultimo persiste
        # tras un reinicio (ver async_added_to_hass/extra_state_attributes).
        self._delegate_last_active: dict[str, tuple[str, float]] = {}
        self._delegate_overshoot_strikes: dict[str, int] = {}
        self._delegate_needs_explicit_off: set[str] = set()

        # Ultimo modo hvac activo (no "apagado") — lo usa async_turn_on
        # (ver TURN_ON/TURN_OFF en _refresh_hvac_modes) para volver
        # exactamente a donde estaba la zona, no a un generico "Auto": si
        # se habia bloqueado a mano a "solo calor", encender debe volver a
        # "solo calor", no saltar a otra cosa.
        self._last_active_hvac_mode: HVACMode | None = (
            self._attr_hvac_mode if self._attr_hvac_mode != HVACMode.OFF else None
        )

        # Suavizado EMA del sensor externo + margen de gracia si se queda
        # "congelado" (ver TEMP_EMA_HALFLIFE_SECONDS/STALE_SENSOR_* arriba,
        # y `_read_current_temp`).
        self._temp_ema = ema_module.Ema(TEMP_EMA_HALFLIFE_SECONDS)
        self._sensor_stale = False

        # Deteccion de ventana abierta SIN sensor dedicado (respaldo
        # opcional, ver CONF_AUTO_WINDOW_DETECTION y window_algorithm.py).
        self._window_detector = window_algorithm.WindowSlopeDetector()

        # Deteccion de posible fallo del equipo — solo informa (ver
        # `_check_equipment_failure`).
        self._equipment_run: tuple[str, object, float] | None = None
        self._equipment_failure_suspected = False

        # Aprendido de CONF_HOME_POWER_SENSOR (ver power_model.py), SOLO
        # para los actuadores que no tengan ni sensor propio ni potencia
        # estimada declarada (ver CONF_ACTUATOR_POWER) — {entity_id:
        # {"learned_power_w","reliable","samples_used",
        # "samples_discarded_other_zone"}}. Se refresca en el ciclo lento
        # (ver _async_refresh_forecast), igual que thermal_model.
        self._power_model: dict = {}

    # ---------------------------------------------------------- estado ----

    @property
    def extra_state_attributes(self) -> dict:
        zone_power_w, zone_power_breakdown = self._zone_power_w()
        return {
            "reason": self._reason,
            "active_preset": self._active_preset_name,
            "priority": self.zone.get(CONF_PRIORITY),
            "simulate": self.zone.get(CONF_SIMULATE, True),
            "thermal_model_reliable": self._thermal_model.get("reliable", False),
            "heating_rate_deg_h": round(self._thermal_model.get("heating_rate_deg_h", 0) or 0, 2),
            "cooling_rate_deg_h": round(self._thermal_model.get("cooling_rate_deg_h", 0) or 0, 2),
            # Capacidad de RETENCION de la zona (cuanto se acerca a la
            # temperatura exterior por hora, con todo apagado, por grado
            # de diferencia — aprendido del historico real, ver
            # thermal_model.py) — decide cuanto merece la pena banquear en
            # el preheat/preenfriado oportunista (ver scheduler.py
            # `_retention_factor`, `_opportunistic_preheat`/
            # `_price_anticipation_preheat`). Se expone el numero crudo Y
            # la etiqueta legible — nunca solo la etiqueta, para que se
            # pueda comprobar el calculo.
            "idle_loss_coeff": round(self._thermal_model.get("idle_loss_coeff", 0) or 0, 3),
            "retention": scheduler.retention_label(self._thermal_model.get("idle_loss_coeff")) if self._thermal_model.get("reliable") else "sin datos todavía",
            "outdoor_now": self._outdoor_now,
            # Previsión exterior tal cual la usa el motor para anticipar
            # (ver scheduler.py, ANTICIPATE_LOOKAHEAD_HOURS) — hora a hora
            # empezando por la actual. Visible para poder comprobar que de
            # verdad hay una previsión real entrando (no plana/constante):
            # sin weather_entity configurada, y sin sensor exterior propio
            # con histórico, se degrada a un valor constante — ver punto 8
            # de DOCS.md.
            "outdoor_forecast": [round(t, 1) for t in self._outdoor_forecast] if self._outdoor_forecast else [],
            # Desviacion AHORA MISMO entre el sensor propio de cada climate.*
            # delegado y el sensor externo de la zona (positiva = el
            # delegado lee mas caliente que el sensor externo) — ver
            # `_compensate_delegate_target`. Vacio si no hay delegados, o si
            # ninguno reporta su propia current_temperature.
            "delegate_temperature_deviations": dict(self._delegate_deviations),
            # Aprendido en vivo (ver _check_delegate_overshoot): que
            # climate.* delegados NO se autorregulan solos al llegar a su
            # consigna y por eso se apagan de verdad, en vez de mantenerlos
            # en su ultimo modo activo. Persiste tras reinicios.
            "delegate_needs_explicit_off": sorted(self._delegate_needs_explicit_off),
            # Sensor externo sin lectura NUEVA de verdad (aunque su
            # `state` siga pareciendo valido) — ver `_read_current_temp`.
            "sensor_stale": self._sensor_stale,
            # Deteccion de ventana SIN sensor dedicado, si esta activada
            # (ver CONF_AUTO_WINDOW_DETECTION) — pendiente actual del
            # sensor exterior en °C/h.
            "window_slope_deg_h": self._window_detector.slope_deg_h,
            # Posible fallo del equipo (ver _check_equipment_failure) —
            # solo informativo, nunca cambia la decision por su cuenta.
            "equipment_failure_suspected": self._equipment_failure_suspected,
            # Potencia total de la zona ahora mismo (W) — suma por
            # actuador ACTIVO, cada uno por su propia fuente (medida >
            # aprendida > estimada, ver CONF_ACTUATOR_POWER/`_zone_power_w`).
            # `zone_power_breakdown` detalla cada actuador que aporta algo.
            "zone_power_w": zone_power_w,
            "zone_power_breakdown": zone_power_breakdown,
            # Marcador para que Battery Orchestrator, si esta instalado,
            # encuentre esta zona SOLO (ver climate_link.py de ese addon):
            # no es una propiedad de esta zona en si, solo un "aqui estoy"
            # discreto — no cambia nada en como funciona esta integracion
            # por si sola.
            "climate_orchestrator_zone": True,
            # Ultima señal leida de Battery Orchestrator (ver
            # grid_signal.py) — None/None si no esta instalado. Solo
            # diagnostico: la decision real ya se tomo con esto mismo en
            # `_async_decide_and_act`, esto es para poder comprobarlo.
            # "forecast" se excluye a proposito: es una lista de hasta 48
            # elementos que cambia en cada publicacion de Battery
            # Orchestrator, y grabarla entera como atributo de CADA zona
            # en CADA ciclo en el recorder seria el mismo derroche que se
            # corrigio alli (ver grid_signal.py) — aqui solo se consume en
            # memoria para la decision (`_price_anticipation_preheat`),
            # nunca se persiste de mas.
            **{f"grid_{k}": v for k, v in grid_signal.read(self.hass).items() if k != "forecast"},
            # TPI (ver scheduler.py `tpi_on_percent`): % del ciclo que los
            # switches del lado activo deben estar encendidos ahora mismo.
            # None si ese lado no esta activo (switches apagados sin mas).
            "tpi_heat_on_percent": self._last_heat_on_percent,
            "tpi_cool_on_percent": self._last_cool_on_percent,
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
        "fan_only", esta zona los detecta igual (ver `_PASSTHROUGH_MODES`);
        un radiador que solo declare "off"/"heat" no aporta nada mas que
        eso."""
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
        if self.zone.get(CONF_HUMIDIFIER_ENTITIES):
            # A diferencia de dry/fan_only, humidificar no es un hvac_mode
            # — es una funcion PARALELA (ClimateEntityFeature.TARGET_
            # HUMIDITY) que convive con cualquier hvac_mode activo, ver
            # `_refresh_hvac_modes`/`_drive_humidifiers`. Basta con que
            # haya al menos un humidifier.* declarado, no hace falta
            # comprobar nada suyo en vivo (un humidifier.* siempre sabe
            # humidificar, es lo unico que hace).
            capability.add("humidify")
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

        Ademas, si algun climate.* delegado declara TAMBIEN "dry" o
        "fan_only" en sus PROPIOS hvac_modes, esta zona los añade tal
        cual, elegibles a mano igual que cualquier otro modo — ver
        `_PASSTHROUGH_MODES` y la rama correspondiente en
        `_async_decide_and_act`. Distinto es el reposo inteligente (ver
        `_smart_idle_action`): ese los usa AUTOMATICAMENTE sin pasar por
        aqui, sin que el hvac_mode de esta zona cambie nunca — solo
        cuando SE ELIGEN A MANO pasa el hvac_mode de la zona a
        DRY/FAN_ONLY de verdad.

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
        if "humidify" in capability:
            # Funcion NATIVA del termostato (no una entidad humidifier.*
            # aparte sin relacion): target_humidity ajustable desde la
            # misma tarjeta, igual que la temperatura — ver
            # `_drive_humidifiers`/`async_set_humidity`.
            features |= ClimateEntityFeature.TARGET_HUMIDITY
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

        # Velocidad de ventilador (ver _available_fan_modes/_pick_fan_mode
        # /async_set_fan_mode) — solo se ofrece si ALGUN climate.* delegado
        # declara de verdad sus propias fan_modes; sin eso, ni se expone
        # la feature ni el selector aparece en la tarjeta.
        fan_modes = self._available_fan_modes()
        if fan_modes:
            features |= ClimateEntityFeature.FAN_MODE
            self._attr_fan_modes = fan_modes
            self._attr_fan_mode = self._manual_fan_mode if self._manual_fan_mode in fan_modes else "auto"
        else:
            self._attr_fan_modes = None
            self._attr_fan_mode = None

        self._attr_supported_features = features
        return capability

    def _available_fan_modes(self) -> list[str]:
        """Union de las velocidades de ventilador REALES que ofrece cada
        climate.* delegado declarado (su propio atributo "fan_modes", en
        vivo — nunca inventadas ni declaradas por el usuario) + "auto"
        siempre en primer lugar (el motor decide sola, ver
        `_pick_fan_mode`). "auto" va primero porque es el comportamiento
        por defecto — quien no quiera tocar nada no tiene que buscarlo en
        medio de una lista. Devuelve [] (sin exponer el selector en
        absoluto) si ningun delegado soporta velocidades — no tiene
        sentido ofrecer un selector con la unica opcion "auto"."""
        ordered = ["auto"]
        seen = {"auto"}
        for entity_id in self.zone.get(CONF_CLIMATE_ENTITIES) or []:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            for m in state.attributes.get("fan_modes") or []:
                if m not in seen:
                    ordered.append(m)
                    seen.add(m)
        return ordered if len(ordered) > 1 else []

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Selector de velocidad de la propia tarjeta — "Auto" (el motor
        decide sola segun la urgencia de cada decision, ver
        `_pick_fan_mode`) o cualquier velocidad REAL que algun delegado
        declare (ver `_available_fan_modes`). Persistente, igual que el
        resto de ajustes manuales de esta zona (temperatura, preset) — se
        queda fijada hasta que la cambies tu mismo, restaurada tras un
        reinicio (ver async_added_to_hass). Un delegado que no soporte la
        velocidad elegida simplemente se queda con su propia velocidad
        actual (ver `_pick_fan_mode`) — nunca un error, solo esa entidad
        en concreto no tiene esa opcion."""
        self._manual_fan_mode = None if fan_mode == "auto" else fan_mode
        self._attr_fan_mode = fan_mode
        self.async_write_ha_state()
        await self._async_decide_and_act()

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

            restored_fan_mode = last_state.attributes.get("fan_mode")
            if restored_fan_mode and restored_fan_mode != "auto":
                # Se restaura el STRING tal cual, aunque el delegado que
                # la ofrecia todavia no este disponible en este instante
                # (arranque en curso) — si al final no la soporta ninguno,
                # `_pick_fan_mode` simplemente no encuentra coincidencia y
                # no se manda nada, nunca un error.
                self._manual_fan_mode = restored_fan_mode

            target_humidity = last_state.attributes.get(ATTR_HUMIDITY)
            if target_humidity is not None:
                # Consigna de humedad ajustada al vuelo desde la tarjeta
                # (async_set_humidity) — persistente, igual que la
                # temperatura; el valor del config solo siembra el arranque.
                try:
                    self._attr_target_humidity = float(target_humidity)
                except (TypeError, ValueError):
                    pass

            learned_off = last_state.attributes.get("delegate_needs_explicit_off")
            if isinstance(learned_off, (list, tuple)):
                # Restaura lo aprendido en vivo sobre que climate.*
                # delegados no se autorregulan solos (ver
                # _check_delegate_overshoot) — solo para los que sigan
                # declarados ahora mismo, por si la lista de actuadores
                # cambio mientras tanto.
                declared = set(self.zone.get(CONF_CLIMATE_ENTITIES) or [])
                self._delegate_needs_explicit_off = {e for e in learned_off if e in declared}

        watched = [e for e in [
            self.zone.get(CONF_CURRENT_TEMP_SENSOR),
            self.zone.get(CONF_OUTDOOR_TEMP_SENSOR),
            self.zone.get(CONF_HUMIDITY_SENSOR),
            *(self.zone.get(CONF_PRESENCE_ENTITIES) or []),
            *(self.zone.get(CONF_DOOR_WINDOW_ENTITIES) or []),
            *(self.zone.get(CONF_CLIMATE_ENTITIES) or []),
            *(self.zone.get(CONF_HUMIDIFIER_ENTITIES) or []),
            *(c.get("sensor") for c in (self.zone.get(CONF_ACTUATOR_POWER) or {}).values() if c.get("sensor")),
            # Señal de Battery Orchestrator (ver grid_signal.py) — si esta
            # instalado, cada cambio de tramo/precio/excedente solar
            # dispara una reevaluacion INMEDIATA en prioridad "ahorro", en
            # vez de esperar al proximo ciclo periodico. Escucharla aqui no
            # falla si la entidad no existe todavia: async_track_state_change_event
            # simplemente no dispara nunca para algo que no existe.
            grid_signal.GRID_SIGNAL_ENTITY_ID,
        ] if e]
        if watched:
            self.async_on_remove(async_track_state_change_event(self.hass, watched, self._handle_reactive_event))

        refresh_minutes = self.zone.get(CONF_FORECAST_REFRESH_MINUTES, DEFAULT_FORECAST_REFRESH_MINUTES)

        # Desfase ESTABLE por zona (mismo entry_id -> mismo desfase, tambien
        # tras un reinicio) antes de arrancar el temporizador periodico -
        # ver `_zone_stagger_seconds` para el motivo: sin esto, TODAS las
        # zonas se dan de alta casi a la vez al arrancar HA, y
        # `async_track_time_interval` no tiene jitter propio, asi que sus
        # recalculos (ya throttleados, ver MODEL_RECOMPUTE_MIN_INTERVAL_
        # SECONDS) siguen cayendo TODOS en el mismo instante para siempre.
        # Confirmado en produccion: el mismo patron de cuelgues intermitentes
        # persistia tras migrar de una RPi5 a un i7 de 8 nucleos - no era
        # falta de CPU, era contencion de bloqueos en el recorder SQLite
        # (que solo permite una escritura a la vez) por varias zonas
        # escaneando dias de historico A LA VEZ, algo que mas nucleos no
        # arreglan.
        stagger_seconds = _zone_stagger_seconds(self.entry.entry_id, refresh_minutes)

        def _start_periodic_refresh(_now) -> None:
            self.async_on_remove(
                async_track_time_interval(self.hass, self._handle_forecast_refresh, timedelta(minutes=refresh_minutes))
            )

        self.async_on_remove(async_call_later(self.hass, stagger_seconds, _start_periodic_refresh))

        # El PRIMER refresco (este de aqui) tambien hacia falta staggearlo,
        # no solo el periodico de arriba: todas las zonas se dan de alta
        # durante el mismo arranque de HA, asi que un `await` directo aqui
        # significa que TODAS disparan su primer calculo completo (el mas
        # caro — nada esta "reliable" todavia, ver `_models_settled`) casi
        # en el mismo instante, justo la ventana de mas riesgo (arranque en
        # frio, con la maquina todavia levantando el resto de integraciones).
        # Confirmado en produccion: el episodio real ocurrio ~15 min despues
        # de un reinicio, con el modelo termico recien convergido pero
        # calculado por primera vez sin ningun reparto. Reparto corto (0-60s,
        # no la ventana completa de `refresh_minutes`) para no perder
        # capacidad de respuesta real al arrancar, solo evitar la rafaga.
        startup_stagger_seconds = _zone_stagger_seconds(self.entry.entry_id, 1)
        self.async_on_remove(async_call_later(self.hass, startup_stagger_seconds, self._handle_forecast_refresh))

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
        """Lectura EN VIVO del sensor externo, suavizada con una media
        movil exponencial (EMA, ver ema.py y TEMP_EMA_HALFLIFE_SECONDS)
        para no reaccionar a un pico de ruido puntual del sensor.

        Ademas, un margen de gracia si el sensor deja de dar lecturas
        NUEVAS de verdad aunque su `state` siga pareciendo valido —
        "congelado" sin llegar a marcarse unavailable, p.ej. una bateria
        agotada. Hasta STALE_SENSOR_HARD_TIMEOUT_SECONDS se sigue
        confiando en el ultimo valor suavizado (marcado `_sensor_stale`
        para que se note en `reason`/atributos) en vez de dejar de golpe
        de proteger los limites de seguridad de la zona; pasado eso, se
        da por no disponible de verdad, igual que antes."""
        sensor = self.zone.get(CONF_CURRENT_TEMP_SENSOR)
        if not sensor:
            return None
        state = self.hass.states.get(sensor)
        now = dt_util.utcnow()

        if state is not None and state.state not in ("unknown", "unavailable"):
            try:
                raw = float(state.state)
            except (TypeError, ValueError):
                raw = None
            if raw is not None:
                self._sensor_stale = False
                return self._temp_ema.update(raw, state.last_updated or now)

        # Sin lectura valida ahora mismo: ver si todavia hay margen de
        # gracia sobre la ultima lectura suavizada.
        age = self._temp_ema.age_seconds(now)
        if age is not None and age <= STALE_SENSOR_HARD_TIMEOUT_SECONDS:
            self._sensor_stale = True
            return self._temp_ema.value
        self._sensor_stale = False  # ni siquiera queda margen que ofrecer — de verdad no disponible
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

    def _smart_idle_action(self, current_temp: float | None, heat_target: float | None, deadband: float) -> tuple[str, str | None]:
        """Reposo INTELIGENTE — sin interruptor propio: coexiste solo con
        el modo mas automatico que tenga la zona (Auto en una zona con
        calor y frio de verdad; el unico modo que le queda a una zona de
        un solo sentido, que ya es "lo mas automatico" que puede ofrecer,
        ver la llamada en `_async_decide_and_act`). No hace falta un
        toggle aparte: si la zona ya esta dejando que el motor decida sola
        entre todo lo que tiene disponible, usar tambien dry/fan_only
        cuando el delegado los soporte es la misma idea, no una nueva.

        Solo se llama cuando el motor YA decidio "idle" (dentro de margen,
        ni calor ni frio hacen falta): en vez de apagar del todo, un
        climate.* delegado que TAMBIEN sepa deshumidificar o solo ventilar
        (segun `_last_full_capability`, detectado en vivo — nunca
        declarado a mano) puede aprovecharse. Deshumidificar tiene
        prioridad sobre ventilar: responde a un problema medido (humedad
        por encima del umbral configurado — ese umbral SI es configurable,
        ver CONF_DRY_HUMIDITY_THRESHOLD), no solo a comodidad. Ninguno de
        los dos persigue nunca una temperatura ni sustituye a calor/frio
        cuando de verdad hacen falta, y ninguno cambia el hvac_mode de
        esta zona — solo la orden que recibe el delegado.

        OJO con "dry": en la inmensa mayoria de aires acondicionados
        reales NO es un modo neutro para la temperatura — el compresor
        sigue funcionando (a menos velocidad, con menos caudal de aire)
        para poder condensar humedad, y eso enfria la zona como efecto
        secundario. Si la zona esta cerca del limite INFERIOR de confort
        (su consigna de calor, cuando la tiene), deshumidificar puede
        empujarla por debajo de esa consigna y obligar a calentar justo
        despues — se paga la energia de deshumidificar Y la de corregir
        el sobreenfriamiento que acaba de causar, mas gasto que si
        simplemente se hubiera apagado. Por eso solo se permite "dry"
        cuando hay margen de sobra por encima de la consigna de calor (un
        `deadband` entero, el mismo margen que usa el resto del motor
        para "no merece la pena tocarlo todavia"); en una zona sin
        consigna de calor (solo frio) no hay ese riesgo, se permite
        siempre que la humedad lo pida."""
        if "dry" in self._last_full_capability:
            humidity = self._read_humidity_now()
            threshold = float(self.zone.get(CONF_DRY_HUMIDITY_THRESHOLD, DEFAULT_DRY_HUMIDITY_THRESHOLD))
            heat_margin_ok = (
                heat_target is None or current_temp is None or current_temp >= heat_target + deadband
            )
            if humidity is not None and humidity >= threshold:
                if heat_margin_ok:
                    return "dry", f"humedad {humidity:.0f}% ≥ {threshold:.0f}%: deshumidificando en vez de apagar"
                # Se queda sin deshumidificar (ni "dry" ni "idle" a secas
                # bloquean fan_only mas abajo) para no arriesgarse a
                # enfriar de mas cerca de la consigna de calor.
        if "fan_only" in self._last_full_capability:
            return "fan_only", "dentro de margen: ventilando en vez de apagar del todo"
        return "idle", None

    def _real_door_window_open(self) -> bool:
        for e in self.zone.get(CONF_DOOR_WINDOW_ENTITIES) or []:
            state = self.hass.states.get(e)
            if state is not None and state.state == "on":
                return True
        return False

    def _check_equipment_failure(self, action: str, current_temp: float, now) -> None:
        """Deteccion simple de posible fallo del equipo — nada de caja
        negra: si llevamos EQUIPMENT_FAILURE_DETECTION_MINUTES seguidos
        pidiendo calor/frio de verdad (no reposo, no dry/fan_only) y la
        temperatura EXTERNA apenas se ha movido en la direccion esperada,
        es sospechoso — una valvula atascada, un rele que no conmuta...
        Solo informa (log + atributo `equipment_failure_suspected`),
        nunca actua por su cuenta ni sustituye al aprendizaje de
        sobre-consigna (`_check_delegate_overshoot`, que es lo contrario:
        el equipo no se para solo)."""
        if action not in ("heat", "cool"):
            self._equipment_run = None
            self._equipment_failure_suspected = False
            return
        if self._equipment_run is None or self._equipment_run[0] != action:
            self._equipment_run = (action, now, current_temp)
            return

        run_action, start_ts, start_temp = self._equipment_run
        elapsed_min = (now - start_ts).total_seconds() / 60
        if elapsed_min < EQUIPMENT_FAILURE_DETECTION_MINUTES:
            return

        delta = current_temp - start_temp
        progressed = delta >= EQUIPMENT_FAILURE_MIN_DELTA_DEG if run_action == "heat" \
            else -delta >= EQUIPMENT_FAILURE_MIN_DELTA_DEG
        if progressed:
            # se ha movido de verdad — reinicia la ventana de vigilancia
            # desde aqui, en vez de arrastrar el punto de partida original.
            self._equipment_run = (run_action, now, current_temp)
            self._equipment_failure_suspected = False
        elif not self._equipment_failure_suspected:
            self._equipment_failure_suspected = True
            _LOGGER.warning(
                "%s: llevo %d min pidiendo %s sin que la temperatura se mueva lo esperado "
                "(%.1f°C ahora, %.1f°C al empezar) — posible fallo del equipo",
                self.zone.get("name"), int(elapsed_min), run_action, current_temp, start_temp,
            )

    def _all_declared_actuators(self) -> list[str]:
        return (
            (self.zone.get(CONF_HEAT_SWITCHES) or [])
            + (self.zone.get(CONF_COOL_SWITCHES) or [])
            + (self.zone.get(CONF_CLIMATE_ENTITIES) or [])
            + (self.zone.get(CONF_HUMIDIFIER_ENTITIES) or [])
        )

    def _climate_actuators(self) -> list[str]:
        """Igual que `_all_declared_actuators`, pero SIN el humidificador
        (CONF_HUMIDIFIER_ENTITIES) — es una entidad secundaria de la zona
        (ver const.py: "integrada en el funcionamiento normal... nunca
        sustituye a calor/frio"), no parte del calor/frio en si. Usar la
        lista completa para el consumo de la zona inflaba `zone_power_w`
        con el consumo del humidificador aunque el aire acondicionado en
        si no aportara nada todavia — tanto para la prevencion de
        sobrecarga (CONF_MAX_POWER_W, pensada para calor/frio) como para
        lo que se comparte con Battery Orchestrator (ver grid_signal.py de
        ese addon), lo que importa es solo calor/frio."""
        return (
            (self.zone.get(CONF_HEAT_SWITCHES) or [])
            + (self.zone.get(CONF_COOL_SWITCHES) or [])
            + (self.zone.get(CONF_CLIMATE_ENTITIES) or [])
        )

    def _actuator_active(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        if entity_id.startswith("climate."):
            return state.attributes.get("hvac_action") in ("heating", "cooling")
        return state.state == "on"

    def _actuator_power_w(self, entity_id: str) -> tuple[float | None, str]:
        """(vatios, fuente) de ESTE actuador CONCRETO ahora mismo — nunca
        de la zona entera, ver CONF_ACTUATOR_POWER en const.py: "measured"
        (su propio sensor declarado), "learned" (aprendido de
        CONF_HOME_POWER_SENSOR, ver power_model.py), "estimated" (valor
        fijo declarado de su ficha tecnica), o (None, "none") si no hay
        nada que ofrecer."""
        config = (self.zone.get(CONF_ACTUATOR_POWER) or {}).get(entity_id) or {}
        sensor = config.get("sensor")
        if sensor:
            state = self.hass.states.get(sensor)
            if state is not None and state.state not in ("unknown", "unavailable"):
                try:
                    return float(state.state), "measured"
                except (TypeError, ValueError):
                    pass
        learned = self._power_model.get(entity_id)
        if learned and learned.get("reliable") and learned.get("learned_power_w") is not None:
            return float(learned["learned_power_w"]), "learned"
        estimated = config.get("estimated_w")
        if estimated:
            return float(estimated), "estimated"
        return None, "none"

    def _zone_power_w(self) -> tuple[float | None, dict]:
        """Potencia TOTAL de la zona ahora mismo: suma de cada actuador de
        CALOR/FRIO (nunca el humidificador, ver `_climate_actuators`) que
        este REALMENTE activo (`_actuator_active`), cada uno por su propia
        fuente (`_actuator_power_w`) — nunca un unico numero de zona como
        antes, ver CONF_ACTUATOR_POWER. Devuelve (total, desglose) —
        desglose es {entity_id: {"watts","source"}}, solo de los que de
        verdad aportan algo. (None, {}) si nada esta activo o no hay
        ningun dato de potencia disponible."""
        breakdown: dict[str, dict] = {}
        total = 0.0
        any_known = False
        for entity_id in self._climate_actuators():
            if not self._actuator_active(entity_id):
                continue
            watts, source = self._actuator_power_w(entity_id)
            if watts is None:
                continue
            breakdown[entity_id] = {"watts": watts, "source": source}
            total += watts
            any_known = True
        return (total if any_known else None), breakdown

    def _zone_estimated_power_w(self) -> float | None:
        """A diferencia de `_zone_power_w` (solo actuadores YA activos),
        esto suma la potencia de TODOS los actuadores declarados,
        esten o no encendidos ahora mismo — "cuanto consumiria esta zona
        SI se pusiera a calentar/enfriar ya". Hace falta para el banco de
        confort (ver scheduler._opportunistic_preheat): mientras la zona
        esta en idle decidiendo si merece la pena arrancar, `_zone_power_w`
        siempre daria None (nada activo todavia). None si ningun actuador
        declarado tiene ni sensor, ni potencia aprendida fiable, ni
        estimada a mano — nunca se inventa un numero. Solo calor/frio
        (ver `_climate_actuators`), nunca el humidificador."""
        total = 0.0
        any_known = False
        for entity_id in self._climate_actuators():
            watts, _source = self._actuator_power_w(entity_id)
            if watts is None:
                continue
            total += watts
            any_known = True
        return total if any_known else None

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

        # Sensor general de consumo de la casa: el declarado a mano en
        # esta zona (CONF_HOME_POWER_SENSOR) tiene prioridad si existe;
        # sin el, se cae AUTOMATICAMENTE al que ya tiene declarado Battery
        # Orchestrator (ver grid_signal.py) — asi el aprendizaje funciona
        # solo, sin que el usuario tenga que declarar el mismo sensor dos
        # veces en dos integraciones distintas. Sin ninguno de los dos
        # (ni aqui ni Battery Orchestrator instalado), no se aprende nada
        # — nunca una estimacion inventada.
        home_power_sensor = self.zone.get(CONF_HOME_POWER_SENSOR, "") or grid_signal.read(self.hass).get("home_power_sensor") or ""
        # Consumo aprendido (ver power_model.py) — SOLO para los
        # actuadores de CALOR/FRIO (nunca el humidificador, entidad
        # secundaria — ver `_climate_actuators`) que no tengan ni sensor
        # propio ni potencia estimada declarada (CONF_ACTUATOR_POWER): para
        # esos ya no hace falta aprender nada, se usan tal cual.
        actuator_power = self.zone.get(CONF_ACTUATOR_POWER) or {}
        entities_to_learn = [
            e for e in self._climate_actuators()
            if not actuator_power.get(e, {}).get("sensor") and not actuator_power.get(e, {}).get("estimated_w")
        ]

        # Recalcular el modelo termico y el de potencia es CARO (escanean
        # dias de historico del recorder, ver MODEL_RECOMPUTE_MIN_INTERVAL_
        # SECONDS arriba para el porque) — a diferencia de la previsión
        # exterior de arriba, que SI conviene refrescar cada ciclo. Una vez
        # ya son fiables, espaciar el recalculo real a como mucho una vez
        # cada MODEL_RECOMPUTE_MIN_INTERVAL_SECONDS; mientras no lo sean
        # (zona nueva, historico insuficiente todavia) se sigue intentando
        # en cada ciclo normal para converger lo antes posible.
        now = dt_util.utcnow()
        if not self._models_settled(entities_to_learn) or self._model_last_computed_ts is None or (
            (now - self._model_last_computed_ts).total_seconds() >= MODEL_RECOMPUTE_MIN_INTERVAL_SECONDS
        ):
            self._thermal_model = await thermal_model.async_get_model(
                self.hass, self.zone, int(self.zone.get(CONF_HISTORY_DAYS_FOR_INERTIA, DEFAULT_HISTORY_DAYS_FOR_INERTIA)),
                fallback=self._thermal_model,
            )
            self._power_model = await power_model.async_get_power_model(
                self.hass, entities_to_learn, self.entry.entry_id, home_power_sensor,
                int(self.zone.get(CONF_HISTORY_DAYS_FOR_INERTIA, DEFAULT_HISTORY_DAYS_FOR_INERTIA)),
                fallback=self._power_model,
            ) if home_power_sensor and entities_to_learn else {}
            self._model_last_computed_ts = now

        await self._async_decide_and_act()

    def _models_settled(self, entities_to_learn: list[str]) -> bool:
        """True si tanto el modelo termico como el de potencia (para cada
        entidad que de verdad haga falta aprender ahora mismo) ya son
        fiables — no hace falta seguir escaneando dias de historico cada
        `forecast_refresh_minutes` mientras no haya nada nuevo que
        aprender. Si aparece una entidad nueva sin dato fiable todavia
        (p.ej. se añadio un actuador sin sensor propio), esto vuelve a
        dar False solo (su clave no estara en `self._power_model` con
        "reliable": True), forzando un recalculo real."""
        if not self._thermal_model.get("reliable"):
            return False
        return all(self._power_model.get(e, {}).get("reliable") for e in entities_to_learn)

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
            self._maybe_write_ha_state()
            return

        current_temp = self._read_current_temp()
        self._attr_current_temperature = current_temp
        humidity = self._read_humidity_now()
        self._attr_current_humidity = round(humidity) if humidity is not None else None
        if current_temp is None:
            self._attr_available = False
            self._maybe_write_ha_state()
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

        # Deteccion de ventana SIN sensor dedicado (respaldo opcional, ver
        # CONF_AUTO_WINDOW_DETECTION/window_algorithm.py) — se actualiza
        # cada ciclo con la lectura EXTERNA actual, para saber cuando la
        # zona pide calor/frio de verdad (sin eso no puede distinguir una
        # bajada normal de una anomala en contra de lo pedido).
        window_alert = False
        if self.zone.get(CONF_AUTO_WINDOW_DETECTION):
            window_alert = self._window_detector.update(current_temp, dt_util.utcnow(), wants_heat, wants_cool)

        real_door_open = self._real_door_window_open()

        # Urgencia de la decision — SOLO para elegir la velocidad de
        # ventilador en los delegados que la soporten (ver `_pick_fan_mode`
        # mas abajo), nunca cambia nada mas: "urgente" es el tramo de
        # seguridad de `scheduler.decide_action` (por debajo/encima de
        # min_temp/max_temp), donde interesa la maxima potencia de
        # ventilador para recuperar cuanto antes; el resto (reactivo
        # normal, anticipacion, banco de confort por sol/precio) es
        # "tranquilo" — no hay prisa real, tiene sentido una velocidad
        # baja/silenciosa o "auto" si el equipo la ofrece.
        urgent = False

        force_off = False
        if self._attr_hvac_mode == HVACMode.OFF:
            action = "idle"
            heat_target, cool_target = preset_heat, preset_cool
            self._reason = "apagado desde el termostato"
        elif real_door_open or window_alert:
            action = "idle"
            heat_target, cool_target = preset_heat, preset_cool
            self._reason = "puerta/ventana abierta: en pausa" if real_door_open else (
                f"posible ventana abierta (pendiente {self._window_detector.slope_deg_h:.1f}°C/h en contra "
                "de lo pedido, sin sensor dedicado): en pausa"
            )
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
            # Elegido A MANO desde el termostato (dry/fan_only) — el
            # hvac_mode de la zona SI pasa a mostrar esto de verdad,
            # distinto del reposo inteligente automatico (mas abajo), que
            # nunca toca el hvac_mode. No persigue ninguna temperatura, se
            # relega tal cual a quien de verdad lo soporte en
            # `_async_execute`/`_drive_climate_actuator`.
            action = _PASSTHROUGH_MODES[self._attr_hvac_mode]
            heat_target = cool_target = None
            self._reason = f"modo {action} fijado a mano desde el termostato"
        else:
            heat_target, cool_target = preset_heat, preset_cool
            grid = grid_signal.read(self.hass)
            action, decide_reason = scheduler.decide_action(
                current_temp=current_temp, heat_target=heat_target, cool_target=cool_target,
                priority=self.zone.get(CONF_PRIORITY, "confort"), deadband=deadband,
                min_temp=min_temp, max_temp=max_temp,
                outdoor_now=self._outdoor_now, outdoor_forecast=self._outdoor_forecast,
                heating_rate_deg_h=self._thermal_model.get("heating_rate_deg_h", 0.0),
                cooling_rate_deg_h=self._thermal_model.get("cooling_rate_deg_h", 0.0),
                idle_loss_coeff=self._thermal_model.get("idle_loss_coeff", 0.0),
                grid_tier=grid["tier"], solar_surplus_now_w=grid["solar_surplus_now_w"],
                zone_estimated_power_w=self._zone_estimated_power_w(),
                grid_forecast=grid["forecast"],
            )
            self._reason = f"{preset_reason} — {decide_reason}"
            urgent = "de seguridad de la zona" in decide_reason
            if action == "idle" and self._attr_hvac_mode == self._default_hvac_mode(self._last_full_capability):
                # Ya esta dentro de margen: en vez de apagar sin mas, ver
                # si el reposo inteligente tiene algo mejor que hacer con
                # lo que de verdad hay conectado. Solo coexiste con el modo
                # mas automatico de la zona (Auto si tiene calor y frio de
                # verdad; el unico modo que le queda a una de un solo
                # sentido) — si el usuario bloqueo la zona a mano a "solo
                # calor" en una zona que tambien tiene frio, esta claro que
                # quiere control manual, no que el motor decida nada mas
                # por su cuenta.
                smart_action, smart_reason = self._smart_idle_action(current_temp, heat_target, deadband)
                if smart_reason:
                    action = smart_action
                    self._reason += f" — {smart_reason}"

        if self._sensor_stale:
            self._reason += " — aviso: sensor externo sin lectura nueva, usando la última suavizada"

        # Deteccion de posible fallo del equipo — solo informa (log +
        # atributo), nunca cambia `action`.
        self._check_equipment_failure(action, current_temp, dt_util.utcnow())

        # Prevencion simple de sobrecarga (ver CONF_MAX_POWER_W): si la
        # zona YA esta al limite (o por encima) de potencia configurada y
        # esto seria un arranque NUEVO (no estaba ya calentando/enfriando
        # el ciclo anterior), se pospone — lo que ya estuviera encendido
        # no se corta por esto, solo se evita sumar mas.
        max_power = self.zone.get(CONF_MAX_POWER_W) or 0
        if action in ("heat", "cool") and max_power > 0:
            already_active = self._attr_hvac_action in (HVACAction.HEATING, HVACAction.COOLING)
            zone_power, _breakdown = self._zone_power_w()
            if not already_active and zone_power is not None and zone_power >= float(max_power):
                self._reason += f" — pospuesto: potencia actual {zone_power:.0f}W ≥ máximo {float(max_power):.0f}W"
                action = "idle"

        # Solo el tramo normal (Auto/calor/frio decidiendo solo, NUNCA
        # apagado a mano ni puerta/ventana ni un modo dry/fan_only fijado
        # a mano) puede dejar un climate.* delegado "en reposo mantenido"
        # en vez de apagarlo de verdad al llegar a la consigna — ver
        # `_drive_climate_idle`. Los demas casos siguen siendo apagado
        # real, sin ambiguedad.
        climate_idle_keep = action == "idle" and not force_off and self._attr_hvac_mode not in (HVACMode.OFF, *_PASSTHROUGH_MODES)

        # TPI (ver scheduler.py `tpi_on_percent`): solo se calcula para el
        # lado que de verdad esta activo ahora mismo — los switches del
        # otro lado, o si estamos en idle/off, se apagan sin mas (ver
        # `_tpi_desired_on`). Solo afecta a switches, nunca a climate.*
        # delegados (ya tienen su propio control interno).
        heat_on_percent = scheduler.tpi_on_percent(current_temp, heat_target, self._outdoor_now, heating=True) \
            if action == "heat" and heat_target is not None else None
        cool_on_percent = scheduler.tpi_on_percent(current_temp, cool_target, self._outdoor_now, heating=False) \
            if action == "cool" and cool_target is not None else None
        tpi_cycle_minutes = float(self.zone.get(CONF_TPI_CYCLE_MINUTES, DEFAULT_TPI_CYCLE_MINUTES))
        self._last_heat_on_percent, self._last_cool_on_percent = heat_on_percent, cool_on_percent

        self._update_target_attrs(heat_target, cool_target)
        target_for_actuator = heat_target if action == "heat" else cool_target if action == "cool" else (heat_target or cool_target)
        real_action = await self._async_execute(
            action, target_for_actuator, capability, current_temp, deadband, climate_idle_keep, force_off=force_off,
            heat_on_percent=heat_on_percent, cool_on_percent=cool_on_percent, tpi_cycle_minutes=tpi_cycle_minutes,
            urgent=urgent,
        )
        # HVACAction.OFF (apagado de verdad, el modo elegido es "apagado")
        # es DISTINTO de HVACAction.IDLE (encendida, dentro de margen, sin
        # nada que hacer ahora mismo) — HA y cualquier puente Matter/
        # HomeKit distinguen los dos; confundirlos hace que un termostato
        # "apagado" se vea como "en espera" en la UI.
        self._attr_hvac_action = HVACAction.OFF if self._attr_hvac_mode == HVACMode.OFF \
            else _ACTION_MAP.get(real_action, HVACAction.IDLE)

        # Humidificacion: funcion PARALELA a calor/frio/dry/fan_only, no un
        # hvac_mode mas — activa siempre que la zona no este apagada ni en
        # pausa por puerta/ventana, sea cual sea el hvac_mode concreto ("
        # integrada en el funcionamiento automatico", ver CONF_HUMIDIFIER_
        # ENTITIES en const.py).
        humidify_active = self._attr_hvac_mode != HVACMode.OFF and not force_off
        await self._drive_humidifiers(humidify_active)

        self._maybe_write_ha_state()

    def _maybe_write_ha_state(self) -> None:
        """Ver WRITE_MIN_INTERVAL_SECONDS arriba para el motivo: escribe
        AL INSTANTE si algo que de verdad importa cambio desde la ultima
        escritura (disponibilidad, accion real, modo, o el motivo en
        texto — que ya cambia solo cuando cambia algo real, ver `_reason`
        en todo `_async_decide_and_act`); si lo unico que se movio es el
        jitter numerico de la EMA de temperatura (o similar), se retrasa
        como mucho WRITE_MIN_INTERVAL_SECONDS. Nunca deja de escribir del
        todo — solo agrupa las escrituras que no aportan nada nuevo que
        alguien mirando el dashboard o una automatizacion fuera a notar."""
        signature = (self._attr_available, self._attr_hvac_action, self._attr_hvac_mode, self._reason)
        now = dt_util.utcnow()
        significant_change = signature != self._last_written_signature
        elapsed_enough = (
            self._last_state_write_ts is None
            or (now - self._last_state_write_ts).total_seconds() >= WRITE_MIN_INTERVAL_SECONDS
        )
        if significant_change or elapsed_enough:
            self.async_write_ha_state()
            self._last_state_write_ts = now
            self._last_written_signature = signature

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

    async def _async_execute(
        self, action: str, target_temp: float | None, capability: str, current_temp: float | None,
        deadband: float, climate_idle_keep: bool, force_off: bool = False,
        heat_on_percent: float | None = None, cool_on_percent: float | None = None, tpi_cycle_minutes: float = DEFAULT_TPI_CYCLE_MINUTES,
        urgent: bool = False,
    ) -> str:
        """Ejecuta la decision sobre TODOS los actuadores declarados —
        tantos como se quiera de cada tipo (ver const.py). `action` ya
        viene decidido como uno solo ("heat"/"cool"/"dry"/"fan_only"/
        "idle") por scheduler.py, el reposo inteligente opcional, o el
        modo fijado a mano, asi que nunca se manda mas de una cosa a la
        vez. `current_temp` es la lectura del sensor EXTERNO de la zona
        (el que de verdad gobierna la decision) — se pasa a los climate.*
        delegados para compensar la desviacion frente a su propio sensor
        interno, ver `_compensate_delegate_target`.

        `force_off`: salta el anti-ciclado de los switches (ver
        `_drive_switch`) — solo lo usa el aviso de puerta/ventana abierta,
        que es urgente de verdad; un "idle" normal (dentro de margen) sigue
        respetando el tiempo minimo encendido.

        `climate_idle_keep`: solo cuando la zona llega a su consigna
        decidiendo SOLA en Auto/calor/frio (nunca apagado a mano, puerta/
        ventana, o un modo dry/fan_only fijado a mano) — cada climate.*
        delegado pasa por `_drive_climate_idle` en vez del apagado directo,
        ver ahi el criterio real (mantener vs apagar, aprendido).

        `heat_on_percent`/`cool_on_percent`: TPI (ver scheduler.py
        `tpi_on_percent`) — el % del ciclo (`tpi_cycle_minutes`) que un
        switch de ese lado debe estar encendido, en vez de un simple
        on/off. None cuando ese lado no esta activo ahora mismo (los
        switches de ese lado se apagan sin mas). Solo afecta a switches,
        nunca a climate.* delegados (ya tienen su propio control interno).

        Los switches SOLO entienden calor/frio (un switch no tiene forma
        de ventilar o deshumidificar) — con "dry"/"fan_only" simplemente
        se apagan. Cada climate.* delegado se gobierna por SUS PROPIOS
        `hvac_modes` (consultados en vivo, ver `_drive_climate_actuator`)
        — recibe una unica orden con el modo que toque cada vez; el que no
        lo soporte se ignora sin mas.

        `urgent`: solo afecta a la VELOCIDAD DE VENTILADOR de los climate.*
        delegados que la soporten (ver `_pick_fan_mode`) — True en el tramo
        de seguridad de scheduler.py (por debajo/encima de min_temp/
        max_temp), False en cualquier otro caso (reactivo normal,
        anticipacion, banco de confort). Nunca cambia switches ni el
        hvac_mode en si.

        Devuelve la accion REAL resultante — en modo switch puede no
        coincidir con `action` si el anti-ciclado todavia no deja cambiar
        de estado."""
        simulate = bool(self.zone.get(CONF_SIMULATE, True))
        real_heat = real_cool = False
        real_other: str | None = None
        target_temp = target_temp if target_temp is not None else self._attr_current_temperature or 20.0
        now = dt_util.utcnow()

        if capability in ("heat", "heat_cool"):
            if heat_on_percent is not None:
                desired_heat_on = self._tpi_desired_on("heat", heat_on_percent, tpi_cycle_minutes, now)
            else:
                desired_heat_on = False
                self._tpi_cycle_start.pop("heat", None)  # sin demanda: el proximo ciclo empieza limpio, en fase "encendido"
            # Si el frio esta activo DE VERDAD ahora mismo (Auto cambiando
            # de lado), apagar el calor es tan urgente como el aviso de
            # puerta/ventana — salta el anti-ciclado igual que el (ver
            # `force_off`). Sin esto, un switch de calor que se acaba de
            # encender hace menos de CONF_MIN_ON_SECONDS se queda
            # "atascado" encendido por el anti-ciclado mientras el de frio
            # arranca en paralelo: calentando y enfriando la misma zona a
            # la vez, el peor derroche posible. Nunca fuerza el ENCENDIDO,
            # solo el apagado del lado que ya no toca — el lado nuevo sigue
            # respetando su propio anti-ciclado al arrancar.
            heat_force = force_off or (not desired_heat_on and action == "cool")
            for sw in self.zone.get(CONF_HEAT_SWITCHES) or []:
                if await self._drive_switch(sw, desired_heat_on, simulate, force=heat_force):
                    real_heat = True

        if capability in ("cool", "heat_cool"):
            if cool_on_percent is not None:
                desired_cool_on = self._tpi_desired_on("cool", cool_on_percent, tpi_cycle_minutes, now)
            else:
                desired_cool_on = False
                self._tpi_cycle_start.pop("cool", None)
            # Mismo razonamiento que `heat_force` arriba, en sentido
            # contrario (calor tomando el relevo del frio).
            cool_force = force_off or (not desired_cool_on and action == "heat")
            for sw in self.zone.get(CONF_COOL_SWITCHES) or []:
                if await self._drive_switch(sw, desired_cool_on, simulate, force=cool_force):
                    real_cool = True

        for entity_id in self.zone.get(CONF_CLIMATE_ENTITIES) or []:
            if climate_idle_keep:
                result = await self._drive_climate_idle(entity_id, current_temp, deadband, simulate)
            else:
                result = await self._drive_climate_actuator(entity_id, action, target_temp, current_temp, simulate, urgent)
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

    async def _drive_climate_actuator(
        self, entity_id: str, action: str, target_temp: float, current_temp: float | None, simulate: bool,
        urgent: bool = False,
    ) -> str:
        """Consulta los `hvac_modes` NATIVOS de este climate.* delegado
        (nunca una declaracion nuestra) para saber si puede hacer lo que
        hace falta ahora ("heat"/"cool"/"dry"/"fan_only"). Si puede, se le
        manda ese modo — con temperatura solo para heat/cool, corregida
        segun la desviacion medida frente al sensor externo de la zona
        (ver `_compensate_delegate_target`), ya que dry/fan_only no
        persiguen ninguna consigna —; si no, se le manda "off" (si lo
        soporta) y se deja en paz — asi un equipo con varias capacidades
        se activa solo en la que corresponda sin ninguna deteccion
        especial, y el que no soporte el modo que toca se ignora sin
        mas."""
        state = self.hass.states.get(entity_id)
        supported = list((state.attributes.get("hvac_modes") if state else None) or [])
        can_do = action in ("heat", "cool", "dry", "fan_only") and action in supported

        if can_do:
            if action in ("heat", "cool"):
                # Se recuerda la consigna REAL (sin corregir) que se
                # persigue de verdad — es lo que usa `_drive_climate_idle`
                # mas tarde para mantener este delegado en su ultimo modo
                # activo al llegar a la consigna, y para detectar si se
                # desvia (ver `_check_delegate_overshoot`). Se guarda
                # SIEMPRE, incluso en simulacion, para que el reposo
                # aprendido tambien se pueda probar sin tocar equipos
                # reales.
                self._delegate_last_active[entity_id] = (action, target_temp)
                self._delegate_overshoot_strikes[entity_id] = 0
            if not simulate:
                # No mandar el modo si el delegado YA esta en ese modo —
                # se compara contra su propio `state.state` (el hvac_mode
                # vivo), nunca una cache nuestra: asi tambien se entera si
                # alguien lo cambio por fuera (app del fabricante, mando
                # fisico) y no repite una orden que no hace falta.
                if state is None or state.state != action:
                    await self.hass.services.async_call(
                        "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": action}, blocking=False)
                if action in ("heat", "cool"):
                    compensated = self._compensate_delegate_target(entity_id, state, target_temp, current_temp)
                    # Mismo criterio para la temperatura: solo se manda si
                    # de verdad difiere de la que el propio delegado ya
                    # tiene puesta (ver TEMP_SEND_TOLERANCE_DEG) — sin
                    # esto, `set_temperature` se repetia en CADA ciclo
                    # aunque nada hubiera cambiado, confirmado como
                    # trafico real e innecesario hacia el dispositivo.
                    current_target = _safe_float(state.attributes.get("temperature")) if state else None
                    if current_target is None or abs(current_target - compensated) > TEMP_SEND_TOLERANCE_DEG:
                        await self.hass.services.async_call(
                            "climate", "set_temperature", {"entity_id": entity_id, "temperature": compensated}, blocking=False)
                    await self._drive_delegate_fan_mode(entity_id, state, urgent)
            elif action in ("heat", "cool"):
                # En simulacion no se manda nada real, pero se calcula y
                # guarda igual la desviacion — asi el atributo de
                # diagnostico es fiable desde el primer dia, sin esperar a
                # desactivar el modo simulacion para verla por primera vez.
                self._compensate_delegate_target(entity_id, state, target_temp, current_temp)
            return action

        if not simulate and "off" in supported and state is not None and state.state != "off":
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "off"}, blocking=False)
        return "idle"

    async def _drive_delegate_fan_mode(self, entity_id: str, state, urgent: bool) -> None:
        """Manda `set_fan_mode` a ESTE delegado si de verdad hace falta
        cambiarlo — ver `_pick_fan_mode` (elección a mano en
        `self._manual_fan_mode` o automática por urgencia) y
        FAN_MODE_URGENT_KEYWORDS/FAN_MODE_GENTLE_KEYWORDS. Nunca se llama
        para un delegado sin `fan_modes` propias (la lista sale vacía y
        `_pick_fan_mode` devuelve None sin más). No repite la orden si el
        delegado ya está en la velocidad elegida — mismo criterio que el
        resto de `_drive_climate_actuator`."""
        if state is None:
            return
        fan_modes = list(state.attributes.get("fan_modes") or [])
        desired_fan = _pick_fan_mode(fan_modes, urgent, self._manual_fan_mode)
        if desired_fan and desired_fan != state.attributes.get("fan_mode"):
            await self.hass.services.async_call(
                "climate", "set_fan_mode", {"entity_id": entity_id, "fan_mode": desired_fan}, blocking=False)

    async def _drive_climate_idle(self, entity_id: str, current_temp: float | None, deadband: float, simulate: bool) -> str:
        """Que hacer con ESTE climate.* delegado en concreto al llegar a
        la consigna decidiendo solo en Auto/calor/frio (ver
        `climate_idle_keep` en `_async_decide_and_act`) — no todos se
        tratan igual:

        Por defecto se MANTIENE en su ultimo modo activo (heat/cool, ver
        `_delegate_last_active`) con la consigna SIEMPRE corregida en vivo
        (`_compensate_delegate_target`, reactiva a cada cambio real de
        sensor — nunca por sondeo) — asi el propio delegado se autorregula
        con su logica interna, sin ciclos de encendido/apagado de mas, y
        con precision gracias a la correccion continua.

        Pero eso asume que el delegado de verdad SABE pararse solo. Si no
        es asi — un equipo sin histéresis interna real seguiria
        calentando/enfriando de mas aunque ya deberia estar satisfecho —
        se aprende en vivo (ver `_check_delegate_overshoot`, nada de caja
        negra: un contador de veces que el sensor EXTERNO sigue
        desviandose en la direccion equivocada mientras se mantiene
        encendido) y, a partir de ahi, ese delegado en concreto se apaga
        de verdad cada vez que llegue a su consigna — persistido tras
        reinicios (ver `delegate_needs_explicit_off` en
        extra_state_attributes / async_added_to_hass)."""
        state = self.hass.states.get(entity_id)
        supported = list((state.attributes.get("hvac_modes") if state else None) or [])
        last = self._delegate_last_active.get(entity_id)

        if entity_id not in self._delegate_needs_explicit_off and last is not None:
            last_mode, last_target = last
            if last_mode in supported:
                self._check_delegate_overshoot(entity_id, last_mode, last_target, current_temp, deadband)

        if entity_id in self._delegate_needs_explicit_off or last is None or last[0] not in supported:
            if not simulate and "off" in supported and state is not None and state.state != "off":
                await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "off"}, blocking=False)
            return "idle"

        last_mode, last_target = last
        if not simulate:
            compensated = self._compensate_delegate_target(entity_id, state, last_target, current_temp)
            if state is None or state.state != last_mode:
                await self.hass.services.async_call(
                    "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": last_mode}, blocking=False)
            # Mismo criterio que en `_drive_climate_actuator` (ver
            # TEMP_SEND_TOLERANCE_DEG): no repetir `set_temperature` si el
            # delegado ya tiene puesta, en la practica, la misma consigna.
            current_target = _safe_float(state.attributes.get("temperature")) if state else None
            if current_target is None or abs(current_target - compensated) > TEMP_SEND_TOLERANCE_DEG:
                await self.hass.services.async_call(
                    "climate", "set_temperature", {"entity_id": entity_id, "temperature": compensated}, blocking=False)
            # En reposo mantenido nunca hay prisa (ya esta satisfecha, ver
            # docstring) — urgent=False siempre aqui, pero SI respeta una
            # velocidad elegida a mano (`self._manual_fan_mode`).
            await self._drive_delegate_fan_mode(entity_id, state, urgent=False)
        else:
            self._compensate_delegate_target(entity_id, state, last_target, current_temp)
        return "idle"

    def _check_delegate_overshoot(
        self, entity_id: str, last_mode: str, last_target: float, current_temp: float | None, deadband: float
    ) -> None:
        """Aprendizaje simple y explicable — nada de caja negra ni de
        modelo entrenado: mientras se MANTIENE este delegado en su ultimo
        modo activo sin apagarlo (ver `_drive_climate_idle`), si el sensor
        EXTERNO (el que de verdad gobierna la zona) sigue moviendose en la
        direccion EQUIVOCADA mas alla de la histéresis normal —
        sobre-temperatura en calor, infra-temperatura en frio—, es que
        este equipo en concreto no sabe pararse solo (no tiene, o esta
        integracion no ve, su propia histéresis interna). Un par de veces
        seguidas (no un pico puntual del sensor) y se aprende a apagarlo
        de verdad de aqui en adelante — se persiste tras reinicios."""
        if current_temp is None:
            return
        overshoot = (
            (last_mode == "heat" and current_temp > last_target + deadband) or
            (last_mode == "cool" and current_temp < last_target - deadband)
        )
        if not overshoot:
            self._delegate_overshoot_strikes[entity_id] = 0
            return
        strikes = self._delegate_overshoot_strikes.get(entity_id, 0) + 1
        self._delegate_overshoot_strikes[entity_id] = strikes
        if strikes >= OVERSHOOT_STRIKES_THRESHOLD:
            self._delegate_needs_explicit_off.add(entity_id)
            _LOGGER.info(
                "%s: %s se mantenia encendido mas alla de su consigna repetidas veces — "
                "a partir de ahora se apaga de verdad al llegar a la consigna",
                self.zone.get("name"), entity_id,
            )

    def _compensate_delegate_target(
        self, entity_id: str, state, target_temp: float, current_temp: float | None
    ) -> float:
        """El climate.* delegado decide EL SOLO cuando se da por
        satisfecho, segun SU PROPIO sensor interno — que casi nunca
        coincide exactamente con el sensor externo configurado para la
        zona (el de un aire acondicionado, por ubicacion/calibracion,
        suele leer distinto que un sensor de pared). Si se le mandara la
        consigna real tal cual, el delegado podria darse por satisfecho
        antes o despues de que el sensor externo — el que de verdad
        gobierna esta zona — llegue a esa temperatura.

        Se corrige sumando a la consigna la desviacion medida AHORA MISMO
        entre los dos sensores, recalculada cada ciclo (no es constante:
        varia con la propia calefaccion/refrigeracion en marcha) y
        recortada al rango real que admite el delegado (`min_temp`/
        `max_temp` propios, si los declara) para no mandarle nunca algo
        fuera de lo que acepta. Sin desviacion detectable — el delegado no
        reporta su propia `current_temperature`, o el sensor externo no
        esta disponible ahora mismo — se manda la consigna real sin
        tocar, nunca se inventa una correccion. La desviacion calculada
        queda visible en el atributo `delegate_temperature_deviations`
        para poder revisarla."""
        delegate_temp = state.attributes.get("current_temperature") if state else None
        if delegate_temp is None or current_temp is None:
            self._delegate_deviations.pop(entity_id, None)
            return target_temp
        try:
            deviation = float(delegate_temp) - float(current_temp)
        except (TypeError, ValueError):
            self._delegate_deviations.pop(entity_id, None)
            return target_temp

        self._delegate_deviations[entity_id] = round(deviation, 2)
        compensated = target_temp + deviation
        min_t = state.attributes.get("min_temp")
        max_t = state.attributes.get("max_temp")
        try:
            if min_t is not None:
                compensated = max(compensated, float(min_t))
            if max_t is not None:
                compensated = min(compensated, float(max_t))
        except (TypeError, ValueError):
            pass
        return compensated

    def _tpi_desired_on(self, side: str, on_percent: float, cycle_minutes: float, now) -> bool:
        """TPI: dentro de cada ciclo de `cycle_minutes`, el switch de este
        lado ("heat"/"cool") esta encendido durante el primer `on_percent`
        (0..1) del ciclo, apagado el resto — un patron simple "encendido
        al principio, apagado al final", el mismo esquema clasico de
        control proporcional por tiempo. `_drive_switch` sigue aplicando
        su propio anti-ciclado por debajo (CONF_MIN_ON_SECONDS/
        CONF_MIN_OFF_SECONDS), asi que un ciclo TPI muy corto frente a esos
        minimos simplemente se queda limitado por ellos, nunca al reves."""
        cycle_seconds = max(60.0, cycle_minutes * 60)
        start = self._tpi_cycle_start.get(side)
        if start is None or (now - start).total_seconds() >= cycle_seconds:
            start = now
            self._tpi_cycle_start[side] = start
        elapsed = (now - start).total_seconds()
        return elapsed < on_percent * cycle_seconds

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

    async def _drive_humidifiers(self, active: bool) -> None:
        """Cada humidifier.* delegado (ver CONF_HUMIDIFIER_ENTITIES) se
        gobierna igual que un climate.* dejado "en reposo mantenido" (ver
        `_drive_climate_idle`, mismo espiritu): se enciende con la
        consigna de la zona (`_attr_target_humidity`) y se deja que su
        propia logica interna decida cuando humidificar de verdad — no
        hace falta reimplementar aqui la histeresis, el propio
        humidifier.* ya sabe pararse solo al llegar a su consigna, igual
        que cualquier humidificador domestico normal. Se apaga solo
        cuando la zona esta genuinamente apagada o en pausa por puerta/
        ventana (`active=False`, ver `_async_decide_and_act`) — nunca por
        una decision de calor/frio, es una funcion paralela."""
        simulate = bool(self.zone.get(CONF_SIMULATE, True))
        for entity_id in self.zone.get(CONF_HUMIDIFIER_ENTITIES) or []:
            state = self.hass.states.get(entity_id)
            if simulate:
                continue
            if active:
                if state is None or state.state != "on":
                    await self.hass.services.async_call("humidifier", "turn_on", {"entity_id": entity_id}, blocking=False)
                # Mismo criterio que en climate.* (ver TEMP_SEND_TOLERANCE_
                # DEG/HUMIDITY_SEND_TOLERANCE_PCT arriba): no repetir
                # `set_humidity` si el humidificador ya tiene puesta esa
                # consigna.
                current_humidity = _safe_float(state.attributes.get("humidity")) if state else None
                if current_humidity is None or abs(current_humidity - self._attr_target_humidity) >= HUMIDITY_SEND_TOLERANCE_PCT:
                    await self.hass.services.async_call(
                        "humidifier", "set_humidity", {"entity_id": entity_id, "humidity": self._attr_target_humidity}, blocking=False)
            elif state is not None and state.state != "off":
                await self.hass.services.async_call("humidifier", "turn_off", {"entity_id": entity_id}, blocking=False)

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

    async def async_set_humidity(self, humidity: int) -> None:
        """Consigna de humedad UNICA por zona (no por preset, a diferencia
        de la temperatura) — ajustarla aqui es tan persistente como
        cambiarla en "Configurar", restaurada tras un reinicio igual que
        el resto."""
        self._attr_target_humidity = float(humidity)
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
