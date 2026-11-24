# Base Power - Home Assistant Integration (Unofficial)

[![GitHub Release](https://img.shields.io/github/v/release/jerrit/ha-base-power?style=flat-square)](https://github.com/jerrit/ha-base-power/releases)
[![HACS Validation](https://github.com/jerrit/ha-base-power/actions/workflows/validate.yml/badge.svg)](https://github.com/jerrit/ha-base-power/actions/workflows/validate.yml)
[![Home Assistant Marketplace](https://img.shields.io/badge/Home%20Assistant-Marketplace-41BDF5.svg?style=flat-square)](https://www.home-assistant.io/integrations/marketplace/)
[![License: MIT](https://img.shields.io/github/license/jerrit/ha-base-power?style=flat-square)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/jerrit/ha-base-power?style=flat-square)](https://github.com/jerrit/ha-base-power/stargazers)
[![HA Version](https://img.shields.io/badge/HA-2025.1%2B-blue?style=flat-square&logo=home-assistant)](https://www.home-assistant.io/)

Unofficial Home Assistant integration for [Base Power](https://basepowercompany.com/) home battery systems. This project is not affiliated with or endorsed by Base Power.

> Monitor your Base Power battery level, power usage, backup time, and energy consumption directly in Home Assistant.

## Supported hardware

| Battery | SKU | Capacity | Status |
|---------|-----|----------|--------|
| Single Battery | `GRO_SINGLE` | 25 kWh | Confirmed |
| Double Battery | `GRO_DUAL` | 50 kWh | Confirmed |
| Wall Battery | `GRO_WALL` | 20 kWh | Not confirmed yet |
| Base Core | `BASE1_SINGLE` | 39.2 kWh | Confirmed (issue #26) |
| Dual Base Core | `BASE1_DUAL` | 78.4 kWh | Not confirmed yet |

"Not confirmed yet" means the SKU and size come from Base's checkout, but no owner has sent a report. Live readings (battery level, power, grid) should work on any Base battery. If yours isn't confirmed, or Home Assistant logs a warning about unknown battery hardware, please open an issue with your [diagnostics file](#reporting-a-problem). Base reports a single Base Core as `GEN3 SINGLE`. Dual Core owners especially: `GEN3 DUAL` isn't mapped yet, so the integration estimates the size from Base's backup time (see [How capacity is worked out](#how-capacity-is-worked-out)), and your diagnostics file shows whether the estimate is right.

## Features

- **Battery Level** — State of charge, updated about once a minute. Base sends the exact percent only during an outage. On grid it's worked out from Base's 750 W backup time (see [Battery Level on grid](#battery-level-on-grid))
- **Backup Time (Current Load)** — Hours of backup if the house keeps using power at its current rate
- **System Capacity** — detected from your battery model: Wall 20 kWh, original 25 or 50 kWh, Base Core 39.2 or 78.4 kWh. If Base doesn't report a known model, it's estimated from Base's backup time, and the capacity chosen in the integration options is the last fallback
- **Current Power** — Live home load in watts from the battery snapshot
- **Grid Power** — On when grid voltage is at least 100 V and Base has not flagged an outage. Off during an outage, when voltage falls below 50 V, or when the battery reports an off-grid state. Unknown when that telemetry is missing
- **Battery State** — The battery's mode from Base's newer API: on grid, outage, no home power, overcurrent, overcurrent standby, or telemetry unavailable
- **Backup Time (750 W Essentials)** — Hours of backup if you cut down to essentials only (about 750 W: fridge, lights, Wi-Fi). Usually much longer than the current-load estimate
- **24-hour Energy Mix** — Grid, solar, and battery energy to the home over the last 24 hours
- **Grid Support** — Whether Base is using the battery to support the grid, and how many events ran in the last 24 hours
- **Costs** — Energy cost today, bill so far, projected bill, and solar buyback credit for accounts billed by Base
- **Energy Monitoring** — 15-minute interval energy from recent usage, for the Energy Dashboard
- **Daily Statistics** — Peak, low, total, and interval count from today's usage
- **Battery Status** — Operating state (Installed, In Service, etc.)
- **Battery Charging** — On while power goes into the battery; otherwise on after the state of charge rises, off after it falls, while the battery is full, or while it sits idle
- **Solar Status** — Whether the dashboard reports solar
- **WiFi** — The network the battery is on, as Base reports it, and its signal strength
- **Battery Connectivity** — Whether the gateway is connected
- **Billing** — Bill amount and due date when the account returns them

Home Usage Today, Solar to Home, and Daily Total (Home Usage) use today's hourly samples from the v2 daily overview (`GetDailyOverview`, which takes the address string). `MobileGetUsageEnergy`, which takes the numeric service location id, runs only when the overview fails or has no hourly samples for today. Daily Total (Home Usage) is the same home load sum. Battery to Home is 0 when the 24-hour mix shows no battery energy, and unknown otherwise, because Base does not split battery energy by day. Recent usage, billing, cycles, and Wi-Fi keep the address string. Grid amps are ``power_from_grid_kw * 1000 / voltage`` when both are present. A negative value is export. The daily average grid interval is today's usage total divided by the number of 15-minute intervals.

## Installation

### Marketplace (Recommended, Home Assistant 2026.11 and newer)

[![Open in Home Assistant](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jerrit&repository=ha-base-power&category=integration)

1. Go to **Settings** → **Marketplace**
2. Search for **Base Power**
3. Click **Install**. Setup starts right away, no restart needed

Already installed through HACS? Home Assistant 2026.11 moves it to the Marketplace automatically, and updates keep coming.

### HACS (Home Assistant before 2026.11)

1. Open HACS in Home Assistant
2. Search for **Base Power**
3. Click **Download**
4. Restart Home Assistant

### Trying a beta

Betas are off by default. Go to **Settings** → **Devices & services** → **Marketplace**, open the **Base Power** device, and enable its **Pre-release** switch (it's hidden until you enable it). Turn it on, and the beta shows up as an update. Turn it off to go back to stable releases.

### Manual

1. Copy the `custom_components/base_power` folder to your `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for "Base Power"
3. Enter your Base Power account email
4. Check your email for a verification code and enter it
5. Your account is configured automatically — no Service Location ID required

The device is named after your site's street and city, for example "Base Power 1 Main St, Austin", so homes with more than one site can tell them apart. That name shows on dashboards and in screenshots. To hide your address, rename the device: **Settings** → **Devices & services** → **Base Power** → the device → the pencil icon. Renaming the device doesn't change entity IDs, so dashboards and automations keep working.

## Entities

### Sensors

| Entity | Description | Unit |
|--------|-------------|------|
| Battery Level | State of charge. Exact from the battery snapshot during an outage; on grid, estimated from the 750 W backup time. The `source` attribute shows `snapshot` or `backup_hours_estimate` | % |
| Battery Backup Time (Current Load) | Backup remaining if the house keeps drawing what it draws right now. Moves up and down with your load | hours |
| Battery Count | Battery units, from the detected model, otherwise from the battery type Base reports (single, dual, or wall), otherwise the chosen capacity. `source` shows which. No long-term statistics since 1.14.0 | — |
| Battery Status | Operating state (Installed, In Service, etc.) | — |
| System Capacity | Detected from the battery SKU, then the battery model, then Base's nameplate, then an estimate from Base's backup time, then the Battery Capacity option. See [How capacity is worked out](#how-capacity-is-worked-out). Attributes show `capacity_source`, `estimated_usable_kwh`, `estimated_capacity_kwh`, `nameplate_capacity_kwh`, `hardware_sku`, and the raw `batteries` list | kWh |
| Current Power | Live home load, or the latest 15-minute average kW when live load is missing | W |
| Current Interval Energy | Energy for the current 15-min period (average kW ÷ 4) | kWh |
| Daily Peak | Highest 15-min interval today | kWh |
| Daily Low | Lowest 15-min interval today | kWh |
| Daily Total Energy | Today's 15-min intervals only, each average kW ÷ 4 | kWh |
| Intervals Today | Number of data intervals today | — |
| Home Usage Today (All Sources) | Everything the house used today, whatever the source. Base reports home load here, not grid draw | kWh |
| Solar to Home Energy | Energy from solar panels | kWh |
| Battery to Home Energy | Energy discharged from battery today. 0 when the 24-hour mix shows none, otherwise unknown | kWh |
| Total Home Energy | Everything the house used today. Same value as Home Usage Today (All Sources) | kWh |
| Self-Sufficiency | % of home usage covered by solar + battery, capped at 100 | % |
| Grid Current | Grid kW × 1000 ÷ volts. Negative means export | A |
| Grid Voltage | Latest grid voltage, updated every minute | V |
| Power from Grid | Live power drawn from the grid. Negative means export | W |
| Battery Power | Live battery power. Positive means the battery is discharging, negative means it's charging | W |
| Solar Power | Live solar output. Only on homes with solar | W |
| Solar Export Today | Solar energy sent to the grid today. Only on homes with solar | kWh |
| Daily Total (Home Usage) | Same value as Home Usage Today (All Sources). Off by default on new installs | kWh |
| Daily Average Grid Interval | Today's usage total ÷ intervals today | kWh |
| Battery State | On grid, outage, no home power, overcurrent, overcurrent standby, or telemetry unavailable. Attributes show `observed_at`, and off grid, `state_of_energy_percent` and `overcurrent_limit_kw` | — |
| Battery Backup Time (750 W Essentials) | Backup remaining if the house is cut down to a steady 750 W of essentials. Base's "essentials only" estimate. Updated every minute | hours |
| Grid to Home (24 h) | Grid energy to the home over the last 24 hours | kWh |
| Solar to Home (24 h) | Solar energy to the home over the last 24 hours | kWh |
| Battery to Home (24 h) | Battery energy to the home over the last 24 hours | kWh |
| Grid Support Events (24 h) | Grid support events in the last 24 hours. Attributes show the latest `last_start` and `last_end` | — |
| Energy Cost Today | Estimated grid energy cost since midnight. The Base app's daily overview shows the last 24 hours instead; that figure is the `cost_last_24h` attribute. Unknown without Base billing | $ |
| Bill to Date | Net amount accrued in the current billing cycle. Unknown without Base billing | USD |
| Projected Bill | Projected consumption charges for the cycle. Unknown without Base billing | USD |
| Solar Buyback Credit | Solar credit in the current cycle. Unknown without Base billing | USD |
| Home Usage (Total) | Running total of everything the house used. Never resets, and counts the last hour of each day that the daily sensors miss. Best for the Energy Dashboard | kWh |
| Solar to Home (Total) | Running total of solar used by the home. Only on homes with solar | kWh |

Base posts each day's last hour a few minutes after midnight, after the daily sensors have reset, so the daily sensors miss that hour. The (Total) sensors add every hour once, whenever it arrives, and keep their count across restarts. They start with the last 24 hours when first added.

Base's hourly data only reaches back 24 hours. If Home Assistant or Base is down for longer, the hours in between never arrive. Every 6 hours the integration compares each finished day with Base's daily total and adds what is missing, so the (Total) sensors catch up. The catch-up lands at the time it is found, not at the hours it was used. Days before the sensors were first added are not filled in.

Usage and energy sensors turn **unavailable** when Base has sent no new usage data for four refreshes in a row (about an hour). Live sensors such as Grid Power and Grid Voltage are not affected.

Homes that Base doesn't supply with energy (battery only) don't get the billing sensors: Last Bill Amount, Bill Due Date, Energy Cost Today, Bill to Date, Projected Bill, and Solar Buyback Credit. Any of them that still has a value stays.

The 24-hour sensors are a sliding window, so they have no state class and do not feed long-term energy statistics. Use the (Total) sensors for the Energy Dashboard.

### Binary Sensors

| Entity | Description |
|--------|-------------|
| Grid Power | Grid supply is available. Attributes show `voltage_volts`, `active_outage`, `voltage_source`, and `battery_state`. An off-grid `battery_state` turns it off even while voltage and `active_outage` look normal |
| Solar Connected | Whether solar panels are present |
| Battery Connected | Gateway is connected |
| Battery Charging | On while Battery Power shows at least 0.2 kW going into the battery, even at 99 or 100%, and off as soon as that stops. Without a power reading, it's on after the state of charge rises. One rise holds it on for an hour; on a steady charge the hold follows the pace, up to 3 hours. A 1% bounce back up right after a drop doesn't count. Off after it falls, while the battery is full, while the battery is powering the home, and while Battery Power shows it idle. Unknown only when Base sends no power reading and the charge hasn't moved yet |
| Grid Support | On during a grid support event: when the grid is up and the battery is powering the home (checked every 60 seconds), or when Base's event list shows one running. Grid support never turns Grid Power off, so outage automations won't fire on it. Attributes show `detected_by` and `battery_output_kw` |

### Diagnostic Sensors

| Entity | Description | Unit |
|--------|-------------|------|
| WiFi Signal | Signal strength of the battery's WiFi network. The `last_updated` attribute shows when Base measured it | % |
| Battery Hardware | Hardware SKU from the battery account, for example `GRO_SINGLE`. Attributes show `plan_name` when Base sends one, and `battery_models` (generation and type of each battery, for example `GEN1 SINGLE`) | — |
| WiFi SSID | The network the battery is on, as Base reports it. Attributes show `state` (connected, connecting, not_connected) and `source` (`base`, or `option` when it comes from the integration options) | — |
| Next Service Visit | The next service visit Base has scheduled. Unknown when none is planned | time |
| Last Bill Amount | Amount of your last energy bill. Before 1.14 this was named Bill Amount and showed spend so far, the same as Bill to Date | USD |
| Bill Due Date | Due date of the oldest unpaid energy bill. Unknown when nothing is owed or Base doesn't bill you for energy. Attributes show `amount_due`, `past_due`, `outstanding_balance`, `next_bill_date`, and `next_autopay_date` | date |
| Account ID | Base's account id for the site. Named Asset ID before 1.14 | — |

## Energy Dashboard

Base doesn't report a grid-only total. **Home Usage (Total)** is everything the house used, solar and battery included. So which sensors to use depends on whether you have solar.

**No solar:**

| Energy Dashboard slot | Sensor to use |
|----------------------|---------------|
| Grid consumption | Home Usage (Total) |
| Grid cost (optional) | Energy Cost Today (Base-billed homes only) |

**With solar:** don't put Home Usage (Total) under Grid consumption and Solar to Home (Total) under Solar panels together. Home usage already includes the solar, so the dashboard would count it twice. Use a grid meter from your utility or a CT clamp if you have one. Otherwise, make a grid import sensor from the two totals. Add this to `configuration.yaml` (check the entity IDs against yours):

```yaml
template:
  - sensor:
      - name: "Base Power Grid Import (Total)"
        unique_id: base_power_grid_import_total
        unit_of_measurement: kWh
        device_class: energy
        state_class: total_increasing
        state: >
          {{ (states('sensor.base_power_home_usage_total') | float(0)
              - states('sensor.base_power_solar_to_home_total') | float(0)) | round(3) }}
        availability: >
          {{ has_value('sensor.base_power_home_usage_total')
             and has_value('sensor.base_power_solar_to_home_total') }}
```

Then use **Base Power Grid Import (Total)** for Grid consumption and **Solar to Home (Total)** for Solar panels. Battery energy still counts as grid import, because Base doesn't split battery energy by day. The difference only shows up while the battery is powering the house, during an outage or a grid support event.

Use the (Total) sensors. The daily sensors still work but miss each day's last hour, about 5% of daily use. There's no battery row: Battery to Home Energy stays unknown on most days, so it can't feed the dashboard.

Home Usage Today and Solar to Home fill from today's hourly samples. Battery to Home stays unknown. Recent usage fills Current Power, the daily totals, and the interval sensors. Daily totals count local-midnight onward, not the whole trailing 24 hours.

## Technical Details

- **Protocol**: gRPC-Web over HTTPS (not standard gRPC)
- **Authentication**: Clerk (email OTP → JWT with 60-second lifetime, native mobile API pattern)
- **Polling**: Service status and the battery snapshot every 60 seconds. The daily overview every 15 minutes. Dashboard, Wi-Fi, billing, the billing cycle, the account list, the location, the daily totals, and the home's battery product every 6 hours, and again on startup. When one of those fails, or the numeric location id is missing, the 6-hour batch retries after 15 minutes, then 30, 60, and so on, up to 6 hours. About 125 calls an hour
- **Retired calls**: Base retired most of its older `dashboard.DashboardAPI` on 2026-11-24. When Base answers a call with "unknown method" (gRPC status 12), the integration logs one warning and stops calling it until Home Assistant restarts
- **Fallbacks**: 15-minute usage and today's energy come from the newer daily overview. The older recent-usage and usage-energy calls run only when the overview fails or lacks that data. Grid voltage comes from `GetServiceStatus`, and the newer v2 voltage call fills in when it fails, returns nothing, or Base sends no numeric location id. Intervals Today shows `usage_source` and `energy_source`, and Grid Power shows `voltage_source` (`service_status` or `v2`)
- **Battery**: detected from the SKU or battery model. When neither is known, Base's nameplate from `GetHome` is used if Base sends it, then an estimate from the 750 W backup time, then the Battery Capacity option: 20, 25, 39.2, 50, or 78.4 kWh
- **Battery %**: `state_of_energy_percent` from the v2 battery snapshot while off grid. On grid, estimated from the 750 W backup time (below)
- **Backup Time**: the v2 battery snapshot's hours at the current home load. At 750 W, the daily overview's newest 15-minute sample while it's under 2 hours old, since it matches the Base app; otherwise the snapshot, which reads about 4% higher. Off grid, always the snapshot
- **Live power**: `power_flow` from the v2 battery snapshot (grid, battery, solar, and home, in 0.1 kW steps)
- **WiFi**: `gatewayWifi` from `GetServiceStatus`, or the snapshot's Wi-Fi, names the network the battery is on. The signal comes from v2 `ListWifiNetworks`
- **Time zone**: Day totals use the site's time zone from Base, and Home Assistant's when Base sends none
- **Timeouts**: Every request to Base and to Clerk times out after 20 seconds. A 60-second poll is skipped while the one before it is still waiting
- **Sign-in**: Only a session Clerk says is gone (401, 404, or 422) asks you to sign in again. A rate limit, bot-protection block (403), or server error is retried on the next refresh
- **Grid voltage and outage**: `GetServiceStatus`, called with the numeric service location id from `GetDashboardRoot`, plus the snapshot's battery state
- **Service Location ID**: Found during setup through `ListLocations`, with `GetAvailableLocations` as a fallback. Not entered by hand. The device is named after the site's street and city. The address id and the numeric service location id are different values.

## Check this release before trusting it

Restart Home Assistant after updating. The integration keeps the already-loaded code until then.

Confirm the sensors below after restarting Home Assistant. A passing check with the grid up does not prove the outage sensor.

- **Grid Power, grid up.** It should be **on**, with `voltage_volts` around the normal service voltage and `active_outage` false. Confirmed on the development account at about 245 V. The integration uses the numeric service location id from the dashboard. The address string still gets an empty body from these calls.
- **Grid Power, during an outage.** Still needs a test. Cut grid power at the panel, leave the house on the battery, and force an update. The sensor turns **off** when Base sets the active-outage flag, and also when voltage falls below 50 V. The September test stayed **on** because the request used the address string and the body came back empty. A missing response stays **unknown**. The sensor does not keep the previous "grid is up" reading.
- **Battery Level.** On grid this is an estimate from the 750 W backup time, with `source: backup_hours_estimate`. Compare with the Base app. It should be within about 1%, and up to an hour behind. During an outage it should switch to `source: snapshot`, the exact value.
- **Battery Backup Time.** The snapshot's hours at the current load. It should move as the house load changes.
- **Current Power.** The snapshot's live home load. Without one, the latest 15-minute sample is average kW, and watts are that value times 1000.
- **Battery Charging while full.** A flat 100% reads off. Confirmed on the development account.
- **Bill Due Date.** Compare with the due date in the Base app. Unknown is correct when the bill is paid. `due_at_utc` shows the raw time Base sent, useful if the date looks a day off.
- **Last Bill Amount.** Compare with your last Base invoice. Empty on plans without energy billing.
- **Grid to Home and Solar to Home.** `MobileGetUsageEnergy` uses the numeric service location id and no time fields. `UsageEnergy parsed` should show today's summed grid and solar kWh. Battery to Home stays unknown. A missing series stays unknown.
- **Daily Total Energy.** It sums 15-minute samples from local midnight onward. Each sample is average kW divided by 4. The previous 24-hour sum was about four times too large.
- **Daily Total (Home Usage).** Same value as Home Usage Today. Unknown when that sum is missing.
- **Daily Average Grid Interval.** Today's usage total divided by the number of 15-minute intervals today. Unknown when today has no intervals.
- **Grid Current.** `power_from_grid_kw * 1000 / voltage_volts` when voltage is above 0. Negative power is export and stays negative. Unknown when either reading is missing.
- **Grid Voltage, Power from Grid, Battery Power, Solar Power.** Read from the same 60-second service-status and snapshot poll as Grid Current, so they add no API calls. Base sends kW in steps of 0.1, so the sensors move in 100 W steps. Unknown when Base sends no reading. Battery Power is Base's `from_storage` flow. Discharging is positive. Charging was negative on the retired service context (-0.2 to -1.0 kW during top-ups, and -16.7 kW during a grid charge reported in issue #29). The v2 snapshot's sign during a charge still needs a check. A very slow charge can still read 0.
- **Battery Charging.** Follows Battery Power when it shows a charge, confirmed during top-ups on the development account. Otherwise Base's whole-percent charge decides, so the sensor holds the last direction it saw. Check it during a real recharge after a discharge.
- **WiFi SSID.** Should match the network shown in the Base app without choosing anything. `source` should read `base`.
- **Next Service Visit.** Not seen with a value yet. If Base has a visit scheduled for you, check that the time matches the app.
- **Solar Export Today.** Not tested on a solar home yet.
- **Capacity.** Detected from the battery SKU, then from the battery model, then Base's nameplate, then the backup-time estimate. The Battery Capacity setting (20, 25, 39.2, 50, or 78.4 kWh) is used when none of those work. `capacity_source` shows which one is in use (`sku`, `model`, `nameplate`, `estimated`, `option`, or `default`). On any battery, `estimated_capacity_kwh` should match your real size once the battery has been at 50% or more for a few minutes. If it doesn't, please send diagnostics.

A separate Shiner app API was not investigated.

## How capacity is worked out

The integration uses the first of these that works:

1. **Battery SKU** from your Base account, such as `GRO_SINGLE`.
2. **Battery model** Base reports for the site. Only `GEN1` is mapped so far.
3. **Nameplate size** from Base's `GetHome` call, when Base includes the product. Not every account gets it.
4. **Estimate from backup time.** Base's "backup at 750 W" figure is the usable energy left divided by 0.75 kW. Divided by the charge level, that gives the battery's usable size. On a 25 kWh Single Battery that's 22.5 kWh, so Base treats about 90% as usable. The integration takes the median of the last hour of readings at 50% charge or higher, divides by 0.9, and uses the nearest known size, but only if it's within 12% of one. The battery type (single, dual, wall) narrows the choice to sizes with that many units.
5. **Battery Capacity option** you chose in the settings.

The estimate ranks above the option because the settings form pre-fills 25 kWh, so many installs never chose it. A reading that isn't close to any known size is shown in `estimated_usable_kwh` but not used. The 90% figure comes from one battery so far. If a Core keeps a different reserve, the estimate won't be close to a known size, and capacity falls back to your option rather than a wrong size.

After a restart the estimate needs a few minutes of readings, so System Capacity can show the option briefly first.

## Battery Level on grid

Base stopped sending the battery percent while on grid when it retired its older API on 2026-11-24. It still sends the backup time at a steady 750 W, which is the energy left above Base's 10% reserve:

`percent = 10 + hours × 0.75 kW ÷ capacity kWh × 100`

A Base Core owner checked this against the retired percent over a full charge from 17% to 100%, and it matched to within rounding (issue #29). It uses System Capacity, so a wrong capacity gives a wrong percent; it's capped at 100%. The 750 W time comes from the daily overview, in 15-minute steps, so Battery Level can be up to an hour behind the app. During an outage Base sends the exact percent again, and the sensor uses it.

## Changelog

### v1.15.0
- **Live sensors are back after Base retired its older API** on 2026-11-24 (issues #29, #30, #31). Battery Level, Battery Power, Power from Grid, Solar Power, Current Power, Backup Time, Battery Connected, Grid Voltage, and Grid Power read unknown. They now come from `GetServiceStatus` and the v2 battery snapshot. The live poll no longer stops when Base sends no numeric location id.
- **Battery Level on grid** is estimated from the 750 W backup time, since Base no longer sends the percent while on grid. During an outage it's exact.
- **Battery Backup Time (Current Load)** reads the snapshot's value (issue #31).
- **Battery Charging** reads off instead of unknown while the battery sits idle (issue #26).
- **Solar sensors** no longer appear on homes without solar that report a tiny solar reading every hour (issue #26).
- **`GEN3 SINGLE`** is now recognized as a Base Core, 39.2 kWh (issue #26).
- A call Base has retired logs one warning and isn't called again until restart. About 125 calls an hour, down from 132.
- **Battery Charging** turns on while Battery Power shows the battery charging, including top-ups from 99% to 100%. It stayed off through those before, because a 99 to 100 rise looked like rounding. It turns off as soon as the charge stops. (This was 1.14.1, which wasn't released on its own.)

### v1.14.0
- **System Capacity** for batteries the integration doesn't know, such as `GEN3`, is estimated from Base's 750 W backup time (`capacity_source: estimated`). It's only used when it lands within 12% of a known size. The raw reading is always on `estimated_usable_kwh`. See [How capacity is worked out](#how-capacity-is-worked-out).
- Base's nameplate size from the new `GetHome` call is used when Base sends it (`capacity_source: nameplate`), and the product name shows on System Capacity. One extra call every 6 hours.
- **Battery Count** comes from the battery type (single, dual, or wall) when the model isn't mapped, so a `GEN3` dual counts as 2. A `source` attribute shows where it came from.
- Diagnostics include a `capacity_estimate` summary.
- The Energy Dashboard section no longer pairs Home Usage (Total) with Solar to Home (Total), which counted solar twice. Homes with solar get a grid import template instead. The Battery discharge row is gone.
- The device is named after the site's street and city, and its model is the battery SKU or model. Existing installs pick up the address on the next restart. Entity IDs don't change.
- Unknown battery hardware (an unmapped SKU, or a `GEN3` battery) logs one warning. System Capacity shows `hardware_sku` and the raw `batteries` list.
- **Battery Charging** no longer sticks on for 3 hours after a single 1% rise. One rise holds it for an hour, and a steady charge stretches that up to 3 hours. A 1% bounce right after a drop doesn't count.
- A 6-hour call that keeps failing backs off (15 min, 30, 60, … up to 6 hours) instead of rerunning the batch every 15 minutes.
- A 403 from Clerk's bot protection no longer asks you to sign in again. Only 401, 404, and 422 do.
- Setup uses Home Assistant's HTTP session helper.
- The Core capacity choices are 39.2 and 78.4 kWh, matching detection. Saved 40 and 80 kWh choices switch over automatically.
- **Energy Cost Today** has a state class and resets at midnight, so it can be the cost entity in the Energy Dashboard.
- **Battery Count** no longer records long-term statistics. Home Assistant shows a repair, "The entity no longer has a state class". Choosing delete is safe; see [Troubleshooting](#battery-count-says-the-entity-no-longer-has-a-state-class).
- Adds **Home Usage (Total)** and, on homes with solar, **Solar to Home (Total)**: running totals that never reset. The daily sensors miss each day's last hour because Base posts it after midnight. Use the new sensors in the Energy Dashboard.
- Renamed Bill Amount to **Last Bill Amount** and fixed it. It showed spend so far this cycle, the same as Bill to Date. It now shows your last bill. The entity ID doesn't change.
- Homes without Base energy service no longer get billing sensors that are always unknown. Existing ones are removed.
- System Capacity falls back to the battery model Base reports when the SKU is missing (`capacity_source: model`). Battery Hardware shows the model in `battery_models`.
- Solar Connected also turns on when Base lists solar for the site.
- Setup lists your sites and lets you pick one when the account has more than one.
- Adds **Grid Voltage** (V) and live **Power from Grid**, **Battery Power**, and, on homes with solar, **Solar Power** (W). Voltage was only an attribute on the Grid Power binary sensor before.
- A rate limit or server error from Clerk no longer asks you to sign in again and no longer stops the 15-minute refresh.
- Requests time out after 20 seconds, and 60-second polls no longer stack up behind a slow one.
- **Battery Charging** holds its direction. It used to read unknown most of the time below 100%.
- **Battery Status** shows Updating Firmware and No Install Planned. Firmware updates showed as Offline before.
- The WiFi network is detected from Base. The **WiFi Network** select is removed, and the option is only a fallback.
- **Battery Backup Time (750 W Essentials)** updates every minute.
- Adds **Next Service Visit** and, on homes with solar, **Solar Export Today**.
- The (Total) sensors catch up from Base's daily totals after more than 24 hours of downtime.
- Usage and energy sensors turn unavailable when Base's usage data stops updating, so an old value isn't shown as current.
- A failed 6-hour call is retried after 15 minutes. Solar sensors are added when Base reports solar after setup.
- Money sensors use the monetary device class with USD. **Asset ID** is renamed **Account ID**.
- Daily Total (Home Usage) and Total Home Energy repeat Home Usage Today and are off by default on new installs.
- Adds a **Download diagnostics** file with tokens, email, ids, and WiFi names removed. Debug logs no longer include raw API replies.
- Needs Home Assistant 2025.1 or newer.

### v1.13.2
- Fixed Total Home Energy and Self-Sufficiency showing "unknown" for about an hour after midnight. They now show 0 after the reset and climb once Base posts the first hour.
- System Capacity is detected from the battery model: Wall Battery 20 kWh (`GRO_WALL`), Single Battery 25 kWh (`GRO_SINGLE`), Double Battery 50 kWh (`GRO_DUAL`), Base Core 39.2 kWh (`BASE1_SINGLE`), and Dual Base Core 78.4 kWh (`BASE1_DUAL`).
- The detected model now wins over the Battery Capacity setting, which many installs saved at the pre-filled 25 kWh. The setting still applies when Base doesn't report a known model.
- Adds a 20 kWh (1 Wall) choice to the Battery Capacity setting.

### v1.13.1
- Renamed Grid to Home Energy to **Home Usage Today (All Sources)**, and Daily Total (Grid) to **Daily Total (Home Usage)**. Base reports total home usage here, not grid draw, so the old names were wrong. The values are unchanged.
- Renamed the backup sensors so the difference is clear: **Battery Backup Time (Current Load)** assumes the house keeps using power at today's rate, and **Battery Backup Time (750 W Essentials)** assumes you cut down to essentials only.
- Fixed Total Home Energy for homes with solar. It added solar and battery on top of home usage, which already includes them, so solar was counted twice. It now equals home usage. Self-Sufficiency used the same inflated total, so it now reads correctly too (capped at 100%). Homes without solar or battery discharge see no change.
- Fixed Bill Due Date, which was always unknown. The older billing call it read has no due date. It now reads the energy account from Base's newer billing API, with the amount due, past-due flag, balance, next bill date, and next autopay date as attributes. It stays unknown when nothing is owed.
- Adds the `GRO_DUAL` SKU (two 25 kWh units, 50 kWh), confirmed by a user. Two-battery homes that never saved a capacity now get 50 kWh automatically.
- Energy Cost Today gets a `cost_last_24h` attribute. The sensor counts from midnight, while the Base app's daily overview shows the last 24 hours, so the two didn't match.
- Fixed Grid Support missing live events. It read only Base's event list, which refreshes every 15 minutes and lagged behind an event in progress. It now also turns on within a minute when the grid is up and the battery is powering the home (0.2 kW or more). Thanks to a Reddit user for the screenshots that caught this.
- Fixed today's energy showing yesterday's total for about an hour after midnight. Base posts the new day's first hour late, and the integration kept the last value until then. Home Usage Today, Total Home Energy, Solar to Home, and the related sensors now start at 0 at local midnight. A failed refresh during the day still keeps the last value.
- The README now covers installing from the Home Assistant Marketplace, which replaced HACS in Home Assistant 2026.11. Nothing changes for existing installs.
- Existing installs keep their entity IDs, so dashboards and automations keep working. Only the display names change.

### v1.13.0
- Base's current app uses a newer API. This release adds calls to it alongside the older API the integration has always used. Base has not announced anything, but the older API may be phased out over time. I'm tracking these changes and will move sensors to the newer API as needed.
- Adds data from Base's newer `dashboard.mobile.v2` API, which the current app uses: Battery State, Battery Backup Time (750 W), a 24-hour grid, solar, and battery energy mix, Grid Support with an event count, Energy Cost Today, Bill to Date, Projected Bill, and Solar Buyback Credit.
- Grid Power turns off when the battery snapshot reports an outage, no home power, or overcurrent. The snapshot never turns it on. A failed snapshot call clears the old state.
- Battery to Home Energy is 0 when the 24-hour mix shows no battery energy, instead of unknown.
- The cost and bill sensors stay unknown on accounts not billed by Base.
- 15-minute usage and today's energy now come from the newer daily overview. The older calls run only as a fallback. The values matched point for point in testing.
- Grid voltage gets a fallback: when the older grid status call fails or returns nothing, the newer voltage call fills in.
- The battery snapshot runs every 10 minutes, and every 60 seconds while the outage flag, voltage, or the last snapshot points to an outage. A failing snapshot call does not speed this up.
- Intervals Today shows `usage_source` and `energy_source`. Grid Power shows `voltage_source`. Each is `overview`, `legacy`, or `v2`.
- Battery Hardware shows the battery's hardware SKU and plan name. System Capacity shows `capacity_source` (`option`, `sku`, or `default`) and `detected_capacity_kwh`. Capacity comes from the SKU when Base reports a known one: `GRO_WALL` 20 kWh, `GRO_SINGLE` 25 kWh, `GRO_DUAL` 50 kWh (2 units), `BASE1_SINGLE` 39.2 kWh (Base Core), `BASE1_DUAL` 78.4 kWh (2 Base Core). The Battery Capacity option is used only when the SKU is missing or unknown. If yours shows another SKU, please open an issue with it and your system size.
- Grid Power shows `battery_state`, so an off state caused by the battery snapshot is visible in its attributes.
- Hourly energy and cost samples that are not finite numbers are skipped, so one bad hour cannot break the daily sensors.
- About 131 calls an hour, close to the 129 in 1.12.1, while adding the new data.

### v1.12.1
- Energy and daily usage sensors update every 15 minutes again. In 1.12.0 the 60-second grid poll kept restarting the 15-minute refresh timer, so usage only loaded at startup and the daily sensors stayed at 0 after midnight (#13).
- The amps sensor is renamed from Grid Power to Grid Current. The outage sensor keeps the name Grid Power. Entity IDs and history do not change.

### v1.12.0
- Usage energy is parsed from the hourly series. Grid to Home, Solar to Home, Total Home Energy, Self-Sufficiency, and Daily Total (Grid) use today's samples. Battery to Home stays unknown.
- Only `MobileGetUsageEnergy` sends the numeric service location id. Recent usage, billing, usage cycles, and Wi-Fi keep the address string.
- Recent usage values are average kW, not kWh. Watts are that value times 1000, interval energy is the value divided by 4, and the daily total counts only today's intervals. The old math was about four times too high.
- Daily Average Grid Interval is today's usage total divided by the number of 15-minute intervals. Grid Power (amps) is grid kilowatts times 1000 divided by voltage. A negative value is export.
- The current billing cycle start is parsed. Its end, the server clock at request time, is not used to decide which cycle is current.
- gRPC status is read from the trailer frame in the response body as well as from the headers.
- The Clerk session id is no longer written to the log in full.
- Polling is tiered: service context and grid status every 60 seconds, recent usage and usage energy every 15 minutes, and dashboard, Wi-Fi, billing, and usage cycles every 6 hours. About 129 calls an hour instead of about 216.
- Bill amount and bill due date stay empty on plans that have no billing info. That empty response is expected.

### v1.11.0
- Capacity choices are 25, 40, 50, and 80 kWh. Base Core's published size is 39.2 kWh per unit (78.4 for two); the options use those sizes rounded.
- Grid voltage, outage state, battery percent, and backup hours use the numeric service location id from the dashboard. `MobileGetGridStatus` and `MobileGetServiceContext` stay empty when called with the address string.
- Grid Power is on when the latest voltage is at least 100 V and Base has not set an outage. It is off when the outage flag is set, or when voltage is below 50 V. A missing response stays unknown.
- Battery Level reads `stateOfEnergyRaw`. Battery Backup Time reads `availableBackup` hours. Current Power uses the live home load when service context includes it.
- Battery Charging reports off while state of charge stays full.
- Bill amount is read from the nested billing message some accounts return.
- A missing usage-cycle contract is logged at debug instead of filling the error log every poll.
- Drop `aiohttp` from manifest requirements so hassfest passes. Home Assistant already provides it.
- Wi-Fi signal follows the SSID the user selects. The integration no longer substitutes the strongest network.

### v1.10.0
- Replace the battery unit-count field with a capacity choice: 25 kWh, 40 kWh, or 50 kWh. A 40 kWh single unit is no longer stored as 25 kWh. Existing integer settings keep working until the options are saved again.
- Poll grid status every 60 seconds so an outage does not wait for the 5-minute refresh. v1.11.0 uses that same poll for voltage, outage state, battery percent, backup time, and current power

### v1.9.0
- Add **WiFi Network select entity** — a dropdown on the Base Power device card lets you choose a preferred WiFi network directly from Home Assistant, with available networks populated from a live scan sorted by signal strength
- Add **WiFi SSID dropdown in options** — the gear icon on the integration page now shows a live list of nearby networks with signal strength percentages instead of a plain text field; selecting "Auto (strongest signal)" clears the preference
- Add **battery unit count setting** — configure the number of battery units (1–10) via the gear icon on the integration page, useful when auto-detection doesn't match your actual hardware

### v1.8.0
- Fix battery count detection for 2-unit (50 kWh) systems — parser now counts repeated Field 3 sub-messages (one per battery unit) in addition to reading sub-field 1, so two-battery installs correctly report capacity and backup time
- Expanded protobuf debug logging: all unknown top-level fields are now logged with field number and value to aid future API reverse-engineering

### v1.7.0
- Auto-discover Service Location ID — setup is now email + OTP only, no manual ID lookup required
- Add Self-Sufficiency % sensor: `(solar + battery) ÷ total home consumption`
- Add Total Home Energy sensor: `grid + solar + battery` kWh (Energy Dashboard compatible)
- Add Battery Charging binary sensor: inferred from SoC delta between polls
- Add Daily Average Grid Interval sensor (already fetched, now exposed)
- Fix Solar Connected: falls back to `solar_to_home_kwh > 0` when API field is unset
- Fix Battery Backup Time: now a dynamic estimate from live SoC and average home load
- Energy Dashboard: add `suggested_display_precision` to all energy sensors

### v1.6.0
- Battery Level now shows real SoC% from `MobileGetGridStatus` (replaces self-calibration)
- Add Grid Power sensor: current draw in amps from the latest 15-min grid reading
- Add Daily Total (Grid) sensor: total kWh from the grid status summary
- Add parse support for rich `MobileGetGridStatus` response (battery SoC and power data)

### v1.5.0
- Add grid power outage detection binary sensor
- Add battery SoC from grid status API (replaces self-calibration when available)
- Add battery status enum sensor (Installed, In Service, etc.)
- Add WiFi signal strength and SSID diagnostic sensors
- Add energy breakdown sensors (grid-to-home, solar-to-home, battery-to-home)
- Add billing amount and due date diagnostic sensors
- Add asset ID diagnostic sensor
- Energy breakdown sensors are compatible with HA Energy Dashboard

### v1.4.0
- Remove device_class=ENERGY from non-cumulative sensors to fix HA state_class warnings
- Add debug logging for protobuf field identification
- Fix battery_count and has_solar field mappings

### v1.3.0
- Switch to native mobile API authentication pattern (`_is_native=1`)
- Add `x-mobile` header to match Clerk Expo SDK behavior
- Remove cookie-based auth in favor of Authorization header
- Extract session JWT directly from sign-in response when available
- Improved authentication reliability

### v1.2.0
- Dedicated Clerk session for auth (separate from HA's shared session)
- Token rotation tracking through full sign-in flow
- Debug logging for auth diagnostics

### v1.0.0
- Initial release with full sensor support
- Email OTP config flow
- gRPC-Web API integration
- Energy Dashboard compatible sensors

## Troubleshooting

### "Authentication failed" during setup
- Ensure you're using the same email as your Base Power app account
- The verification code expires quickly — enter it promptly

### No data / sensors unavailable
- New installations may take time before telemetry data starts flowing
- Check that your battery is connected to WiFi in the Base Power app

### Reauthentication required
- Clerk sessions can expire. Go to the integration and click **Reconfigure** to re-authenticate.

### How often does this poll Base?
About 125 calls an hour, all from your Home Assistant to Base's cloud. Battery level, power, and grid status every 60 seconds. Usage every 15 minutes. Billing and account details every 6 hours. Failed calls back off instead of retrying every minute. Base hasn't published a rate limit. The 60-second calls are what keep outage detection quick; the rest are spread out to keep the load low.

### Battery Count says "The entity no longer has a state class"
Expected after updating to 1.14.0 or newer. Battery Count is a fixed number, so it no longer keeps long-term statistics. Choose to delete them in the repair. The sensor keeps working and its entity ID doesn't change.

### Reporting a problem
- Go to **Settings** → **Devices & Services** → **Base Power**, open the three-dot menu, and choose **Download diagnostics**. Attach the file to your issue. Tokens, your email, site and account ids, and WiFi names are already removed.

## Thanks

- [@simonhogg](https://github.com/simonhogg) removed `aiohttp` from the manifest requirements and bumped the checkout action.
- [@universal9gsm](https://github.com/universal9gsm) reported the Core battery sizes and proposed a capacity override.
- [@yzfdave](https://github.com/yzfdave) asked for the 40 kWh Base Core option.
- [@jabone](https://github.com/jabone) fixed the nested bill amount, the grid sensor that defaulted to on, and charging while the battery stays full.
- [@christr](https://github.com/christr) cut power at the panel and showed that Grid Power stayed on.
- [@Sodium-Hydrogen](https://github.com/Sodium-Hydrogen) reported the usage-cycle error on every poll.
- [@colforbin13](https://github.com/colforbin13) found that the grid poll kept the 15-minute usage refresh from running, and suggested the fix.

## Contributing

Contributions are welcome! Please open an issue or PR on [GitHub](https://github.com/jerrit/ha-base-power).

## Disclaimer

This integration communicates with Base Power's private API, which may change without notice. Use at your own risk.

Base's current app uses a newer API than the one this integration started on. Since v1.13.0 the integration calls both. The older API may be phased out over time, and sensors will move to the newer API as that happens.

## License

MIT
