# Climate Orchestrator — Setup guide

## 1. Prerequisites

- The official **Mosquitto broker** add-on installed and running (Settings
  → Add-ons → Store), with Home Assistant's MQTT integration configured
  (usually auto-configures itself when you install Mosquitto).
- A temperature sensor for each zone you want to manage (a `sensor.*`
  reporting °C).
- Each zone's actuator: either a `switch.*` that turns your heater/AC on
  and off, or an existing `climate.*` entity to delegate to.

## 2. MQTT broker

Under **Configuration → MQTT broker**:

- **Host**: `core-mosquitto` if you use the official Mosquitto add-on
  (default). For an external broker, its IP/hostname.
- **Port**: `1883` by default.
- **Username/Password**: whatever you configured in the Mosquitto add-on.

Without a reachable MQTT broker, zones are still planned and executed (if
they have an actuator), but won't appear as `climate.*` in Home Assistant
— the UI shows "MQTT: not connected".

## 3. Adding a zone

Under **Configuration → Zones → + Add zone**:

- **Name**: however you want it to appear in Home Assistant.
- **Capability**: heat only, cool only, or both (e.g. a reversible heat
  pump zone).
- **Priority**:
  - *Comfort*: acts as soon as temperature drifts out of range, no
    waiting. Recommended for maximum comfort without thinking about
    savings.
  - *Savings*: uses the learned thermal inertia to start as late as
    possible while still arriving on time for each comfort window — fewer
    actuator-on hours, in exchange for trusting the learned model.
  - *Manual*: the zone never decides on its own; it only reflects what you
    command by hand from the Home Assistant thermostat.
- **Temperature sensor**: required.
- **Humidity sensor / own outdoor sensor**: optional.
- **Actuator**:
  - *Switch*: declare the heat and/or cool `switch.*`. The add-on turns
    them on/off itself with hysteresis and anti short-cycling.
  - *Delegate to climate.\**: declare the existing `climate.*`; the add-on
    only sends it mode and target temperature.
- **Temperatures**: comfort (when the schedule window is active or
  someone's present), eco (outside the window, nobody present) and away
  (reference, see safety minimum below). Hysteresis: margin before
  acting/stopping.
- **Anti short-cycling**: minimum seconds the actuator must stay on/off
  before it can switch again.
- **Presence**: `person.*`/`device_tracker.*`/`binary_sensor.*` entities —
  if any is "home"/"on", the zone counts as occupied RIGHT NOW (future
  presence is never predicted, that would be a black box). With "presence
  can override the schedule" on: someone home bumps to comfort even if
  the schedule says otherwise; nobody home drops to eco even if the
  schedule says comfort (the latter only in "savings" priority).
- **Schedule**: "comfort" windows per weekday. Outside them, the zone is
  in "eco". With no window declared, the zone stays in "eco" all day
  (only acting for the safety minimum).

## 4. Safety protection

Regardless of schedule or presence, a heating zone never lets the
temperature drop more than 3°C below its "away" level (frost protection),
and a cooling zone never lets it rise more than 3°C above (heat-stroke
protection). Not configurable on purpose — it's the last safety net.

## 5. Learned thermal inertia

Only learned for zones with a "switch" actuator (the add-on needs to know
for certain when it was on). It needs at least a handful of continuous
20+ minute runs in the same state in Home Assistant's history — usually a
few days of real use. Until then, conservative defaults are used, and the
UI says so ("thermal inertia: initial estimate").

## 6. Manual override from Home Assistant

Changing mode or target temperature from the Home Assistant thermostat
card (or Google Home/Alexa) puts that zone into "manual override" for 2
hours (fixed in this version). While it lasts, the engine stops deciding
for that zone on its own; afterwards it returns to the automatic plan by
itself. It can be cleared early by hand from the "Current status" tab.

## 7. Simulation mode

With "Simulation mode" on (default), the add-on computes the plan and
publishes it to Home Assistant, but NEVER turns anything on/off or sends
commands to a real actuator. Review its reasoning and timing for a few
days before turning it off.
