# VOX The Odyssey — IMAX monitor

This monitor checks VOX Cinemas Egypt for **The Odyssey** at **City Centre Almaza**, **IMAX only**, starting from **19 August 2026**.

It runs hourly through GitHub Actions and sends a Pushover notification after every check.

## What it checks

1. Opens the VOX The Odyssey showtimes page.
2. Finds date links exposed by VOX from 19 Aug 2026 onward.
3. Opens each exposed date.
4. Finds IMAX showtimes at City Centre Almaza.
5. Opens each IMAX booking link.
6. Attempts to confirm that a seat-selection page is available.
7. Sends Pushover:
   - 🚨 when seats appear available
   - ❌ when no confirmed seats are available
   - ⚠️ when VOX requires login or the booking page cannot be confirmed
   - ⚠️ if the monitor itself fails

## GitHub setup

Create a private GitHub repository and upload:

- `monitor.py`
- `requirements.txt`
- `.github/workflows/monitor.yml`

Then go to:

**Repository → Settings → Secrets and variables → Actions → New repository secret**

Create:

- `PUSHOVER_USER_KEY` = your Pushover User Key
- `PUSHOVER_APP_TOKEN` = your Pushover Application/API Token

Do NOT put either secret directly into `monitor.py`.

Then run the workflow once manually using:

**Actions → VOX Odyssey IMAX Monitor → Run workflow**

After that, GitHub will schedule it hourly.

### Important

GitHub scheduled workflows are not guaranteed to start at the exact minute because GitHub may delay scheduled jobs during periods of high load. The intended frequency is once per hour.

The script does not purchase tickets or select seats. It only checks whether a booking/seat-selection page appears available.

VOX can change its website structure. If that happens, the monitor may report an `unknown` result rather than falsely claiming seats are available.
