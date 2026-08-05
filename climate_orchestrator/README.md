<h1 align="center">🌡️ Climate Orchestrator</h1>

<p align="center">
  Calefaccion y aire acondicionado adaptativos — por presencia, horario<br>
  e inercia termica real de cada zona. Sin cajas negras.
</p>

<p align="center">
  <img alt="Home Assistant Add-on" src="https://img.shields.io/badge/Home%20Assistant-Add--on-8b5cf6?style=flat-square&labelColor=0b0a16">
  <img alt="Determinista" src="https://img.shields.io/badge/planificador-determinista-22d3ee?style=flat-square&labelColor=0b0a16">
  <img alt="Sin cajas negras" src="https://img.shields.io/badge/sin%20cajas%20negras-eae8f7?style=flat-square&labelColor=0b0a16">
</p>

<p align="center">
  🇪🇸 Español · <a href="README.en.md">🇬🇧 Read in English</a>
</p>

---

Add-on de Home Assistant, hermano de [Battery Orchestrator](https://github.com/neoalarrode/Battery-Orchestrator),
que planifica y ejecuta la calefaccion/refrigeracion de cada zona de tu
casa cada minuto, en directo contra tu instalacion real. Un motor propio,
determinista y legible de arriba a abajo — nada de EMHASS, nada de
integraciones tipo `versatile_thermostat` con parametros dificiles de
razonar — mas una interfaz web donde declaras tu mismo cada zona, sensor y
horario. Cada zona se expone de vuelta a Home Assistant como una entidad
`climate.*` real (via MQTT Discovery), asi que sigue funcionando en
Lovelace, Google Home o Alexa como cualquier termostato — solo que quien
decide cuando encender es este motor, no una caja negra.

## Por que existe

`versatile_thermostat` y similares resuelven bien el control fino de un
termostato, pero la decision de CUANDO calentar suele reducirse a un
horario fijo o a presets sin mucha explicacion. Climate Orchestrator hace
lo mismo que ya hace Battery Orchestrator con las baterias: un algoritmo
de dos pasadas que se puede leer entero, donde cada decision de cada hora
viene con su motivo en texto plano ("precalentando: arranca justo para
llegar a 21°C a las 07:00", "sin actuar: dentro de rango", "manteniendo
minimo de seguridad, proteccion anti-heladas"...).

## Que hace

- **Planifica cada zona por separado**, combinando el horario que declares
  (franjas de "confort"), la presencia real medida ahora mismo (persona,
  device_tracker...) y la previsión meteorologica exterior (de una
  entidad `weather.*` de HA, o de tu propio sensor exterior).
- **Aprende la inercia termica real de cada zona** de su propio
  historico: cuantos grados por hora sube calentando, cuantos pierde con
  el actuador apagado segun la diferencia con el exterior — nunca un
  numero inventado ni un modelo fisico generico de catalogo.
- **Precalienta lo justo**: en prioridad "ahorro", no enciende hasta el
  ultimo momento en el que, a la velocidad que de verdad calienta esa
  zona, todavia le da tiempo a llegar a confort cuando toca. En prioridad
  "confort", actua en cuanto hace falta, sin esperar.
- **Respeta minimos de seguridad** (anti-heladas / anti-golpe-de-calor)
  pase lo que pase con el horario o la presencia.
- **Anti-ciclado**: tiempo minimo encendido/apagado configurable por zona,
  para no destrozar un rele por decimas de grado.
- **Expone cada zona como `climate.*` en Home Assistant** via MQTT
  Discovery: aparece en Lovelace, Google Home, Alexa... Cambiar el modo o
  la temperatura desde ahi pasa a "anulacion manual" durante un tiempo
  configurable, tras el cual la zona vuelve sola al plan automatico.
- **Dos formas de actuar**: enciende/apaga un switch de calefactor/AC
  directamente (con histeresis), o delega en un `climate.*` que ya exista
  (p.ej. una valvula termostatica con su propia electronica).
- **Panel de solo lectura (wallpanel)**: igual que Battery Orchestrator, un
  puerto propio para dejarlo fijo en una tablet de pared sin pasar por el
  login de Home Assistant.
- **Todo configurable desde la web**: zonas, horarios, sensores, broker
  MQTT — nada hardcodeado. Configuracion exportable/importable.

## Instalacion

1. Instala y configura el addon oficial **Mosquitto broker** (Ajustes →
   Add-ons → Tienda) si todavia no lo tienes, y la integracion MQTT de
   Home Assistant.
2. En Home Assistant: **Ajustes → Add-ons → Tienda de add-ons → ⋮ →
   Repositorios**, y añade:
   ```
   https://github.com/neoalarrode/Climate-Orchestrator
   ```
3. Busca "Climate Orchestrator" en la tienda, instálalo e inícialo.
4. Ábrelo desde el panel lateral (usa Ingress) y da de alta tu primera
   zona.

Instrucciones paso a paso en [DOCS.md](DOCS.md).

## Estado del proyecto

En desarrollo activo — ver [CHANGELOG.md](CHANGELOG.md). Empieza siempre
en modo simulación: verás exactamente lo que haría el add-on sin tocar tus
actuadores reales, hasta que confíes en sus decisiones.
