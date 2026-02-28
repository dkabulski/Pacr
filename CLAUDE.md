# RunWhisperer — OpenClaw Skill

## Project Layout

- `SKILL.md` — tool catalog for OpenClaw agent (defines available commands)
- `src/_token_utils.py` — shared Strava token management (stdlib only)
- `src/strava_auth.py` — one-time OAuth setup (`uv run src/strava_auth.py authorize`)
- `src/strava_sync.py` — fetch & cache Strava activities (`uv run src/strava_sync.py sync`)
- `src/pot10.py` — Power of 10 race results by athlete ID ⚠ experimental
- `src/analyze.py` — rate sessions against plan + HR/pace zones
- `src/plan.py` — manage training plan JSON via stdin
- `src/tgbot/formatters.py` — HTML formatters and data helpers for Telegram messages
- `src/tgbot/context.py` — athlete context building, VDOT helpers, Claude plan generation
- `src/tgbot/bot.py` — Telegram CLI entry point (send + interactive bot)
- `config/` — OpenClaw personality, agent behaviour, athlete profile template
- `data/` — runtime data (gitignored): tokens, activities, plans, results
- `docker/` — container config for deployment
- `tests/` — pytest suite

## Conventions

- Python 3.12, PEP 723 inline metadata on each script for `uv run`
- `_token_utils.py` uses stdlib only (no PEP 723 header needed)
- All scripts use `python-fire` for CLI subcommands
- Data persists as JSON in `data/`
- British English in user-facing text
- Ruff for linting + formatting, mypy for type checking

## Key Commands

```bash
just setup       # install dev deps
just lint        # ruff check
just fmt         # ruff format
just test        # pytest
just test-cov    # pytest with coverage
just sync        # fetch Strava activities
just plan        # show training plan
just deploy      # symlink to OpenClaw skills dir
just tg-send     # send a test message to Telegram
just tg-bot      # start interactive Telegram bot
just docker-build  # build Docker image
just docker-up     # start telegram-bot container
just docker-logs   # follow container logs
just docker-down   # stop containers
```

## Data Files

| File | Contents |
|------|----------|
| `data/tokens.json` | Strava OAuth tokens (chmod 600) |
| `data/athlete.json` | Strava athlete profile |
| `data/activities.json` | Cached Strava activities |
| `data/race_results.json` | Power of 10 + manual race results |
| `data/training_plan.json` | Current training plan |
| `data/athlete_zones.json` | HR and pace zones |
| `data/training_log.json` | Analyzed session log |
| `data/conversation_history.json` | Telegram chat history (persisted across restarts) |
