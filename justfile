# Pacr — development commands

# Default recipe
default:
    @just --list

# Install dev dependencies
setup:
    uv sync --extra dev

# Lint with ruff
lint:
    uv run ruff check .

# Auto-fix lint issues
fix:
    uv run ruff check --fix .

# Format code
fmt:
    uv run ruff format .

# Type check
typecheck:
    uv run mypy src/_token_utils.py src/strava_utils/strava_auth.py src/strava_utils/strava_sync.py src/strava_utils/pot10.py src/coach_utils/analyze.py src/coach_utils/plan.py src/coach_utils/training_load.py src/coach_utils/adherence.py src/coach_utils/records.py src/coach_utils/wellness.py src/coach_utils/readiness.py src/tgbot/debrief.py src/tgbot/formatters.py src/tgbot/context.py src/tgbot/handlers.py src/tgbot/claude_chat.py src/tgbot/km_query.py src/tgbot/bot.py

# Run tests
test *ARGS:
    uv run pytest {{ARGS}}

# Run tests with coverage
test-cov:
    uv run pytest --cov=src --cov-report=term-missing

# Sync Strava activities
sync DAYS="365":
    uv run src/strava_utils/strava_sync.py sync --days={{DAYS}}

# Show current training plan
plan:
    uv run src/coach_utils/plan.py show

# Write data/athlete_zones.json from max heart rate (Jack Daniels percentages)
# Usage: just zones 185
# Optional: just zones 185 250 95  (maxhr, cycling FTP watts, swim CSS seconds/100m)
zones MAXHR='190' CYCLING_FTP='' SWIM_CSS='':
    #!/usr/bin/env python3
    import json, pathlib
    m = {{MAXHR}}
    zones = {
        "hr_zones": {
            "zone1": [round(m * 0.50), round(m * 0.64)],
            "zone2": [round(m * 0.65), round(m * 0.79)],
            "zone3": [round(m * 0.80), round(m * 0.87)],
            "zone4": [round(m * 0.88), round(m * 0.92)],
            "zone5": [round(m * 0.93), m],
        },
        "pace_zones": {
            "easy":        [300, 360],
            "tempo":       [255, 299],
            "threshold":   [240, 254],
            "interval":    [210, 239],
            "repetition":  [180, 209],
        },
    }
    ftp_str = "{{CYCLING_FTP}}"
    if ftp_str:
        ftp = int(ftp_str)
        zones["cycling"] = {
            "ftp": ftp,
            "power_zones": {
                "recovery":  [0, round(ftp * 0.55)],
                "endurance": [round(ftp * 0.56), round(ftp * 0.75)],
                "tempo":     [round(ftp * 0.76), round(ftp * 0.90)],
                "threshold": [round(ftp * 0.91), round(ftp * 1.05)],
                "vo2max":    [round(ftp * 1.06), round(ftp * 1.20)],
                "anaerobic": [round(ftp * 1.21), round(ftp * 1.50)],
            },
        }
    css_str = "{{SWIM_CSS}}"
    if css_str:
        css = int(css_str)
        zones["swimming"] = {
            "css_per_100m": css,
            "pace_zones": {
                "recovery":  [css + 20, css + 40],
                "endurance": [css + 5, css + 19],
                "threshold": [css - 3, css + 4],
                "vo2max":    [css - 15, css - 4],
                "sprint":    [0, css - 16],
            },
        }
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/athlete_zones.json").write_text(json.dumps(zones, indent=2))
    print(json.dumps(zones, indent=2))

# Strava OAuth authorisation (opens browser)
auth:
    uv run src/strava_utils/strava_auth.py authorize

# Check Strava token validity
auth-status:
    uv run src/strava_utils/strava_auth.py status

# Install ruff pre-commit hooks
pre-commit:
    uvx pre-commit install

# Send a test message to Telegram
tg-send TEXT="Test from Pacr":
    uv run src/tgbot/bot.py send --text="{{TEXT}}"

# Start interactive Telegram bot
tg-bot:
    uv run src/tgbot/bot.py bot

# Send morning briefing (today's session + week progress)
briefing:
    uv run src/tgbot/bot.py morning_briefing

# Docker commands
docker-build:
    docker compose -f docker/docker-compose.yml build

docker-up:
    docker compose -f docker/docker-compose.yml up -d telegram-bot

docker-logs:
    docker compose -f docker/docker-compose.yml logs -f telegram-bot

docker-down:
    docker compose -f docker/docker-compose.yml down
