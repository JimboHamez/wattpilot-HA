# Fault report — Wattpilot Flex 22, firmware 43.4

**Subject:** PV-surplus charging is denied by the flexible-tariff price rule while the flexible
tariff feature is switched off, in a country the price feed does not cover

| | |
|---|---|
| Reported by | [your name] |
| Contact | [your email / phone] |
| Site | Australia (charger time zone `aest`, UTC+10) |
| Date of observation | 30 July 2026 |
| Case / support ID | [if you already have one] |
| Dealer / installer | [optional] |

## Device

| Property | Value |
|---|---|
| Model (`typ` / `styp`) | `wattpilot_flex` / `wattpilot_flex_c6` |
| Variant (`var`) | 22 (22 kW) |
| OEM (`oem`) | `fronius` |
| Firmware (`fwv`, `onv`) | **43.4** |
| Firmware build (`apd`) | `wattpilot_flex-secure-release` 43.4, built `Apr 28 2026 12:58:50`, sha256 `8154f5f8ffcfc41f428b355625604c86ffd158ac` |
| Serial (`sse`) | [please fill in — withheld from this document] |
| Charger clock (`loc`) | `2026-07-30T13:25:53 +10:00` (`tzt` = `aest`, `tds` = 3) |
| Paired PV system (`cci`) | deviceFamily `DataManager`, model `PILOT`, paired, connected, `status` 0 (`message` "ok") |

## Summary

In Eco mode with PV-surplus charging enabled, the charger repeatedly stopped charging and reported
`modelStatus` **17 — `NotChargingBecauseFallbackAwattar`**, i.e. the aWATTar/Lumina Strom market
price rule denied charging. This happened **while the flexible tariff setting was off**
(`ful = false`), and while the PV system was exporting up to 1.9 kW to the grid.

The deciding factor was **Max Price** (`awp`), which stood at **3** (EUR cent) against a fetched
market price of **13.76** cent for the current hour. After Max Price was raised to `999999`,
charging ran continuously under `modelStatus` 12 — `ChargingBecausePvSurplus`, with no other
setting changed.

The site is in Australia. The country list for the price feed (`awc`) offers no Australian option
and, as far as we can determine, no way to select "no market" — so the price rule cannot be turned
off at its source, and a low Max Price silently blocks solar charging.

## Expected behaviour

1. With the flexible tariff feature switched off, the market price rule should not influence
   whether charging is allowed.
2. In a country with no supported market price feed, it should be possible to disable the price
   feed entirely, or the price rule should not be applied.
3. PV-surplus charging (Eco mode, surplus above the configured start power) should not be denied
   for a price reason when it is not drawing from the grid.

## Observed behaviour

### Evidence A — Max Price = 3, charging repeatedly denied

Sampled every 20 s from the charger's own property set. Times are UTC; add 10 h for charger local
time (13:08–13:10 AEST). `pGrid` is negative when exporting.

| Time (UTC) | `car` | `modelStatus` | `alw` | Charge power | `pGrid` | `pPv` |
|---|---|---|---|---|---|---|
| 03:08:32 | Charging | 12 `ChargingBecausePvSurplus` | true | 1392 W | +1811 W | 2770 W |
| 03:08:52 | Complete | **17 `NotChargingBecauseFallbackAwattar`** | false | 0 W | +350 W | 4338 W |
| 03:09:12 | Complete | **17 `NotChargingBecauseFallbackAwattar`** | false | 0 W | **−810 W** | 5624 W |
| 03:09:32 | Complete | **17 `NotChargingBecauseFallbackAwattar`** | false | 0 W | **−1912 W** | 6531 W |
| 03:09:52 | Charging | 12 `ChargingBecausePvSurplus` | true | 10 W | −901 W | 5400 W |
| 03:10:12 | Charging | 12 `ChargingBecausePvSurplus` | true | 1397 W | +1419 W | 4190 W |

Note the 03:09:32 row: the site was exporting 1912 W, above the configured surplus start power of
1400 W (`fst`), and the charger still refused to charge, giving the price fallback as the reason.
`cll.pvSurplus` read 6 A during this window.

Configuration at the time:

| Setting | Value |
|---|---|
| `lmo` (charging mode) | 4 (Eco) |
| `ful` (flexible tariff) | **false — switched off** |
| `awp` (Max Price) | **3** (EUR cent) |
| `awc` (price country) | 1 (Germany) |
| `awcp` (current price window) | 13.76 cent, 2026-07-30 03:00–04:00 UTC |
| `fup` (PV surplus) | true |
| `fap` (charge pause allowed) | true |
| `fst` (surplus start power) | 1400 W |
| `fmt` (min charge time) | 300000 ms |
| `frc` (force state) | 0 (neutral) |
| `amp` | 32 A |
| `psm` | 1 |

### Evidence B — Max Price = 999999, same conditions, charging uninterrupted

Max Price was raised to `999999`. Nothing else was changed: still `lmo` 4 (Eco), `ful` false,
`awc` 1 (Germany), `fup` true, `fst` 1400 W, and the same market price of 13.76 cent for the hour.

Ten consecutive samples over 3 min 40 s (charger local 13:25:53 – 13:28:52 +10:00):

| Time (charger local) | `car` | `modelStatus` | `alw` | Charge power | `pGrid` | `pPv` | `cll.pvSurplus` |
|---|---|---|---|---|---|---|---|
| 13:25:53 | Charging | 12 `ChargingBecausePvSurplus` | true | 1830 W | −2197 W | 5407 W | 32 A |
| 13:26:12 | Charging | 12 | true | 1821 W | −2062 W | 5165 W | 32 A |
| 13:26:32 | Charging | 12 | true | 1828 W | −2314 W | 5524 W | 32 A |
| 13:26:52 | Charging | 12 | true | 1823 W | −1278 W | 5519 W | 32 A |
| 13:27:12 | Charging | 12 | true | 1819 W | −1255 W | 5462 W | 32 A |
| 13:27:32 | Charging | 12 | true | 1819 W | −1102 W | 5473 W | 32 A |
| 13:27:52 | Charging | 12 | true | 1819 W | −1082 W | 5458 W | 32 A |
| 13:28:12 | Charging | 12 | true | 1819 W | −1099 W | 5469 W | 32 A |
| 13:28:32 | Charging | 12 | true | 1816 W | −1023 W | 5478 W | 32 A |
| 13:28:52 | Charging | 12 | true | 1819 W | −951 W | 5479 W | 32 A |

No occurrence of `modelStatus` 17, and the surplus current limit rose from 6 A to 32 A.

**Max Price is therefore the deciding input, and it is being applied even though the flexible
tariff feature is switched off.**

## The country list

The price country setting (`awc`) offers 51 values in the documented API — Austria, Germany,
France, the Nordic and Baltic markets, and so on. We can find **no value for Australia and no
"none" / "disabled" value**. Two questions for you:

1. Is there an `awc` value (or another setting) that disables the market price feed entirely?
2. If not, can the price rule be suppressed when no supported market applies?

Worth noting: the charger already knows where it is — its own time zone is reported as `aest`
(UTC+10), while the tariff feed was configured for the German market and priced in EUR cent.

## Impact

Solar self-consumption charging is silently prevented. The charger reports the car as `Complete`
during the blocked windows, so from the app and from the car's side it looks as though charging
simply finished, with no indication that a price rule in an inapplicable market denied it. Any
owner outside the covered countries who has a low Max Price value will lose surplus charging
without an obvious cause.

## Workaround in place

Max Price set to `999999`, which makes the price rule unreachable in practice. This is a
workaround, not a fix — the price feed is still fetched and the rule is still evaluated.

## Requested actions

1. Confirm whether `modelStatus` 17 (`NotChargingBecauseFallbackAwattar`) being reached with
   `ful = false` is intended behaviour. If it is, please document that Max Price applies
   independently of the flexible tariff switch.
2. Provide a way to disable the market price feed where no supported market exists — either an
   `awc` "none" value, or by not applying the price rule when the feature is off.
3. Consider not shipping / not retaining a Max Price value low enough to block charging outright,
   or warning in the app when the current market price exceeds Max Price and charging is being
   denied for that reason.
4. Consider surfacing the denial reason in the app. `modelStatus` already carries it; the app
   showed only that charging had stopped.

## How the data was captured

The values above are the charger's own properties, read over its local WebSocket API on the LAN
using the open-source `wattpilot-api` Python client, in a read-only Home Assistant integration.
No settings were written during the measurements; Max Price was changed by hand between the two
windows. `modelStatus` code names are quoted from the go-e Charger API v2 documentation, which the
Wattpilot shares. If you would prefer this reproduced through the Fronius app or a service log
instead, tell us what to capture and we will do so.

Happy to provide the full property dump (with serial and credentials removed), longer sample runs,
or to re-test any firmware you would like checked.
