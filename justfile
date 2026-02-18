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
    uv run mypy _token_utils.py analyze.py plan.py telegram_bot.py

# Run tests
test *ARGS:
    uv run pytest {{ARGS}}

# Run tests with coverage
test-cov:
    uv run pytest --cov=. --cov-report=term-missing

# Sync Strava activities
sync DAYS="30":
    uv run strava_sync.py sync --days={{DAYS}}

# Show current training plan
plan:
    uv run plan.py show

# Send a test message to Telegram
tg-send TEXT="Test from RunWhisperer":
    uv run telegram_bot.py send --text="{{TEXT}}"

# Start interactive Telegram bot
tg-bot:
    uv run telegram_bot.py bot

# Deploy skill to OpenClaw
deploy:
    @mkdir -p ~/.openclaw/workspace/skills
    @ln -sfn "$(pwd)" ~/.openclaw/workspace/skills/running-coach
    @echo "Deployed to ~/.openclaw/workspace/skills/running-coach"
