<h1 align="center">🌡️ Climate Orchestrator</h1>

<p align="center">
  Adaptive heating and air conditioning — driven by presence, schedule<br>
  and each zone's real thermal inertia. No black boxes.
</p>

<p align="center">
  <img alt="Home Assistant Add-on" src="https://img.shields.io/badge/Home%20Assistant-Add--on-8b5cf6?style=flat-square&labelColor=0b0a16">
  <img alt="Deterministic" src="https://img.shields.io/badge/planner-deterministic-22d3ee?style=flat-square&labelColor=0b0a16">
  <img alt="No black boxes" src="https://img.shields.io/badge/no%20black%20boxes-eae8f7?style=flat-square&labelColor=0b0a16">
</p>

<p align="center">
  🇬🇧 English · <a href="README.md">🇪🇸 Leer en español</a>
</p>

---

Home Assistant add-on, sibling of [Battery Orchestrator](https://github.com/neoalarrode/Battery-Orchestrator),
that plans and executes heating/cooling for every zone in your home,
every minute, live against your real installation. A custom, deterministic
engine you can read top to bottom — no EMHASS, no `versatile_thermostat`
style hard-to-reason parameters — plus a web UI where you declare every
zone, sensor and schedule yourself. Each zone is exposed back to Home
Assistant as a real `climate.*` entity (via MQTT Discovery), so it keeps
working in Lovelace, Google Home or Alexa like any thermostat — it's just
this engine deciding when to turn on, not a black box.

## Why it exists

`versatile_thermostat` and similar integrations handle fine-grained
thermostat control well, but the decision of WHEN to heat is usually a
fixed schedule or a preset with little explanation. Climate Orchestrator
does what Battery Orchestrator already does for batteries: a two-pass
algorithm you can read in full, where every hourly decision comes with a
plain-text reason ("preheating: starts just in time to reach 21°C by
07:00", "no action: within range", "holding safety minimum, frost
protection"...).

## What it does

- **Plans each zone independently**, combining the schedule you declare
  ("comfort" windows), real presence measured right now (person,
  device_tracker...) and the outdoor weather forecast (from an HA
  `weather.*` entity, or your own outdoor sensor).
- **Learns each zone's real thermal inertia** from its own history: how
  many degrees per hour it actually gains while heating, how many it
  loses with the actuator off relative to the outdoor delta — never a
  made-up number or a generic textbook physical model.
- **Preheats just enough**: in "savings" priority, it won't turn on until
  the latest moment at which, at that zone's actually-measured heating
  rate, it can still reach comfort exactly on time. In "comfort" priority,
  it acts as soon as needed, no waiting.
- **Respects safety minimums** (frost / heat-stroke protection) no matter
  what the schedule or presence say.
- **Anti short-cycling**: configurable minimum on/off time per zone, so a
  relay isn't destroyed over tenths of a degree.
- **Exposes each zone as `climate.*` in Home Assistant** via MQTT
  Discovery: shows up in Lovelace, Google Home, Alexa... Changing mode or
  target temperature from there becomes a "manual override" for a
  configurable time, after which the zone returns to the automatic plan
  on its own.
- **Two ways to act**: switch a heater/AC on/off directly (with
  hysteresis), or delegate to an existing `climate.*` entity (e.g. a
  thermostatic radiator valve with its own electronics).
- **Read-only wallpanel**: like Battery Orchestrator, its own port to keep
  the panel fixed on a wall tablet without going through Home Assistant's
  login.
- **Everything configurable from the web**: zones, schedules, sensors,
  MQTT broker — nothing hardcoded. Exportable/importable configuration.

## Installation

1. Install and set up the official **Mosquitto broker** add-on (Settings →
   Add-ons → Store) if you don't have it yet, plus Home Assistant's MQTT
   integration.
2. In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ →
   Repositories**, and add:
   ```
   https://github.com/neoalarrode/Climate-Orchestrator
   ```
3. Find "Climate Orchestrator" in the store, install it and start it.
4. Open it from the sidebar (uses Ingress) and add your first zone.

Step-by-step setup instructions in [DOCS.en.md](DOCS.en.md).

## Project status

Actively developed — see [CHANGELOG.md](CHANGELOG.md). Always starts in
simulation mode: you'll see exactly what the add-on would do without
touching your real actuators, until you trust its decisions.
