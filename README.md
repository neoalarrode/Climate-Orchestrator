<h1 align="center">🌡️ Climate Orchestrator</h1>

<p align="center">
  Calefacción y aire acondicionado adaptativos — por presencia física,<br>
  presets, puertas/ventanas e inercia térmica real de cada zona. Sin cajas negras.
</p>

<p align="center">
  <img alt="HACS" src="https://img.shields.io/badge/HACS-Custom-8b5cf6?style=flat-square&labelColor=0b0a16">
  <img alt="Determinista" src="https://img.shields.io/badge/planificador-determinista-22d3ee?style=flat-square&labelColor=0b0a16">
  <img alt="Sin cajas negras" src="https://img.shields.io/badge/sin%20cajas%20negras-eae8f7?style=flat-square&labelColor=0b0a16">
</p>

<p align="center">
  🇪🇸 Español · <a href="README.en.md">🇬🇧 Read in English</a>
</p>

---

Integración de Home Assistant instalable vía **HACS**, hermana de
[Battery Orchestrator](https://github.com/neoalarrode/Battery-Orchestrator),
que gestiona la calefacción/refrigeración de cada zona de tu casa con un
motor propio, determinista y legible de arriba a abajo — nada de EMHASS,
nada de parámetros difíciles de razonar tipo `versatile_thermostat`. Cada
zona que declares se convierte en una entidad `climate.*` **nativa** de
Home Assistant: funciona en Lovelace, Google Home, Alexa o cualquier
puente Matter/HomeKit exactamente igual que cualquier otro termostato.

## Por qué es una integración, no un add-on

La primera versión de este proyecto era un add-on externo con su propia
web. Se descartó a propósito: un add-on solo puede sondear Home Assistant
por REST cada pocos segundos, o depender de MQTT/websockets de repuesto
para reaccionar. Una integración vive **dentro** del propio bus de eventos
de HA — así que "se abrió una ventana" o "alguien acaba de entrar en la
habitación" se traduce en una reacción real al instante, no en "dentro de
hasta 20 segundos". Es la arquitectura correcta para un termostato.

## Qué hace

- **Una zona = una entrada de integración** (añade "+ Añadir integración"
  tantas veces como habitaciones — igual que `versatile_thermostat`). Cada
  zona expone su propio `climate.*` con motivo de la decisión visible en
  sus atributos.
- **Reacciona al instante** a temperatura, presencia y puertas/ventanas —
  vía el bus de eventos de HA (`async_track_state_change_event`), no por
  sondeo. Una puerta/ventana abierta pausa la zona en el momento, sin
  esperar a nada.
- **Presets con nombre en vez de horario fijo, configurables como
  entidades**: "Confort: 21/25, Ausente: 17/28, Fiesta: 23/24"...
  tantos como quieras, cada uno con su consigna de invierno (calor) y de
  verano (frío) por separado. Cada consigna es su propia entidad
  `number.*` — se puede subir un grado el preset "Confort" desde Lovelace
  o una automatización, sin volver a "Configurar" la zona. Se activan
  solos según la presencia FÍSICA real de la habitación (sensores
  PIR/mmWave, no solo "en casa" por el móvil) — o a mano, como una
  elección persistente hasta que vuelvas a "Automático".
- **Modo "Auto" al estilo Matter, además de calor/frío por separado**: una
  zona con calor y frío de verdad expone `off`/`auto`/`heat`/`cool` — en
  "Auto" (el System Mode Auto estándar de Matter) tiene las dos consignas
  activas a la vez, decide sola cuál toca cada momento; si prefieres
  bloquearla a mano a "solo calor" o "solo frío" (p.ej. en verano), sigue
  pudiéndose. Listo para cualquier puente Matter/HomeKit sin traducciones.
- **Techo y suelo de seguridad siempre activos**: "nunca por debajo de
  X°C en invierno, nunca por encima de X°C en verano", aunque no haya
  nadie — independientes del preset activo o de la presencia.
- **Aprende la inercia térmica real** de cada zona con su propio
  historial (recorder de HA): grados/hora calentando, coeficiente de
  pérdida frente al exterior — nunca un número inventado.
- **Se anticipa al clima exterior, no a tu presencia**: si la previsión
  meteorológica indica que se acerca un cambio de temperatura, empieza a
  actuar de forma sostenida con antelación (usando la inercia térmica
  aprendida) en vez de esperar a salirse de rango y tener que compensarlo
  de golpe a máxima potencia. En prioridad "ahorro", además, ensancha el
  margen de histéresis cuando la previsión es estable, para reducir
  ciclos de encendido. Nunca se predice presencia — solo clima, que es un
  dato observable.
- **Capacidad detectada, no declarada a mano**: nada de elegir "solo
  calor / solo frío / ambos" en un desplegable. Declaras los actuadores
  que de verdad tienes — cuantos `climate.*` delegados quieras (cada uno
  gobernado por SUS PROPIOS `hvac_modes` nativos, sin campos separados de
  calor/frío: una bomba de calor reversible solo hace falta añadirla una
  vez) más los switches de calor y de frío que tengas, en listas
  independientes — y la zona calcula sola qué puede hacer, exponiendo a
  Home Assistant/Matter/HomeKit exactamente el conjunto estándar
  (`off`/`heat`/`cool`/`heat_cool`) que sus actuadores reales soportan.
  Nunca se activan calor y frío a la vez.
- **Modo y preset persistentes vs. temperatura como anulación temporal**:
  cambiar el modo (apagado/auto, o apagado/calor en una zona de un solo
  sentido) o el preset desde el termostato es una elección que se queda
  (se restaura tras un reinicio,
  igual que cualquier termostato real); cambiar la temperatura objetivo es
  una anulación temporal con caducidad configurable, tras la cual la zona
  vuelve sola al preset activo.
- **Modo simulación** por zona: calcula y muestra lo que haría, sin tocar
  ningún actuador real, hasta que confíes en sus decisiones.

## Instalación

1. HACS → ⋮ → Repositorios personalizados → añade
   `https://github.com/neoalarrode/Climate-Orchestrator` como tipo
   **Integración**.
2. Instala "Climate Orchestrator" y reinicia Home Assistant.
3. **Ajustes → Dispositivos y servicios → Añadir integración** → busca
   "Climate Orchestrator" → sigue el asistente para dar de alta tu primera
   zona. Repite por cada habitación.

Guía de configuración completa, campo a campo, en [DOCS.md](DOCS.md).

## Estado del proyecto

En desarrollo activo — ver [CHANGELOG.md](CHANGELOG.md). Activa el modo
simulación en cada zona nueva y revisa unos días el atributo `reason`
antes de dejarla actuar de verdad.
