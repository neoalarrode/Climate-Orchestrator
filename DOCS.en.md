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

**Step 1 — General**: zone name and priority. No capability
(heat/cool/both) is asked here: it's computed automatically from the
actuators you declare in step 3.

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

**Step 3 — Actuators**: three lists, add as many as you actually have of
each (you can combine all three in the same zone):

- **Delegated climate.\***: existing `climate.*` entities (a thermostatic
  valve, an air conditioner with its own electronics...). Each is
  governed by its OWN native `hvac_modes` — read live from the device
  itself, never declared by you. If it supports `heat`, it activates in
  `heat` when heating is needed; if it supports `cool`, in `cool` when
  cooling is needed; if it genuinely supports both (a reversible heat
  pump), it gets whichever is correct each time — a single command,
  never two stepping on each other. **There are no separate "climate for
  heat" / "climate for cool" fields** — it's the SAME list, and each
  entity contributes whatever it can actually do. This isn't limited to
  heat/cool: if the device also declares `dry` (dehumidify) or `fan_only`
  (fan only), this zone inherits those too — see point 9.
- **Heating switches** and **cooling switches**: unlike a `climate.*`, a
  switch can't self-report what it's for, so these do go in their
  matching list. The integration turns them on/off with hysteresis and
  anti short-cycling (minimum on/off time).

The zone's final capability (heat only / cool only / both) — and
therefore which modes Home Assistant and any Matter/HomeKit bridge
expose — is computed automatically from what you added here. A radiator
with a thermostatic valve and an air conditioner coexist in the same zone
without declaring anything else; heating and cooling are never activated
at once.

**Step 4 — Presets**: instead of a fixed schedule, you declare a list of
named presets with separate heat/cool setpoints, as text: `Comfort:
21/25, Away: 17/28, Party: 23/24` (a single value like `Comfort: 21`
works for a single-direction zone). As many as you want. Why no
schedule: a "07:00-23:00 = comfort" window doesn't know if anyone's
really in the room — presets, combined with real presence, adapt to
what's actually happening. This text only SEEDS the initial value: right
after the zone is created, each setpoint becomes its own `number.*`
entity (see point 5) — the text isn't read again.

**Step 5 — Automatic switching**: choose which of the presets you just
declared activates when there IS presence and which when there ISN'T.
These two are what "Auto" mode (the default active preset) uses.

**Step 6 — Safety limits**: hysteresis, and the **minimum and maximum
that are ALWAYS enforced**, no matter the active preset, presence, or
manual mode — the configurable-per-zone "never below 12°C in winter even
with nobody home, never above 30°C in summer". Also minimum on/off times
(only relevant with a switch actuator).

**Step 7 — Presence, doors/windows and options**: presence entities,
door/window sensors, history days for thermal inertia, forecast refresh
interval, the smart idle humidity threshold (see point 10), and
simulation mode.

## 4. Presence: the room's PHYSICAL sensors, not "home"

The presence field is meant primarily for the zone's own PHYSICAL
presence sensors — PIR, mmWave radar, an occupancy/motion
`binary_sensor` for that specific room: "is someone HERE right now?", not
"is someone home?". `person.*`/`device_tracker.*` are also accepted, as
an extra signal (useful mainly to know NOBODY is anywhere in the house),
but they aren't the primary use case. Future presence is never
predicted — that would be a black box — only what's measured right now.

## 5. Presets: automatic, or pinned by hand — and adjustable as entities

The **"Auto"** preset (the default) picks between the "with presence" and
"without presence" presets you declared, based on physical presence
measured now. Picking ANY OTHER preset by hand — from the thermostat
card, Google Home, Alexa, or a Matter/HomeKit bridge — is a **standing**
choice: it stays pinned (restored across restarts) until you set it back
to "Auto" yourself. Useful for "I'm staying on Vacation mode today even
if it detects presence" without disabling anything.

Each preset also exposes one or two `number.*` entities of its own (one
for its heat setpoint, one for its cool setpoint, depending on the
zone's capability) — e.g. "Comfort (heat)" and "Comfort (cool)". They can
be adjusted live from Lovelace or your own automation at any time; the
zone uses the live value of those entities to decide, not the text you
typed in the wizard (that only created them the first time).

There's one more preset, **"Manual"**, that you don't declare yourself:
it activates automatically as soon as you adjust the temperature
directly from the thermostat card (dragging the setpoint, not picking a
preset). It's just as standing as any other — it keeps that temperature
until you switch to another preset or back to "Auto" yourself, it doesn't
expire on its own. It has no `number.*` entity of its own (you set it
directly on the thermostat), but it's restored across restarts too.

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

## 9. "Auto" mode (Matter-ready), plus heat/cool separately

A zone with genuine heating AND cooling exposes FOUR modes: `off`,
`auto`, `heat`, `cool`. In "Auto" mode (the default) the zone has TWO
active setpoints at once (the heat and cool setpoints of the active
preset, see point 5): it heats if it drops below the heat one, cools if
it rises above the cool one, and does nothing in between — this is
exactly Matter's standard "Auto" System Mode (a low setpoint + a high
setpoint), so any Matter/HomeKit bridge recognizes it with no translation
or extra setup. If you'd rather lock the zone by hand to "heat only" or
"cool only" (e.g. turning cooling off for winter), you can still pick
those modes separately. A single-direction zone (heat only, or cool
only) still exposes just that one mode — "Auto" wouldn't make sense
there.

The mapping from Home Assistant's hvac modes to the Matter Thermostat
cluster's `SystemMode` (the one any bridge uses, including
`home-assistant-matter-hub`) is direct and needs no translation on our
part: `off`→Off, `heat`→Heat, `cool`→Cool, `heat_cool`→**Auto**,
`dry`→Dry, `fan_only`→FanOnly — exactly what this zone exposes (see point
10 for when `dry`/`fan_only` show up). Also, whenever there's more than
just "off" to offer, the zone declares on/off as a real switch (not just
one more mode buried in a dropdown) — this is what makes the power
button show up on any Matter/HomeKit/Google Home bridge instead of
staying hidden.

**Honest caveat**: even though Matter carries `dry`/`fan_only` fine,
Apple Home, Google Home and Alexa typically limit what they SHOW for a
thermostat to Heat/Cool/Auto/Off — they may not surface those as a
button in those specific apps even though the data travels correctly.
From Home Assistant itself (thermostat card, Lovelace) they're always
visible.

## 10. `dry`/`fan_only` modes: by hand, or automatic without leaving Auto

A delegated `climate.*` can declare more than heat/cool in its own
`hvac_modes` — many air conditioners also support `dry` (dehumidify) or
`fan_only` (fan only). Climate Orchestrator detects those the same way as
heat/cool — live, never by hand — and this zone adds them as selectable
modes. A radiator that only declares `off`/`heat` still contributes
neither. There are two distinct ways to reach them:

- **By hand**: picking "Dry" or "Fan only" from the thermostat card (or
  Google Home/Alexa/Matter) DOES change the zone's mode to that — like
  any other mode — and sends it straight to whichever device supports
  it; it doesn't chase any temperature, it's a direct choice of yours, as
  standing as any other mode.
- **Smart idle, automatic — no separate switch to turn on**: as soon as
  the zone no longer needs heat or cool (within margin) and **is still in
  its most automatic mode** (Auto for a zone with genuine heating and
  cooling; the only mode a single-direction zone has — if you locked the
  zone by hand to "heat only" on one that also has cooling, that's
  clearly manual control, and smart idle stays out of it), the delegated
  device can make itself useful instead of turning off completely:
  - **Fan**, for comfort, if the delegate supports `fan_only`.
  - **Dehumidify**, taking priority over fan, if the delegate supports
    `dry` and measured humidity goes over a threshold — configurable per
    zone (wizard step 7, or "Configure"; 65% by default), like any other
    limit. Also needs a humidity sensor declared in step 2.

  **Here, unlike picking it by hand, the zone's hvac mode never
  changes** — to the user/Matter/HomeKit it stays "in Auto" even while
  the device underneath is fanning or dehumidifying for a while, only
  the command sent to the delegate changes.

In both cases, if the delegate doesn't support what's asked of it, it's
simply turned off — nothing is ever forced on it.

## 11. Safety limits

Configurable per zone (wizard step 6): a minimum that heating always
respects and a maximum that cooling always respects, no matter the active
preset, presence, or manual mode. Built exactly for "I don't care if
nobody's home, never below X in winter / never above X in summer".

## 12. Sensor deviation on delegated climate.\*

A delegated `climate.*` (an air conditioner, a thermostatic valve...)
decides on its own when it's satisfied based on **its own internal
sensor** — which almost never matches exactly the external sensor you
declared for the zone (step 2): by location, calibration, or simply by
being inside the device itself, it typically reads differently than a
wall sensor. If it were sent the real setpoint as-is, the delegate could
consider itself satisfied before or after the external sensor — the one
that actually governs this zone — reaches that temperature.

Climate Orchestrator corrects this every cycle: it measures the
deviation RIGHT NOW between the delegate's own sensor (its
`current_temperature` attribute) and the zone's external sensor, and
adds it to the real setpoint before sending it — so the delegate
considers itself satisfied exactly when the external sensor would too,
always clamped to the range the delegate itself accepts (its own
`min_temp`/`max_temp`) so it's never asked for something outside what it
supports. Recalculated every time (the deviation isn't constant — it
varies while heating/cooling is actually running) and visible in the
zone entity's `delegate_temperature_deviations` attribute, one per
delegated `climate.*`. With no detectable deviation — the delegate
doesn't report its own `current_temperature`, or the external sensor
isn't available right now — the real setpoint is sent unmodified, never
a made-up correction.

## 13. Mode vs. preset vs. temperature

None of these expire on their own anymore — all three are **standing**
choices, each restored across restarts:

- **Changing the MODE** (off / auto — or off / heat for a
  single-direction zone, see point 9).
- **Changing the PRESET** by hand, any declared one or "Auto" (see point 5).
- **Adjusting the TEMPERATURE** directly from the thermostat card:
  switches the zone to the "Manual" preset (see point 5) — it keeps that
  temperature until you switch to another preset yourself.

## 14. Learned thermal inertia

Learned from both actuator types, not just switches: a plain `switch.*`
uses its own on/off history directly; a delegated `climate.*` uses the
`hvac_action` attribute from ITS OWN history (heating/cooling vs.
idle/off) — most climate integrations report it. If a particular entity
never does, that zone simply keeps conservative defaults (flagged
`thermal_model_reliable: false` in the entity's attributes) — never a
made-up number.

## 15. Simulation mode

With "Simulation mode" on for a zone (default), the integration computes
and publishes what it would do (visible in the entity's attributes), but
never sends a real command to any actuator. Review the `reason` attribute
for a few days before turning it off.
