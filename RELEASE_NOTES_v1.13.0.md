## Base Power 1.13.0

**About the API:** Base's current app uses a newer API. This release adds calls to it alongside the older API the integration has always used. Base has not announced anything, but the older API may be phased out over time. I'm tracking these changes and will move sensors to the newer API as needed.

- Adds data from Base's newer `dashboard.mobile.v2` API, which the current app uses: Battery State, Battery Backup Time (750 W), a 24-hour grid, solar, and battery energy mix, Grid Support with an event count, Energy Cost Today, Bill to Date, Projected Bill, and Solar Buyback Credit.
- Grid Power turns off when the battery snapshot reports an outage, no home power, or overcurrent. The snapshot never turns it on. A failed snapshot call clears the old state.
- Battery to Home Energy is 0 when the 24-hour mix shows no battery energy, instead of unknown.
- The cost and bill sensors stay unknown on accounts not billed by Base.
- 15-minute usage and today's energy now come from the newer daily overview. The older calls run only as a fallback. The values matched point for point in testing.
- Grid voltage gets a fallback: when the older grid status call fails or returns nothing, the newer voltage call fills in.
- The battery snapshot runs every 10 minutes, and every 60 seconds while the outage flag, voltage, or the last snapshot points to an outage. A failing snapshot call does not speed this up.
- Intervals Today shows `usage_source` and `energy_source`. Grid Power shows `voltage_source`. Each is `overview`, `legacy`, or `v2`.
- About 131 calls an hour, close to the 129 in 1.12.1, while adding the new data.

Restart Home Assistant after updating. The integration keeps the already-loaded code until then.

If your account is billed by Base, please check the new cost and bill sensors against the app and open an issue if they differ. They could not be tested on an account without Base billing.
