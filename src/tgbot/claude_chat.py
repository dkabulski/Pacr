"""Claude orchestration — tool definitions, execution, and conversation loop."""

from __future__ import annotations

import logging

from tgbot.context import (
    CLAUDE_MODEL,
    _build_static_context,
)
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
            "long runs specifically. Use sort_by='distance_desc' with limit=10 "
            "to find the longest runs — do NOT use limit > 100."
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
                "sort_by": {
                    "type": "string",
                    "enum": [
                        "date_desc",
                        "date_asc",
                        "distance_desc",
                        "distance_asc",
                    ],
                    "description": (
                        "Sort order (default 'date_desc'). Use 'distance_desc' "
                        "to find longest runs, 'distance_asc' for shortest."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Max activities to return (default 10, max 100). "
                        "Use compute_distance for counting large sets."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "check_records",
        "description": (
            "Check the athlete's personal records and PBs — fastest race times "
            "(5K, 10K, half marathon, marathon), longest run, biggest training "
            "week and month, and longest activity streak. Call when the athlete "
            "asks about their PBs, records, fastest times, or personal bests."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "plan_adherence",
        "description": (
            "Calculate how closely the athlete has followed their training plan. "
            "Returns adherence percentage, completed/partial/missed session counts, "
            "and rest day compliance. Call when the athlete asks about plan "
            "compliance, adherence, consistency, or whether they've been sticking "
            "to the plan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "weeks": {
                    "type": "integer",
                    "description": "Number of weeks to assess (default 4).",
                }
            },
            "required": [],
        },
    },
    {
        "name": "analyse_splits",
        "description": (
            "Analyse the per-kilometre splits and laps of a specific activity "
            "for pacing patterns. Detects negative/positive splits, fast starts, "
            "fades, and consistent pacing. Call when the athlete asks about their "
            "splits, pacing, laps, or how even their effort was."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "activity_id": {
                    "type": "integer",
                    "description": "Strava activity ID to analyse splits for.",
                }
            },
            "required": ["activity_id"],
        },
    },
    {
        "name": "log_wellness",
        "description": (
            "Log an injury, pain, soreness, or wellness concern. "
            "ONLY call this when the athlete is explicitly reporting a NEW "
            "symptom in their current message — e.g. 'my knee is sore today' "
            "or 'I felt a twinge in my calf'. "
            "Do NOT call based on past memories, previous conversations, or "
            "contextual mentions of body parts (e.g. 'I have hills on the way' "
            "is NOT a wellness report). The athlete must be directly and "
            "deliberately reporting a physical issue right now."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_type": {
                    "type": "string",
                    "description": (
                        "Type of issue: pain, soreness, tightness, fatigue, "
                        "swelling, numbness, or other."
                    ),
                },
                "body_part": {
                    "type": "string",
                    "description": (
                        "Affected body part, e.g. 'left knee', 'right calf'."
                    ),
                },
                "severity": {
                    "type": "integer",
                    "description": "Severity 1-10 (1=mild, 10=severe).",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional additional context.",
                },
            },
            "required": ["entry_type", "body_part", "severity"],
        },
    },
    {
        "name": "check_wellness",
        "description": (
            "Check current wellness status: active issues, detected patterns, "
            "or resolve an existing issue. Call when the athlete asks about "
            "their injuries, wellness log, or says an issue is resolved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "resolve_id": {
                    "type": "string",
                    "description": ("Entry ID to resolve (marks issue as resolved)."),
                }
            },
            "required": [],
        },
    },
    {
        "name": "assess_readiness",
        "description": (
            "Assess the athlete's race readiness and goal progress. Analyses "
            "VDOT predictions, volume benchmarks, long run coverage, CTL trend, "
            "and returns an overall readiness rating. Call when the athlete asks "
            "if they're ready for a race, on track for their goal, or how their "
            "training is progressing toward a target."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": (
                        "Optional goal override, e.g. 'half marathon in 1:30h'. "
                        "If omitted, uses the current training plan goal."
                    ),
                }
            },
            "required": [],
        },
        "cache_control": {"type": "ephemeral"},
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
                _MAX_FILE_CHARS = 20_000
                text = path.read_text()
                if len(text) > _MAX_FILE_CHARS:
                    result = (
                        text[:_MAX_FILE_CHARS]
                        + f"\n... [truncated — file is {len(text)} chars]"
                    )
                else:
                    result = text
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
                from memory.store import index_activities, index_debriefs
                from tgbot.debrief import load_debriefs

                indexed = index_activities(all_acts)
                index_debriefs(load_debriefs())

                from coach_utils.records import check_new_records

                new_pbs = check_new_records(all_acts)

                result = (
                    f"Sync complete. Activities cache updated "
                    f"({indexed} indexed to memory)."
                )
                if note:
                    result += f"\n\nNew activity analysis:\n{note}"
                if new_pbs:
                    pb_lines = [
                        f"  New PB: {pb['category'].replace('_', ' ').title()}"
                        for pb in new_pbs
                    ]
                    result += "\n\n" + "\n".join(pb_lines)
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
            types = None if sport_key == "all" else types_for_key(sport_key)
            acts = strava_sync._load_cached()

            if breakdown in ("month", "year"):
                # Pre-filter by sport
                if types:
                    acts = [
                        a
                        for a in acts
                        if a.get("type") in types or a.get("sport_type") in types
                    ]
                # Optionally narrow by period
                if period_str:
                    parsed = parse_period(period_str)
                    if parsed:
                        s_str = parsed[0].isoformat()
                        e_str = parsed[1].isoformat()
                        acts = [
                            a for a in acts if s_str <= a.get("date", "")[:10] <= e_str
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
                    result = f"Top {breakdown}s by distance:\n" + "\n".join(lines)
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
            limit = min(int(inp.get("limit", 10)), 100)
            workout_type_filter = inp.get("workout_type", "")
            sort_by = inp.get("sort_by", "date_desc")
            from memory.store import _WORKOUT_TYPE_LABELS

            _sort_keys = {
                "date_desc": lambda a: a.get("date", ""),
                "date_asc": lambda a: a.get("date", ""),
                "distance_desc": lambda a: a.get("distance_km", 0.0),
                "distance_asc": lambda a: a.get("distance_km", 0.0),
            }
            _sort_reverse = {
                "date_desc": True,
                "date_asc": False,
                "distance_desc": True,
                "distance_asc": False,
            }
            filtered = [
                a
                for a in strava_sync._load_cached()
                if date_from <= a.get("date", "")[:10] <= date_to
                and (
                    not workout_type_filter
                    or _WORKOUT_TYPE_LABELS.get(
                        a.get("workout_type") or 0, "default run"
                    )
                    == workout_type_filter
                )
            ]
            filtered.sort(
                key=_sort_keys.get(sort_by, _sort_keys["date_desc"]),
                reverse=_sort_reverse.get(sort_by, True),
            )
            acts = filtered[:limit]
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
                    f"{len(acts)} activit{'y' if len(acts) == 1 else 'ies'}"
                    f" found (sorted by {sort_by}).\n" + "\n".join(rows)
                )
        elif block.name == "check_records":
            from coach_utils.records import scan_for_records

            try:
                acts = strava_sync._load_cached()
                recs = scan_for_records(acts)
                if not recs:
                    result = "No personal records found — sync some activities first."
                else:
                    lines = ["Personal records:"]
                    for key, val in recs.items():
                        label = key.replace("_", " ").title()
                        if "time_str" in val:
                            lines.append(
                                f"  {label}: {val['time_str']} ({val.get('date', '?')})"
                            )
                        elif "distance_km" in val:
                            when = val.get(
                                "date", val.get("week", val.get("month", "?"))
                            )
                            lines.append(
                                f"  {label}: {val['distance_km']:.1f} km ({when})"
                            )
                        elif "days" in val:
                            lines.append(f"  {label}: {val['days']} days")
                    result = "\n".join(lines)
            except Exception as e:
                result = f"Records check failed: {e}"
        elif block.name == "plan_adherence":
            from coach_utils.adherence import calculate_adherence

            weeks = int(block.input.get("weeks", 4))
            try:
                data = calculate_adherence(weeks)
                honoured = data["rest_days_honoured"]
                rest_total = data["rest_days_total"]
                result = (
                    f"Plan adherence ({weeks}wk): "
                    f"{data['adherence_pct']:.0f}%\n"
                    f"  Completed: {data['completed']}, "
                    f"Partial: {data['partial']}, "
                    f"Missed: {data['missed']}\n"
                    f"  Rest days: {honoured}"
                    f"/{rest_total} honoured"
                )
            except Exception as e:
                result = f"Adherence check failed: {e}"
        elif block.name == "analyse_splits":
            from coach_utils.analyze import analyse_splits

            activity_id = block.input.get("activity_id")
            if not activity_id:
                result = "activity_id is required."
            else:
                try:
                    # Check cached activities first
                    acts = strava_sync._load_cached()
                    cached = next((a for a in acts if a["id"] == activity_id), None)
                    if cached and cached.get("splits_metric"):
                        split_data = analyse_splits(cached)
                    else:
                        # Fetch detail fields on demand
                        detail = strava_sync._fetch_detail_fields(activity_id)
                        act = cached.copy() if cached else {"id": activity_id}
                        act["laps"] = detail["laps"]
                        act["splits_metric"] = detail["splits_metric"]
                        split_data = analyse_splits(act)

                    if split_data["split_count"] == 0:
                        result = "No split data available for this activity."
                    else:
                        pace_strs = []
                        for p in split_data["split_paces"]:
                            m, s = int(p // 60), int(p % 60)
                            pace_strs.append(f"{m}:{s:02d}")
                        mean_m = int(split_data["mean_pace_s"] // 60)
                        mean_s = int(split_data["mean_pace_s"] % 60)
                        result = (
                            f"Split analysis ({split_data['split_count']} splits):\n"
                            f"  Paces: {', '.join(pace_strs)}\n"
                            f"  Mean: {mean_m}:{mean_s:02d}/km, "
                            f"CV: {split_data['cv']:.1%}\n"
                            f"  Flags: {', '.join(split_data['flags']) or 'none'}"
                        )
                except Exception as e:
                    result = f"Split analysis failed: {e}"
        elif block.name == "log_wellness":
            from coach_utils.wellness import log_entry as wellness_log

            entry_type = block.input.get("entry_type", "pain")
            body_part = block.input.get("body_part", "")
            severity = int(block.input.get("severity", 5))
            notes = block.input.get("notes", "")
            if not body_part:
                result = "body_part is required."
            else:
                try:
                    entry = wellness_log(entry_type, body_part, severity, notes)
                    result = (
                        f"Logged: {entry_type} in {body_part}, "
                        f"severity {entry['severity']}/10. "
                        f"ID: {entry['id']}"
                    )
                except Exception as e:
                    result = f"Failed to log wellness entry: {e}"
        elif block.name == "check_wellness":
            from coach_utils.wellness import (
                detect_patterns,
                get_active_issues,
                resolve_entry,
            )

            resolve_id = block.input.get("resolve_id", "")
            if resolve_id:
                ok = resolve_entry(resolve_id)
                result = (
                    f"Issue {resolve_id} resolved."
                    if ok
                    else f"Issue {resolve_id} not found."
                )
            else:
                try:
                    active = get_active_issues()
                    patterns = detect_patterns()
                    lines = []
                    if active:
                        lines.append(f"Active issues ({len(active)}):")
                        for issue in active:
                            lines.append(
                                f"  [{issue['id']}] {issue['date']} \u2014 "
                                f"{issue['type']} in {issue['body_part']}, "
                                f"severity {issue['severity']}/10"
                            )
                    else:
                        lines.append("No active wellness issues.")
                    if patterns:
                        lines.append(f"\nPatterns detected ({len(patterns)}):")
                        for p in patterns:
                            lines.append(
                                f"  \u26a0 {p['type']}: {p['body_part']} \u2014 "
                                f"{p['detail']}"
                            )
                    result = "\n".join(lines)
                except Exception as e:
                    result = f"Wellness check failed: {e}"
        elif block.name == "assess_readiness":
            from coach_utils.readiness import assess_readiness

            goal_override = block.input.get("goal", "")
            try:
                data = assess_readiness(goal_override or None)
                if data["overall"] == "insufficient_data":
                    result = (
                        "Insufficient data for readiness assessment. "
                        "Set a training plan and sync some activities first."
                    )
                else:
                    lines = [
                        f"Race readiness: {data['overall'].replace('_', ' ').upper()}",
                        f"Goal: {data['goal']}",
                        f"Weekly avg: {data['weekly_avg_km']:.1f} km "
                        f"({data['volume_status']})",
                        f"Longest recent run: "
                        f"{data['longest_recent_run_km']:.1f} km "
                        f"({data['long_run_status']})",
                        f"CTL: {data['ctl']:.1f} (trend: {data['ctl_trend']})",
                    ]
                    if data["vdot"]:
                        lines.append(f"VDOT: {data['vdot']}")
                    pos = data["signals"]["positive"]
                    neg = data["signals"]["negative"]
                    if pos:
                        lines.append("Positive: " + "; ".join(pos))
                    if neg:
                        lines.append("Concerns: " + "; ".join(neg))
                    result = "\n".join(lines)
            except Exception as e:
                result = f"Readiness assessment failed: {e}"
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


def call_claude(
    api_key: str,
    history: list[dict],
    sport_key: str = "run",
    model: str | None = None,
) -> str:
    """Run one full Claude conversation turn (with up to 5 tool-call rounds).

    Args:
        api_key: Anthropic API key.
        history: Conversation history (list of message dicts).
        sport_key: Active sport filter key (e.g. "run", "ride", "all").
        model: Claude model ID to use. Defaults to CLAUDE_MODEL env var.

    Returns:
        The assistant's text reply.
    """
    import anthropic

    active_model = model or CLAUDE_MODEL

    query = next(
        (
            m["content"][:500]
            for m in reversed(history)
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ),
        "",
    )
    # Block 1: large static context — cached (stable for ~60s between turns)
    static_context = _build_static_context(sport_key=sport_key)
    system: list[dict] = [
        {
            "type": "text",
            "text": static_context,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    # Block 2: memory results — NOT cached (changes per query, tiny in size)
    if query:
        try:
            from memory.store import query_memories

            memories = query_memories(query, n_results=5)
            if memories:
                mem_lines = ["\nRelevant coaching notes from previous sessions:"]
                for m in memories:
                    mem_lines.append(f"  - {m['text']}")
                system.append({"type": "text", "text": "\n".join(mem_lines)})
        except Exception:
            logger.warning("Failed to retrieve memories", exc_info=True)

    client = anthropic.Anthropic(api_key=api_key)
    messages = list(history)
    cached_system = system

    msg = client.messages.create(
        model=active_model,
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
            model=active_model,
            max_tokens=1500,
            system=cached_system,
            messages=messages,
            tools=TOOLS,
        )

    text = next((b.text for b in msg.content if hasattr(b, "text")), "")

    # If Claude completed tool calls but returned no text, nudge it once.
    if not text and rounds > 0:
        logger.info("Claude returned no text after tool calls — nudging for response")
        messages = [
            *messages,
            {"role": "assistant", "content": msg.content},
            {
                "role": "user",
                "content": [{"type": "text", "text": "Please now answer my question."}],
            },
        ]
        msg = client.messages.create(
            model=active_model,
            max_tokens=1500,
            system=cached_system,
            messages=messages,
        )
        text = next((b.text for b in msg.content if hasattr(b, "text")), "")

    logger.info("Claude reply: %d chars, %d tool round(s)", len(text), rounds)
    return text
