# Dynamic pricing: what the charger does today, and feeding it Amber

Observed 2026-09-11 on a Wattpilot Home 11 J, firmware 43.4, `cy = australia`, via
`wattpilot-api` 1.4.0. Candidate follow-up issues are at the end.

## What the charger does today

Eco mode (`lmo = 4`, the enum's "Awattar") combines PV-surplus charging with a spot-price
rule: charge when the market price is below `awp`. The price feed is aWATTar, selected by
`awc` (Austria=0, Germany=1). On this charger:

| key    | value                                   | note |
|--------|-----------------------------------------|------|
| `awc`  | 1                                       | Germany — on an Australian install |
| `awpl` | 18 hourly entries, 20.3 → 22.3 → 21.4 ct… | German market prices, fetched by the charger |
| `awcp` | `{start, end, marketprice}`             | current slot |
| `awp`  | 999999                                  | price cap effectively disabled |
| `ful`  | false (read-only)                       | "useDynamicPricing (Lumina, aWattar)" — a newer provider flag, not settable |

So the price rule is present but pointed at the wrong market, and disabled by the cap.
`modelStatus` was 12 (ChargingBecausePvSurplus); the price branch would report 7
(ChargingBecauseAwattarPriceLow).

Amber Electric is the obvious local feed: Home Assistant Core has an `amberelectric`
integration exposing the current general price and a forecast in c/kWh at 30-minute
resolution — the same unit and a compatible cadence.

## Option A — push Amber prices into `awpl` (untested)

`awpl` and `awcp` are marked read/write in the API definition. If the charger honours a
written list, an automation could write Amber's forecast into `awpl` (unix-second
`start`/`end`, `marketprice[]`) and the current slot into `awcp`, and `awp` becomes the
Amber price cap. The app would then show real prices, and the charger's own status
would explain price-driven charging.

Unknowns, each a five-minute test while `awp` is 999999 (so nothing changes behaviour):

1. Does a written `awpl` read back unchanged?
2. Does it survive the charger's own aWATTar refresh (check again after an hour)?
3. With `awp` lowered below the written price, does `modelStatus` go to 7?

If any of these fail, this option is dead and B is the answer.

## Option B — do the price logic in Home Assistant (works today)

Leave the charger's list alone. An automation on the Amber price entity sets
`frc = 2` (force on) when the price is below a threshold and `frc = 0` (neutral) otherwise,
so PV-surplus and scheduler logic resume in between. Everything needed exists in the
integration now, a firmware refresh cannot undo it, and it can use Amber's *forecast* to
pick the cheapest slots — which the charger's "below cap" rule cannot.

## Candidate issues

- **Expose `awpl` / `awcp` as sensors.** The price list the charger is acting on is
  invisible in HA; a forecast sensor with the hourly list as attributes makes the eco
  mode's behaviour explainable. (Today it would show German prices on this install,
  which is itself worth surfacing.)
- **Test and, if it works, document writing `awpl` from an external feed** (Option A).
  If it holds, an `amberelectric`-fed automation example belongs in the README's
  automations section.
- **README example for Option B** — Amber price → `frc`, with a forecast-based variant.
- **`awc` has no Australian value.** Worth a note in the README that the aWATTar feed is
  meaningless outside AT/DE and `awp` should stay disabled unless a list is supplied.
