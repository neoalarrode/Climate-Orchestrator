"""
Presets con nombre, en vez de un horario fijo.

Por que se elimino el horario: una franja "07:00-23:00 = confort" no sabe
si hay alguien de verdad en la habitacion — asume una rutina fija. Los
presets, combinados con presencia REAL medida ahora mismo (nunca prevista,
seria una caja negra — ver climate.py), se adaptan a lo que de verdad esta
pasando: si vuelves antes o tarde, la zona reacciona al instante en vez de
esperar a la hora programada.

Cada zona declara una lista de presets (nombre + temperatura objetivo,
p.ej. "Confort: 21", "Fiesta: 23", "Vacaciones: 16") y designa cual usar
si hay presencia y cual si no. `PRESET_AUTO` es el modo por defecto: deja
que Climate Orchestrator elija solo entre esos dos segun la presencia
real. Elegir CUALQUIER OTRO preset a mano (termostato, voz, Google Home,
un puente Matter/HomeKit) es una eleccion PERSISTENTE — igual que el modo
calor/frio/auto — que se queda fijada hasta que vuelvas a poner
"Automatico" tu mismo.
"""

from __future__ import annotations

PRESET_AUTO = "Automático"


def parse_presets(text: str) -> list[dict]:
    """Convierte el texto "Nombre: temperatura, Nombre: temperatura..."
    declarado en el asistente en una lista de presets. Lanza ValueError
    con un mensaje legible si el texto no tiene el formato esperado."""
    presets: list[dict] = []
    seen = set()
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"«{chunk}» no tiene el formato «Nombre: temperatura»")
        name, temp_str = chunk.split(":", 1)
        name = name.strip()
        if not name or name == PRESET_AUTO:
            raise ValueError(f"«{name}» no es un nombre de preset valido")
        try:
            temp = float(temp_str.strip())
        except ValueError as e:
            raise ValueError(f"«{temp_str.strip()}» no es una temperatura valida para «{name}»") from e
        if name in seen:
            raise ValueError(f"el preset «{name}» esta repetido")
        seen.add(name)
        presets.append({"name": name, "target_temp": temp})
    if not presets:
        raise ValueError("declara al menos un preset")
    return presets


def format_presets(presets: list[dict]) -> str:
    """Inverso de `parse_presets`, para precargar el campo de texto al
    editar una zona ya creada."""
    return ", ".join(f"{p['name']}: {p['target_temp']}" for p in presets)


def resolve_active_preset(preset_mode: str, presets: list[dict], presence_preset: str,
                           away_preset: str, presence_now: bool | None) -> tuple[str, float, str]:
    """Devuelve (nombre_del_preset_activo, temperatura_objetivo, motivo).

    `presence_now`: True/False si hay lectura fiable de los sensores de
    presencia FISICA declarados (ver climate.py — pensados para ser
    sensores de presencia de la propia habitacion, tipo PIR o mmWave, no
    solo "en casa"), None si no hay ninguno declarado o ninguno da un dato
    fiable ahora mismo.
    """
    by_name = {p["name"]: p["target_temp"] for p in presets}

    if preset_mode != PRESET_AUTO:
        if preset_mode in by_name:
            return preset_mode, by_name[preset_mode], f"preset «{preset_mode}» fijado a mano"
        # preset borrado de la configuracion pero seguia activo: cae a automatico
        preset_mode = PRESET_AUTO

    if presence_now is None:
        return away_preset, by_name.get(away_preset), "automático sin sensor de presencia fiable: usando el preset de ausencia"
    if presence_now:
        return presence_preset, by_name.get(presence_preset), "automático: presencia detectada en la zona"
    return away_preset, by_name.get(away_preset), "automático: sin presencia en la zona"
