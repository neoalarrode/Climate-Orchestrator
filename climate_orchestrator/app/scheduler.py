"""
Motor de planificacion de una zona de clima.

Misma filosofia que el scheduler de Battery Orchestrator: nada de
programacion lineal ni de modelos termicos de caja negra, dos pasadas
simples y deterministas que se pueden leer de arriba a abajo.

  PASADA A: convierte el horario declarado (mas la presencia AHORA MISMO,
  nunca prevista — prever presencia humana si que seria una caja negra) en
  un nivel de confort objetivo para cada hora del horizonte: "confort",
  "eco" o "ausente".

  PASADA B: simula la temperatura de la zona hora a hora con el modelo
  termico APRENDIDO de tu propio historico (ver thermal_model_store.py:
  cuantos grados/hora sube realmente con el actuador encendido, cuantos
  pierde realmente estando apagado) y decide en que hora hace falta
  empezar a actuar para llegar justo a tiempo a cada subida de nivel — ni
  antes (gasto de mas) ni despues (incomodidad). Esto SOLO aplica en
  prioridad "ahorro"; en "confort" actua en cuanto hace falta, sin esperar
  al ultimo momento; en "manual" no decide nada, solo refleja al usuario.

El resultado es un plan hora a hora, mas la accion concreta para la hora
actual, cada una con su motivo en texto plano.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

# Franja de seguridad minima/maxima que nunca se cruza pase lo que pase,
# aunque el horario/presencia digan lo contrario (proteccion anti-heladas
# en calefaccion, anti-golpe-de-calor en refrigeracion).
FROST_PROTECTION_DELTA = 3.0  # °C por debajo de "ausente" en modo calor
HEAT_PROTECTION_DELTA = 3.0   # °C por encima de "ausente" en modo frio


@dataclass
class HourPlan:
    dt: datetime
    level: str              # "confort" | "eco" | "ausente"
    target_temp: float
    outdoor_temp: float
    predicted_temp: float
    action: str = "idle"    # "heat" | "cool" | "idle"
    reason: str = ""


def target_level_for_hour(dt: datetime, schedule: list[dict]) -> str:
    """"confort" si dt cae dentro de alguna franja del horario declarado,
    "eco" en caso contrario. Sin franjas declaradas: "eco" todo el dia (el
    usuario tiene que declarar al menos una franja para tener calefaccion
    automatica; hasta entonces, solo proteccion anti-heladas/anti-calor)."""
    if not schedule:
        return "eco"
    weekday = dt.weekday()
    hm = dt.hour * 60 + dt.minute
    for window in schedule:
        days = window.get("days") or []
        if days and weekday not in days:
            continue
        start_h, start_m = (int(x) for x in window["start"].split(":"))
        end_h, end_m = (int(x) for x in window["end"].split(":"))
        start, end = start_h * 60 + start_m, end_h * 60 + end_m
        if start <= hm < end:
            return "confort"
    return "eco"


def build_target_levels(now: datetime, horizon_hours: int, schedule: list[dict],
                         presence_now: bool | None, presence_overrides_schedule: bool,
                         priority: str) -> list[str]:
    """
    Nivel objetivo por hora, SOLO a partir del horario declarado — salvo la
    hora actual (indice 0), que se puede ajustar con la presencia real
    medida ahora mismo (nunca prevista, ver cabecera del modulo):

      - presente ahora pero el horario dice "eco" -> sube a "confort" (a
        alguien no se le deja pasar frio/calor porque el horario no lo
        prevea).
      - nadie presente ahora y el horario dice "confort" -> baja a "eco",
        pero SOLO en prioridad "ahorro" (en "confort" se respeta el
        horario declarado tal cual, por si la ausencia es un hueco corto
        y no compensa dejar que la zona se enfrie/caliente de mas).
    """
    hours = [now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=i) for i in range(horizon_hours)]
    levels = [target_level_for_hour(h, schedule) for h in hours]
    if presence_overrides_schedule and presence_now is not None:
        if presence_now and levels[0] == "eco":
            levels[0] = "confort"
        elif not presence_now and levels[0] == "confort" and priority == "ahorro":
            levels[0] = "eco"
    return levels


def _level_temp(level: str, comfort_temp: float, eco_temp: float, away_temp: float) -> float:
    return {"confort": comfort_temp, "eco": eco_temp, "ausente": away_temp}.get(level, eco_temp)


def build_plan(
    now: datetime,
    levels: list[str],
    outdoor_forecast: list[float],
    current_temp: float,
    comfort_temp: float,
    eco_temp: float,
    away_temp: float,
    hvac_capability: str,
    priority: str,
    deadband: float,
    heating_rate_deg_h: float,
    cooling_rate_deg_h: float,
    idle_loss_coeff: float,
    thermal_model_reliable: bool,
) -> list[HourPlan]:
    """
    heating_rate_deg_h / cooling_rate_deg_h: cuantos grados por hora sube
    (o baja, en frio) la temperatura REAL de la zona con el actuador
    encendido a maxima potencia, medido de tu propio historico (ver
    thermal_model_store.py). idle_loss_coeff: fraccion de la diferencia
    con el exterior que se pierde/gana por hora con el actuador APAGADO
    (modelo de Newton simple, tambien calibrado con tu historico).

    thermal_model_reliable=False cuando todavia no hay historico
    suficiente: se usan los valores por defecto (conservadores) pasados en
    heating_rate_deg_h/etc., y cada hora de "ahorro" que dependa de ellos
    lo deja dicho en el motivo.
    """
    horizon = len(levels)
    hours = [now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=i) for i in range(horizon)]
    heating = hvac_capability in ("heat", "heat_cool")
    cooling = hvac_capability in ("cool", "heat_cool")

    targets = [_level_temp(lv, comfort_temp, eco_temp, away_temp) for lv in levels]

    # Proxima hora en la que el nivel SUBE (calor) respecto a la hora i,
    # y el objetivo al que sube — para saber cuanto margen hay antes de
    # tener que estar ya a esa temperatura.
    next_rise_idx: list[int | None] = [None] * horizon
    next_rise_target: list[float | None] = [None] * horizon
    for i in range(horizon - 1, -1, -1):
        if i + 1 < horizon and targets[i + 1] > targets[i]:
            next_rise_idx[i] = i + 1
            next_rise_target[i] = targets[i + 1]
        elif i + 1 < horizon and next_rise_idx[i + 1] is not None and targets[next_rise_idx[i + 1]] > targets[i]:
            next_rise_idx[i] = next_rise_idx[i + 1]
            next_rise_target[i] = next_rise_target[i + 1]
    # simetrico para frio: proxima hora en la que el nivel BAJA (hace mas
    # falta refrigerar antes, p.ej. para llegar fresco a la hora de dormir)
    next_drop_idx: list[int | None] = [None] * horizon
    next_drop_target: list[float | None] = [None] * horizon
    for i in range(horizon - 1, -1, -1):
        if i + 1 < horizon and targets[i + 1] < targets[i]:
            next_drop_idx[i] = i + 1
            next_drop_target[i] = targets[i + 1]
        elif i + 1 < horizon and next_drop_idx[i + 1] is not None and targets[next_drop_idx[i + 1]] < targets[i]:
            next_drop_idx[i] = next_drop_idx[i + 1]
            next_drop_target[i] = next_drop_target[i + 1]

    plan: list[HourPlan] = []
    temp = current_temp

    for i in range(horizon):
        target = targets[i]
        outdoor = outdoor_forecast[i]
        hp = HourPlan(dt=hours[i], level=levels[i], target_temp=target, outdoor_temp=outdoor, predicted_temp=temp)

        if priority == "manual":
            hp.reason = "modo manual: sin gestion automatica, solo refleja el control manual"
            plan.append(hp)
            continue

        action = "idle"
        floor = target - FROST_PROTECTION_DELTA if heating else None
        ceiling = target + HEAT_PROTECTION_DELTA if cooling and not heating else None
        # Objetivo REAL contra el que se limita el avance simulado de esta
        # hora si se actua (variable, no siempre `target`): al precalentar
        # con antelacion para una subida futura de nivel, el limite tiene
        # que ser esa temperatura futura, no la del nivel de AHORA MISMO.
        sim_goal = target
        decided = False

        # 1) Precalentamiento/preenfriamiento con antelacion (solo "ahorro"):
        # se evalua ANTES que el mantenimiento del nivel actual y SIN
        # condicionarlo al deadband del nivel actual — si no, en cuanto la
        # simulacion alcanza el nivel "eco" de camino a "confort", el
        # precalentamiento se para en seco ahi y nunca completa la subida
        # (bug detectado probando con datos de ejemplo, ver CHANGELOG).
        if priority == "ahorro":
            if heating and next_rise_idx[i] is not None and heating_rate_deg_h > 0:
                deadline_idx, deadline_target = next_rise_idx[i], next_rise_target[i]
                gap = max(0.0, deadline_target - temp)
                if gap > 0:
                    hours_until = deadline_idx - i
                    lead_hours_needed = math.ceil(gap / heating_rate_deg_h)
                    if hours_until <= lead_hours_needed:
                        action, sim_goal, decided = "heat", deadline_target, True
                        note = "" if thermal_model_reliable else " (estimacion inicial, todavia sin historico suficiente)"
                        hp.reason = (f"precalentando: arranca justo para llegar a {deadline_target:.1f}°C "
                                     f"a las {hours[deadline_idx].strftime('%H:%M')}{note}")
                    elif temp >= target - deadband:
                        # ya cubre el nivel actual, solo falta esperar al momento de precalentar
                        decided = True
                        start_at = hours[deadline_idx - lead_hours_needed]
                        hp.reason = f"sin actuar todavia: llegaria a tiempo empezando a las {start_at.strftime('%H:%M')}"
            elif cooling and next_drop_idx[i] is not None and cooling_rate_deg_h > 0:
                deadline_idx, deadline_target = next_drop_idx[i], next_drop_target[i]
                gap = max(0.0, temp - deadline_target)
                if gap > 0:
                    hours_until = deadline_idx - i
                    lead_hours_needed = math.ceil(gap / cooling_rate_deg_h)
                    if hours_until <= lead_hours_needed:
                        action, sim_goal, decided = "cool", deadline_target, True
                        note = "" if thermal_model_reliable else " (estimacion inicial, todavia sin historico suficiente)"
                        hp.reason = (f"preenfriando: arranca justo para llegar a {deadline_target:.1f}°C "
                                     f"a las {hours[deadline_idx].strftime('%H:%M')}{note}")
                    elif temp <= target + deadband:
                        decided = True
                        start_at = hours[deadline_idx - lead_hours_needed]
                        hp.reason = f"sin actuar todavia: llegaria a tiempo empezando a las {start_at.strftime('%H:%M')}"

        # 2) Mantenimiento del nivel de la hora actual (confort/eco/ausente).
        if not decided and heating and temp < target - deadband:
            action, sim_goal, decided = "heat", target, True
            if priority == "confort":
                hp.reason = f"calentando hacia {target:.1f}°C (prioridad confort: actua en cuanto hace falta)"
            else:
                hp.reason = f"calentando hacia {target:.1f}°C (sin proxima subida de nivel que esperar)"
        elif not decided and cooling and not heating and temp > target + deadband:
            action, sim_goal, decided = "cool", target, True
            if priority == "confort":
                hp.reason = f"enfriando hacia {target:.1f}°C (prioridad confort: actua en cuanto hace falta)"
            else:
                hp.reason = f"enfriando hacia {target:.1f}°C (sin proxima bajada de nivel que esperar)"

        # 3) Proteccion minima/maxima de seguridad, pase lo que pase.
        if not decided and heating and floor is not None and temp < floor:
            action, sim_goal, decided = "heat", target, True
            hp.reason = f"manteniendo minimo de seguridad ({floor:.1f}°C, proteccion anti-heladas)"
        elif not decided and cooling and ceiling is not None and temp > ceiling:
            action, sim_goal, decided = "cool", target, True
            hp.reason = f"manteniendo maximo de seguridad ({ceiling:.1f}°C, proteccion anti-golpe-de-calor)"

        if not decided:
            hp.reason = f"sin actuar: dentro de rango de {target:.1f}°C (±{deadband:.1f}°C)"

        hp.action = action

        # Avance de la temperatura simulada para la siguiente hora: con el
        # actuador activo, la tasa aprendida (ya incluye la perdida pasiva,
        # es la tasa NETA observada de verdad); sin actuar, modelo de
        # Newton simple hacia la temperatura exterior prevista.
        if action == "heat":
            temp = min(sim_goal + deadband, temp + heating_rate_deg_h)
        elif action == "cool":
            temp = max(sim_goal - deadband, temp - cooling_rate_deg_h)
        else:
            temp = temp + idle_loss_coeff * (outdoor - temp)

        plan.append(hp)

    return plan
