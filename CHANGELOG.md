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
- **Presets con nombre en vez de horario fijo** ("Confort: 21, Ausente:
  17, Fiesta: 23"...), activados automáticamente por presencia FÍSICA
  real de la zona (sensores PIR/mmWave, no solo "en casa") o fijados a
  mano de forma persistente — el horario/franjas se eliminó a propósito:
  no sabe si hay alguien de verdad en la habitación.
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
- Actuadores de calor y frío independientes (switch o climate.* cada
  uno, en cualquier combinación — un radiador puede tener su propia
  entidad climate.*, igual que un aire acondicionado puede ser un switch
  simple), con detección automática de equipo reversible compartido para
  mandar una única orden con el modo correcto.
- Modo (apagado/calor/frío/auto) y preset activo persistentes vía
  RestoreEntity; temperatura objetivo como anulación temporal con
  caducidad configurable.
- Modo simulación por zona, activo por defecto.
