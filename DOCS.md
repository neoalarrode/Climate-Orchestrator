# Climate Orchestrator — Guía de configuración

## 1. Instalación

1. HACS debe estar instalado en tu Home Assistant.
2. HACS → menú ⋮ (arriba a la derecha) → **Repositorios personalizados**
   → pega `https://github.com/neoalarrode/Climate-Orchestrator` → tipo
   **Integración** → **Añadir**.
3. Busca "Climate Orchestrator" en HACS, instálala.
4. Reinicia Home Assistant (obligatorio: es una integración nueva, no
   basta con recargar).
5. **Ajustes → Dispositivos y servicios → Añadir integración** → busca
   "Climate Orchestrator".

## 2. Una zona = una entrada de integración

Cada vez que sigues el asistente, das de alta **una** zona (una
habitación, un espacio). Para gestionar el salón y dos dormitorios,
repites "Añadir integración" tres veces. Cada zona vive como su propio
dispositivo, con su propia entidad `climate.*` — bórrala como cualquier
otra integración (Ajustes → Dispositivos y servicios → esa entrada → ⋮ →
Eliminar) si dejas de necesitarla.

## 3. El asistente, paso a paso

**Paso 1 — General**: nombre de la zona, capacidad (solo calor / solo
frío / calor y frío), prioridad, modo de control.

- **Prioridad**: *Confort* actúa en cuanto la temperatura se sale de
  rango; *Ahorro* usa la inercia térmica aprendida para arrancar lo más
  tarde posible; *Manual* nunca decide sola, solo refleja el control
  manual del termostato.
- **Modo de control**: *Solo horario* ignora la presencia; *Solo
  presencia* ignora el horario (confort si hay alguien ahora mismo, eco
  si no); *Híbrido* combina ambos (horario de base, la presencia real
  puede subir/bajar el nivel de la hora actual).

**Paso 2 — Sensores**: sensor de temperatura (obligatorio), humedad
(opcional), sensor exterior propio de la zona (opcional, más preciso que
el general) y una entidad `weather.*` con previsión horaria (opcional,
recomendada para la prioridad "ahorro").

**Paso 3 — Actuador**: calor y frío tienen CADA UNO su propio actuador,
independiente:

- *Switch*: la integración enciende/apaga un `switch.*` con histéresis y
  anti-ciclado (tiempo mínimo encendido/apagado).
- *climate.\**: delega en una entidad `climate.*` que ya exista (una
  válvula termostatica, un aire acondicionado con su propia
  electrónica...). Se le manda su modo correcto ("heat"/"cool"/"off") y
  la temperatura objetivo.

Calor y frío son independientes de verdad: cualquier combinación vale. Un
radiador puede ser switch simple O tener su propia entidad `climate.*`
(una válvula termostática, por ejemplo) igual que un aire acondicionado —
ninguno de los dos campos asume un tipo de dispositivo concreto, solo si
lo controlas por switch o delegando en un climate.* ya existente. Si tu
instalación tiene un actuador de calor y otro de frío DISTINTOS (sea cual
sea su tipo), declara cada uno en su campo — la integración nunca los
activa a la vez. Si tienes un único equipo
reversible (aire acondicionado con bomba de calor), pon la MISMA entidad
`climate.*` en el campo de calor y en el de frío: se detecta solo y se le
manda una única orden con el modo que toque según la estación, nunca dos
órdenes que se pisen.

**Paso 4 — Temperaturas**: confort, eco, ausencia, histéresis, límites
mínimo/máximo, tiempos mínimos de encendido/apagado (solo relevantes con
actuador tipo switch).

**Paso 5 — Horario, presencia y opciones**: franja horaria de confort
(inicio, fin, días), entidades de presencia, si la presencia puede anular
el horario, sensores de puerta/ventana, duración de la anulación manual
de temperatura, días de histórico para la inercia térmica, frecuencia de
recálculo del plan, y modo simulación.

## 4. Editar una zona

Ajustes → Dispositivos y servicios → Climate Orchestrator → la zona que
quieras → **Configurar**. Se abre un único formulario con todos los
campos precargados. Al guardar, la zona se recarga entera.

## 5. Puertas y ventanas

Cualquier sensor de puerta/ventana declarado que esté "abierto" pausa la
zona **al instante** (vía el bus de eventos de HA, no hay que esperar a
ningún ciclo), sea cual sea el plan o el modo. Al cerrarse, vuelve sola al
cálculo automático.

## 6. Modo vs. temperatura: dos comportamientos distintos

- **Cambiar el MODO** (apagado / calor / frío / auto) desde la tarjeta de
  termostato, Google Home, Alexa o un puente Matter/HomeKit es una
  elección que se **queda** — no caduca sola, se restaura tras un
  reinicio, igual que cualquier termostato real. Bloquear un `heat_cool`
  a "solo frío" en verano, por ejemplo, se mantiene hasta que lo cambies
  tú.
- **Cambiar la TEMPERATURA objetivo** es una anulación **temporal**: dura
  lo que hayas configurado (por defecto 2h) y después la zona vuelve sola
  al plan automático.

## 7. Protección de seguridad

Sea cual sea el horario, la presencia o el modo manual, una zona de calor
nunca deja que la temperatura baje más de 3°C por debajo de su nivel
"ausencia" (anti-heladas), y una de frío nunca deja que suba más de 3°C
por encima (anti-golpe-de-calor). No es configurable a propósito: es la
última red de seguridad.

## 8. Inercia térmica aprendida

Se aprende de los dos tipos de actuador, no solo de switch: con un
`switch.*` propio se usa directamente su historial de encendido/apagado;
con un `climate.*` delegado (un radiador con válvula termostática, un
aire acondicionado con su propia electrónica...) se usa el atributo
`hvac_action` de SU historial (heating/cooling frente a idle/off) — la
mayoría de integraciones de clima lo publican. Si una entidad concreta
nunca lo reporta, simplemente no se encuentran tramos válidos y esa zona
se queda con valores conservadores por defecto (marcado
`thermal_model_reliable: false` en los atributos de la entidad) — nunca
se inventa una cifra.

## 9. Modo simulación

Con "Modo simulación" activo en una zona (por defecto), la integración
calcula y publica lo que haría (visible en los atributos de la entidad),
pero nunca manda una orden real a ningún actuador. Revisa unos días el
atributo `reason` antes de desactivarlo.
