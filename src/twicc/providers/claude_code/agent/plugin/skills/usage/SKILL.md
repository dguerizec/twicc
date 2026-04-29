---
name: usage
description: Show current Anthropic API usage quotas and cost estimates. Use when the user asks about their usage, quota utilization, rate limits, or spending.
---

# TwiCC Usage

Display the latest Anthropic API usage quota snapshot with cost estimates.

## When to use

- The user asks about their current API usage or quota
- The user wants to know their rate limit utilization
- The user asks about spending or cost estimates
- The user wants to check if they're close to hitting a quota limit

## How to check usage

Run the `twicc usage` CLI command via the Bash tool:

```bash
twicc usage
```

No options — always returns the latest snapshot.

**Prerequisite:** Requires OAuth credentials to be configured. If not set up, the command returns an error.

## Output format

The command outputs a JSON object:

```json
{
  "fetched_at": "2025-03-10T15:30:00+00:00",
  "five_hour_utilization": 45.0,
  "five_hour_resets_at": "2025-03-10T18:00:00+00:00",
  "seven_day_utilization": 30.0,
  "seven_day_resets_at": "2025-03-14T00:00:00+00:00",
  "seven_day_opus_utilization": 25.0,
  "seven_day_opus_resets_at": "2025-03-14T00:00:00+00:00",
  "seven_day_sonnet_utilization": 10.0,
  "seven_day_sonnet_resets_at": "2025-03-14T00:00:00+00:00",
  "seven_day_oauth_apps_utilization": null,
  "seven_day_oauth_apps_resets_at": null,
  "seven_day_cowork_utilization": null,
  "seven_day_cowork_resets_at": null,
  "five_hour_temporal_pct": 62.5,
  "five_hour_burn_rate": 72.0,
  "five_hour_started_at": "2025-03-10T13:00:00+00:00",
  "seven_day_temporal_pct": 45.0,
  "seven_day_burn_rate": 66.7,
  "seven_day_started_at": "2025-03-07T00:00:00+00:00",
  "seven_day_opus_temporal_pct": 45.0,
  "seven_day_sonnet_temporal_pct": 45.0,
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
```

### Key fields

- **Utilization values** — percentages (e.g., 45.0 = 45% of quota used)
- **`five_hour_*`** — 5-hour rolling window quota
- **`seven_day_*`** — 7-day rolling window quota (global + per-model: opus, sonnet)
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

1. Focus on the most relevant quotas (5-hour and 7-day global)
2. Show utilization as percentages (e.g., "45% of your 5-hour quota used")
3. Mention reset times in a human-readable format
4. Use burn rate to assess risk: <100 is safe, >100 means the quota will be exhausted before reset
5. Highlight if any quota is above 80% (approaching limit)
5. Include cost estimates when the user asks about spending
6. Per-model quotas (opus, sonnet) are useful when the user asks about a specific model
7. If the user needs more info, redirect them to https://claude.ai/settings/usage
