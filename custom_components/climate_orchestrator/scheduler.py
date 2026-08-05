"""
Motor de decision de una zona.

Es CONTINUO, no un plan de dia completo — a diferencia de la primera
version de este proyecto (y de Battery Orchestrator), aqui no hay
horario que anticipar: los presets se activan por presencia REAL (ver
presets.py) o a mano, nunca por una franja horaria prevista de antemano.

Pero eliminar el horario NO significa eliminar la anticipacion: el
sistema SI debe adelantarse a cambios de temperatura exterior previstos
— es mas eficiente (y mas comodo) ir ajustando la zona de forma sostenida
mientras todavia hay margen, que esperar a que se salga de rango y tener
que actuar a maxima potencia de golpe. La diferencia con la version
anterior es DE DONDE viene esa anticipacion: antes venia de un horario
(¿cuando sube el nivel programado?), ahora viene UNICAMENTE de la
previsión meteorologica exterior (dato observable, nunca una prediccion
de presencia — eso seria una caja negra, y solo se usa medido en directo).

Nada de programacion lineal: una funcion, unas pocas ramas, cada una con
su motivo en texto plano.

  1. Limites de seguridad de la zona (min_temp/max_temp, ver const.py):
     SIEMPRE se respetan, pase lo que pase con el preset activo, la
     presencia o el modo manual — el "no me importa que no haya nadie,
     nunca por debajo de 12°C en invierno / nunca por encima de 30°C en
     verano" que se pidio explicitamente.

  2. Reactivo: si la zona YA esta fuera de rango del preset activo, actua
     ya. El margen de esta comprobacion es la histéresis declarada en
     prioridad "confort", o un margen mas ancho en "ahorro" (ver
     `_ahorro_extra_margin`, se estrecha solo si la previsión exterior
     empeora en las proximas horas).

  3. Anticipatorio (aplica en "confort" Y "ahorro" por igual — no es una
     cuestion de ahorro, es evitar el golpe de "esperar y luego a tope"):
     si la zona esta DENTRO de rango ahora mismo pero, proyectando su
     deriva pasiva con la previsión exterior y la inercia termica
     aprendida, se preve que se salga de rango en las proximas horas y ya
     no de tiempo a recuperarlo empezando mas tarde, se empieza a actuar
     YA, de forma sostenida — en vez de esperar a que la zona ya se haya
     salido de rango y tener que compensarlo de golpe.

  4. Prioridad "manual": nunca decide, deja el control a la anulacion
     manual o al preset fijado a mano.
"""

from __future__ import annotations

AHORRO_MAX_MARGIN_DEG = 1.5       # cuanto mas ancho puede llegar a ser el margen de "ahorro" frente a "confort", en el mejor de los casos
AHORRO_MARGIN_SENSITIVITY = 0.5   # cuantos °C se recorta ese margen extra por cada °C que empeore la previsión en el horizonte de aviso
AHORRO_LOOKAHEAD_HOURS = 3        # cuantas horas de previsión exterior se miran para juzgar la tendencia (ahorro)
ANTICIPATE_LOOKAHEAD_HOURS = 3    # cuantas horas de previsión exterior se miran para anticipar una salida de rango (confort Y ahorro)
REFERENCE_RATE_DEG_H = 1.0        # tasa de referencia (°C/h) a partir de la cual una zona se considera "rapida" y se le da margen completo


def decide_action(
    current_temp: float,
    heat_target: float | None,
    cool_target: float | None,
    priority: str,
    deadband: float,
    min_temp: float,
    max_temp: float,
    outdoor_now: float | None,
    outdoor_forecast: list[float],
    heating_rate_deg_h: float,
    cooling_rate_deg_h: float,
    idle_loss_coeff: float,
) -> tuple[str, str]:
    """Devuelve (accion, motivo). `accion` es "heat" | "cool" | "idle".

    `heat_target`/`cool_target`: las DOS consignas del preset activo
    (ver presets.py) — una zona de calor y frio ("Auto", el unico modo
    dual que expone climate.py, para que sea compatible con el System
    Mode estandar de Matter) tiene las dos a la vez, calienta si baja de
    `heat_target` y enfria si sube de `cool_target`; una zona de un solo
    sentido solo trae rellena la que le corresponde (la otra es None).

    `outdoor_forecast`: previsión horaria empezando por la hora actual
    (indice 0), o lista vacia si no hay ninguna fuente declarada — ver
    outdoor.py. Sin previsión disponible, el motor sigue funcionando
    (reactivo puro, sin anticipacion ni ensanche de margen), nunca falla.
    """
    heating = heat_target is not None
    cooling = cool_target is not None

    if heating and current_temp < min_temp:
        return "heat", f"por debajo del mínimo de seguridad de la zona ({min_temp:.1f}°C)"
    if cooling and current_temp > max_temp:
        return "cool", f"por encima del máximo de seguridad de la zona ({max_temp:.1f}°C)"

    if priority == "manual":
        return "idle", "modo manual: sin gestión automática"

    heat_deadband = cool_deadband = deadband
    heat_note = cool_note = ""
    if priority == "ahorro":
        if heating:
            extra, why = _ahorro_extra_margin(True, outdoor_now, outdoor_forecast, heating_rate_deg_h)
            heat_deadband = deadband + extra
            heat_note = f" ({why})"
        if cooling:
            extra, why = _ahorro_extra_margin(False, outdoor_now, outdoor_forecast, cooling_rate_deg_h)
            cool_deadband = deadband + extra
            cool_note = f" ({why})"

    if heating and current_temp < heat_target - heat_deadband:
        return "heat", f"calentando hacia {heat_target:.1f}°C{heat_note}"
    if cooling and current_temp > cool_target + cool_deadband:
        return "cool", f"enfriando hacia {cool_target:.1f}°C{cool_note}"

    if heating:
        action, reason = _anticipate(True, current_temp, heat_target, deadband, outdoor_forecast, idle_loss_coeff, heating_rate_deg_h)
        if action != "idle":
            return action, reason
    if cooling:
        action, reason = _anticipate(False, current_temp, cool_target, deadband, outdoor_forecast, idle_loss_coeff, cooling_rate_deg_h)
        if action != "idle":
            return action, reason

    parts = []
    if heating:
        parts.append(f"calor {heat_target:.1f}°C (±{heat_deadband:.1f}){heat_note}")
    if cooling:
        parts.append(f"frío {cool_target:.1f}°C (±{cool_deadband:.1f}){cool_note}")
    return "idle", f"dentro de rango: {', '.join(parts) if parts else 'sin consigna activa'}"


def _ahorro_extra_margin(heating: bool, outdoor_now: float | None, outdoor_forecast: list[float],
                          rate_deg_h: float) -> tuple[float, str]:
    """Cuantos °C de mas se le puede dar de margen a la histéresis en
    prioridad "ahorro", y por que. Combina dos factores independientes,
    cada uno limitando el margen por su cuenta (nunca lo amplian, solo lo
    recortan desde el maximo):

      - Tendencia exterior: si la previsión de las proximas
        `AHORRO_LOOKAHEAD_HOURS` horas empeora (mas frio en calefaccion,
        mas calor en refrigeracion), se recorta el margen proporcionalmente
        — mejor no confiar en un margen ancho si el exterior va a jugar en
        contra.
      - Velocidad real de la zona (inercia termica aprendida): una zona
        lenta no se puede permitir tanto margen como una rapida, porque
        tarda mas en recuperar terreno si hace falta.
    """
    if outdoor_now is None or not outdoor_forecast:
        base_max = AHORRO_MAX_MARGIN_DEG * 0.5
        trend_note = "sin previsión exterior: margen moderado"
    else:
        lookahead = outdoor_forecast[:AHORRO_LOOKAHEAD_HOURS] or [outdoor_now]
        trend = lookahead[-1] - outdoor_now
        worsening = max(0.0, -trend) if heating else max(0.0, trend)
        base_max = max(0.0, AHORRO_MAX_MARGIN_DEG - worsening * AHORRO_MARGIN_SENSITIVITY)
        trend_note = "previsión exterior estable" if worsening < 0.5 else "la previsión exterior empeora, margen recortado"

    responsiveness = max(0.0, min(1.0, (rate_deg_h or 0.0) / REFERENCE_RATE_DEG_H))
    extra = base_max * responsiveness
    if responsiveness < 0.5:
        return extra, f"{trend_note}, zona lenta ({rate_deg_h:.1f}°C/h)"
    return extra, trend_note


def _anticipate(heating: bool, current_temp: float, target_temp: float, deadband: float,
                 outdoor_forecast: list[float], idle_loss_coeff: float, rate_deg_h: float) -> tuple[str, str]:
    """Proyecta la deriva PASIVA de la zona (sin actuar) durante
    `ANTICIPATE_LOOKAHEAD_HOURS`, hora a hora, con el mismo modelo de
    Newton simple que usa el aprendizaje de inercia (ver
    thermal_model.py): cada hora, la temperatura se acerca a la exterior
    prevista esa hora en proporcion a `idle_loss_coeff`. Si en algun punto
    de esa proyeccion la zona cruzaria el umbral de confort, y el tiempo
    que queda hasta ese cruce no basta para recuperarlo actuando a la
    velocidad real conocida de la zona si se empezase justo entonces,
    arranca YA — de forma sostenida, no de golpe cuando ya sea tarde.

    Sin previsión exterior (`outdoor_forecast` vacia) o sin tasa de
    actuacion conocida, no se anticipa nada: se cae al comportamiento
    puramente reactivo, nunca se inventa una previsión."""
    if not outdoor_forecast or not rate_deg_h or rate_deg_h <= 0:
        return "idle", ""

    threshold = (target_temp - deadband) if heating else (target_temp + deadband)
    temp = current_temp
    for hours_ahead, outdoor_h in enumerate(outdoor_forecast[:ANTICIPATE_LOOKAHEAD_HOURS], start=1):
        temp = temp + idle_loss_coeff * (outdoor_h - temp)
        crossed = (temp < threshold) if heating else (temp > threshold)
        if not crossed:
            continue
        gap = abs(target_temp - temp)
        recover_hours = gap / rate_deg_h
        # Comparacion con el valor FRACCIONARIO de `recover_hours`, sin
        # redondear hacia arriba a un minimo de 1h: una zona muy rapida
        # (recover_hours << 1) no necesita anticipacion aunque el cruce
        # este a "solo" 1h vista — el propio tramo reactivo (mas arriba)
        # ya llega a tiempo de sobra cuando de verdad haga falta.
        if hours_ahead <= recover_hours:
            action = "heat" if heating else "cool"
            return action, (
                f"anticipando: la previsión exterior sacaría la zona de rango en ~{hours_ahead}h; "
                f"empieza ya de forma sostenida para no tener que actuar de golpe"
            )
        break  # se sale de rango en el horizonte, pero todavia hay tiempo de sobra antes de tener que actuar

    return "idle", ""
