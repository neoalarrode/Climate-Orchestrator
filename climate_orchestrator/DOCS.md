# Climate Orchestrator — Guía de configuración

## 1. Requisitos previos

- El addon oficial **Mosquitto broker** instalado y arrancado (Ajustes →
  Add-ons → Tienda), con la integración MQTT de Home Assistant configurada
  (normalmente se autoconfigura sola al instalar Mosquitto).
- Un sensor de temperatura por cada zona que quieras gestionar (un
  `sensor.*` que reporte °C).
- El actuador de cada zona: o bien un `switch.*` que encienda/apague tu
  calefactor/AC, o un `climate.*` ya existente en el que delegar.

## 2. Broker MQTT

En la pestaña **Configuración → Broker MQTT**:

- **Host**: `core-mosquitto` si usas el addon oficial de Mosquitto (valor
  por defecto). Si usas un broker externo, la IP/hostname que corresponda.
- **Puerto**: `1883` por defecto.
- **Usuario/Contraseña**: los que hayas configurado en el addon Mosquitto
  (usuario del propio Home Assistant si usas login integrado, o el que
  hayas creado a mano).

Sin un broker MQTT accesible, las zonas se siguen planificando y
ejecutando (si tienen actuador), pero no aparecerán como `climate.*` en
Home Assistant — la interfaz avisa con el indicador "MQTT: sin conectar".

## 3. Dar de alta una zona

En **Configuración → Zonas → + Añadir zona**:

- **Nombre**: como quieras que aparezca en Home Assistant.
- **Capacidad**: solo calor, solo frío, o ambos (una zona con bomba de
  calor reversible, por ejemplo).
- **Prioridad**:
  - *Confort*: actúa en cuanto la temperatura se sale de rango, sin
    esperar. Recomendada si quieres máxima comodidad sin pensar en ahorro.
  - *Ahorro*: usa la inercia térmica aprendida para arrancar lo más tarde
    posible y llegar justo a tiempo a cada franja de confort — menos horas
    de actuador encendido a cambio de fiarse del modelo aprendido.
  - *Manual*: la zona no decide nada sola; solo refleja lo que ordenes a
    mano desde el termostato de Home Assistant.
- **Sensor de temperatura**: obligatorio.
- **Sensor de humedad / sensor exterior propio**: opcionales.
- **Actuador**:
  - *Switch*: declara el `switch.*` de calor y/o de frío. El addon los
    enciende/apaga él mismo con histéresis y anti-ciclado.
  - *Delegar en climate.\**: declara el `climate.*` ya existente; el addon
    solo le manda modo y temperatura objetivo.
- **Temperaturas**: confort (cuando la franja horaria está activa o hay
  presencia), eco (fuera de franja, sin presencia) y ausente (referencia,
  ver protección mínima más abajo). Histéresis: margen antes de
  actuar/parar.
- **Anti-ciclado**: segundos mínimos que el actuador debe permanecer
  encendido/apagado antes de poder cambiar otra vez.
- **Presencia**: entidades `person.*`/`device_tracker.*`/`binary_sensor.*`
  — si alguna está "en casa"/"on", la zona se considera ocupada AHORA
  MISMO (nunca se predice presencia futura, sería una caja negra). Con
  "la presencia puede anular el horario" activo: alguien en casa sube
  a confort aunque el horario diga que no toca; nadie en casa baja a eco
  aunque el horario diga que sí toca (esto último solo en prioridad
  "ahorro").
- **Horario**: franjas de "confort" por día de la semana. Fuera de ellas,
  la zona está en "eco". Sin ninguna franja declarada, la zona está en
  "eco" todo el día (solo actúa por protección mínima).

## 4. Protección de seguridad

Sea cual sea el horario o la presencia, una zona de calor nunca deja que
la temperatura baje más de 3°C por debajo de su nivel "ausente"
(anti-heladas), y una zona de frío nunca deja que suba más de 3°C por
encima (anti-golpe-de-calor). No es configurable a propósito: es la última
red de seguridad.

## 5. Inercia térmica aprendida

Solo se aprende de zonas con actuador tipo "switch" (el addon necesita
saber con certeza cuándo estuvo encendido). Hace falta al menos un puñado
de tramos de al menos 20 minutos seguidos en el mismo estado dentro del
historial de Home Assistant — normalmente unos pocos días de uso real. Hasta
entonces se usan valores conservadores por defecto, y la interfaz lo deja
dicho ("inercia térmica: estimación inicial").

## 6. Anulación manual desde Home Assistant

Cambiar el modo o la temperatura objetivo desde la tarjeta de termostato
de Home Assistant (o Google Home/Alexa) pone esa zona en "anulación
manual" durante 2 horas (fijo en esta versión). Mientras dure, el motor
deja de decidir por su cuenta para esa zona; pasado ese tiempo, vuelve
sola al plan automático. Se puede retirar antes a mano desde la pestaña
"Estado actual".

## 7. Modo simulación

Con "Modo simulación" activo (por defecto), el addon calcula el plan y lo
publica en Home Assistant, pero NUNCA enciende/apaga ni manda órdenes a
ningún actuador real. Revisa unos días los motivos y las horas que elegiría
antes de desactivarlo.
