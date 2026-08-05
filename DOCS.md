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

**Paso 1 — General**: nombre de la zona y prioridad. No se pide
capacidad (calor/frío/ambos): se calcula sola a partir de los actuadores
que declares en el paso 3.

- **Confort**: actúa en cuanto la zona se sale de rango del preset
  activo.
- **Ahorro**: margen de histéresis más ancho (menos ciclos de encendido),
  que se estrecha solo si la previsión exterior empeora en las próximas
  horas.
- **Manual**: nunca decide sola; solo refleja el control manual del
  termostato.

**Paso 2 — Sensores**: sensor de temperatura (obligatorio), humedad
(opcional), sensor exterior propio de la zona (opcional, más preciso que
el general) y una entidad `weather.*` con previsión horaria (opcional,
recomendada — se usa para anticipar cambios exteriores, ver más abajo).

**Paso 3 — Actuadores**: tres listas, añade tantos como tengas de
verdad de cada uno (puedes combinar los tres tipos en la misma zona):

- **climate.\* delegados**: entidades `climate.*` que ya existan (una
  válvula termostática, un aire acondicionado con su propia
  electrónica...). Cada una se gobierna por SUS PROPIOS `hvac_modes`
  nativos — leídos en vivo del propio dispositivo, nunca declarados por
  ti. Si soporta `heat`, se activa en `heat` cuando toca calentar; si
  soporta `cool`, en `cool` cuando toca enfriar; si soporta los dos de
  verdad (una bomba de calor reversible), se le manda el que corresponda
  cada vez — una única orden, nunca dos que se pisen. **No hay campos
  separados de "climate para calor" y "climate para frío"**: es la MISMA
  lista, y cada entidad aporta lo que de verdad sepa hacer.
- **Switches de calefacción** y **switches de refrigeración**: a
  diferencia de un `climate.*`, un switch no puede autodeclarar para qué
  sirve, así que estos sí van en su lista correspondiente. La integración
  los enciende/apaga con histéresis y anti-ciclado (tiempo mínimo
  encendido/apagado).

La capacidad final de la zona (solo calor / solo frío / ambos) —y por
tanto qué modos expone Home Assistant y cualquier puente Matter/HomeKit—
se calcula sola a partir de lo que hayas añadido aquí. Un radiador con
válvula termostática y un aire acondicionado conviven en la misma zona
sin declarar nada más; nunca se activan calor y frío a la vez.

**Paso 4 — Presets**: en vez de un horario fijo, declaras una lista de
presets con nombre y temperatura, como texto: `Confort: 21, Ausente: 17,
Fiesta: 23`. Tantos como quieras. Por qué no hay horario: una franja
"07:00-23:00 = confort" no sabe si hay alguien de verdad en la
habitación — los presets, combinados con presencia real, se adaptan a lo
que de verdad está pasando.

**Paso 5 — Cambio automático**: eliges qué preset de los que acabas de
declarar se activa cuando SÍ hay presencia y cuál cuando NO la hay. Estos
dos son los que usa el modo "Automático" (el preset activo por defecto).

**Paso 6 — Límites de seguridad**: histéresis, y el **mínimo y máximo que
SIEMPRE se respetan**, sea cual sea el preset activo, la presencia o el
modo manual — el "nunca por debajo de 12°C en invierno aunque no haya
nadie, nunca por encima de 30°C en verano" configurable por zona. También
los tiempos mínimos de encendido/apagado (solo relevantes con actuador
tipo switch).

**Paso 7 — Presencia, puertas/ventanas y opciones**: entidades de
presencia, sensores de puerta/ventana, duración de la anulación manual de
temperatura, días de histórico para la inercia térmica, frecuencia de
recálculo de la previsión, y modo simulación.

## 4. Presencia: sensores FÍSICOS de la habitación, no "en casa"

El campo de presencia está pensado sobre todo para sensores de presencia
FÍSICA propios de la zona — PIR, radar mmWave, un `binary_sensor` de
ocupación o movimiento de esa habitación concreta: "¿hay alguien AHORA
MISMO aquí?", no "¿está alguien en casa?". `person.*`/`device_tracker.*`
también se aceptan, como señal adicional (útiles sobre todo para saber
que NO hay nadie en toda la casa), pero no son el caso de uso principal.
Nunca se predice presencia futura — sería una caja negra —, solo se mide
la de ahora mismo.

## 5. Presets: automático, o fijado a mano

El preset **"Automático"** (el que aplica por defecto) elige solo entre
el preset "con presencia" y el "sin presencia" que declaraste, según la
presencia física medida ahora. Elegir CUALQUIER OTRO preset a mano —desde
la tarjeta de termostato, Google Home, Alexa o un puente Matter/HomeKit—
es una elección **persistente**: se queda fijada (se restaura tras un
reinicio) hasta que tú mismo vuelvas a poner "Automático". Útil para "hoy
me quedo en Vacaciones aunque detecte presencia" sin tener que desactivar
nada.

## 6. Editar una zona

Ajustes → Dispositivos y servicios → Climate Orchestrator → la zona que
quieras → **Configurar**. Se abre un único formulario con todos los
campos precargados (los presets se editan como el mismo texto libre del
asistente). Al guardar, la zona se recarga entera.

## 7. Puertas y ventanas

Cualquier sensor de puerta/ventana declarado que esté "abierto" pausa la
zona **al instante** (vía el bus de eventos de HA, no hay que esperar a
ningún ciclo), sea cual sea el preset activo o el modo. Al cerrarse,
vuelve sola al cálculo automático.

## 8. Anticipación al clima exterior (no a tu presencia)

Eliminar el horario no significa eliminar la anticipación: sigue siendo
más eficiente (y más cómodo) ajustar la zona de forma sostenida mientras
todavía hay margen, que esperar a salirse de rango y tener que actuar a
máxima potencia de golpe. La diferencia es de dónde viene esa
anticipación:

- **Nunca se predice presencia** — sería una caja negra. Los presets
  cambian solo por presencia medida en directo.
- **Sí se usa la previsión meteorológica exterior** (observable, no
  inventada): si el pronóstico indica que se acerca un cambio de
  temperatura, la zona empieza a actuar YA, de forma sostenida, en vez de
  esperar a que ya se haya salido de rango. Combina la previsión con la
  inercia térmica real aprendida de esa zona (una zona rápida no necesita
  tanta antelación como una lenta).
- En prioridad **"ahorro"**, además, el margen de histéresis se ensancha
  cuando la previsión es estable (menos ciclos de encendido) y se
  estrecha solo si empeora.

## 9. Límites de seguridad

Configurables por zona (paso 6 del asistente): un mínimo que la
calefacción siempre respeta y un máximo que la refrigeración siempre
respeta, sea cual sea el preset activo, la presencia o el modo manual.
Pensado exactamente para "no me importa que no haya nadie, nunca por
debajo de X en invierno / nunca por encima de X en verano".

## 10. Modo vs. preset vs. temperatura: tres comportamientos distintos

- **Cambiar el MODO** (apagado / calor / frío / auto) es una elección que
  se **queda** — no caduca sola, se restaura tras un reinicio.
- **Cambiar el PRESET** (a mano, cualquiera de los declarados) también es
  **persistente** — igual que el modo, ver punto 5.
- **Cambiar la TEMPERATURA** objetivo es una anulación **temporal**: dura
  lo que hayas configurado (por defecto 2h) y después la zona vuelve sola
  al preset activo.

## 11. Inercia térmica aprendida

Se aprende de los dos tipos de actuador, no solo de switch: con un
`switch.*` propio se usa directamente su historial de encendido/apagado;
con un `climate.*` delegado se usa el atributo `hvac_action` de SU
historial (heating/cooling frente a idle/off) — la mayoría de
integraciones de clima lo publican. Si una entidad concreta nunca lo
reporta, esa zona se queda con valores conservadores por defecto (marcado
`thermal_model_reliable: false` en los atributos de la entidad) — nunca
se inventa una cifra.

## 12. Modo simulación

Con "Modo simulación" activo en una zona (por defecto), la integración
calcula y publica lo que haría (visible en los atributos de la entidad),
pero nunca manda una orden real a ningún actuador. Revisa unos días el
atributo `reason` antes de desactivarlo.
