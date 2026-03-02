"""Claude orchestration — tool definitions, execution, and conversation loop."""

from __future__ import annotations

import logging

from tgbot.context import CLAUDE_MODEL, _build_athlete_context
from tgbot.handlers import (
    _BLOCKED_FILES,
    _auto_analyse_new_activities,
    _validate_data_path,
)

logger = logging.getLogger("pacr")

TOOLS = [
    {
        "name": "sync_strava",
        "description": (
            "Fetch the athlete's latest Strava activities and update the "
            "local cache. Call this when the athlete asks to sync, refresh, "
            "update, or fetch their Strava data or recent runs. Use a larger "
            "days value when the athlete asks for a full or historical sync."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": (
                        "How many days of history to fetch (default 365). "
                        "Use 3650 for a full 10-year backfill."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "name": "save_plan",
        "description": (
            "Save a modified training plan to disk. Use this when the athlete "
            "asks to change, adjust, move, or update any part of their plan. "
            "Construct the complete modified plan — preserving all unchanged "
            "weeks — and call this tool to persist it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "object",
                    "description": (
                        "Complete training plan with 'goal' and 'weeks' array."
                    ),
                }
            },
            "required": ["plan"],
        },
    },
    {
        "name": "list_data_files",
        "description": (
            "List the available data files in the data directory. "
            "Call this to discover what files exist before reading one."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_data_file",
        "description": (
            "Read the contents of a data file by name (e.g. 'athlete.json', "
            "'training_log.json'). Use list_data_files first if unsure what exists. "
            "tokens.json is never accessible."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename to read, e.g. 'athlete.json'",
                }
            },
            "required": ["filename"],
        },
    },
    {
        "name": "save_memory",
        "description": (
            "Save a coaching insight, athlete preference, or session note to "
            "long-term memory so it can inform future conversations. Call this "
            "when the athlete shares: how a session felt, race debrief observations, "
            "training preferences, injury notes, or any personal detail worth "
            "remembering. Do NOT call for routine factual queries."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": (
                        "Self-contained coaching note, e.g. 'Tempo session on "
                        "2026-03-02 felt harder than expected — busy streets, "
                        "heavy legs. Athlete prefers the track for tempo work.'"
                    ),
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "session_feedback",
                        "race_debrief",
                        "preference",
                        "injury",
                        "general",
                    ],
                },
            },
            "required": ["text", "category"],
        },
    },
    {
        "name": "compute_distance",
        "description": (
            "Calculate total distance and activity count for a sport over a "
            "time period. Use this for any question about how far the athlete "
            "has run, cycled, swum, or walked over a specific period — "
            "including follow-up questions like 'what about 2024?' that refer "
            "to a previous distance query. Use breakdown='month' or "
            "breakdown='year' to rank all periods and answer questions like "
            "'biggest month', 'best year', 'peak training month'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": (
                        "Time period in natural language: 'last year', '2024',"
                        " 'this month', 'january 2024', 'last week', 'ever'. "
                        "Omit when using breakdown to scan all history."
                    ),
                },
                "sport": {
                    "type": "string",
                    "enum": ["run", "ride", "swim", "walk", "hike", "all"],
                    "description": "Sport type (defaults to 'run').",
                },
                "breakdown": {
                    "type": "string",
                    "enum": ["month", "year"],
                    "description": (
                        "Group totals by this granularity and return them "
                        "sorted by distance. Use to answer 'biggest month', "
                        "'best year', 'peak training month', etc."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "lookup_activities",
        "description": (
            "Search cached Strava activities by date range and/or workout type. "
            "Use this when the athlete asks about a specific past run, a particular "
            "date, their longest run, fastest pace, any historical session detail "
            "beyond the last four weeks, or questions about races, workouts, or "
            "long runs specifically. Use workout_type to filter by type, and set "
            "limit high (e.g. 9999) when counting all matching activities."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {
                    "type": "string",
                    "description": "Start date YYYY-MM-DD (inclusive)",
                },
                "date_to": {
                    "type": "string",
                    "description": "End date YYYY-MM-DD (inclusive)",
                },
                "workout_type": {
                    "type": "string",
                    "enum": ["race", "long run", "workout", "default run"],
                    "description": (
                        "Filter by Strava workout type. 'race' = flagged as a "
                        "race by the athlete; 'long run' = marked as long run; "
                        "'workout' = structured workout; 'default run' = "
                        "unclassified run."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Max activities to return (default 10). "
                        "Set to 9999 to retrieve all matching activities."
                    ),
                },
            },
            "required": [],
        },
    },
]


def execute_tools(msg: object) -> list:
    """Run all tool calls in msg and return tool_results list."""
    from strava_utils import strava_sync

    tool_results = []
    for block in msg.content:  # type: ignore[union-attr]
        if block.type != "tool_use":
            continue
        if block.name == "list_data_files":
            import _token_utils

            available = [
                f.name
                for f in sorted(_token_utils.DATA_DIR.iterdir())
                if f.is_file() and f.name not in _BLOCKED_FILES
            ]
            result = (
                "Available files: " + ", ".join(available)
                if available
                else "No data files found."
            )
        elif block.name == "read_data_file":
            filename = block.input.get("filename", "")
            path = _validate_data_path(filename)
            if path is None:
                result = f"Access denied: {filename}"
            elif not path.exists():
                result = f"{filename} not found."
            else:
                result = path.read_text()
        elif block.name == "save_plan":
            from coach_utils import plan as plan_mod

            try:
                plan_dict = block.input.get("plan", {})
                if not isinstance(plan_dict, dict) or "weeks" not in plan_dict:
                    result = "Invalid plan — must contain a 'weeks' array."
                else:
                    plan_mod._save_plan(plan_dict)
                    result = f"Plan saved ({len(plan_dict['weeks'])} weeks)."
            except Exception as e:
                result = f"Failed to save plan: {e}"
        elif block.name == "sync_strava":
            try:
                days = int(block.input.get("days", 365))
                before_ids = {a["id"] for a in strava_sync._load_cached()}
                strava_sync.sync(days)
                note = _auto_analyse_new_activities(before_ids)
                all_acts = strava_sync._load_cached()
                from memory.store import index_activities

                indexed = index_activities(all_acts)
                result = (
                    f"Sync complete. Activities cache updated "
                    f"({indexed} indexed to memory)."
                )
                if note:
                    result += f"\n\nNew activity analysis:\n{note}"
                try:
                    from tgbot.telegram_send import _send_telegram_message

                    new_count = len(all_acts) - len(before_ids)
                    tg_lines = [
                        f"✅ Strava sync complete ({days}d window).",
                        f"  • {new_count} new "
                        f"activit{'y' if new_count == 1 else 'ies'} fetched",
                        f"  • {indexed} activities indexed to memory",
                    ]
                    if note:
                        tg_lines.append(f"\n{note}")
                    _send_telegram_message("\n".join(tg_lines), parse_mode="HTML")
                except Exception:
                    logger.warning(
                        "sync_strava: Telegram notification failed", exc_info=True
                    )
            except Exception as e:
                result = f"Sync failed: {e}"
        elif block.name == "save_memory":
            from datetime import UTC, datetime

            from memory.store import save_memory

            text = block.input.get("text", "").strip()
            category = block.input.get("category", "general")
            if not text:
                result = "save_memory: text is required."
            else:
                metadata: dict[str, str | int | float] = {
                    "category": category,
                    "date": datetime.now(tz=UTC).strftime("%Y-%m-%d"),
                }
                ok = save_memory(text, metadata)
                result = (
                    "Memory saved." if ok else "Memory unavailable — could not save."
                )
        elif block.name == "compute_distance":
            from collections import defaultdict

            from tgbot.km_query import (
                compute_km,
                describe_period,
                parse_period,
                sport_label,
                types_for_key,
            )

            period_str = block.input.get("period", "")
            sport_key = block.input.get("sport", "run")
            breakdown = block.input.get("breakdown", "")
            types = (
                None if sport_key == "all" else types_for_key(sport_key)
            )
            acts = strava_sync._load_cached()

            if breakdown in ("month", "year"):
                # Pre-filter by sport
                if types:
                    acts = [
                        a for a in acts
                        if a.get("type") in types
                        or a.get("sport_type") in types
                    ]
                # Optionally narrow by period
                if period_str:
                    parsed = parse_period(period_str)
                    if parsed:
                        s_str = parsed[0].isoformat()
                        e_str = parsed[1].isoformat()
                        acts = [
                            a for a in acts
                            if s_str <= a.get("date", "")[:10] <= e_str
                        ]
                key_len = 7 if breakdown == "month" else 4
                groups: dict[str, dict] = defaultdict(
                    lambda: {"total_km": 0.0, "count": 0}
                )
                for a in acts:
                    k = a.get("date", "")[:key_len]
                    if not k:
                        continue
                    groups[k]["total_km"] += a.get("distance_km", 0.0)
                    groups[k]["count"] += 1
                if not groups:
                    result = "No activities found."
                else:
                    ranked = sorted(
                        groups.items(),
                        key=lambda x: x[1]["total_km"],
                        reverse=True,
                    )
                    word = sport_label(types) if types else "activity"
                    lines = [
                        f"  {k}: {v['total_km']:.1f} km"
                        f" ({v['count']} {word}"
                        f"{'s' if v['count'] != 1 else ''})"
                        for k, v in ranked[:12]
                    ]
                    result = (
                        f"Top {breakdown}s by distance:\n"
                        + "\n".join(lines)
                    )
            else:
                parsed = parse_period(period_str) if period_str else None
                if parsed is None:
                    result = (
                        f"Could not parse period: {period_str!r}"
                        if period_str
                        else "Specify a period (e.g. 'last year', '2024')."
                    )
                else:
                    start, end = parsed
                    stats = compute_km(acts, start, end, types)
                    label = describe_period(start, end)
                    word = sport_label(types) if types else "activity"
                    s = "s" if stats["count"] != 1 else ""
                    result = (
                        f"{stats['total_km']:.1f} km across "
                        f"{stats['count']} {word}{s} {label}."
                    )
        elif block.name == "lookup_activities":
            inp = block.input
            date_from = inp.get("date_from", "")
            date_to = inp.get("date_to", "9999-12-31")
            limit = int(inp.get("limit", 10))
            workout_type_filter = inp.get("workout_type", "")
            from memory.store import _WORKOUT_TYPE_LABELS

            acts = [
                a
                for a in strava_sync._load_cached()
                if date_from <= a.get("date", "")[:10] <= date_to
                and (
                    not workout_type_filter
                    or _WORKOUT_TYPE_LABELS.get(
                        a.get("workout_type") or 0, "default run"
                    ) == workout_type_filter
                )
            ][:limit]
            if not acts:
                result = "No activities found for those filters."
            else:
                rows = []
                for a in acts:
                    date = a.get("date", "")[:10]
                    dist = a.get("distance_km", 0)
                    pace = a.get("pace", "N/A")
                    hr = a.get("avg_hr")
                    hr_str = f", HR {hr:.0f} bpm" if hr else ""
                    s = a.get("moving_time_s", 0)
                    t = (
                        f"{s // 3600}h{(s % 3600) // 60:02d}m"
                        if s >= 3600
                        else f"{s // 60}:{s % 60:02d}"
                    )
                    elev = a.get("elevation_m")
                    elev_str = f", elev {elev:.0f}m" if elev else ""
                    wtype = _WORKOUT_TYPE_LABELS.get(
                        a.get("workout_type") or 0, "default run"
                    )
                    rows.append(
                        f"{date} — {a.get('name', 'Run')}: "
                        f"{dist:.2f}km in {t} @ {pace}/km{hr_str}{elev_str}"
                        f" [{wtype}]."
                    )
                result = (
                    f"{len(acts)} activit{'y' if len(acts) == 1 else 'ies'} found.\n"
                    + "\n".join(rows)
                )
        else:
            result = f"Unknown tool: {block.name}"
        tool_results.append(
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            }
        )
    return tool_results


def call_claude(api_key: str, history: list[dict], sport_key: str = "run") -> str:
    """Run one full Claude conversation turn (with up to 5 tool-call rounds).

    Args:
        api_key: Anthropic API key.
        history: Conversation history (list of message dicts).
        sport_key: Active sport filter key (e.g. "run", "ride", "all").

    Returns:
        The assistant's text reply.
    """
    import anthropic

    query = next(
        (
            m["content"][:500]
            for m in reversed(history)
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ),
        "",
    )
    system_prompt = _build_athlete_context(sport_key=sport_key, query=query)
    client = anthropic.Anthropic(api_key=api_key)
    messages = list(history)
    cached_system = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        system=cached_system,
        messages=messages,
        tools=TOOLS,
    )

    rounds = 0
    for _ in range(5):  # max tool-call rounds
        if msg.stop_reason != "tool_use":
            break
        tool_names = [b.name for b in msg.content if hasattr(b, "name")]
        logger.info("Claude tool call(s): %s", tool_names)
        tool_results = execute_tools(msg)
        rounds += 1
        messages = [
            *messages,
            {"role": "assistant", "content": msg.content},
            {"role": "user", "content": tool_results},
        ]
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            system=cached_system,
            messages=messages,
            tools=TOOLS,
        )

    text = next((b.text for b in msg.content if hasattr(b, "text")), "")
    logger.info("Claude reply: %d chars, %d tool round(s)", len(text), rounds)
    return text
