"""Constantes de Climate Orchestrator.

Una entrada de configuracion (ConfigEntry) = UNA zona. Se añaden tantas
zonas como habitaciones se quieran gestionar repitiendo "+ Añadir
integración" — mismo patrón que versatile_thermostat, y el que HA
recomienda para integraciones con varias instancias independientes.
"""

from __future__ import annotations

DOMAIN = "climate_orchestrator"

# --------------------------------------------------------------- claves ----

# NADA de "actuator_mode" ni de declarar capacidad de calor/frio a mano:
# se listan los actuadores que de verdad tiene la zona, y todo lo demas
# (que puede hacer cada uno, que hvac_modes expone la entidad final)
# se DEDUCE de ahi en climate.py — nunca una redundancia que el usuario
# tenga que mantener sincronizada el mismo a mano.
#
#   - "climate_entities": lista de climate.* YA EXISTENTES en los que
#     delegar (una valvula termostatica, un aire acondicionado con su
#     propia electronica...), tantos como se quiera. Cada uno se gobierna
#     por SUS PROPIOS hvac_modes nativos (leidos en vivo del propio
#     climate.*, no declarados aqui) — si soporta "heat", se activa en
#     "heat" cuando toca calentar; si soporta "cool", en "cool" cuando
#     toca enfriar; si soporta los dos (un equipo reversible de verdad),
#     se le manda el que corresponda cada vez, una unica orden, nunca dos
#     que se pisen. Un radiador con valvula termostatica y un aire
#     acondicionado conviven en la misma lista sin declarar nada mas.
#   - "heat_switches"/"cool_switches": listas de switch.* — a diferencia
#     de un climate.*, un switch no puede autodeclarar para que sirve, asi
#     que aqui si hace falta decir de que lado es cada uno. Tantos como se
#     quiera por lado (p.ej. dos radiadores en switches separados que
#     deben encenderse juntos).
#
# La capacidad final de la zona (heat/cool/heat_cool, y por tanto que
# hvac_modes expone el climate.* de Climate Orchestrator a Home
# Assistant/Matter/HomeKit) se calcula sola a partir de lo declarado aqui
# — ver `ClimateOrchestratorZone._compute_capability()` en climate.py.
CONF_CLIMATE_ENTITIES = "climate_entities"
CONF_HEAT_SWITCHES = "heat_switches"
CONF_COOL_SWITCHES = "cool_switches"
CONF_CURRENT_TEMP_SENSOR = "current_temp_sensor"
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_OUTDOOR_TEMP_SENSOR = "outdoor_temp_sensor"
CONF_WEATHER_ENTITY = "weather_entity"

# Presets con nombre en vez de horario (ver presets.py: se elimino la
# franja horaria fija a proposito — no sabe si hay alguien de verdad en
# la habitacion). "Nombre: temperatura, Nombre: temperatura..." declarado
# como texto libre, para poder tener tantos presets como se quiera
# ("Confort", "Fiesta", "Vacaciones"...) sin una lista con "+ añadir" a
# medida en el asistente. `presence_preset`/`away_preset` designan cuales
# de esos nombres usar automaticamente segun la presencia FISICA real
# (ver CONF_PRESENCE_ENTITIES mas abajo) cuando el preset activo es
# presets.PRESET_AUTO — el modo por defecto.
CONF_PRESETS_TEXT = "presets_text"
CONF_PRESENCE_PRESET = "presence_preset"
CONF_AWAY_PRESET = "away_preset"

CONF_DEADBAND = "deadband"

# Techo/suelo de seguridad de la zona: SIEMPRE se respetan, pase lo que
# pase con el preset activo o la presencia — "nunca por debajo de 12°C en
# invierno aunque no haya nadie, nunca por encima de 30°C en verano". No
# son solo limites informativos del selector de temperatura: scheduler.py
# los aplica como accion obligatoria si se cruzan.
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"

CONF_MIN_ON_SECONDS = "min_on_seconds"
CONF_MIN_OFF_SECONDS = "min_off_seconds"
CONF_PRIORITY = "priority"                     # "confort" | "ahorro" | "manual"

# Sensores de PRESENCIA FISICA de la propia zona (PIR, mmWave, radar de
# presencia...) — "¿hay alguien AHORA MISMO en esta habitacion?", no "¿esta
# alguien en casa?". Un binary_sensor de ocupacion/movimiento de la
# habitacion es la señal principal pensada aqui; person.*/device_tracker.*
# tambien se aceptan como señal adicional (utiles sobre todo para saber
# que NADIE esta en toda la casa), pero no son el caso de uso principal.
CONF_PRESENCE_ENTITIES = "presence_entities"
CONF_DOOR_WINDOW_ENTITIES = "door_window_entities"
CONF_HISTORY_DAYS_FOR_INERTIA = "history_days_for_inertia"
CONF_FORECAST_REFRESH_MINUTES = "forecast_refresh_minutes"
CONF_SIMULATE = "simulate"                     # modo simulacion: calcula y muestra, nunca actua de verdad

DEFAULT_DEADBAND = 0.3
DEFAULT_MIN_TEMP = 15.0
DEFAULT_MAX_TEMP = 30.0
DEFAULT_MIN_ON_SECONDS = 300
DEFAULT_MIN_OFF_SECONDS = 300
DEFAULT_HISTORY_DAYS_FOR_INERTIA = 14
DEFAULT_FORECAST_REFRESH_MINUTES = 10
DEFAULT_OUTDOOR_HORIZON_HOURS = 6  # ya no hace falta un horizonte largo (sin horario que anticipar): solo AHORRO_LOOKAHEAD_HOURS de scheduler.py + margen

# Inercia termica: valores conservadores por defecto hasta que haya
# historico suficiente (ver thermal_model.py) — nunca un numero inventado
# como si fuera real, siempre marcado con `reliable=False` hasta entonces.
DEFAULT_HEATING_RATE_DEG_H = 0.9
DEFAULT_COOLING_RATE_DEG_H = 1.2
DEFAULT_IDLE_LOSS_COEFF = 0.08
