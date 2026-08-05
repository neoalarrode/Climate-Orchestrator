# Changelog

## 0.1.0 — primera versión

- Motor de planificación por zona (dos pasadas: nivel objetivo por
  horario/presencia, simulación con inercia térmica aprendida).
- Aprendizaje de inercia térmica desde el histórico de Home Assistant
  (grados/hora calentando, coeficiente de pérdida en reposo).
- Ejecución por switch (con histéresis y anti-ciclado) o delegando en un
  `climate.*` existente.
- Zonas expuestas a Home Assistant como `climate.*` reales vía MQTT
  Discovery, con anulación manual temporal desde la propia tarjeta de
  termostato.
- Protección mínima anti-heladas / anti-golpe-de-calor, siempre activa.
- Interfaz web con Ingress: estado en vivo por zona, configuración
  completa (zonas, horarios, presencia, MQTT), exportar/importar
  configuración.
- Panel de solo lectura (wallpanel) en puerto propio.
- Modo simulación activo por defecto.
