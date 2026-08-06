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
presets con nombre y consigna de calor/frío por separado, como texto:
`Confort: 21/25, Ausente: 17/28, Fiesta: 23/24` (en una zona de un solo
sentido, basta un valor: `Confort: 21`). Tantos como quieras. Por qué no
hay horario: una franja "07:00-23:00 = confort" no sabe si hay alguien de
verdad en la habitación — los presets, combinados con presencia real, se
adaptan a lo que de verdad está pasando. Este texto solo SIEMBRA el valor
inicial: nada más crear la zona, cada consigna pasa a ser su propia
entidad `number.*` (ver punto 5) — el texto no se vuelve a leer.

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
presencia, sensores de puerta/ventana, días de histórico para la inercia térmica, frecuencia de
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

## 5. Presets: automático, o fijado a mano — y ajustables como entidades

El preset **"Automático"** (el que aplica por defecto) elige solo entre
el preset "con presencia" y el "sin presencia" que declaraste, según la
presencia física medida ahora. Elegir CUALQUIER OTRO preset a mano —desde
la tarjeta de termostato, Google Home, Alexa o un puente Matter/HomeKit—
es una elección **persistente**: se queda fijada (se restaura tras un
reinicio) hasta que tú mismo vuelvas a poner "Automático". Útil para "hoy
me quedo en Vacaciones aunque detecte presencia" sin tener que desactivar
nada.

Cada preset expone además una o dos entidades `number.*` propias (una
para su consigna de calor, otra para la de frío, según la capacidad de la
zona) — p.ej. "Confort (calor)" y "Confort (frío)". Se pueden ajustar en
caliente desde Lovelace o desde una automatización tuya en cualquier
momento; el valor vivo de esas entidades es lo que usa la zona para
decidir, no el texto que escribiste en el asistente (ese solo sirvió para
crearlas la primera vez).

Hay un preset más, **"Manual"**, que no declaras tú: se activa solo en
cuanto ajustas la temperatura directamente desde la tarjeta del
termostato (arrastrando la consigna, no eligiendo un preset). Es tan
persistente como cualquier otro — se queda con esa temperatura hasta que
tú mismo cambies a otro preset o vuelvas a "Automático", no caduca sola.
No tiene entidad `number.*` propia (la pones tú directo en el
termostato), pero se restaura igual tras un reinicio.

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

## 9. Modo "Auto" (compatible con Matter), además de calor/frío por separado

Una zona con calor y frío de verdad (los dos, no un solo sentido) expone
CUATRO modos: `off`, `auto`, `heat`, `cool`. En modo "Auto" (el que se
usa por defecto) la zona tiene DOS consignas activas a la vez (la de
calor y la de frío del preset activo, ver punto 5): calienta si baja de
la de calor, enfría si sube de la de frío, y no hace nada entre medias —
es exactamente el System Mode "Auto" estándar de Matter (consigna baja +
consigna alta), así que cualquier puente Matter/HomeKit lo reconoce sin
traducciones ni configuración aparte. Si prefieres bloquear la zona a
mano a "solo calor" o "solo frío" (p.ej. apagar la refrigeración en
invierno), sigues pudiendo elegir esos modos por separado. Una zona de un
solo sentido (solo calor, o solo frío) sigue ofreciendo únicamente ese
modo — "Auto" no tendría sentido ahí.

## 10. Límites de seguridad

Configurables por zona (paso 6 del asistente): un mínimo que la
calefacción siempre respeta y un máximo que la refrigeración siempre
respeta, sea cual sea el preset activo, la presencia o el modo manual.
Pensado exactamente para "no me importa que no haya nadie, nunca por
debajo de X en invierno / nunca por encima de X en verano".

## 11. Modo vs. preset vs. temperatura

Aquí ya no hay ninguna anulación con caducidad — los tres son elecciones
**persistentes**, cada una se restaura sola tras un reinicio:

- **Cambiar el MODO** (apagado / auto — o apagado / calor en una zona de
  un solo sentido, ver punto 9).
- **Cambiar el PRESET** a mano, cualquiera de los declarados o
  "Automático" (ver punto 5).
- **Ajustar la TEMPERATURA** directamente desde la tarjeta del
  termostato: pasa la zona al preset "Manual" (ver punto 5) — se queda
  con esa temperatura hasta que tú mismo cambies a otro preset.

## 12. Inercia térmica aprendida

Se aprende de los dos tipos de actuador, no solo de switch: con un
`switch.*` propio se usa directamente su historial de encendido/apagado;
con un `climate.*` delegado se usa el atributo `hvac_action` de SU
historial (heating/cooling frente a idle/off) — la mayoría de
integraciones de clima lo publican. Si una entidad concreta nunca lo
reporta, esa zona se queda con valores conservadores por defecto (marcado
`thermal_model_reliable: false` en los atributos de la entidad) — nunca
se inventa una cifra.

## 13. Modo simulación

Con "Modo simulación" activo en una zona (por defecto), la integración
calcula y publica lo que haría (visible en los atributos de la entidad),
pero nunca manda una orden real a ningún actuador. Revisa unos días el
atributo `reason` antes de desactivarlo.
