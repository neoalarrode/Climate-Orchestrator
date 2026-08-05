"""Climate Orchestrator: calefaccion y aire acondicionado adaptativos por
presencia, presets e inercia termica real de cada zona. Sin cajas negras.

Una entrada de configuracion = una zona (ver config_flow.py). Dos
plataformas: "climate" (la zona en si) y "number" (las consignas de calor
y frio de cada preset, como entidades ajustables — ver number.py)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

PLATFORMS = ["climate", "number"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """La pestaña de Opciones (ClimateOrchestratorOptionsFlow) actualiza
    `entry.data` directamente y dispara esto: recargar la entrada entera es
    mas simple y fiable que intentar mutar la entidad ya viva en caliente
    (cambia hasta la capacidad -> los hvac_modes disponibles)."""
    await hass.config_entries.async_reload(entry.entry_id)
