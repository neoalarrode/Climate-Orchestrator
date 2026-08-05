<h1 align="center">🌡️ Climate Orchestrator</h1>

<p align="center">
  Calefacción y aire acondicionado adaptativos — por presencia, horario,<br>
  puertas/ventanas e inercia térmica real de cada zona. Sin cajas negras.
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
- **Tres modos de control por zona**: solo horario, solo presencia real
  (nunca prevista — sería una caja negra), o híbrido (horario + la
  presencia real puede subir/bajar el nivel de la hora actual).
- **Aprende la inercia térmica real** de cada zona con su propio
  historial (recorder de HA): grados/hora calentando, coeficiente de
  pérdida frente al exterior — nunca un número inventado.
- **Precalienta lo justo**: en prioridad "ahorro", no enciende hasta el
  último momento en el que, a la velocidad real de esa zona, todavía
  llega a tiempo. En "confort", actúa en cuanto hace falta.
- **Calor y frío con actuadores independientes**: un radiador (switch) y
  un aire acondicionado (`climate.*`) distintos conviven sin pisarse —
  nunca se activan los dos a la vez. Si el mismo equipo hace ambas cosas
  (una bomba de calor reversible), se detecta solo y se le manda una
  única orden con el modo correcto según la estación.
- **Modo persistente vs. anulación temporal**: cambiar el modo
  (apagado/calor/frío/auto) desde el termostato es una elección que se
  queda (se restaura tras un reinicio, igual que cualquier termostato
  real); cambiar la temperatura objetivo es una anulación temporal con
  caducidad configurable, tras la cual la zona vuelve sola al plan.
- **Protección de seguridad** anti-heladas / anti-golpe-de-calor, siempre
  activa, pase lo que pase con horario, presencia o modo manual.
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
