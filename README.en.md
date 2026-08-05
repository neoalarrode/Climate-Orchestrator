<h1 align="center">🌡️ Climate Orchestrator</h1>

<p align="center">
  Adaptive heating and air conditioning — driven by presence, schedule,<br>
  doors/windows and each zone's real thermal inertia. No black boxes.
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
- **Three control modes per zone**: schedule only, real presence only
  (never predicted — that would be a black box), or hybrid (schedule +
  real presence can bump the current hour's level up or down).
- **Learns each zone's real thermal inertia** from its own history (HA's
  recorder): °C/hour while heating, loss coefficient vs. outdoor delta —
  never a made-up number.
- **Preheats just enough**: in "savings" priority, it won't turn on until
  the latest moment at which, at that zone's real measured rate, it still
  arrives on time. In "comfort" priority, it acts as soon as needed.
- **Independent heating and cooling actuators**: a radiator (switch) and
  a separate air conditioner (`climate.*`) coexist without stepping on
  each other — never both active at once. If the same unit does both (a
  reversible heat pump), it's auto-detected and sent a single command
  with the correct mode for the season.
- **Standing mode vs. temporary override**: changing the mode
  (off/heat/cool/auto) from the thermostat is a choice that sticks
  (restored across restarts, like any real thermostat); changing the
  target temperature is a temporary override with a configurable expiry,
  after which the zone returns to the automatic plan on its own.
- **Safety protection**: frost / heat-stroke protection, always active no
  matter what schedule, presence, or manual mode say.
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
