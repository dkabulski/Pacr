# Contributing to Pacr

Thanks for your interest in contributing. Pacr is a personal running coach bot — contributions that keep it focused and simple are most welcome.

## What to contribute

- Bug fixes
- Improvements to existing features
- Better test coverage
- Documentation clarifications

If you want to add a significant new feature, open an issue first to discuss it.

## Development setup

```bash
# 1. Clone and install dev dependencies
git clone https://github.com/dkabulski/Pacr.git
cd Pacr
just setup

# 2. Copy and fill in credentials
cp .env.example .env

# 3. Run the test suite
just test
```

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), [just](https://github.com/casey/just).

## Code style

- **Formatter**: `just fmt` (ruff format)
- **Linter**: `just lint` (ruff check)
- **Type checker**: `just typecheck` (mypy)
- British English in user-facing strings
- Keep scripts self-contained with PEP 723 inline metadata where applicable

Run all checks before submitting:

```bash
just fmt && just lint && just typecheck && just test
```

## Project structure

```
src/
  _token_utils.py      # shared token/data-dir management
  strava_utils/        # Strava OAuth, sync, Power of 10
  coach_utils/         # session analysis, training plan, load metrics
  tgbot/               # Telegram bot (handlers, formatters, Claude chat)
```

## Pull requests

1. Fork the repo and create a branch from `main`
2. Make your changes with tests where appropriate
3. Ensure all checks pass (`just fmt && just lint && just typecheck && just test`)
4. Open a pull request with a clear description of what and why

## Reporting issues

Open a GitHub issue with:
- What you expected to happen
- What actually happened
- Relevant logs or error output
- Your Python and uv versions

## Licence

By contributing you agree that your contributions will be licensed under the [MIT Licence](LICENSE).
