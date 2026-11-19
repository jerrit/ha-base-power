## Base Power 1.14.0 beta 2

This is a beta. To get it: go to **Settings → Devices & services → Marketplace**, open the **Base Power** device, and enable the **Pre-release** switch, which is off and hidden by default. Turn it on, then install the update. If something looks wrong, please open an issue.

### Fixes
- **Energy Dashboard counted solar twice.** Beta 1 said to put Home Usage (Total) under Grid consumption and Solar to Home (Total) under Solar panels. Home usage already includes solar, so on a solar home that counts it twice. If you have solar, see the README's Energy Dashboard section for a grid import sensor to use instead. Without solar, nothing changes. Also remove Battery to Home Energy from the dashboard if you added it. It stays unknown on most days.
- **Battery Charging stuck on.** A single 1% rise held it on for 3 hours, even with the battery sitting idle at its reserve. One rise now holds it for an hour. On a steady charge the hold follows the pace, up to 3 hours. A 1% bounce back up right after a drop doesn't count.
- **Asked to sign in again after a 403.** Base's sign-in service sometimes answers 403 from its bot protection. That no longer asks for a new sign-in. Only a session that's gone (401, 404, 422) does.
- **API calls could creep up.** If one of the 6-hour calls kept failing, all nine reran every 15 minutes. Retries now back off: 15 minutes, 30, 60, and so on, up to 6 hours.
- **Energy Cost Today** couldn't be picked as the cost entity in the Energy Dashboard. It now can.

### Changes
- **The device is named after your site's street and city,** not its id, and the model shows your battery SKU (for example `GRO_DUAL`). Existing installs pick this up when Home Assistant restarts after the update. Entity IDs don't change.
- **Unknown battery hardware logs a warning** once, with the SKU and battery model Base reports. System Capacity shows them as `hardware_sku` and `batteries` attributes.
- **Base Core capacity choices** are now 39.2 and 78.4 kWh, the same sizes detection uses. A saved 40 or 80 kWh choice switches over on its own.
- **Battery Count** no longer records long-term statistics. Home Assistant may show a repair offering to delete the old ones. That's expected.
- The README has a supported-hardware table and a polling FAQ.

### Please check
- **Base Core owners:** please download diagnostics (Base Power's three-dot menu → Download diagnostics) and attach it to an issue. Base reports some batteries as `GEN3`, which we think is Core but can't map without a report.
- If Home Assistant logs a warning about unknown battery hardware, please open an issue with your diagnostics.
- Watch **Battery Charging** during a recharge and while the battery sits at its reserve.

Restart Home Assistant after updating.
