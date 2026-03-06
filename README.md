# Pacr

![Pacr](assets/pacr-logo.svg)

<p align="center">
  An AI running coach that lives in Telegram and knows your entire Strava history.
</p>

---

Ask it anything about your training. It answers from your real data, remembers what you tell it, and gives you coaching opinions — not just numbers.

```
How many km did I run last year?
→ You logged 2 036.6 km across 179 runs in 2025.

What about 2024?
→ You logged 2 148.0 km across 180 runs in 2024.

Am I ready for a half marathon in 1:30?
→ Race readiness: ON TRACK. CTL 72.4 (rising), weekly avg 68.4 km.
  Concern: longest recent run is 18 km — needs one more long effort.
```

---

## Screenshots

![Pacr](assets/example-1.png)
![Pacr](assets/example-2.png)
![Pacr](assets/example-3.png)

---

## What it does

### Ask anything about your training
Chat with it like a coach. It pulls from your full Strava history and understands follow-up questions — so "what about 2024?" after asking about last year just works.

- Distance totals by week, month, year, or any period
- Personal bests: fastest 5k, longest run, biggest week, longest streak
- Race history, pacing analysis, training trends
- Works across all sports — running, cycling, hiking, swimming, walking

### Post-run debrief & long-term memory
When a new activity is detected, the bot prompts you to rate how it felt (RPE 1–10). That feedback is saved to a local vector store and surfaced in future conversations — so the coach gets to know you over time.

```
🏃 New run detected: "Tuesday Tempo" — 12.4 km @ 4:32/km
How did that feel? Reply with RPE 1–10 (or "skip").

→ 7 — legs were heavy in the last 3 km

RPE 7/10 logged.
```

### Training load & race readiness
See your fitness, fatigue and form (CTL/ATL/TSB) at a glance, with a 12-week weekly km chart. Ask whether you're ready for a specific race and goal time — the bot cross-checks your current load, long run coverage, CTL trend, and VDOT.

### Zone breakdown
See how your training volume is distributed across HR zones. Flags grey-zone (zone 3) overuse and checks you're keeping enough volume easy.

```
/breakdown 8
→ Zone Breakdown — last 8 weeks (412 km, 38 activities)
  Z1 Recovery   ░░░░░░░░░░░░░░   2%    8.2 km
  Z2 Easy       ████████████░░░  72%  296.6 km
  Z3 Tempo      ███░░░░░░░░░░░░  14%   57.7 km
  Z4 Threshold  ██░░░░░░░░░░░░░   9%   37.1 km
  Z5 VO2max     █░░░░░░░░░░░░░░   3%   12.4 km
  ✅ 74% easy — good polarisation
```

### Training plan
Generate a structured training plan for any goal race and target time, following Jack Daniels' methodology (base → build → sharpen → taper). Weeks always run Monday–Sunday. Optionally cap training days per week and maximum weekly km so the plan fits your life.

- `/planview` — compact week-by-week overview with phase, date range, and km totals
- `/week` — this week's sessions vs what you've completed
- `/week 5` — jump to any specific week in the plan
- `/editweek 3 make it a recovery week` — modify a specific week with natural language
- `/adherence` — percentage score across completed, partial, and missed sessions

Sessions are formatted with emojis: 🔥 tempo/intervals, 🍃 easy, ⏳ long runs.

### Race predictions & pacing
Predict race times across standard distances using your current VDOT, or calculate training paces for any target time.

```
/predict
→ Based on VDOT 53.2:
  5K:     18:30    10K:    38:28
  Half:   1:24:12  Marathon: 2:56:44

/pace 1:25 half
→ Goal pace: 4:01/km
  Easy: 5:05–5:30    Tempo: 4:05–4:15
  Interval: 3:42     Repetition: 3:28
```

### Weekly debrief
Every Sunday evening the bot sends an automatic check-in with your week-vs-plan summary, asks how the week went, and carries your response into the coaching context.

### Injury & wellness tracking
Log niggles in plain language. The bot tracks them over time, detects patterns (e.g. recurring knee soreness after long runs), and factors them into its coaching advice.

```
My left knee has been a bit sore after long runs, about a 3/10.
→ Logged. I'll keep an eye on it.

Any injury patterns I should know about?
→ ⚠ recurring_soreness: left knee — 3 entries in 14 days.
```

### Automatic Strava sync
Strava activities are synced automatically every 30 minutes (polling) or instantly via Strava webhooks. New activities are detected, analysed against your plan and HR zones, and the coaching analysis is sent to the chat — no action needed.

### Model switching
Switch between Claude models on the fly with `/model haiku`, `/model sonnet`, or `/model opus`. Heavier tasks (plan generation, week editing) always use Sonnet regardless of the chat model.

---

## Getting Started

### What you'll need

- A [Strava API application](https://www.strava.com/settings/api) (free — takes ~2 minutes to create)
- A [Telegram bot](https://t.me/BotFather) token (free — create via @BotFather)
- An [Anthropic API key](https://console.anthropic.com/) for Claude
- Python 3.12+ and [uv](https://docs.astral.sh/uv/) installed locally

### Setup

```bash
# 1. Clone the repo
git clone <repo-url> Pacr
cd Pacr

# 2. Install dependencies
uv sync --extra dev

# 3. Copy the example config and fill in your keys
cp .env.example .env
# Edit .env — add STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET,
#              TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY

# 4. Authorise with Strava (opens a browser window)
uv run src/strava_utils/strava_auth.py authorize

# 5. Sync your activities
uv run src/strava_utils/strava_sync.py sync

# 6. Set your HR zones (replace 190 with your max HR)
uv run src/coach_utils/training_load.py zones 190

# 7. Start the bot
uv run src/tgbot/bot.py bot
```

Once the bot is running, open Telegram and send `/start`.

> **Tip — find your Telegram chat ID:**
> ```bash
> curl "https://api.telegram.org/bot<TOKEN>/getUpdates" | jq '.result[0].message.chat.id'
> ```

### Running with Docker

The easiest way to keep the bot running continuously:

```bash
# Build and start in the background
just docker-build
just docker-up

# Follow logs
just docker-logs

# Stop
just docker-down
```

All data (activities, plan, memory) is stored in `./data` on your host and persists across restarts.

---

## Commands

| Command | What it does |
|---------|-------------|
| `/start` | Overview of your status and today's session |
| `/sync [days]` | Sync Strava activities (default: last 365 days) |
| `/today` | Today's prescribed training session |
| `/countdown` | Days to race day |
| `/week [N]` | This week vs plan, or a specific week — e.g. `/week 5` |
| `/next` | Next 5 upcoming sessions |
| `/last` | Full breakdown of your last activity |
| `/summary` | Last 7 days: distance, time, pace |
| `/plan` | Current week of training plan |
| `/planview` | Week-by-week plan overview with phase and km totals |
| `/editweek <N> <instruction>` | Edit a plan week with natural language — e.g. `/editweek 3 make it a recovery week` |
| `/setplan <goal> [--days=N] [--max-km=N]` | Generate a new AI training plan — e.g. `/setplan half marathon on April 3 2026 in 1:21h --days=5 --max-km=70` |
| `/analyse` | Analyse last activity: pacing flags, HR zones, coaching opinion |
| `/reanalyse` | Re-run analysis on the last activity |
| `/load` | Training load: CTL (fitness), ATL (fatigue), TSB (form) + weekly km chart |
| `/readiness` | Race readiness assessment |
| `/adherence [weeks]` | Plan adherence score — e.g. `/adherence 8` |
| `/breakdown [weeks]` | Volume by HR zone — e.g. `/breakdown 8` |
| `/results` | Cached race results |
| `/predict` | Predict race times from VDOT |
| `/pace` | Pace calculator + training zones |
| `/zones` | Your HR and pace training zones |
| `/motivation` | Get a motivational quote |
| `/wellness` | Injury and wellness log |
| `/sport [type]` | Filter by sport: `run` / `ride` / `hike` / `swim` / `walk` / `all` |
| `/model [name]` | Switch AI model: `haiku` / `sonnet` / `opus` |
| `/clear` | Clear conversation history |
| `/help` | List all commands |

You can also send any free-text message to chat directly with the coach.

---

## All the things you can ask

### Distance & mileage

```
How many km did I run last year?
What about 2024?
What's my biggest month ever?
Best year for cycling?
How far have I walked this month?
```

### Personal records

```
What are my PBs?
What's my longest run ever?
What was my fastest 10k?
Biggest week I've ever had?
```

### Training history

```
Show me my races from 2024
What was my longest run in January?
How many runs did I do over 30 km?
```

### Training load & race readiness

```
How's my training load?
Am I ready for a half marathon in 1:30?
Is my CTL high enough for a marathon?
/readiness
/breakdown 8
```

### Plan & adherence

```
/planview
→ W01  base        Mar 03–09    5 sessions  ~52 km ◀
  W02  base        Mar 10–16    5 sessions  ~58 km
  W03  build       Mar 17–23    5 sessions  ~65 km
  ...

/week 3
→ Week 3 — build
  ✓ 2026-03-17 — 🍃 easy: Easy aerobic 10 km
  ✓ 2026-03-18 — 🔥 tempo: Threshold 8 km
  · 2026-03-20 — 🍃 easy: Recovery 8 km
  · 2026-03-21 — 🔥 intervals: 6×800m at interval pace
  · 2026-03-22 — ⏳ long: Long run 20 km

/setplan half marathon on May 17 2026 in 1:21h --days=5 --max-km=70
→ Generating your plan...

/editweek 3 make it a recovery week
/countdown
How well have I been sticking to my plan?
Move Tuesday's tempo run to Thursday.
```

### Predictions & pacing

```
/predict
/pace 1:25 half
How were my splits on Thursday's run?
Was my last race evenly paced?
What pace should I run a 10k in 38 minutes?
```

### Wellness & injuries

```
My left knee has been a bit sore, about a 3/10.
Any injury patterns I should know about?
My knee is fine now.
/wellness
```

### Switching sport & model

```
/sport ride    → all commands now show only rides
/sport all     → back to all sports
/model haiku   → switch to fast model for quick queries
/model sonnet  → switch back to default
```

---

## Technical Details

<details>
<summary>Project structure</summary>

```
Pacr/
├── src/
│   ├── _token_utils.py          # Shared token management (stdlib only)
│   ├── strava_utils/
│   │   ├── strava_auth.py       # OAuth setup
│   │   ├── strava_sync.py       # Activity sync + cache (retry/backoff)
│   │   ├── strava_webhook.py    # Strava webhook receiver (push events)
│   │   └── pot10.py             # Power of 10 / manual race results
│   ├── coach_utils/
│   │   ├── analyze.py           # Session analysis + HR zone classification
│   │   ├── plan.py              # Training plan management
│   │   ├── training_load.py     # CTL/ATL/TSB metrics
│   │   ├── readiness.py         # Race readiness assessment
│   │   ├── adherence.py         # Plan adherence scoring
│   │   ├── wellness.py          # Injury & wellness tracking
│   │   └── records.py           # Personal records detection
│   ├── memory/
│   │   └── store.py             # ChromaDB vector memory
│   └── tgbot/
│       ├── bot.py               # Entry point + scheduled jobs
│       ├── handlers.py          # Command handlers
│       ├── claude_chat.py       # Claude tool definitions + orchestration
│       ├── context.py           # Athlete context + VDOT helpers
│       ├── formatters.py        # Telegram HTML formatters
│       ├── debrief.py           # RPE debrief storage
│       ├── km_query.py          # Local distance queries (no API calls)
│       └── telegram_send.py     # Telegram message sending
├── config/
│   ├── SOUL.md                  # Coaching personality
│   ├── AGENTS.md                # Agent behaviour rules
│   └── athlete-profile.md       # Athlete intake template
├── docker/
│   ├── Dockerfile.skills
│   └── docker-compose.yml
└── tests/
```

</details>

<details>
<summary>Data files (stored in data/, gitignored)</summary>

| File | Contents |
|------|----------|
| `tokens.json` | Strava OAuth tokens |
| `athlete.json` | Strava athlete profile |
| `activities.json` | Cached Strava activities |
| `race_results.json` | Race results (Power of 10 + manual) |
| `records.json` | Personal records (auto-detected PBs) |
| `training_plan.json` | Current training plan |
| `athlete_zones.json` | HR and pace zones |
| `training_log.json` | Analysed session history |
| `debriefs.json` | Post-run RPE debriefs |
| `wellness_log.json` | Injury and wellness entries |
| `settings.json` | Bot settings (model, sport filter) |
| `conversation_history.json` | Telegram chat history |
| `chroma/` | ChromaDB vector store (coaching memory) |

</details>

<details>
<summary>Development commands</summary>

```bash
just setup       # install dev deps
just lint        # ruff check
just fix         # ruff auto-fix
just fmt         # ruff format
just typecheck   # mypy
just test        # pytest
just test-cov    # pytest with coverage
just auth        # Strava OAuth authorisation
just auth-status # check token validity
```

</details>

<details>
<summary>Race results</summary>

Add race results manually or bulk-import from a script:

```bash
# Single race
uv run src/strava_utils/pot10.py add --date=2025-06-15 --event=parkrun --distance=5K --time=22:30

# Bulk import (edit import_races.sh with your history)
bash import_races.sh
```

> Power of 10 web scraping (`pot10.py fetch`) is experimental — the site is being rebuilt. Manual entry via `add` is recommended.

</details>

<details>
<summary>GCP deployment (future)</summary>

For a production-grade, always-on deployment:

- **Cloud Run** — containerised bot, scales to zero between messages
- **Cloud Scheduler** — daily sync cron + morning briefing
- **Secret Manager** — replace `.env` with GCP-managed secrets
- **Artifact Registry + Cloud Build** — push to `main` → build → deploy
- **Estimated cost** — ~$5–10/month

</details>
