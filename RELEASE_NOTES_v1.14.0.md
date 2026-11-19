## Base Power 1.14.0

Running energy totals that never miss an hour, live power and voltage sensors, better battery size detection, and fewer surprise sign-in requests.

### Fixes
- **Daily energy missed the last hour of each day.** Base posts the 11 PM hour a few minutes after midnight, after the daily sensors have reset, so that hour was never counted. That is about 5% of daily use missing from the Energy Dashboard. The new running-total sensors below count it.
- **Bill Amount** showed spend so far this cycle, the same as Bill to Date. It's now **Last Bill Amount** and shows your last bill. The entity ID doesn't change.
- **Asked to sign in again for no reason.** A rate limit, server error, or 403 from Base's sign-in service was treated as a rejected session. That asked for a new sign-in, made every entity unavailable, and stopped the 15-minute refresh. Now it's retried on the next refresh. Only a session that's gone (401, 404, 422) asks you to sign in.
- **Battery Charging** read unknown most of the time below 100%. Base reports the charge as a whole percent, so two polls in a row rarely differ. It now holds the last direction it saw: one 1% rise holds it for an hour, and a steady charge stretches that up to 3 hours. A 1% bounce right after a drop doesn't count.
- **Battery Status** showed Offline during a firmware update. It now shows **Updating Firmware**, and **No Install Planned** where it applies.
- **Slow replies no longer pile up.** Requests time out after 20 seconds, and a 60-second poll is skipped while the one before it is still waiting.
- **Failed calls retry sooner, without piling on.** A call that fails during the 6-hour refresh is retried after 15 minutes, then 30, 60, and so on, up to 6 hours.
- **Brief blanks in the live sensors.** One failed call to Base made Battery Level, Backup Time, the power sensors, Battery Connected, and Battery Charging read unknown for a minute. They now keep the last reading for up to 3 minutes. Grid Power still needs a live voltage reading, so it never shows the grid as up based on an old reading.
- **Old values no longer look current.** Usage and energy sensors turn unavailable when Base has sent no new usage data for about an hour.
- **Energy Cost Today** can now be picked as the cost entity in the Energy Dashboard.

### New
- **Home Usage (Total)** and, on homes with solar, **Solar to Home (Total)**. These running totals never reset at midnight. Each hour is counted once, whenever Base posts it, and the count survives restarts. If Home Assistant or Base is down for more than a day, they add the missed energy from Base's daily totals once the day is over. See the README's Energy Dashboard section for how to use them.
- **Grid Voltage** sensor. Voltage used to be only an attribute on the Grid Power binary sensor.
- **Power from Grid**, **Battery Power**, and, on homes with solar, **Solar Power**: live power in watts, updated every minute. Power from Grid is negative while exporting. Battery Power is positive while the battery discharges.
- **Solar Export Today**, on homes with solar.
- **Next Service Visit**: the next visit Base has scheduled, when there is one.
- **WiFi is detected for you.** Base reports which network the battery is on, so **WiFi SSID** and **WiFi Signal** follow it without any setup.
- **Picking a site during setup.** If your account has more than one site, setup lists them by street address and you pick one.
- **Download diagnostics.** The integration's menu has a diagnostics file to attach to bug reports. Tokens, your email, site and account ids, and WiFi names are removed.

### Better battery size detection
System Capacity now tries these in order, and its `capacity_source` attribute shows which one it used:
1. The battery SKU, as in 1.13.2 (`sku`).
2. The battery model Base lists for the site (`model`). Battery Hardware shows it in a `battery_models` attribute.
3. Base's nameplate size, when Base's newer `GetHome` call includes it (`nameplate`). Not every account gets it. This adds one call every 6 hours.
4. An estimate from Base's "backup at 750 W" figure, for batteries the integration doesn't recognize yet, such as ones Base reports as `GEN3` (`estimated`). It's only used when it lands within 12% of a size Base sells, and only from readings at 50% charge or higher.
5. Your Battery Capacity setting (`option`).

**Battery Count** also comes from the battery type Base reports (single, dual, or wall) when the model isn't mapped, so a `GEN3` dual counts as 2. A new `source` attribute shows where it came from. Unknown battery hardware logs one warning with what Base reported.

### Changes
- **The device is named after your site's street and city,** not its id, and the model shows your battery SKU. Existing installs pick this up when Home Assistant restarts after the update. Entity IDs don't change.
- **Battery-only homes** (no energy service from Base) no longer get billing sensors that are always unknown: Last Bill Amount, Bill Due Date, Energy Cost Today, Bill to Date, Projected Bill, and Solar Buyback Credit. Ones already set up are removed. A billing sensor that has a value stays.
- **Battery Count** no longer records long-term statistics. Home Assistant may show a repair, "The entity no longer has a state class", offering to delete the old ones. Choosing delete is safe.
- The **WiFi Network** select is removed. The WiFi option stays as a fallback and can be left empty.
- **Asset ID** is renamed **Account ID**, which is what Base calls it. The entity ID doesn't change.
- **Base Core capacity choices** are now 39.2 and 78.4 kWh, the same sizes detection uses. A saved 40 or 80 kWh choice switches over on its own.
- Money sensors show **USD**, not **$**, and use Home Assistant's monetary type.
- **Daily Total (Home Usage)** and **Total Home Energy** repeat Home Usage Today. They are off by default on new installs. Existing installs keep them.
- Debug logs no longer include raw API replies, which could contain your address.
- Needs Home Assistant 2025.1 or newer.

### After updating
- If you use the Energy Dashboard, switch to **Home Usage (Total)**. If you have solar, use the grid import template in the README instead, so solar isn't counted twice.
- If System Capacity changes, check `capacity_source` to see why. If it looks wrong, open an issue and attach your diagnostics (Base Power's three-dot menu → Download diagnostics).
- **Base Core, Wall, and two-battery owners:** these models aren't confirmed yet. Diagnostics from your system would help.

### Known issues
- After a restart, System Capacity can show your setting for a few minutes before the estimate takes over.
- Battery Power has so far stayed at 0 while charging, not gone negative. Reports from a recharge are welcome.

Restart Home Assistant after updating.
