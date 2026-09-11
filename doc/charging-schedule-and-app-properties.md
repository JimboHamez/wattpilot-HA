# Charging schedule and app-only properties (firmware 43.4, observed 2026-09-11)

Notes from reading a Wattpilot Home 11 J (serial 91111999, firmware 43.4) over the
local WebSocket API with `wattpilot-api` 1.4.0, while it was charging a 2026 Subaru
Trailseeker. None of the properties below are exposed by the integration today, and
one of them carries a value the library's API definition does not know.

## The schedule the app sets is three properties

The Wattpilot app's "charging times" (allow / block charging by time of day) are
stored on the charger as `sch_week`, `sch_satur` and `sch_sund`. Each is an object:

```
sch_week: {
  control: 3,
  ranges: [
    { begin: {hour: 0, minute: 0, second: 0}, end: {hour: 6,  minute: 0, second: 0} },
    { begin: {hour: 8, minute: 0, second: 0}, end: {hour: 15, minute: 0, second: 0} },
  ]
}
```

Weekday, Saturday and Sunday were identical on this charger (00:00–06:00 and
08:00–15:00). They are read/write, so the schedule could be shown and edited from
Home Assistant — today it is only visible in the app.

**`control = 3` is not in the API definition.** `wattpilot.yaml` documents the
`control` enum as `Disabled=0, Inside=1, Outside=2`; the charger reports `3`.

The iOS app's scheduler screen (same layout for workdays, Saturday and Sunday) has
three controls: **Limit charging times** (toggle), **Charge with PV surplus** (toggle)
and **Times** (the ranges). With both toggles on the charger reports `3`, which reads
as a bitmask rather than an enum:

| Limit charging times | Charge with PV surplus | `control` |
|----------------------|------------------------|-----------|
| off                  | –                      | 0         |
| on                   | off                    | 1         |
| on                   | on                     | 3 (= 1 + 2) |

That is: bit 0 limits charging to the ranges, bit 1 allows PV-surplus charging outside
them. Confirmed 2026-09-11 by switching "Charge with PV surplus" off on the workdays
screen: `sch_week.control` went 3 → 1 while `sch_satur` and `sch_sund` stayed at 3 —
so the toggles are per day-type, and the app writes only the day-type being edited.
The library's `Outside=2` is that second bit seen on its own. An entity for this field
should treat it as flags and pass unknown values through rather than raise.

The scheduler's effect is visible through `modelStatus`, which the integration already
decodes: `5 NotChargingBecauseScheduler` when a window blocks charging. During this
observation `modelStatus` was `12 ChargingBecausePvSurplus` with `lmo = 4` (Eco): the
window allowed charging and eco logic set the current from surplus.

## Properties the app uses for its range and energy figures

These are described in the API definition as "only stored for app" — the charger does
nothing with them, but the app's numbers are computed from them, and writing them over
the API changes what the app shows.

| key   | seen  | meaning                                                                 |
|-------|-------|-------------------------------------------------------------------------|
| `cco` | 18    | Car consumption, kWh/100 km. The app's "km per hour" is power ÷ `cco`. At 4.5 kW this gives the ~25 km/h the app showed; the car's measured figure was 14.5–17.5 kWh/100 km. |
| `esk` | true  | Whether the energy limit is shown in kWh rather than km.                 |
| `dwo` | null  | Charging energy limit for this session, Wh (`null` = off). Not the next-trip energy. |
| `fte` | 74000 | Next-trip energy, Wh. Already exposed as a number entity.               |
| `ftt` | 21600 | Next-trip time, seconds.                                                |
| `tpa` | 4493  | 30 s average total power, used by the charger for next-trip prediction. |

`cco` and `dwo` are the useful ones. A number entity for `cco` would let an automation
keep the app's range estimate honest from a measured efficiency; `dwo` set at plug-in
from a known state of charge gives an AC charger a "charge to N %" it cannot do on its
own. (Either needs the car's SOC from elsewhere — the charger never sees it; the OCPP
`SoC` measurand is DC-only.)

Also seen and not exposed: `awpl`, the hourly price list for dynamic pricing (a list of
`{start, end, marketprice}`), and `awcp`, the current entry.

## Suggested integration work

1. Treat schedule `control` as flags (bit 0 = limit times, bit 1 = allow PV surplus
   outside the times) rather than the 0/1/2 enum, and tolerate unknown values.
2. Expose `sch_week` / `sch_satur` / `sch_sund` — at minimum as read-only sensors with
   the ranges as attributes, ideally editable through a service.
3. Number entity for `cco` (kWh/100 km) and for `dwo` (Wh, nullable).
4. Sensor for `tpa` (already used by the charger; a cheap "what the charger thinks the
   30 s power is").

## How this was read

```python
from wattpilot_api import Wattpilot, load_api_definition
wp = Wattpilot(host, password, auto_reconnect=False)
await wp.connect()               # then wait for wp.properties_initialized
props = wp.all_properties        # 558 keys on this firmware
defs = load_api_definition(split_properties=False).properties
```

## Appendix: documented properties with no entity (firmware 43.4, integration 0.9.2)

Diffed on 2026-09-11: the charger reported 558 properties, `wattpilot-api` 1.4.0
documents 313, the integration exposes 80 (typed client attributes plus catalog ids).
Excluding Wi-Fi, OTA, LED colour and identity housekeeping, these 58 documented
properties have no entity:

| group | keys |
|-------|------|
| scheduler / app-only | `sch_week` `sch_satur` `sch_sund` `cco` `dwo` `esk` `cdi` `tpa` |
| dynamic pricing | `awpl` (hourly price list) `awcp` (current slot) — `awp` is exposed |
| current limits | `ama` `mca` `acu` (effective allowed current) `amt` `adi` `al1`–`al5` `pnp` — `clp` gained the *Charging Current Preset* select after 0.9.2 (#18) |
| load balancing | `loa` `lof` `log` `lom` `lop` `los` `lot` `loty` `map` — only `loe` exists |
| PV / eco tuning | `po` `psh` `sh` `fzf` `zfo` `mci` `mcpd` `mcpea` `fsp` `psmd` `fsptws` `pwm` `ido` |
| timestamps / diagnostics | `lcctc` `lccfc` `lccfi` `lmsc` `lpsc` `lfspt` `msi` `etop` `fwc` `cpe` `cpr` `rcd` `ferm` `frm` |

Undocumented but possibly useful (no title in the API definition): `whs` / `whg`
(session Wh split, 13819 / 12.4 at the time — likely grid vs solar), `ws`, `rmav` /
`rmiv` (max / min mains voltage), `avgfhz`.

The client's `set_property()` has no generic service; everything else on the client
(`set_next_trip*`, cloud API toggle, firmware install) is wired.
