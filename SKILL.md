# RunWhisperer Skill

## Tools

### Strava Authentication
```bash
uv run {baseDir}/strava_auth.py authorize
```
Start the Strava OAuth flow. Opens a local server on port 8000, exchanges the authorisation code for tokens, and saves them to `data/tokens.json`.

```bash
uv run {baseDir}/strava_auth.py status
```
Check token validity and attempt refresh if expired.

### Activity Sync
```bash
uv run {baseDir}/strava_sync.py sync --days=<N>
```
Fetch the last N days of Strava activities (default 30). Normalises and caches to `data/activities.json`.

```bash
uv run {baseDir}/strava_sync.py show --last=<N>
```
Display the last N cached activities (default 10).

```bash
uv run {baseDir}/strava_sync.py show --id=<activity_id>
```
Display a specific cached activity by ID.

### Race Results (Power of 10)
```bash
uv run {baseDir}/pot10.py fetch --athlete_id=<id>
```
Fetch race results from Power of 10 by athlete ID. Saves to `data/race_results.json`.

```bash
uv run {baseDir}/pot10.py fetch --athlete_id=<id> --verbose
```
Verbose mode: prints HTTP status, final URL, HTML structure info. Caches raw HTML to `data/po10_raw.html`.

```bash
uv run {baseDir}/pot10.py show
```
Display cached race results.

```bash
uv run {baseDir}/pot10.py add --date=<YYYY-MM-DD> --event=<name> --distance=<dist> --time=<HH:MM:SS> [--position=<N>] [--notes=<text>]
```
Manually add a race result (fallback when scraping fails).

### Training Plan
```bash
echo '<json>' | uv run {baseDir}/plan.py set
```
Set a new training plan. Reads JSON from stdin. Must contain a `weeks` array with `sessions`.

```bash
uv run {baseDir}/plan.py show
```
Display the current training plan.

```bash
echo '<json>' | uv run {baseDir}/plan.py update --week=<N>
```
Update a specific week in the plan. Reads week JSON from stdin.

```bash
uv run {baseDir}/plan.py clear
```
Delete the current training plan.

### Session Analysis
```bash
uv run {baseDir}/analyze.py latest
```
Analyse the most recent cached activity against the training plan and zones.

```bash
uv run {baseDir}/analyze.py activity --id=<activity_id>
```
Analyse a specific activity by ID.

### Telegram Notifications
```bash
uv run {baseDir}/telegram_bot.py send --text="<message>"
```
Send a message to the athlete's Telegram chat. Uses HTML formatting.

```bash
uv run {baseDir}/telegram_bot.py send_summary --period=daily
```
Send a daily summary (latest activity). Use `--period=weekly` for a 7-day overview.

```bash
uv run {baseDir}/telegram_bot.py bot
```
Start the interactive Telegram bot (long-polling). The athlete can then use `/start`, `/sync`, `/plan`, `/today`, `/analyze`, `/results`, and `/help` directly from Telegram.

## Coaching Methodology

This skill follows **Jack Daniels' Running Formula** principles:
- Training paces based on VDOT and recent race performance
- Structured mesocycles with base, quality, and taper phases
- Easy runs truly easy (Zone 1–2), quality sessions purposeful
- Progressive overload with the 10% weekly volume rule
- Recovery prioritised — adaptation happens during rest

## Data Files

All data stored in `{baseDir}/data/`:
- `tokens.json` — Strava OAuth tokens
- `athlete.json` — Strava athlete profile
- `activities.json` — cached Strava activities
- `race_results.json` — Power of 10 + manual results
- `training_plan.json` — current training plan
- `athlete_zones.json` — HR and pace zones
- `training_log.json` — analysed session history

## Cron

Sync activities daily:
```cron
0 6 * * * sync_strava
```

The `sync_strava` cron job runs: `uv run {baseDir}/strava_sync.py sync --days=7`

Send daily summary at 07:00:
```cron
0 7 * * * send_daily_summary
```

The `send_daily_summary` cron job runs: `uv run {baseDir}/telegram_bot.py send_summary --period=daily`
