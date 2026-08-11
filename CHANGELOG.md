# Changelog

## 0.10.8
Un episodio real ocurrió ~15 min después de reiniciar con v0.10.7 ya activa — reveló dos huecos que el reparto de v0.10.7 no cubría, ambos concentrados justo en el arranque en frío (la ventana de más riesgo: nada es "reliable" todavía, la máquina está levantando el resto de integraciones a la vez).
- **Arreglo importante**: v0.10.7 repartía en el tiempo el temporizador PERIÓDICO de cada zona, pero el PRIMER cálculo (el que se dispara nada más darse de alta la zona, dentro de `async_added_to_hass`) seguía disparándose sin ningún reparto — y es el más caro de todos, porque ningún modelo es fiable todavía. Con varias zonas dándose de alta en el mismo arranque de HA, todas seguían lanzando su primer cálculo completo casi al mismo instante. Ahora también se reparte (0-60s, una ventana corta para no perder capacidad de respuesta real al arrancar, solo evitar la ráfaga).
- Mejora: el escaneo de "qué actuadores de las DEMÁS zonas estuvieron encendidos" (`power_model._active_intervals`, para descartar solapamiento con una máquina exterior compartida) se repetía entero cada vez que CUALQUIER zona necesitaba aprender su potencia — con varias zonas, aunque ya repartidas en el tiempo, podían seguir coincidiendo dentro de una franja de minutos. Ahora se cachea a nivel de proceso (compartido entre todas las zonas, no por zona) durante 30 minutos.

## 0.10.7
Tras migrar HAOS de una RPi5 a un i7 de 8 núcleos intentando resolver el patrón de cuelgues intermitentes de HA Core (v0.10.6), seguía pasando — señal de que no era falta de CPU/E-disco bruta (un i7 de 8 núcleos no debería sudar con esto), sino contención de bloqueos.
- **Arreglo importante**: todas las zonas se dan de alta en HA casi a la vez al arrancar (mismo momento de `async_added_to_hass`), y `async_track_time_interval` no tiene ningún jitter propio — así que, aunque ya se había reducido la FRECUENCIA del recálculo de modelos en v0.10.6, cuando SÍ tocaba recalcular, todas las zonas seguían cayendo en el mismo instante exacto, sin excepción, para siempre. Varias zonas escaneando días de histórico A LA VEZ contra el mismo fichero SQLite (el recorder de HA por defecto) se serializan entre sí — SQLite solo permite una escritura a la vez, y una lectura larga puede bloquearla mientras dura — algo que ningún número de núcleos paraleliza, porque el cuello de botella es el propio motor de la base de datos, no la CPU.
  - Ahora cada zona arranca su temporizador periódico con un desfase ESTABLE (deriva del propio `entry_id`, repartido uniformemente dentro de la ventana de `forecast_refresh_minutes`; el mismo desfase siempre, también tras un reinicio) — con varias zonas, sus recálculos quedan repartidos en el tiempo en vez de formar una ráfaga sincronizada en cada ciclo.

## 0.10.6
**Causa raíz confirmada en producción** del patrón de cuelgues intermitentes de HA Core en RPi5: aislado el problema desactivando Battery Orchestrator primero (sin efecto, seguía pasando) y Climate Orchestrator después (se resolvió al instante) — con un log de monitorización externa (Pangolin) mostrando el patrón exacto: caídas de "unhealthy" cada ~8-10 minutos, justo el periodo de `forecast_refresh_minutes` (10 min por defecto).
- **Arreglo importante de rendimiento**: `_async_refresh_forecast` (disparado cada `forecast_refresh_minutes`) recalculaba el modelo térmico (`thermal_model.async_get_model`) y el de potencia (`power_model.async_get_power_model`) **desde cero en cada disparo**, sin importar si ya eran fiables — cada uno escanea hasta `history_days_for_inertia` (14 días por defecto) de histórico del recorder de VARIAS entidades (sensor de temperatura, exterior, cada actuador...), y el modelo de potencia además escanea el histórico de los actuadores de TODAS las demás zonas declaradas para descartar solapamiento con una máquina exterior compartida. Con varias zonas, eso son varios escaneos de varios días de recorder en paralelo cada 10 minutos, sin parar — carga real y periódica de CPU/E-disco sobre un dispositivo limitado.
  - Las propiedades físicas que aprenden estos modelos (inercia térmica del edificio, consumo típico de un actuador) no cambian de un ciclo a otro, a diferencia de la previsión meteorológica exterior (que sí conviene refrescar cada `forecast_refresh_minutes`, eso no cambia). Ahora, una vez el modelo ya es fiable (`_models_settled`), el recálculo real se espacia a como mucho una vez cada 6 horas; mientras una zona todavía no tiene histórico suficiente (recién creada, o con un actuador nuevo sin aprender), se sigue intentando en cada ciclo normal para converger lo antes posible — nunca se retrasa la primera vez que hace falta un dato nuevo.

## 0.10.5
Investigando un incidente real en producción: HA Core en una RPi5 se reiniciaba repetidamente por "watchdog missed API response" — no un fallo, sino Core genuinamente ocupado/sin responder al ping de salud del Supervisor. Los logs del host mostraron el contenedor de Core acumulando 10-18GB leídos y 2-3GB escritos en disco en cada ventana de ~1.5-2h antes de cada reinicio: E/S de disco real y sostenida, compatible con escrituras excesivas al recorder de HA.
- **Arreglo importante de rendimiento**: `async_write_ha_state()` (lo que graba el estado y los atributos de la zona en el recorder) se llamaba SIN NINGÚN límite en cada evento reactivo — y `current_temperature` viene de una media móvil exponencial con 2 decimales que prácticamente SIEMPRE cambia un poco en cada lectura nueva del sensor, así que el filtro de "sin cambios" que Home Assistant aplica por defecto antes de escribir casi nunca llegaba a actuar. Con varios sensores vigilados por zona (temperatura, humedad, potencia de cada actuador...) reportando cada pocos segundos, esto podía traducirse en varias escrituras al recorder por minuto y por zona — carga de E/S real e innecesaria, multiplicada por cuantas zonas haya declaradas.
  - Ahora (`_maybe_write_ha_state`) se escribe AL INSTANTE, sin ningún retraso, en cuanto cambia algo que de verdad importa (disponibilidad, acción real, modo, o el motivo en texto plano) — nunca se retrasa una acción real. Si lo único que cambió desde la última escritura es el jitter numérico de la temperatura suavizada, se agrupa como mucho una vez cada 20 segundos.
  - No hay certeza absoluta de que esto sea la causa completa del incidente (el propio host tenía además varias construcciones Docker de otros add-ons compitiendo por recursos en la misma ventana), pero es una reducción real y verificable de la carga que esta integración impone sobre el recorder, en la misma línea que las optimizaciones ya aplicadas en Battery Orchestrator (v0.11.18-v0.11.21) para el mismo síntoma.

## 0.10.4
Revisión a fondo de la lógica de cambio y asignación de modos dentro de Auto (heat_cool): se detectó que la deshumidificación bajaba la temperatura por debajo de la consigna y disparaba gasto de más. Se encontraron y corrigieron varios problemas reales de eficiencia:
- **Arreglo importante**: `_smart_idle_action` activaba "dry" (deshumidificar) en cuanto la humedad superaba el umbral, sin mirar lo cerca que estuviera la zona de su consigna de calor. En la mayoría de aires acondicionados reales, "dry" enfría como efecto secundario (el compresor sigue funcionando, a menos velocidad) — si la zona estaba cerca del límite inferior de confort, deshumidificar la empujaba por debajo y obligaba a calentar justo después: se pagaba la energía de deshumidificar Y la de corregir el sobreenfriamiento causado. Ahora "dry" solo se permite con margen de sobra (un `deadband` entero) por encima de la consigna de calor; sin consigna de calor (zona solo frío) no cambia nada.
- **Arreglo importante**: el anti-ciclado de los switches (`CONF_MIN_ON_SECONDS`/`CONF_MIN_OFF_SECONDS`, 5 min por defecto) podía dejar un switch de calor "atascado" encendido mientras el de frío arrancaba en paralelo, si Auto cambiaba de lado antes de que pasara ese tiempo mínimo — calentando y enfriando la misma zona a la vez, el peor derroche posible. Ahora apagar un lado porque el otro acaba de activarse de verdad salta el anti-ciclado (igual que ya hacía el aviso de puerta/ventana abierta) — nunca fuerza el encendido, solo el apagado del lado que ya no toca.
- Arreglo: `thermal_model.py` combinaba mal los tramos "apagado" de calor y frío para aprender la pérdida térmica pasiva de la zona (`idle_loss_coeff`) — usaba `heat_runs or cool_runs` (elige uno de los dos, descartando el otro entero) en vez de `heat_runs + cool_runs` (los junta). Con eso se tiraban a la basura la mitad de las muestras disponibles sin motivo; ese coeficiente alimenta directamente la anticipación (`scheduler._anticipate`), así que una estimación peor la dispara demasiado pronto o demasiado tarde.
- Nuevo: `presets.parse_presets` ahora rechaza un preset con la consigna de calor igual o por encima de la de frío (p.ej. escribir "25/21" en vez de "21/25") — con eso, Auto no encontraría nunca una temperatura real que no pidiera calor y frío a la vez, sin importar cuál. Se valida al declarar el preset en la configuración.
- Nuevo: red de seguridad EN VIVO en `scheduler.decide_action` para el mismo caso — las consignas number.* son editables en caliente desde Lovelace o una automatización, así que la validación de arriba no cubre cambios después de creada la zona. Si ocurre, la zona pasa a "idle" con un motivo claro en vez de perseguir dos consignas imposibles.

## 0.10.3
- Nuevo: **banco de confort térmico por precio** (`scheduler._price_anticipation_preheat`), además del ya existente por excedente solar instantáneo (`_opportunistic_preheat`). Ahora, en prioridad "ahorro", si el pronóstico que Battery Orchestrator ya calcula para sí mismo (atributo `forecast` de `sensor.battery_orchestrator_grid_signal`, hasta 48h vista) muestra una hora punta próxima (hasta 4h vista) sin excedente solar que la cubra, y la hora actual todavía no lo es, la zona adelanta la actuación usando su inercia térmica como depósito de confort gratis mientras todavía es barato — en vez de esperar reactivamente a que llegue la hora cara. Nunca cruza `min_temp`/`max_temp` ni se dispara si la hora punta ya es ahora mismo (ese caso ya lo cubre el margen recortado de `_economic_factor`). Sin Battery Orchestrator instalado, o sin pronóstico todavía, el comportamiento es exactamente el de antes.
  - El pronóstico se consume solo en memoria para esta decisión — a propósito NO se expone como atributo de la entidad `climate.*` (a diferencia del resto de la señal de red, que sí es diagnóstico visible): son hasta 48 elementos que cambiarían en cada publicación de Battery Orchestrator, y grabarlo entero en el recorder por cada zona en cada ciclo repetiría el mismo derroche de recursos que se corrigió en Battery Orchestrator (ver su v0.11.18/0.11.19).

## 0.10.2
- Arreglo: `zone_power_w` (el consumo de la zona, usado por la prevención de sobrecarga `CONF_MAX_POWER_W`, el banco de confort térmico y lo que se comparte con Battery Orchestrator) sumaba también el humidificador — una entidad secundaria de la zona, no parte del calor/frío en sí. Confirmado en producción: una zona mostraba 215W (solo el humidificador aprendido) mientras el aire acondicionado, que era el que de verdad estaba enfriando, todavía no tenía dato propio — dando una cifra real pero engañosa. Ahora el cálculo de consumo de la zona (`_zone_power_w`, `_zone_estimated_power_w`, y el aprendizaje de `power_model.py`) usa solo los actuadores de calor/frío declarados (`heat_switches`/`cool_switches`/`climate_entities`), nunca el humidificador.

## 0.10.1
- Nuevo: `CONF_HOME_POWER_SENSOR` (el sensor general de consumo de la casa que usa `power_model.py` para aprender el consumo de cada actuador) ya no hace falta declararlo a mano si Battery Orchestrator está instalado — se detecta automáticamente el que ese addon ya tiene declarado (`sensor.battery_orchestrator_grid_signal`, atributo `home_power_sensor`). El campo declarado a mano sigue teniendo prioridad si existe; sin ninguno de los dos, no se aprende nada, como antes.

## 0.10.0

- Nuevo: integración automática con **Battery Orchestrator** (si está instalado), sin ninguna configuración manual en ningún lado — se detecta por un `entity_id` fijo (`sensor.battery_orchestrator_grid_signal`), y esta zona se marca a sí misma para que Battery Orchestrator también la encuentre sola.
  - **Prioridad "ahorro" con datos económicos reales**: hasta ahora "ahorro" solo ensanchaba el margen de confort según la previsión meteorológica exterior. Ahora también recorta ese margen según el tramo de tarifa y el excedente solar disponible AHORA MISMO (`_economic_factor`, `scheduler.py`) — margen mínimo en punta sin sol, margen completo si el excedente solar cubre la zona, sea cual sea la meteo.
  - **Banco de confort térmico**: si hay excedente solar de sobra para la zona, adelanta hasta `deadband` de más la actuación aprovechando la inercia térmica del edificio como depósito de confort gratis antes de la próxima hora cara (`_opportunistic_preheat`) — nunca cruza los límites de seguridad `min_temp`/`max_temp`.
  - Reacciona AL INSTANTE a cambios de la señal de Battery Orchestrator (mismo mecanismo `async_track_state_change_event` que ya usa para la meteo exterior), no espera al ciclo periódico.
  - Sin Battery Orchestrator instalado, el comportamiento es exactamente el de antes — nada de esto es obligatorio.

## 0.9.0 (sin publicar — implementado, pendiente de revisión)

- **Control proporcional TPI para switches**: en vez de un simple
  on/off, cada switch se enciende un porcentaje del ciclo
  (`tpi_cycle_minutes`, 15 min por defecto) proporcional a cuánto falta
  para la consigna (`scheduler.tpi_on_percent`) — más suave, menos
  ciclos de encendido/apagado. Solo afecta a switches, nunca a
  `climate.*` delegados. Coeficientes (`TPI_COEF_INT`/`TPI_COEF_EXT`)
  fijos por ahora, no configurables. El anti-ciclado
  (`min_on_seconds`/`min_off_seconds`) se sigue respetando por debajo.
  Nuevos atributos `tpi_heat_on_percent`/`tpi_cool_on_percent`.
- **Consumo eléctrico rediseñado: por actuador, no por zona** (cambio
  incompatible con la 0.8.0 — `power_entities`/`estimated_power_w`
  desaparecen, sustituidos por `actuator_power`, un valor por cada
  actuador). Una misma zona puede tener un equipo sin forma de medir su
  consumo real (aire acondicionado con máquina exterior compartida) y
  otro con su propio sensor — cada uno con su propia fuente: sensor
  propio (`measured`), potencia fija estimada de su ficha técnica
  (`estimated`), o **aprendida** de un sensor de potencia general de la
  vivienda (`learned`, ver `power_model.py`) correlacionando sus
  transiciones on/off con el salto visto en ese sensor — **descartando
  muestras que coincidan con otra zona Climate Orchestrator ya activa**,
  para no mezclar el consumo de dos equipos que comparten máquina
  exterior. Nuevo paso "Consumo por actuador" (dinámico, uno por
  actuador declarado) en el asistente y en "Configurar". Nuevos
  atributos `zone_power_w`/`zone_power_breakdown`.

## 0.8.0

Revisado a fondo el código de versatile_thermostat buscando mejoras de
eficiencia y corrección de errores. Adoptadas, simplificadas al espíritu
de esta integración (deterministas, transparentes, opcionales cuando
implican inferencia):

- **Suavizado EMA del sensor externo** — un pico de ruido puntual ya no
  hace que la zona cambie de decisión de golpe.
- **Vigilancia de sensor "congelado"**: si el sensor externo deja de
  actualizarse de verdad (sin llegar a marcarse `unavailable`), se sigue
  confiando en la última lectura suavizada hasta 90 minutos — antes se
  perdía de golpe la protección de límites de seguridad.
- **Detección automática de ventana abierta** sin sensor dedicado
  (opcional, desactivada por defecto): respaldo por caída/subida anómala
  de temperatura para ventanas sin sensor propio.
- **Detección de posible fallo del equipo**: aviso si se lleva 30 min
  pidiendo calor/frío sin que la temperatura se mueva lo esperado.
- **Consumo eléctrico y potencia máxima** (opcional): sensores de
  potencia por zona sumados en vivo, con prevención simple de sobrecarga
  (no arranca actuadores nuevos si ya se está al límite).
- **Menú de configuración por categorías**, más fácil de usar: "Configurar"
  ahora abre un menú (General, Actuadores, Presets, Límites de seguridad,
  Presencia y ventana, Avanzado) en vez de un único formulario gigante
  con ~25 campos a la vez.

Pendiente para una próxima versión: TPI (proporcional, no solo on/off)
para switches — el cambio de motor más grande, se aborda aparte.

## 0.7.1

- Corregido: la zona no arrancaba — `ImportError: cannot import name
  'ATTR_HUMIDITY' from 'homeassistant.const'`. `ATTR_HUMIDITY` vive en
  `homeassistant.components.climate.const` (igual que
  `ATTR_TARGET_TEMP_LOW`/`HIGH`, ya importados de ahí), no en
  `homeassistant.const`. Bug introducido en 0.7.0 (humidificación).

## 0.7.0

- **Nuevo: humidificación**. Una zona puede delegar en entidades
  `humidifier.*` ya existentes (paso 3 del asistente, opcional) para
  humidificar de verdad. No es un modo más: es una función nativa y
  paralela del propio termostato de la zona
  (`ClimateEntityFeature.TARGET_HUMIDITY`, con `target_humidity`/
  `current_humidity` ajustables desde la misma tarjeta), activa siempre
  que la zona no esté apagada ni en pausa por puerta/ventana, sea cual
  sea el hvac_mode concreto. Consigna única por zona (no por preset),
  configurable en "Configurar" o ajustable al vuelo desde la tarjeta.
  Cada `humidifier.*` delegado se enciende con esa consigna y se deja
  que su propia lógica interna decida cuándo humidificar — mismo
  espíritu que el reposo mantenido de los `climate.*` delegados (0.6.0).
- Corregido de paso: el sensor de humedad (`CONF_HUMIDITY_SENSOR`) ahora
  también está en la lista de sensores escuchados — antes solo se leía
  en el siguiente ciclo, sin reaccionar al instante a sus cambios.

## 0.6.1

- Nuevo atributo `outdoor_forecast` en la entidad de la zona: la
  previsión exterior hora a hora tal cual la usa el motor de
  anticipación (`scheduler.py`), para poder comprobar de verdad si está
  entrando una previsión real o una degradada a valor constante (ver
  punto 8 de la guía).

## 0.6.0

- **Corrección de desviación confirmada como continua y en vivo**: ya lo
  era (reacciona al bus de eventos de HA, no a un sondeo) — aclarado en
  la documentación para que quede explícito, sin añadir ningún
  temporizador nuevo.
- **Nuevo: reposo aprendido por delegado**. Al llegar a la consigna, un
  `climate.*` delegado ya NO se apaga por defecto — se mantiene en su
  último modo activo con la consigna siempre corregida en vivo, dejando
  que se autorregule solo (menos ciclos de encendido/apagado). Si un
  delegado en concreto no sabe pararse solo (sigue calentando/enfriando
  de más aunque ya debería estar satisfecho), Climate Orchestrator lo
  aprende en vivo — un contador simple de comportamiento observado, nunca
  un modelo entrenado — y a partir de ahí lo apaga de verdad. Por
  delegado, no por zona; persiste tras reinicios. Nuevo atributo
  `delegate_needs_explicit_off`.
- Puerta/ventana abierta y apagado a mano siguen siendo apagado real
  siempre, sin excepción — el reposo aprendido nunca interviene ahí.

## 0.5.0

**Nuevo: corrección de desviación del sensor en climate.\* delegados.**
Un aire acondicionado o válvula termostática decide él solo cuándo darse
por satisfecho según su propio sensor interno, que casi nunca coincide
con el sensor externo de la zona (ubicación, calibración...). Ahora, en
cada ciclo, se mide esa desviación en vivo y se corrige la consigna real
antes de mandársela al delegado — así se satisface justo cuando el
sensor externo (el que de verdad gobierna la zona) también lo haría,
recortada al rango que el propio delegado admite. Sin desviación
detectable (el delegado no reporta su propia temperatura, o el sensor
externo no está disponible), se manda la consigna real sin tocar.

- Nuevo atributo `delegate_temperature_deviations` en la entidad de la
  zona: la desviación medida ahora mismo por cada `climate.*` delegado,
  para poder revisarla.
- Sin configuración nueva que ajustar — se aplica sola, con
  degradación segura si no hay datos suficientes.

## 0.4.1

Corrección sobre la 0.4.0: se me fue la mano quitando `dry`/`fan_only`
del todo como modos elegibles — no era eso lo pedido. Quedan dos caminos
bien distintos, tal cual se pidió:

- **A mano**: elegir "Deshumidificar"/"Solo ventilador" desde la tarjeta
  del termostato SIGUE cambiando el modo de la zona a eso (como
  cualquier otro modo) y se manda tal cual al equipo que lo soporte.
- **Reposo inteligente automático** (sin interruptor, ver 0.4.0): sigue
  coexistiendo solo con el modo más automático de la zona, pero el
  hvac_mode de la zona NUNCA cambia en este caso — solo la orden que
  recibe el delegado. Esto no se tocó, ya estaba bien desde la 0.4.0.

## 0.4.0

Corrección de diseño tras probarlo en real: `dry`/`fan_only` **ya no son
un modo seleccionable de la zona**. El hvac_mode de la propia integración
nunca sale de apagado/Auto/calor/frío — seguían apareciendo en el
desplegable del termostato y, al elegirlos, sacaban la zona de "Auto" de
verdad, perdiendo la gestión de temperatura mientras tanto. Ahora
`dry`/`fan_only` son solo algo que el **reposo inteligente** le manda
directo al `climate.*` delegado (si lo soporta; si no, se apaga) sin que
el modo de la zona cambie nunca.

- **Quitados los interruptores de "Reposo inteligente: ventilar" y
  "...deshumidificar"**: ya no hace falta activarlos a mano — coexiste
  solo con el modo más automático que tenga la zona (Auto en una con
  calor y frío de verdad; el único modo de una de un solo sentido), que
  ya es "decidir sola entre todo lo disponible", la misma idea. Si
  bloqueas la zona a mano a "solo calor"/"solo frío" en una que también
  tiene el otro lado, el reposo inteligente se aparta — está claro que
  quieres control manual.
- El umbral de humedad para deshumidificar sigue siendo configurable por
  zona (65% por defecto).
- Documentación actualizada (ES/EN): quitado el apartado "a mano" de
  dry/fan_only, reescrita la sección de reposo inteligente.

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
