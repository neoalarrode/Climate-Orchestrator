<p align="center">
  <img src="logo.png" width="120" alt="Climate Orchestrator">
</p>

<h1 align="center">Climate Orchestrator</h1>

<p align="center">
  Adaptive heating and air conditioning — driven by physical presence,<br>
  presets, doors/windows and each zone's real thermal inertia. No black boxes.
</p>

<p align="center">
  <img alt="HACS" src="https://img.shields.io/badge/HACS-Custom-8b5cf6?style=flat-square&labelColor=0b0a16">
  <img alt="Deterministic" src="https://img.shields.io/badge/planner-deterministic-22d3ee?style=flat-square&labelColor=0b0a16">
  <img alt="No black boxes" src="https://img.shields.io/badge/no%20black%20boxes-eae8f7?style=flat-square&labelColor=0b0a16">
</p>

<p align="center">
  🇬🇧 English · <a href="README.md">🇪🇸 Leer en español</a>
</p>

---

A Home Assistant integration installable via **HACS**, sibling of
[Battery Orchestrator](https://github.com/neoalarrode/Battery-Orchestrator),
that manages heating/cooling for every zone in your home with a custom,
deterministic engine you can read top to bottom — no EMHASS, no
hard-to-reason `versatile_thermostat`-style parameters. Every zone you
declare becomes a **native** Home Assistant `climate.*` entity: it works
in Lovelace, Google Home, Alexa, or any Matter/HomeKit bridge exactly like
any other thermostat.

## Why an integration, not an add-on

This project's first version was an external add-on with its own web UI.
It was deliberately dropped: an add-on can only poll Home Assistant over
REST every few seconds, or lean on MQTT/websockets as a workaround to
react faster. An integration lives **inside** HA's own event bus — so "a
window just opened" or "someone just walked into the room" turns into a
real instant reaction, not "within up to 20 seconds". It's the correct
architecture for a thermostat.

## What it does

- **One zone = one integration entry** (click "+ Add integration" once
  per room — same pattern as `versatile_thermostat`). Each zone exposes
  its own `climate.*` with the decision's reason visible in its
  attributes.
- **Reacts instantly** to temperature, presence, and doors/windows — via
  HA's event bus (`async_track_state_change_event`), not polling. An open
  door/window pauses the zone the moment it happens.
- **Named presets instead of a fixed schedule, configurable as
  entities**: "Comfort: 21/25, Away: 17/28, Party: 23/24"... as many as
  you want, each with its own winter (heat) and summer (cool) setpoint.
  Each setpoint is its own `number.*` entity — bump "Comfort" up a degree
  from Lovelace or an automation, no need to go back into "Configure".
  They switch automatically based on the room's real PHYSICAL presence
  (PIR/mmWave sensors, not just "home" on a phone) — or by hand, as a
  standing choice until you set it back to "Auto".
- **Matter-style "Auto" mode, plus heat/cool separately**: a zone with
  real heating and cooling exposes `off`/`auto`/`heat`/`cool` — in "Auto"
  (Matter's standard Auto System Mode) it has both setpoints active at
  once and decides on its own which applies each moment; if you'd rather
  lock it to "heat only" or "cool only" by hand (e.g. for summer), you
  still can. Ready for any Matter/HomeKit bridge with no translation
  needed.
- **Always-enforced safety ceiling and floor**: "never below X°C in
  winter, never above X°C in summer", even with nobody home — independent
  of the active preset or presence.
- **Learns each zone's real thermal inertia** from its own history (HA's
  recorder): °C/hour while heating, loss coefficient vs. outdoor delta —
  never a made-up number.
- **Anticipates the weather, not your presence**: if the forecast shows a
  temperature swing coming, it starts acting gradually ahead of time
  (using the learned thermal inertia) instead of waiting to drift out of
  range and having to compensate all at once at full power. In "savings"
  priority it also widens the hysteresis margin when the forecast is
  stable, to cut down on-cycles. Presence is never predicted — only
  weather, which is observable data.
- **Detected capability, not manually declared**: no "heat only / cool
  only / both" dropdown. You declare the actuators you actually have —
  as many delegated `climate.*` entities as you want (each governed by
  its OWN native `hvac_modes`, no separate heat/cool fields: a reversible
  heat pump only needs adding once) plus whatever heating and cooling
  switches you have, in independent lists — and the zone figures out on
  its own what it can do, exposing to Home Assistant/Matter/HomeKit
  exactly the standard set (`off`/`heat`/`cool`/`heat_cool`) its real
  actuators support. Heat and cool are never activated at once.
- **Standing mode and preset, including "Manual"**: changing the mode
  (off/auto, or off/heat for a single-direction zone) or the preset from
  the thermostat is a choice that sticks (restored across restarts, like
  any real thermostat). Adjusting the temperature directly from the
  thermostat card (without picking a preset) switches the zone to the
  **"Manual"** preset — just as standing as any other, it keeps that
  temperature until you pick another preset yourself, not a
  time-limited override.
- **Per-zone simulation mode**: computes and shows what it would do
  without touching any real actuator, until you trust its decisions.

## Installation

1. HACS → ⋮ → Custom repositories → add
   `https://github.com/neoalarrode/Climate-Orchestrator` as type
   **Integration**.
2. Install "Climate Orchestrator" and restart Home Assistant.
3. **Settings → Devices & services → Add integration** → search
   "Climate Orchestrator" → follow the wizard to add your first zone.
   Repeat for each room.

Full field-by-field setup guide in [DOCS.en.md](DOCS.en.md).

## Project status

Actively developed — see [CHANGELOG.md](CHANGELOG.md). Turn on simulation
mode on every new zone and review its `reason` attribute for a few days
before letting it act for real.

## License

© 2026 Eric Larrode. All rights reserved — see [LICENSE](LICENSE).
The code is visible so it can be installed via HACS, but its use,
copying, or modification outside this repository is not authorized
without explicit permission.
