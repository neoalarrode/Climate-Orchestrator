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
heat and cool), priority, control mode.

- **Priority**: *Comfort* acts as soon as temperature drifts out of
  range; *Savings* uses the learned thermal inertia to start as late as
  possible; *Manual* never decides on its own, only reflects the
  thermostat's manual control.
- **Control mode**: *Schedule only* ignores presence; *Presence only*
  ignores the schedule (comfort if someone's home right now, eco if not);
  *Hybrid* combines both (schedule as the base, real presence can bump
  the current hour's level up or down).

**Step 2 — Sensors**: temperature sensor (required), humidity (optional),
this zone's own outdoor sensor (optional, more precise than a global one)
and a `weather.*` entity with hourly forecast (optional, recommended for
"savings" priority).

**Step 3 — Actuator**: heating and cooling EACH have their own
independent actuator:

- *Switch*: the integration turns a `switch.*` on/off with hysteresis and
  anti short-cycling (minimum on/off time).
- *climate.\**: delegates to an existing `climate.*` entity (a
  thermostatic valve, an air conditioner with its own electronics...). It
  gets sent its correct mode ("heat"/"cool"/"off") and the target
  temperature.

If your setup has a radiator (switch, heat only) and an air conditioner
(climate.\*, cool only), declare each in its own field — the integration
never activates both at once. If you have a single reversible unit (heat
pump AC), put the SAME `climate.*` entity in both the heating and cooling
fields: it's auto-detected and sent a single command with whichever mode
is correct for the season, never two commands stepping on each other.

**Step 4 — Temperatures**: comfort, eco, away, hysteresis, min/max
limits, minimum on/off times (only relevant with a switch actuator).

**Step 5 — Schedule, presence and options**: comfort window (start, end,
days), presence entities, whether presence can override the schedule,
door/window sensors, manual temperature override duration, history days
for thermal inertia, plan refresh interval, and simulation mode.

## 4. Editing a zone

Settings → Devices & services → Climate Orchestrator → the zone you want
→ **Configure**. A single form opens with every field pre-filled. Saving
reloads the whole zone.

## 5. Doors and windows

Any declared door/window sensor that's "open" pauses the zone **instantly**
(via HA's event bus, no cycle to wait for), regardless of the plan or
mode. Once closed, it returns to the automatic calculation on its own.

## 6. Mode vs. temperature: two different behaviors

- **Changing the MODE** (off / heat / cool / auto) from the thermostat
  card, Google Home, Alexa, or a Matter/HomeKit bridge is a choice that
  **sticks** — it doesn't expire on its own, it's restored across
  restarts, like any real thermostat. Locking a `heat_cool` zone to "cool
  only" for summer, for example, stays that way until you change it.
- **Changing the target TEMPERATURE** is a **temporary** override: it
  lasts however long you configured (2h by default) and afterwards the
  zone returns to the automatic plan on its own.

## 7. Safety protection

Regardless of schedule, presence, or manual mode, a heating zone never
lets the temperature drop more than 3°C below its "away" level (frost
protection), and a cooling zone never lets it rise more than 3°C above
(heat-stroke protection). Not configurable on purpose — it's the last
safety net.

## 8. Learned thermal inertia

Only learned for the side (heat and/or cool) that has a switch actuator —
the integration knows for certain when it was on. With a delegated
`climate.*` there's no reliable way to know, so that side keeps
conservative defaults (flagged `thermal_model_reliable: false` in the
entity's attributes) until you declare a switch, or forever if your setup
is entirely delegated `climate.*`.

## 9. Simulation mode

With "Simulation mode" on for a zone (default), the integration computes
and publishes what it would do (visible in the entity's attributes), but
never sends a real command to any actuator. Review the `reason` attribute
for a few days before turning it off.
