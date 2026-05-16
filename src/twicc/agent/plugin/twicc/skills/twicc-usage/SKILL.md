---
name: twicc-usage
description: Show current API usage quotas and cost estimates for every backend provider that tracks usage. Use when the user asks about their usage, quota utilization, rate limits, or spending.
---

# TwiCC Usage

Display the latest usage quota snapshot with cost estimates for every backend
provider that tracks usage (e.g. Claude Code via the Anthropic OAuth API).

## When to use

- The user asks about their current API usage or quota
- The user wants to know their rate limit utilization
- The user asks about spending or cost estimates
- The user wants to check if they're close to hitting a quota limit

## How to invoke

TwiCC's executable varies by launch mode (uvx, dev, installed tool). Resolve it once at the start of each Bash invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run any subcommand via `$TWICC <args>` — **do NOT quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`). The variable may expand to a multi-word command (e.g. `uv run --directory ... run.py`); Bash relies on word-splitting to parse it, and quoting it would treat the entire expansion as a single program name and fail with "No such file or directory". All bash examples below use the unquoted form.

## How to check usage

Run the `twicc usage` CLI command via the Bash tool:

```bash
$TWICC usage
```

No options — always returns the latest snapshot for every provider that
tracks usage and has at least one stored snapshot.

**Prerequisite:** Requires the relevant credentials to be configured for
each provider you want data from (e.g. OAuth credentials for Claude Code).
If no provider has any snapshot yet, the command returns an error.

## Output format

The command outputs a JSON object keyed by provider. Each value is the
snapshot dict for that provider:

```json
{
  "claude_code": {
    "provider": "claude_code",
    "fetched_at": "2025-03-10T15:30:00+00:00",
    "five_hour_utilization": 45.0,
    "five_hour_resets_at": "2025-03-10T18:00:00+00:00",
    "seven_day_utilization": 30.0,
    "seven_day_resets_at": "2025-03-14T00:00:00+00:00",
    "five_hour_temporal_pct": 62.5,
    "five_hour_burn_rate": 72.0,
    "five_hour_started_at": "2025-03-10T13:00:00+00:00",
    "seven_day_temporal_pct": 45.0,
    "seven_day_burn_rate": 66.7,
    "seven_day_started_at": "2025-03-07T00:00:00+00:00",
    "extra_usage_is_enabled": true,
    "extra_usage_monthly_limit": 10000,
    "extra_usage_used_credits": 2345,
    "extra_usage_utilization": 23.5,
    "period_costs": {
      "five_hour": {
        "spent": 2.34,
        "estimated_period": 5.12,
        "estimated_monthly": 734.56
      },
      "seven_day": {
        "spent": 15.67,
        "estimated_period": 45.23,
        "estimated_monthly": 193.84
      }
    }
  }
}
```

### Key fields (per provider)

- **`provider`** — backend provider key (e.g. `"claude_code"`)
- **Utilization values** — percentages (e.g., 45.0 = 45% of quota used)
- **`five_hour_*`** — 5-hour rolling window quota
- **`seven_day_*`** — 7-day rolling window quota (global)
- **`*_temporal_pct`** — percentage of time elapsed in the quota window (0–100), computed at `fetched_at` time
- **`*_burn_rate`** — ratio of utilization to temporal progress as percentage; >100 means on track to exhaust quota before reset
- **`*_started_at`** — start of the current quota window (ISO 8601)
- **`extra_usage_*`** — extra usage billing info (if enabled). `monthly_limit` and `used_credits` are in **cents**, not dollars
- **`period_costs`** — cost estimates:
  - `spent` — actual cost in USD for the period so far
  - `estimated_period` — projected cost for the full period at current pace
  - `estimated_monthly` — projected monthly cost at current pace

### Reset times

Each quota has a `*_resets_at` field showing when the quota window resets (ISO 8601).

## How to present results

1. If multiple providers are present, summarise per provider and call out
   each provider by name (e.g. "Claude Code: 45% of your 5-hour quota used")
2. Focus on the most relevant quotas (5-hour and 7-day global)
3. Show utilization as percentages (e.g., "45% of your 5-hour quota used")
4. Mention reset times in a human-readable format
5. Use burn rate to assess risk: <100 is safe, >100 means the quota will be exhausted before reset
6. Highlight if any quota is above 80% (approaching limit)
7. Include cost estimates when the user asks about spending
8. If the user needs more info on Claude Code usage specifically, redirect them to https://claude.ai/settings/usage
