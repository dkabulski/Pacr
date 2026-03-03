# Pacr

![Pacr](assets/pacr-logo.svg)

AI running coach powered by Claude. Analyses Strava data, looks up race results, and manages training plans following Jack Daniels' Running Formula methodology.

## Overview

Claude calls Python scripts via `uv run` to pull data and manage training. All data persists as JSON in `data/`. The interactive Telegram bot provides a conversational coaching interface with automatic Strava syncing every 30 minutes.

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [just](https://github.com/casey/just) (optional, for dev commands)
- A [Strava API application](https://www.strava.com/settings/api)
- A [Telegram bot](https://t.me/BotFather) token and chat ID

### Setup

```bash
# 1. Clone the repository
git clone <repo-url> Pacr
cd Pacr

# 2. Install dev dependencies
just setup

# 3. Copy and fill in credentials
cp .env.example .env
# Edit .env with your Strava, Telegram, and Anthropic keys

# 4. Authorise with Strava (opens browser)
just auth

# 5. Set your HR zones (replace 190 with your max HR)
just zones 190

# 6. Start the interactive bot
just tg-bot

# — or — run in Docker
just docker-build
just docker-up
```

### Telegram chat ID

After creating your bot via @BotFather, fetch your chat ID:

```bash
curl "https://api.telegram.org/bot<TOKEN>/getUpdates" | jq '.result[0].message.chat.id'
```

## Telegram Bot Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/start` | Greeting and status overview | `/start` |
| `/sync [days]` | Sync Strava activities (default 365 days) | `/sync` or `/sync 50` |
| `/today` | Today's prescribed session | `/today` |
| `/week` | This week's plan vs completed sessions | `/week` |
| `/next` | Next 5 upcoming sessions | `/next` |
| `/last` | Full detail on the last activity | `/last` |
| `/summary` | Last 7 days: distance, time, pace | `/summary` |
| `/plan` | Training plan overview | `/plan` |
| `/setplan <goal>` | Generate a new plan with AI | `/setplan half marathon on April 3 2026 in 1:21h` |
| `/analyse` | Analyse last activity: flags, coaching opinion & debrief | `/analyse` |
| `/reanalyse` | Re-analyse last activity on demand | `/reanalyse` |
| `/results` | Cached race results | `/results` |
| `/load` | Training load: CTL/ATL/TSB + weekly km | `/load` |
| `/adherence [weeks]` | Plan adherence score (default 4 weeks) | `/adherence` or `/adherence 8` |
| `/sport [type]` | Set activity filter: run / ride / hike / swim / walk / all | `/sport ride` |
| `/zones` | HR and pace training zones | `/zones` |
| `/clear` | Clear conversation history | `/clear` |
| `/help` | Show all commands | `/help` |

You can also send free-text messages to chat directly with your AI coach.

## How to Use

### Distance & mileage queries

The coach resolves distance queries using your full Strava history, including follow-up questions that reference the previous answer.

```
How many km did I run last year?
→ You logged 2 036.6 km across 179 runs in 2025.

What about 2024?
→ You logged 2 148.0 km across 180 runs in 2024.

What's my biggest month ever?
→ Top months by distance:
    2024-09: 312.4 km (28 runs)
    2023-08: 298.1 km (25 runs)
    ...

Best year for cycling?
→ Top years by distance:
    2024: 1 204.0 km (87 rides)
    2023: 980.3 km (71 rides)
```

### Personal records

```
What are my PBs?
→ Personal records:
    5k Race: 21:04 (2024-09-14)
    10k Race: 44:30 (2025-04-06)
    Half Marathon Race: 1:41:22 (2025-10-05)
    Longest Run: 42.2 km (2023-06-18)
    Biggest Week: 112.4 km (2024-09-02)
    Longest Streak: 14 days

What's my longest run ever?
→ (calls lookup_activities sorted by distance)
    2023-06-18 — Very Long Sunday: 42.20 km in 4h12m @ 5:58/km [long run]
```

### Training history

```
Show me my races from 2024
→ 8 activities found (sorted by date_desc).
    2024-10-05 — Half Marathon: 21.10 km in 1:41:22 @ 4:48/km [race]
    ...

What was my fastest 10 km run?
→ (sorted by pace, filtered to ~10 km runs)
```

### Training load & race readiness

```
How's my training load?
→ (same as /load — CTL, ATL, TSB and weekly km trend)

Am I ready for a half marathon in 1:30?
→ Race readiness: ON TRACK
    Goal: half marathon in 1:30h (4:16/km)
    Weekly avg: 68.4 km (on target)
    Longest recent run: 18.0 km (needs extension)
    CTL: 72.4 (trend: rising)
    VDOT: 52.0
    Positive: CTL above target; weekly volume consistent
    Concerns: Long run coverage below race distance
```

### Plan adherence

```
How well have I been sticking to my plan?
→ (same as /adherence — completed, partial, missed, rest days honoured)

/adherence 8
→ Plan Adherence (8 weeks)
    Score: 78%
    ✅ Completed: 31
    🔶 Partial: 6
    ❌ Missed: 9
    😴 Rest days honoured: 14/16
```

### Splits & pacing analysis

```
How were my splits on Thursday's run?
→ Split analysis (10 splits):
    Paces: 4:52, 4:48, 4:45, 4:41, 4:38, 4:35, 4:33, 4:30, 4:27, 4:22
    Mean: 4:39/km, CV: 2.8%
    Flags: negative_split

Was my marathon evenly paced?
→ (fetches splits for the activity and reports pacing pattern)
```

### Wellness & injury tracking

```
My left knee has been a bit sore after long runs, about a 3/10.
→ Logged: soreness in left knee, severity 3/10. ID: abc123

Any injury patterns I should know about?
→ Active issues (1):
    [abc123] 2026-03-01 — soreness in left knee, severity 3/10
  Patterns detected (1):
    ⚠ recurring_soreness: left knee — 3 entries in 14 days

My knee is fine now.
→ Issue abc123 resolved.
```

### Training plan management

```
Move Tuesday's tempo run to Thursday.
→ (saves updated plan — all other sessions unchanged)

Make next week a recovery week, cap everything at easy pace.
→ (modifies plan and confirms the change)

/setplan marathon on 15 June 2026 in 3:30
→ Generating a 15-week plan using Jack Daniels mesocycles…
```

### Post-run debrief

After each auto-detected activity you will be prompted:

```
🏃 New run detected: "Tuesday Tempo" — 12.4 km @ 4:32/km
How did that feel? Reply with RPE 1–10 (or "skip").

→ 7 — legs were heavy in the last 3 km

RPE 7/10 logged. (saved to memory and indexed for future context)
```

### Switching sport focus

```
/sport ride
→ Activity filter set to ride. /last, /summary, /load and /analyse now show only rides.

/sport all
→ Activity filter set to all.
```

### Long-term memory

The bot uses a local ChromaDB vector store (`data/chroma/`) to remember coaching insights across conversations. When you share how a session felt, a race debrief, or a training preference, Claude saves it automatically. Relevant memories are retrieved on every message and injected into the coaching context. Strava activities (including `workout_type`) are also indexed on every sync, enabling semantic queries like "how have my races gone?" or "show me my long runs".

### Automatic Strava sync

The bot polls Strava every 30 minutes (configurable via `STRAVA_POLL_INTERVAL` in `.env`). When a new activity is detected it is automatically analysed and a coaching note is sent to the chat. The delay before auto-analysis is controlled by `STRAVA_ANALYSIS_DELAY` (default: 600s / 10 min). Set `LOG_FORMAT=json` for structured JSON log output.

Activity descriptions are fetched on explicit `/sync` calls but skipped during background polls to stay within Strava's rate limits (100 req/15 min).

## Docker

Build and run the Telegram bot in a container with automatic restart:

```bash
# Build image
just docker-build

# Start the bot in the background
just docker-up

# Follow logs
just docker-logs

# Stop
just docker-down
```

The `data/` directory is mounted from the host (`./data`) so all activity, plan, and vector memory data persists across container restarts. The ChromaDB embedding model (~80 MB) is cached in a named Docker volume (`chroma-model-cache`) so it is only downloaded once.

## Development

```bash
just setup       # install dev deps
just lint        # ruff check
just fix         # ruff auto-fix
just fmt         # ruff format
just typecheck   # mypy
just test        # pytest
just test-cov    # pytest with coverage
just pre-commit  # install ruff pre-commit hooks
just sync        # fetch Strava activities (last 365 days)
just plan        # show current training plan
just auth        # Strava OAuth authorisation
just auth-status # check Strava token validity
```

## Project Structure

```
Pacr/
├── CLAUDE.md                    # AI agent project context
├── src/
│   ├── _token_utils.py          # Shared token management (stdlib only)
│   ├── strava_utils/
│   │   ├── strava_auth.py       # OAuth setup
│   │   ├── strava_sync.py       # Activity sync + cache (retry/backoff)
│   │   └── pot10.py             # Power of 10 results [EXPERIMENTAL]
│   ├── coach_utils/
│   │   ├── analyze.py           # Session analysis
│   │   ├── plan.py              # Training plan management
│   │   └── training_load.py     # CTL/ATL/TSB training load metrics
│   ├── memory/
│   │   └── store.py             # ChromaDB vector memory (save/query/index)
│   └── tgbot/
│       ├── __init__.py
│       ├── bot.py               # Thin entry point + fire CLI
│       ├── handlers.py          # Command handlers + BotConfig state
│       ├── claude_chat.py       # Claude tool defs + orchestration
│       ├── telegram_send.py     # Lightweight Telegram send (no circular deps)
│       ├── formatters.py        # HTML formatters and data helpers
│       ├── context.py           # Athlete context + VDOT helpers
│       ├── debrief.py           # Post-run RPE debrief storage
│       └── km_query.py          # Local km/distance queries (no API)
├── config/
│   ├── SOUL.md                  # Coaching personality
│   ├── AGENTS.md                # Agent behaviour rules
│   └── athlete-profile.md       # Athlete intake template
├── docker/
│   ├── Dockerfile.skills
│   └── docker-compose.yml
├── tests/
└── data/                        # Runtime data (gitignored)
```

## Data Files

All stored in `data/` (gitignored):

| File | Contents |
|------|----------|
| `tokens.json` | Strava OAuth tokens (chmod 600) |
| `athlete.json` | Strava athlete profile |
| `activities.json` | Cached Strava activities |
| `race_results.json` | Power of 10 + manual results |
| `training_plan.json` | Current training plan |
| `athlete_zones.json` | HR and pace zones |
| `training_log.json` | Analysed session history |
| `debriefs.json` | Post-run RPE debriefs |
| `conversation_history.json` | Telegram chat history |
| `chroma/` | ChromaDB vector store (coaching memory + activity index) |

## Race Results — Power of 10

> ⚠ **Experimental**: The Power of 10 website is being rebuilt and web scraping
> is unreliable. Manual entry is the recommended primary workflow:

```bash
uv run src/strava_utils/pot10.py add --date=2025-06-15 --event=parkrun --distance=5K --time=22:30
```

Web fetch (may fail):

```bash
uv run src/strava_utils/pot10.py fetch --athlete_id=123456 --verbose
```

## Future Improvements / GCP Deployment

For a production-grade, always-on deployment:

- **Cloud Run** — containerised bot with `gcloud run deploy`, scales to zero between messages
- **Cloud Scheduler** — daily sync cron job + 07:00 morning briefing (replaces `STRAVA_POLL_INTERVAL`)
- **Secret Manager** — replace `.env` file with GCP-managed secrets (`TELEGRAM_BOT_TOKEN`, `STRAVA_CLIENT_SECRET`, `ANTHROPIC_API_KEY`)
- **Artifact Registry + Cloud Build** — CI/CD pipeline: push to `main` → build image → deploy to Cloud Run
- **Estimated cost** — ~$5–10/month (Cloud Run min instances + Scheduler invocations + Secret Manager)
