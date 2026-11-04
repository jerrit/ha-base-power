## Base Power 1.13.0

**About the API:** Base's current app uses a newer API. This release adds calls to it alongside the older API the integration has always used. Base has not announced anything, but the older API may be phased out over time. I'm tracking these changes and will move sensors to the newer API as needed.

- Adds data from Base's newer `dashboard.mobile.v2` API, which the current app uses: Battery State, Battery Backup Time (750 W), a 24-hour grid, solar, and battery energy mix, Grid Support with an event count, Energy Cost Today, Bill to Date, Projected Bill, and Solar Buyback Credit.
- Grid Power turns off when the battery snapshot reports an outage, no home power, or overcurrent. The snapshot never turns it on. A failed snapshot call clears the old state.
- Battery to Home Energy is 0 when the 24-hour mix shows no battery energy, instead of unknown.
- The cost and bill sensors stay unknown on accounts not billed by Base.
- About 163 calls an hour, up from about 129. The battery snapshot runs every 2 minutes, the daily overview every 15 minutes, and the billing cycle every 6 hours.

Restart Home Assistant after updating. The integration keeps the already-loaded code until then.

If your account is billed by Base, please check the new cost and bill sensors against the app and open an issue if they differ. They could not be tested on an account without Base billing.
