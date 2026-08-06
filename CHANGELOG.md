# Changelog

## 0.3.3

Revisada la especificación real del clúster Thermostat de Matter (y del
mapeo que hace `home-assistant-matter-hub` de entidades `climate.*` de HA
a Matter) para confirmar y terminar de estandarizar los modos hvac:

- Confirmado: el mapeo `off/heat/cool/heat_cool→Auto/dry/fan_only` ya
  coincidía exactamente con el `SystemMode` estándar de Matter — sin
  cambios ahí.
- **Nuevo**: cuando la zona ofrece algo más que "apagado", se declara
  `TURN_ON`/`TURN_OFF` — así HA (y cualquier puente Matter/HomeKit/Google
  Home) trata el apagado como un interruptor real, no un modo más
  escondido en un desplegable. Encender vuelve al último modo que tenía
  la zona (si estaba bloqueada a "solo calor", sigue en "solo calor" — no
  salta a "Auto").
- Corregido: `hvac_action` decía "idle" (en espera) incluso con la zona
  apagada de verdad — ahora distingue "apagado" (`off`) de "encendida,
  dentro de margen, sin nada que hacer ahora mismo" (`idle`), como
  esperan HA y cualquier puente Matter/HomeKit.
- Aviso añadido a la documentación: Apple Home/Google Home/Alexa suelen
  limitar lo que MUESTRAN de un termostato a Calor/Frío/Auto/Apagado —
  puede que `dry`/`fan_only` no aparezcan como botón ahí aunque el dato
  viaje bien por Matter.

## 0.3.2

- La humedad del sensor declarado en la zona (paso Sensores) ahora se
  publica como atributo nativo `current_humidity` del `climate.*` — antes
  se leía internamente (reposo inteligente) pero no se mostraba en
  ningún sitio.

## 0.3.1

- Corregido: "Configurar" (editar una zona ya creada) se caía con `500
  Internal Server Error` / "no se pudo cargar el flujo de configuración".
  Causa: `ClimateOrchestratorOptionsFlow` fijaba `self.config_entry` a
  mano en su propio `__init__`, un patrón que versiones recientes de Home
  Assistant ya no permiten (esa clase gestiona `config_entry` ella sola,
  como propiedad). Quitado el constructor propio — se usa tal cual.
  Si además tenías el aviso de puerta/ventana sin cortar de verdad, esto
  puede ser la causa real: sin poder abrir "Configurar" no había forma de
  comprobar ni desactivar "Modo simulación".

## 0.3.0

- **Reconocimiento de modos hvac más allá de calor/frío**: hasta ahora
  solo se detectaba `heat`/`cool` de cada `climate.*` delegado. Si tu aire
  acondicionado también declara `dry` (deshumidificar) o `fan_only` (solo
  ventilador) en sus propios `hvac_modes`, la zona los hereda igual — un
  radiador que solo sepa `off`/`heat` sigue sin aportar nada más. Se
  eligen a mano desde la tarjeta del termostato como cualquier otro modo;
  no persiguen ninguna temperatura, se relegan directos al equipo que de
  verdad los soporte.
- **Nuevo: reposo inteligente (opcional, desactivado por defecto)**. En
  vez de apagar del todo cuando la zona ya está dentro de margen (ni hace
  falta calor ni frío), un `climate.*` delegado que también sepa ventilar
  o deshumidificar puede usarse solo: ventilar por comodidad, o
  deshumidificar si la humedad medida supera un umbral — configurable,
  igual que cualquier otro límite de la zona. Ninguno de los dos sustituye
  nunca a calor/frío cuando de verdad hacen falta. Se activa por zona en
  "Configurar" → nuevas opciones "Reposo inteligente".

## 0.2.6

- Corregido: al abrirse una puerta/ventana, la zona pasaba a "idle" pero
  el apagado real del switch de calor/frío seguía respetando el
  anti-ciclado normal (tiempo mínimo encendido, 300s por defecto) — un
  radiador que se acababa de encender se quedaba calentando con la
  ventana abierta hasta agotar ese margen. Ahora el aviso de
  puerta/ventana salta el anti-ciclado por completo: corta ya. Al
  cerrarse, la zona retoma el cálculo normal sola (la propia entidad ya
  estaba en la lista de sensores escuchados, sin cambios ahí).

## 0.2.5

- **Nuevo preset "Manual"**: ajustar la temperatura directamente desde la
  tarjeta del termostato (sin elegir ningún preset) ya no es una
  anulación con caducidad (2h por defecto) — pasa la zona al preset
  "Manual", tan persistente como cualquier otro: se queda con esa
  temperatura hasta que tú mismo cambies a otro preset o vuelvas a
  "Automático". Se restaura tras un reinicio igual que el resto. Quitado
  el ajuste ahora sin uso "duración de la anulación manual de
  temperatura" del asistente.

## 0.2.4

- Regenerado `icon.png`/`icon@2x.png` para cumplir de verdad la
  especificación de `home-assistant/brands` (sigue siendo la referencia
  técnica aunque ya no haga falta enviarlo allí): 256×256 y 512×512,
  borde a borde sin redondear yo mismo las esquinas (HA aplica su propio
  recorte en el frontend). Quitados `logo.png`/`logo@2x.png` — al ser
  nuestra marca cuadrada (sin un logotipo apaisado distinto), la propia
  norma dice que basta con el icono: se usa como fallback del logo
  automáticamente.

## 0.2.3

- El icono de la integración (buscador de "Añadir integración", página de
  Dispositivos y servicios) ya no depende de un envío externo a
  `home-assistant/brands` — se corrige lo dicho en la 0.2.2. Desde HA
  2026.3, una integración puede servir su propio `icon.png`/`logo.png`
  (más variantes `@2x`/`dark_*` opcionales) desde una carpeta `brand/`
  dentro de la propia integración; HA los expone solo en local via
  `/api/brands/integration/{domain}/icon.png`, sin curación externa ni
  espera de revisión. Movidos los assets a
  `custom_components/climate_orchestrator/brand/`.

## 0.2.2

- Corregido de verdad el problema de la carrera de arranque: el intento
  de la v0.2.1 no bastaba porque un "apagado" grabado en el historial
  DURANTE una carrera se restauraba luego en cualquier reinicio
  posterior, incluso uno sin carrera — perpetuando el bug para siempre.
  Ahora, mientras no se detecta ningún actuador todavía, la entidad se
  marca "no disponible" en vez de escribir un modo resuelto (nunca se
  graba nada que un reinicio futuro pueda restaurar como si fuera real).
  Además, ahora también se escuchan los propios `climate.*` delegados: en
  cuanto uno aparece, la zona reacciona al instante en vez de esperar al
  próximo refresco periódico (hasta 10 minutos).
- Iconos: entidades `number.*` de cada preset con icono propio
  (radiador/copo de nieve según el lado), icono de termostato en la
  entidad `climate.*`, y logo del proyecto añadido al README. El icono
  que aparece en el buscador de "Añadir integración" de Home Assistant
  depende del repositorio oficial `home-assistant/brands` (un envío
  aparte, pendiente).

## 0.2.1

- Corregido: si un `climate.*` delegado tardaba en cargar más que esta
  integración (carrera de arranque), la zona se quedaba forzada a
  "apagado" para siempre, aunque el actuador apareciera después — nada
  volvía a proponerle un modo sensato. Ahora se reintenta al añadirse la
  entidad y en cada refresco periódico hasta que se detecta capacidad
  real por primera vez.
- Corregido: una zona con calor y frío de verdad solo exponía
  `off`/`auto`. "Auto" es una opción MÁS, no sustituye a poder bloquear
  la zona a mano a "solo calor" o "solo frío" — ahora expone los cuatro
  modos.

## 0.2.0 — primera versión publicada

La primera versión de este proyecto fue un add-on Flask externo con MQTT
Discovery. Se descartó antes de publicarse de verdad: un add-on solo
puede sondear Home Assistant o depender de MQTT/websockets de repuesto
para reaccionar rápido, mientras que una integración vive dentro del
propio bus de eventos de HA. Esta es la reescritura completa como
`custom_component` instalable vía HACS — la primera versión con un
release de verdad (antes de esta, cada cambio se empujaba directo a
`main` sin etiquetar, así que HACS no podía ofrecer una actualización).

- Una entrada de integración = una zona (config_flow con asistente
  guiado + options flow para editar), mismo patrón que
  `versatile_thermostat`.
- **Presets con nombre en vez de horario fijo, configurables como
  entidades** ("Confort: 21/25, Ausente: 17/28, Fiesta: 23/24"...), cada
  uno con su consigna de calor ("invierno") y de frío ("verano") por
  separado, expuestas como entidades `number.*` propias (ajustables desde
  Lovelace o una automatización, sin volver a "Configurar" — ver
  number.py). Se activan automáticamente por presencia FÍSICA real de la
  zona (sensores PIR/mmWave, no solo "en casa") o se fijan a mano de
  forma persistente — el horario/franjas se eliminó a propósito: no sabe
  si hay alguien de verdad en la habitación.
- **Modo "Auto" único, compatible con el System Mode estándar de
  Matter**: una zona con calor y frío de verdad ya no deja bloquear a
  mano "solo calor"/"solo frío" — expone únicamente `off`/`auto`, con
  doble consigna simultánea (baja de calor, alta de frío) como cualquier
  termostato Auto estándar. `scheduler.decide_action` pasó a recibir las
  dos consignas directamente en vez de una sola temperatura + una
  capacidad declarada.
- **Motor de decisión continuo** (no un plan de día completo): reactivo
  ante temperatura/presencia/puerta-ventana, y con **anticipación al
  clima exterior** — si la previsión indica un cambio de temperatura
  próximo, empieza a actuar de forma sostenida con antelación (usando la
  inercia térmica aprendida) en vez de esperar a salirse de rango.
  Prioridad "ahorro" además ensancha el margen de histéresis cuando la
  previsión es estable. Nunca se predice presencia, solo clima
  (observable).
- **Límites de seguridad siempre activos** (mínimo/máximo por zona,
  independientes del preset o la presencia) — reemplaza la protección
  relativa anti-heladas/anti-golpe-de-calor de la primera revisión.
- Reacción instantánea a temperatura, presencia y puertas/ventanas vía
  `async_track_state_change_event` (bus de eventos de HA, no sondeo).
- Aprendizaje de inercia térmica desde el recorder de Home Assistant,
  para ambos tipos de actuador (switch propio, o climate.* delegado via
  su atributo `hvac_action`).
- **Capacidad (calor/frío/ambos) detectada, no declarada a mano**: se
  añaden los actuadores que de verdad existen — cuantos `climate.*`
  delegados quieras (gobernados por SUS PROPIOS `hvac_modes` nativos, sin
  campos separados de calor/frío) más listas de switches de calor y de
  frío, tantos como se quiera de cada uno — y la zona calcula sola qué
  puede hacer y qué modos expone a HA/Matter/HomeKit. Un radiador con
  válvula termostática y un aire acondicionado conviven en la misma zona
  sin declarar nada más; una bomba de calor reversible se añade una única
  vez y recibe el modo correcto según la estación, nunca dos órdenes que
  se pisen.
- Modo (apagado/auto, o apagado/calor en zonas de un solo sentido) y
  preset activo persistentes vía RestoreEntity; temperatura objetivo como
  anulación temporal con caducidad configurable.
- Modo simulación por zona, activo por defecto.
- Corregido: el selector de entidades del asistente podía quedarse vacío
  y sin filtrar al escribir (`device_class=None` explícito rompía el
  picker en el frontend).
- Corregido: `PresetNumber` tumbaba el arranque de la zona entera con un
  `TypeError` de MRO al heredar de `NumberEntity` y `RestoreNumber` a la
  vez (`RestoreNumber` ya extiende `NumberEntity`).
