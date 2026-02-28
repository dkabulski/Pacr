# RunWhisperer — development commands

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
    uv run mypy src/_token_utils.py src/strava_auth.py src/strava_sync.py src/pot10.py src/analyze.py src/plan.py src/tgbot/formatters.py src/tgbot/context.py src/tgbot/bot.py

# Run tests
test *ARGS:
    uv run pytest {{ARGS}}

# Run tests with coverage
test-cov:
    uv run pytest --cov=src --cov-report=term-missing

# Sync Strava activities
sync DAYS="365":
    uv run src/strava_sync.py sync --days={{DAYS}}

# Show current training plan
plan:
    uv run src/plan.py show

# Write data/athlete_zones.json from max heart rate (Jack Daniels percentages)
# Usage: just zones 185
zones MAXHR='190':
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
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/athlete_zones.json").write_text(json.dumps(zones, indent=2))
    print(json.dumps(zones, indent=2))

# Send a test message to Telegram
tg-send TEXT="Test from RunWhisperer":
    uv run src/tgbot/bot.py send --text="{{TEXT}}"

# Start interactive Telegram bot
tg-bot:
    uv run src/tgbot/bot.py bot

# Send morning briefing (today's session + week progress)
briefing:
    uv run src/tgbot/bot.py morning_briefing

# Deploy skill to OpenClaw
deploy:
    @mkdir -p ~/.openclaw/workspace/skills
    @ln -sfn "$(pwd)" ~/.openclaw/workspace/skills/running-coach
    @echo "Deployed to ~/.openclaw/workspace/skills/running-coach"

# Docker commands
docker-build:
    docker compose -f docker/docker-compose.yml build

docker-up:
    docker compose -f docker/docker-compose.yml up -d telegram-bot

docker-logs:
    docker compose -f docker/docker-compose.yml logs -f telegram-bot

docker-down:
    docker compose -f docker/docker-compose.yml down
