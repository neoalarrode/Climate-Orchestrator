"""Constantes de Climate Orchestrator.

Una entrada de configuracion (ConfigEntry) = UNA zona. Se añaden tantas
zonas como habitaciones se quieran gestionar repitiendo "+ Añadir
integración" — mismo patrón que versatile_thermostat, y el que HA
recomienda para integraciones con varias instancias independientes.
"""

from __future__ import annotations

DOMAIN = "climate_orchestrator"

# --------------------------------------------------------------- claves ----
CONF_HVAC_CAPABILITY = "hvac_capability"       # "heat" | "cool" | "heat_cool"

# Calor y frio tienen CADA UNO su propio actuador, independiente — no un
# unico "actuator_mode" para toda la zona, y SIN asumir que uno es
# siempre switch y el otro siempre climate.*: cualquier combinacion vale
# (un radiador puede tener su propia entidad climate.*, p.ej. una valvula
# termostatica, igual que un aire acondicionado; y cualquiera de los dos
# puede ser tambien un simple switch). Hace falta sobre todo para el caso
# real mas comun de "heat_cool" con DOS EQUIPOS DISTINTOS (no uno
# reversible): cada lado declara el suyo por separado. Con
# "heat_actuator_mode"/"cool_actuator_mode" cada uno puede ser "switch"
# (se enciende/apaga solo) o "climate" (se delega en un climate.* que ya
# existe) de forma independiente. Si ambos apuntan al MISMO climate.* (un
# equipo reversible de verdad, p.ej. un aire acondicionado con bomba de
# calor), climate.py lo detecta y le manda una unica orden con el modo
# correcto — nunca calor y frio a la vez, y en invierno el propio equipo
# se activa en "heat" si es el que toca, no se queda apagado ni mal
# puesto en "cool".
CONF_HEAT_ACTUATOR_MODE = "heat_actuator_mode"   # "switch" | "climate"
CONF_HEAT_SWITCH = "heat_switch"
CONF_HEAT_CLIMATE = "heat_climate_entity"
CONF_COOL_ACTUATOR_MODE = "cool_actuator_mode"   # "switch" | "climate"
CONF_COOL_SWITCH = "cool_switch"
CONF_COOL_CLIMATE = "cool_climate_entity"
CONF_CURRENT_TEMP_SENSOR = "current_temp_sensor"
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_OUTDOOR_TEMP_SENSOR = "outdoor_temp_sensor"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_COMFORT_TEMP = "comfort_temp"
CONF_ECO_TEMP = "eco_temp"
CONF_AWAY_TEMP = "away_temp"
CONF_DEADBAND = "deadband"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_MIN_ON_SECONDS = "min_on_seconds"
CONF_MIN_OFF_SECONDS = "min_off_seconds"
CONF_PRIORITY = "priority"                     # "confort" | "ahorro" | "manual"
CONF_CONTROL_MODE = "control_mode"             # "horario" | "presencia" | "hibrido"
CONF_SCHEDULE_START = "schedule_start"
CONF_SCHEDULE_END = "schedule_end"
CONF_SCHEDULE_DAYS = "schedule_days"           # lista de 0=lunes..6=domingo, vacia = todos los dias
CONF_PRESENCE_ENTITIES = "presence_entities"
CONF_PRESENCE_OVERRIDES_SCHEDULE = "presence_overrides_schedule"
CONF_DOOR_WINDOW_ENTITIES = "door_window_entities"
CONF_MANUAL_OVERRIDE_HOURS = "manual_override_hours"
CONF_HISTORY_DAYS_FOR_INERTIA = "history_days_for_inertia"
CONF_PLAN_REFRESH_MINUTES = "plan_refresh_minutes"
CONF_SIMULATE = "simulate"                     # modo simulacion: calcula y muestra, nunca actua de verdad

DEFAULT_COMFORT_TEMP = 21.0
DEFAULT_ECO_TEMP = 18.0
DEFAULT_AWAY_TEMP = 16.0
DEFAULT_DEADBAND = 0.3
DEFAULT_MIN_TEMP = 15.0
DEFAULT_MAX_TEMP = 30.0
DEFAULT_MIN_ON_SECONDS = 300
DEFAULT_MIN_OFF_SECONDS = 300
DEFAULT_MANUAL_OVERRIDE_HOURS = 2.0
DEFAULT_HISTORY_DAYS_FOR_INERTIA = 14
DEFAULT_PLAN_REFRESH_MINUTES = 10
DEFAULT_HORIZON_HOURS = 36
DEFAULT_SCHEDULE_START = "07:00"
DEFAULT_SCHEDULE_END = "23:00"

# Inercia termica: valores conservadores por defecto hasta que haya
# historico suficiente (ver thermal_model.py) — nunca un numero inventado
# como si fuera real, siempre marcado con `reliable=False` hasta entonces.
DEFAULT_HEATING_RATE_DEG_H = 0.9
DEFAULT_COOLING_RATE_DEG_H = 1.2
DEFAULT_IDLE_LOSS_COEFF = 0.08

FROST_PROTECTION_DELTA = 3.0
HEAT_PROTECTION_DELTA = 3.0
