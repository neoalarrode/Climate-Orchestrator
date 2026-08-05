"""
Cada preset se expone como una o dos entidades `number.*` (consigna de
calor y de frio por separado, ver presets.py) — asi se pueden ajustar en
caliente desde Lovelace o una automatizacion sin volver a Ajustes ->
Configurar cada vez que quieras subir un grado el preset "Confort".

Se siembran una sola vez con el valor inicial declarado en el asistente
(`presets_text`); a partir de ahi es el propio estado de la entidad
number (restaurado por HA tras un reinicio, como cualquier otro number)
el que manda — climate.py busca el valor VIVO de estas entidades para
decidir, nunca vuelve a leer el texto de la configuracion.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from . import presets as presets_module
from .const import CONF_MAX_TEMP, CONF_MIN_TEMP, CONF_PRESETS_TEXT, DEFAULT_MAX_TEMP, DEFAULT_MIN_TEMP, DOMAIN


def preset_number_unique_id(entry_id: str, preset_name: str, side: str) -> str:
    """Mismo esquema que usa climate.py (`_preset_value`) para encontrar
    esta entidad via el registro de entidades — `side` es "heat" o "cool"."""
    return f"{entry_id}_preset_{slugify(preset_name)}_{side}"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    zone = entry.data
    try:
        parsed = presets_module.parse_presets(zone.get(CONF_PRESETS_TEXT, ""))
    except ValueError:
        parsed = []

    entities: list[NumberEntity] = []
    for preset in parsed:
        entities.append(PresetNumber(entry, preset["name"], "heat", preset["heat_temp"]))
        entities.append(PresetNumber(entry, preset["name"], "cool", preset["cool_temp"]))
    async_add_entities(entities)


class PresetNumber(NumberEntity, RestoreNumber):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "°C"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, preset_name: str, side: str, initial: float) -> None:
        self.entry = entry
        self._attr_unique_id = preset_number_unique_id(entry.entry_id, preset_name, side)
        side_label = "calor" if side == "heat" else "frío"
        self._attr_name = f"{preset_name} ({side_label})"
        self._attr_native_min_value = float(entry.data.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP))
        self._attr_native_max_value = float(entry.data.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP))
        self._attr_native_value = initial
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = last.native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
