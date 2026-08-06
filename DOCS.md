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
  lista, y cada entidad aporta lo que de verdad sepa hacer. Esto no se
  limita a calor/frío: si el equipo declara también `dry`
  (deshumidificar) o `fan_only` (solo ventilador), la zona los hereda
  igual — ver punto 9.
- **Switches de calefacción** y **switches de refrigeración**: a
  diferencia de un `climate.*`, un switch no puede autodeclarar para qué
  sirve, así que estos sí van en su lista correspondiente. La integración
  los enciende/apaga con histéresis y anti-ciclado (tiempo mínimo
  encendido/apagado).
- **humidifier.\* delegados** (opcional): entidades `humidifier.*` ya
  existentes en las que delegar la humidificación — ver punto 13.

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
tipo switch) y la consigna de humedad objetivo (solo si declaraste
`humidifier_entities` en el paso 3, ver punto 13).

**Paso 7 — Presencia, puertas/ventanas y opciones**: entidades de
presencia, sensores de puerta/ventana, días de histórico para la inercia térmica, frecuencia de
recálculo de la previsión, umbral de humedad para el reposo inteligente
(ver punto 10) y modo simulación.

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

El mapeo de modos hvac de Home Assistant al `SystemMode` del clúster
Thermostat de Matter (el que usa cualquier puente, incluido
`home-assistant-matter-hub`) es directo y no necesita ninguna traducción
de nuestra parte: `off`→Off, `heat`→Heat, `cool`→Cool,
`heat_cool`→**Auto**, `dry`→Dry, `fan_only`→FanOnly — exactamente lo que
esta zona expone (ver punto 10 para cuándo aparecen `dry`/`fan_only`).
Además, siempre que haya algo más que "apagado" que ofrecer, la zona
declara encendido/apagado como un interruptor real (no solo un modo más
en un desplegable) — esto es lo que hace que el botón de
encendido/apagado aparezca en cualquier puente Matter/HomeKit/Google Home
en vez de quedar oculto.

**Aviso honesto**: aunque Matter transporta `dry`/`fan_only` sin
problema, Apple Home, Google Home y Alexa suelen limitar lo que MUESTRAN
de un termostato a Calor/Frío/Auto/Apagado — puede que no aparezcan como
botón en esas apps concretas aunque el dato viaje bien. Desde la propia
Home Assistant (tarjeta del termostato, Lovelace) sí se ven siempre.

## 10. Modos `dry`/`fan_only`: a mano, o automáticos sin salir de Auto

Un `climate.*` delegado puede declarar en sus propios `hvac_modes` algo
más que calor/frío: por ejemplo, muchos aires acondicionados también
soportan `dry` (deshumidificar) o `fan_only` (solo ventilador). Climate
Orchestrator los detecta igual que calor/frío — en vivo, nunca a mano —
y esta zona los añade como modos elegibles. Un radiador que solo declare
`off`/`heat` sigue sin aportar ninguno de los dos. Hay dos formas
distintas de llegar a ellos:

- **A mano**: elegir "Deshumidificar" o "Solo ventilador" desde la
  tarjeta del termostato (o Google Home/Alexa/Matter) SÍ cambia el modo
  de la zona a ese — como cualquier otro modo — y lo manda tal cual al
  equipo que lo soporte; no persigue ninguna temperatura, es una elección
  directa tuya, tan persistente como cualquier otro modo.
- **Reposo inteligente, automático — sin interruptor que activar
  aparte**: en cuanto la zona ya no necesita ni calor ni frío (dentro de
  margen) y **sigue en el modo más automático que tenga** (Auto en una
  zona con calor y frío de verdad; el único modo que le queda a una de un
  solo sentido — si bloqueaste la zona a mano a "solo calor" en una que
  también tiene frío, está claro que quieres control manual, y el reposo
  inteligente no interviene), el equipo delegado puede aprovecharse él
  solo en vez de apagarse del todo:
  - **Ventilar** por comodidad, si el delegado soporta `fan_only`.
  - **Deshumidificar**, con prioridad sobre ventilar, si el delegado
    soporta `dry` y la humedad medida supera un umbral — configurable por
    zona (paso 7 del asistente, o "Configurar"; 65% por defecto), igual
    que cualquier otro límite. Necesita además un sensor de humedad
    declarado en el paso 2.

  **Aquí, a diferencia de la elección a mano, el hvac_mode de la zona
  NUNCA cambia**: sigue "en Auto" de cara al usuario/Matter/HomeKit
  aunque por debajo el equipo esté ventilando o deshumidificando un rato
  — solo cambia la orden que recibe el delegado.

En ambos casos, si el delegado no soporta lo que le toca, se apaga sin
más — nunca se le fuerza nada que no sepa hacer.

## 11. Límites de seguridad

Configurables por zona (paso 6 del asistente): un mínimo que la
calefacción siempre respeta y un máximo que la refrigeración siempre
respeta, sea cual sea el preset activo, la presencia o el modo manual.
Pensado exactamente para "no me importa que no haya nadie, nunca por
debajo de X en invierno / nunca por encima de X en verano".

## 12. Desviación del sensor en climate.\* delegados

Un `climate.*` delegado (un aire acondicionado, una válvula
termostática...) decide él solo cuándo darse por satisfecho según **su
propio sensor interno** — que casi nunca coincide exactamente con el
sensor externo que declaraste para la zona (paso 2): por ubicación,
calibración o simplemente por estar dentro del propio aparato, suele
leer distinto que un sensor de pared. Si se le mandara la consigna real
tal cual, el delegado podría darse por satisfecho antes o después de que
el sensor externo — el que de verdad gobierna esta zona — llegue a esa
temperatura.

Climate Orchestrator lo corrige de forma CONTINUA y EN VIVO — no una vez
al empezar a calentar/enfriar y ya está: mide la desviación AHORA MISMO
entre el sensor propio del delegado (su atributo `current_temperature`)
y el sensor externo de la zona, y se la suma a la consigna real antes de
mandársela, cada vez que cualquiera de los dos sensores actualiza su
lectura de verdad (vía el bus de eventos de HA, nunca un sondeo
periódico — el mismo espíritu reactivo de toda la integración, ver punto
1) — así el delegado se da por satisfecho justo cuando el sensor externo
también lo haría, recortada siempre al rango que el propio delegado
admite (`min_temp`/`max_temp` suyos) para no pedirle nunca algo fuera de
lo que acepta. Queda visible en el atributo
`delegate_temperature_deviations` de la entidad de la zona, uno por cada
`climate.*` delegado. Sin desviación detectable — el delegado no reporta
su propia `current_temperature`, o el sensor externo no está disponible
ahora mismo — se manda la consigna real sin tocar, nunca se inventa una
corrección.

**Al llegar a la consigna, qué hacer con cada delegado (aprendido, no a
mano)**: por defecto, un `climate.*` delegado NO se apaga al llegar a su
consigna — se mantiene en su último modo activo (calor o frío) con la
consigna siempre corregida en vivo, dejando que su propia lógica interna
se autorregule (menos ciclos de encendido/apagado, y con precisión
gracias a la corrección continua). Pero eso asume que el delegado de
verdad sabe pararse solo — si no es así (un equipo sin histéresis interna
real seguiría calentando/enfriando de más aunque ya debería estar
satisfecho), Climate Orchestrator lo detecta EN VIVO: si el sensor
externo sigue desviándose en la dirección equivocada más allá de la
histéresis normal mientras se mantiene encendido, un par de veces
seguidas (no un pico puntual), aprende que ESE delegado en concreto
necesita apagado explícito — y a partir de ahí lo apaga de verdad cada
vez que llegue a su consigna. Esto es por delegado, no por zona (dos
equipos en la misma zona pueden comportarse distinto), y persiste tras
reinicios — visible en el atributo `delegate_needs_explicit_off`. Nada
de caja negra: es un contador simple de comportamiento observado, no un
modelo entrenado.

## 13. Humidificación

Además de calor/frío/dry/fan_only, una zona puede delegar en entidades
`humidifier.*` ya existentes (paso 3 del asistente, opcional) para
humidificar de verdad. A diferencia de todo lo anterior, esto NO es un
modo más: es una función **nativa y paralela** del propio termostato de
la zona — `ClimateEntityFeature.TARGET_HUMIDITY`, con su propio
`target_humidity`/`current_humidity` ajustables desde la misma tarjeta,
igual que la temperatura — que convive con cualquier hvac_mode activo
(Auto, calor, frío...). "Integrada en el funcionamiento automático" en
ese sentido: no hace falta estar en un modo concreto para que actúe, solo
que la zona no esté apagada ni en pausa por puerta/ventana.

- **Consigna única por zona** (no por preset, a diferencia de calor/
  frío): un solo valor de humedad objetivo (45% por defecto),
  configurable en "Configurar" o ajustable al vuelo desde la propia
  tarjeta del termostato — se restaura tras un reinicio igual que
  cualquier otro ajuste hecho así.
- **Cómo se controla**: cuando la zona está activa, cada `humidifier.*`
  delegado se enciende con esa consigna y se deja que su propia lógica
  interna decida cuándo humidificar de verdad — el mismo espíritu que el
  reposo mantenido de los `climate.*` delegados (ver punto 12): no hace
  falta reimplementar la histéresis, un humidificador doméstico normal ya
  sabe pararse solo al llegar a su consigna. Se apaga solo cuando la zona
  está genuinamente apagada o en pausa por puerta/ventana — nunca por una
  decisión de calor/frío.
- Deshumidificar (lo contrario) ya estaba cubierto por el modo `dry` de
  un `climate.*` delegado (ver punto 10) — humidificar rellena el hueco
  que faltaba, subir la humedad cuando está demasiado baja.

## 14. Modo vs. preset vs. temperatura

Aquí ya no hay ninguna anulación con caducidad — los tres son elecciones
**persistentes**, cada una se restaura sola tras un reinicio:

- **Cambiar el MODO** (apagado / auto — o apagado / calor en una zona de
  un solo sentido, ver punto 9).
- **Cambiar el PRESET** a mano, cualquiera de los declarados o
  "Automático" (ver punto 5).
- **Ajustar la TEMPERATURA** directamente desde la tarjeta del
  termostato: pasa la zona al preset "Manual" (ver punto 5) — se queda
  con esa temperatura hasta que tú mismo cambies a otro preset.

## 15. Inercia térmica aprendida

Se aprende de los dos tipos de actuador, no solo de switch: con un
`switch.*` propio se usa directamente su historial de encendido/apagado;
con un `climate.*` delegado se usa el atributo `hvac_action` de SU
historial (heating/cooling frente a idle/off) — la mayoría de
integraciones de clima lo publican. Si una entidad concreta nunca lo
reporta, esa zona se queda con valores conservadores por defecto (marcado
`thermal_model_reliable: false` en los atributos de la entidad) — nunca
se inventa una cifra.

## 16. Modo simulación

Con "Modo simulación" activo en una zona (por defecto), la integración
calcula y publica lo que haría (visible en los atributos de la entidad),
pero nunca manda una orden real a ningún actuador. Revisa unos días el
atributo `reason` antes de desactivarlo.
