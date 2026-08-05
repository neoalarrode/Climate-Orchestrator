# Changelog

## 0.1.0 — primera versión (reescrita como integración HACS)

La primera versión de este proyecto fue un add-on Flask externo con MQTT
Discovery. Se descartó antes de publicarse de verdad: un add-on solo
puede sondear Home Assistant o depender de MQTT/websockets de repuesto
para reaccionar rápido, mientras que una integración vive dentro del
propio bus de eventos de HA. Esta es la primera versión de la reescritura
como `custom_component` instalable vía HACS.

- Una entrada de integración = una zona (config_flow con asistente
  guiado + options flow para editar), mismo patrón que
  `versatile_thermostat`.
- **Presets con nombre en vez de horario fijo, configurables como
  entidades** ("Confort: 21/25, Ausente: 17/28, Fiesta: 23/24"...), cada
  uno con su consigna de calor ("invierno") y de frío ("verano") por
  separado, expuestas como entidades `number.*` propias (ajustables desde
  Lovelace o una automatización, sin volver a "Configurar" — ver
  number.py). Se activan automáticamente por presencia FÍSICA real de la
  zona (sensores PIR/mmWave, no solo "en casa") o se fijan a mano de
  forma persistente — el horario/franjas se eliminó a propósito: no sabe
  si hay alguien de verdad en la habitación.
- **Modo "Auto" único, compatible con el System Mode estándar de
  Matter**: una zona con calor y frío de verdad ya no deja bloquear a
  mano "solo calor"/"solo frío" — expone únicamente `off`/`auto`, con
  doble consigna simultánea (baja de calor, alta de frío) como cualquier
  termostato Auto estándar. `scheduler.decide_action` pasó a recibir las
  dos consignas directamente en vez de una sola temperatura + una
  capacidad declarada.
- **Motor de decisión continuo** (no un plan de día completo): reactivo
  ante temperatura/presencia/puerta-ventana, y con **anticipación al
  clima exterior** — si la previsión indica un cambio de temperatura
  próximo, empieza a actuar de forma sostenida con antelación (usando la
  inercia térmica aprendida) en vez de esperar a salirse de rango.
  Prioridad "ahorro" además ensancha el margen de histéresis cuando la
  previsión es estable. Nunca se predice presencia, solo clima
  (observable).
- **Límites de seguridad siempre activos** (mínimo/máximo por zona,
  independientes del preset o la presencia) — reemplaza la protección
  relativa anti-heladas/anti-golpe-de-calor de la primera revisión.
- Reacción instantánea a temperatura, presencia y puertas/ventanas vía
  `async_track_state_change_event` (bus de eventos de HA, no sondeo).
- Aprendizaje de inercia térmica desde el recorder de Home Assistant,
  para ambos tipos de actuador (switch propio, o climate.* delegado via
  su atributo `hvac_action`).
- **Capacidad (calor/frío/ambos) detectada, no declarada a mano**: se
  añaden los actuadores que de verdad existen — cuantos `climate.*`
  delegados quieras (gobernados por SUS PROPIOS `hvac_modes` nativos, sin
  campos separados de calor/frío) más listas de switches de calor y de
  frío, tantos como se quiera de cada uno — y la zona calcula sola qué
  puede hacer y qué modos expone a HA/Matter/HomeKit. Un radiador con
  válvula termostática y un aire acondicionado conviven en la misma zona
  sin declarar nada más; una bomba de calor reversible se añade una única
  vez y recibe el modo correcto según la estación, nunca dos órdenes que
  se pisen.
- Modo (apagado/auto, o apagado/calor en zonas de un solo sentido) y
  preset activo persistentes vía RestoreEntity; temperatura objetivo como
  anulación temporal con caducidad configurable.
- Modo simulación por zona, activo por defecto.
