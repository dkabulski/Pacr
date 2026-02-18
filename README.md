# RunWhisperer

AI running coach built as an [OpenClaw](https://openclaw.dev) skill. Uses Claude to analyse Strava data, look up race results, and manage training plans following Jack Daniels' Running Formula methodology.

## How It Works

The agent (Claude) reads `SKILL.md` to discover available tools, then calls Python scripts via `uv run` to pull data and manage training. All data persists as JSON in `data/`.

At deploy time the repo gets symlinked into `~/.openclaw/workspace/skills/running-coach/`.

## Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [just](https://github.com/casey/just) (optional, for dev commands)
- A [Strava API application](https://www.strava.com/settings/api)

### Installation

```bash
# Install dev dependencies
just setup

# Copy and fill in Strava credentials
cp .env.example .env

# Authorise with Strava (opens browser)
uv run strava_auth.py authorize

# Deploy to OpenClaw
just deploy
```

## Usage

### Strava Sync

```bash
# Fetch last 30 days of activities
uv run strava_sync.py sync

# Fetch last 7 days
uv run strava_sync.py sync --days=7

# Show recent activities
uv run strava_sync.py show
```

### Race Results (Power of 10)

```bash
# Fetch by athlete ID
uv run pot10.py fetch --athlete_id=123456

# Verbose mode (debug HTML parsing)
uv run pot10.py fetch --athlete_id=123456 --verbose

# Manual entry fallback
uv run pot10.py add --date=2025-06-15 --event=parkrun --distance=5K --time=22:30

# Show cached results
uv run pot10.py show
```

### Training Plan

Plans are managed as JSON via stdin — typically Claude generates and pipes them:

```bash
# Set a plan
echo '{"goal":"Sub-45 10K","weeks":[...]}' | uv run plan.py set

# Show current plan
uv run plan.py show

# Update a specific week
echo '{"sessions":[...]}' | uv run plan.py update --week=3

# Clear plan
uv run plan.py clear
```

### Session Analysis

```bash
# Analyse most recent activity
uv run analyze.py latest

# Analyse a specific activity
uv run analyze.py activity --id=12345678
```

Analysis compares actual effort against prescribed sessions and HR/pace zones. Results are appended to `data/training_log.json`.

## Telegram Bot

### Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) on Telegram
2. Copy the bot token to `.env` as `TELEGRAM_BOT_TOKEN`
3. Message your bot, then fetch your chat ID:
   ```bash
   curl https://api.telegram.org/bot<TOKEN>/getUpdates | jq '.result[0].message.chat.id'
   ```
4. Add the chat ID to `.env` as `TELEGRAM_CHAT_ID`

### Usage

```bash
# Send a message
uv run telegram_bot.py send --text="Hello from your coach!"

# Send daily summary (latest activity)
uv run telegram_bot.py send_summary

# Send weekly summary
uv run telegram_bot.py send_summary --period=weekly

# Start interactive bot (long-polling)
uv run telegram_bot.py bot
```

Bot commands: `/start`, `/sync`, `/plan`, `/today`, `/analyze`, `/results`, `/help`

## Development

```bash
just setup       # install dev deps
just lint        # ruff check
just fix         # ruff auto-fix
just fmt         # ruff format
just typecheck   # mypy
just test        # pytest
just test-cov    # pytest with coverage
```

## Project Structure

```
running-coach/
├── SKILL.md                 # OpenClaw tool catalog
├── CLAUDE.md                # AI agent project context
├── _token_utils.py          # Shared token management (stdlib only)
├── strava_auth.py           # OAuth setup
├── strava_sync.py           # Activity sync + cache
├── pot10.py                 # Power of 10 results
├── plan.py                  # Training plan management
├── analyze.py               # Session analysis
├── telegram_bot.py          # Telegram integration
├── config/
│   ├── SOUL.md              # Coaching personality
│   ├── AGENTS.md            # Agent behaviour rules
│   ├── athlete-profile.md   # Athlete intake template
│   └── openclaw.json.example
├── docker/
│   ├── Dockerfile.skills
│   └── docker-compose.yml
├── data/                    # Runtime data (gitignored)
└── tests/
```

## Data Files

All stored in `data/` (gitignored):

| File | Contents |
|------|----------|
| `tokens.json` | Strava OAuth tokens (chmod 600) |
| `athlete.json` | Strava athlete profile |
| `activities.json` | Cached activities |
| `race_results.json` | Power of 10 + manual results |
| `training_plan.json` | Current training plan |
| `athlete_zones.json` | HR and pace zones |
| `training_log.json` | Analysed session history |
