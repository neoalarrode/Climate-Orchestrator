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
- Motor de planificación por zona (dos pasadas: nivel objetivo por
  horario/presencia/modo de control, simulación con inercia térmica
  aprendida) — reutilizado y verificado desde la primera versión.
- Tres modos de control por zona: horario, presencia, híbrido.
- Reacción instantánea a temperatura, presencia y puertas/ventanas vía
  `async_track_state_change_event` (bus de eventos de HA, no sondeo).
- Aprendizaje de inercia térmica desde el recorder de Home Assistant.
- Actuadores de calor y frío independientes (switch o climate.* cada
  uno), con detección automática de equipo reversible compartido para
  mandar una única orden con el modo correcto.
- Modo (apagado/calor/frío/auto) persistente vía RestoreEntity;
  temperatura objetivo como anulación temporal con caducidad
  configurable.
- Protección anti-heladas/anti-golpe-de-calor, siempre activa.
- Modo simulación por zona, activo por defecto.
