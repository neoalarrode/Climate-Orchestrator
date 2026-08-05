# Climate Orchestrator — Setup guide

## 1. Installation

1. HACS must be installed on your Home Assistant.
2. HACS → ⋮ menu (top right) → **Custom repositories** → paste
   `https://github.com/neoalarrode/Climate-Orchestrator` → type
   **Integration** → **Add**.
3. Find "Climate Orchestrator" in HACS, install it.
4. Restart Home Assistant (required: it's a new integration, a reload
   isn't enough).
5. **Settings → Devices & services → Add integration** → search
   "Climate Orchestrator".

## 2. One zone = one integration entry

Each time you go through the wizard, you add **one** zone (one room, one
space). To manage the living room and two bedrooms, run "Add integration"
three times. Each zone lives as its own device with its own `climate.*`
entity — delete it like any other integration (Settings → Devices &
services → that entry → ⋮ → Delete) when you no longer need it.

## 3. The wizard, step by step

**Step 1 — General**: zone name, capability (heat only / cool only /
heat and cool), priority.

- **Comfort**: acts as soon as the zone drifts out of range of the active
  preset.
- **Savings**: wider hysteresis margin (fewer on-cycles), which narrows
  only if the outdoor forecast worsens over the next few hours.
- **Manual**: never decides on its own; only reflects the thermostat's
  manual control.

**Step 2 — Sensors**: temperature sensor (required), humidity (optional),
this zone's own outdoor sensor (optional, more precise than a global one)
and a `weather.*` entity with hourly forecast (optional, recommended —
used to anticipate outdoor changes, see below).

**Step 3 — Actuator**: heating and cooling EACH have their own
independent actuator:

- *Switch*: the integration turns a `switch.*` on/off with hysteresis and
  anti short-cycling (minimum on/off time).
- *climate.\**: delegates to an existing `climate.*` entity (a
  thermostatic valve, an air conditioner with its own electronics...). It
  gets sent its correct mode ("heat"/"cool"/"off") and the target
  temperature.

Heating and cooling are genuinely independent: any combination works. A
radiator can be a plain switch OR have its own `climate.*` entity (a
thermostatic valve, for instance), same as an air conditioner — neither
field assumes a specific device type, only whether you control it by
switch or by delegating to an existing climate.*. If your setup has a
DIFFERENT actuator for heating and cooling (whatever their type), declare
each in its own field — the integration never activates both at once. If
you have a single reversible unit (heat pump AC), put the SAME
`climate.*` entity in both the heating and cooling fields: it's
auto-detected and sent a single command with whichever mode is correct
for the season, never two commands stepping on each other.

**Step 4 — Presets**: instead of a fixed schedule, you declare a list of
named presets with a temperature, as text: `Comfort: 21, Away: 17, Party:
23`. As many as you want. Why no schedule: a "07:00-23:00 = comfort"
window doesn't know if anyone's really in the room — presets, combined
with real presence, adapt to what's actually happening.

**Step 5 — Automatic switching**: choose which of the presets you just
declared activates when there IS presence and which when there ISN'T.
These two are what "Auto" mode (the default active preset) uses.

**Step 6 — Safety limits**: hysteresis, and the **minimum and maximum
that are ALWAYS enforced**, no matter the active preset, presence, or
manual mode — the configurable-per-zone "never below 12°C in winter even
with nobody home, never above 30°C in summer". Also minimum on/off times
(only relevant with a switch actuator).

**Step 7 — Presence, doors/windows and options**: presence entities,
door/window sensors, manual temperature override duration, history days
for thermal inertia, forecast refresh interval, and simulation mode.

## 4. Presence: the room's PHYSICAL sensors, not "home"

The presence field is meant primarily for the zone's own PHYSICAL
presence sensors — PIR, mmWave radar, an occupancy/motion
`binary_sensor` for that specific room: "is someone HERE right now?", not
"is someone home?". `person.*`/`device_tracker.*` are also accepted, as
an extra signal (useful mainly to know NOBODY is anywhere in the house),
but they aren't the primary use case. Future presence is never
predicted — that would be a black box — only what's measured right now.

## 5. Presets: automatic, or pinned by hand

The **"Auto"** preset (the default) picks between the "with presence" and
"without presence" presets you declared, based on physical presence
measured now. Picking ANY OTHER preset by hand — from the thermostat
card, Google Home, Alexa, or a Matter/HomeKit bridge — is a **standing**
choice: it stays pinned (restored across restarts) until you set it back
to "Auto" yourself. Useful for "I'm staying on Vacation mode today even
if it detects presence" without disabling anything.

## 6. Editing a zone

Settings → Devices & services → Climate Orchestrator → the zone you want
→ **Configure**. A single form opens with every field pre-filled (presets
are edited as the same free-text format from the wizard). Saving reloads
the whole zone.

## 7. Doors and windows

Any declared door/window sensor that's "open" pauses the zone
**instantly** (via HA's event bus, no cycle to wait for), regardless of
the active preset or mode. Once closed, it returns to the automatic
calculation on its own.

## 8. Anticipating the weather (not your presence)

Dropping the schedule doesn't mean dropping anticipation: it's still more
efficient (and more comfortable) to adjust a zone gradually while there's
still margin, than to wait until it drifts out of range and have to act
at full power all at once. The difference is where that anticipation
comes from:

- **Presence is never predicted** — that would be a black box. Presets
  only switch on presence measured live.
- **The outdoor weather forecast IS used** (observable, not made up): if
  the forecast shows a temperature swing coming, the zone starts acting
  NOW, gradually, instead of waiting until it's already out of range. It
  combines the forecast with that zone's real learned thermal inertia (a
  fast zone doesn't need as much lead time as a slow one).
- In **"savings"** priority, the hysteresis margin also widens when the
  forecast is stable (fewer on-cycles) and narrows only if it worsens.

## 9. Safety limits

Configurable per zone (wizard step 6): a minimum that heating always
respects and a maximum that cooling always respects, no matter the active
preset, presence, or manual mode. Built exactly for "I don't care if
nobody's home, never below X in winter / never above X in summer".

## 10. Mode vs. preset vs. temperature: three different behaviors

- **Changing the MODE** (off / heat / cool / auto) is a choice that
  **sticks** — it doesn't expire, it's restored across restarts.
- **Changing the PRESET** (by hand, any of the declared ones) is also
  **standing** — same as the mode, see point 5.
- **Changing the target TEMPERATURE** is a **temporary** override: it
  lasts however long you configured (2h by default) and afterwards the
  zone returns to the active preset on its own.

## 11. Learned thermal inertia

Learned from both actuator types, not just switches: a plain `switch.*`
uses its own on/off history directly; a delegated `climate.*` uses the
`hvac_action` attribute from ITS OWN history (heating/cooling vs.
idle/off) — most climate integrations report it. If a particular entity
never does, that zone simply keeps conservative defaults (flagged
`thermal_model_reliable: false` in the entity's attributes) — never a
made-up number.

## 12. Simulation mode

With "Simulation mode" on for a zone (default), the integration computes
and publishes what it would do (visible in the entity's attributes), but
never sends a real command to any actuator. Review the `reason` attribute
for a few days before turning it off.
