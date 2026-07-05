# Usage Statistics

The Web console's Stats tab reconstructs cumulative local usage analytics:
tokens, sessions, active days, streaks, a calendar heatmap, per-model
breakdowns, and API-equivalent cost estimates.

These statistics are reconstructed from the supported agents' own local
histories, not from Fluxion task records. They include supported local usage
made directly in those agents as well as usage initiated through Fluxion.

## Local history sources

| Provider | Local source |
| --- | --- |
| Claude Code | `~/.claude/projects` |
| Codex | `~/.codex/sessions` |
| Antigravity | `~/.gemini/antigravity*/conversations` |

Antigravity stores per-turn input, output, cache-read, model, and timestamp
metadata as protobuf in per-conversation SQLite databases. Fluxion reads those
databases locally and read-only, including active WAL data. Stats does not
require the Antigravity IDE or sidecar to be running.

## Coverage limitations

Stats covers supported local CLI and IDE histories only. Usage from the Claude
desktop app, claude.ai, and ChatGPT web/desktop is served from those products'
backends and is not written to the local histories Fluxion parses.

### Fast mode

Fast-mode turns are structurally difficult or impossible to identify from
local CLI histories:

- Claude Code Fast mode is only best-effort detectable and remains unverified.
- Codex CLI deliberately omits its premium service tier from the local session
  rollout, so Fast turns are indistinguishable from standard turns.

Consequently, local cost estimates may undercount Fast-mode usage.

## Cost estimates

Cost figures are estimates computed from a hand-maintained price table. There
is no official pricing API, and model prices may drift between updates.

Fluxion ships a bundled snapshot of
[superposed-labs/llm-price-table](https://github.com/superposed-labs/llm-price-table)
and refreshes it in the background when stale. Disable or refresh manually:

```env
FLUXION_PRICE_AUTO_REFRESH=false
FLUXION_PRICE_REFRESH_DAYS=1
```

```bash
fluxion-usage --refresh-prices
```

### Subscription view

A subscription is not usage-billed. The Subscription view labels the estimate
as **API-equivalent value**: what the same tokens might have cost through a
metered API, not a charge added to the subscription.

Detected plan names are compared with the list prices in
[`plan_prices.json`](../src/fluxion/usage/plan_prices.json). The Metered view
shows the same estimate framed as spend.

### Pricing scope

The estimate models:

- standard on-demand pricing
- Anthropic prompt-cache writes
- per-request short/long context tiers where the local history exposes enough
  billed-input signal to classify them
- best-effort Fast mode where detectable

It still does **not** model every batch, priority, or data-residency
multiplier. Models without a known rate are reported as uncosted rather than
silently priced at zero.

### Context tiers

For models that publish distinct short/long context tiers, Fluxion picks the
tier per request from the original billed input for that request, before any
cached-input split:

- if billed input `<= context_pricing.short_max`, use the short tier
- if billed input `> context_pricing.short_max`, use the long tier

The threshold comes from the active price-table entry itself, not from a
hard-coded model list in Fluxion's docs.

Codex compaction events are also counted: when a rollout records a `compacted`
event followed by a token-count record with opaque `total_tokens` but no normal
input/output split, Fluxion keeps that token spend in the estimate instead of
dropping it as zero.

## API shape

`GET /api/usage/history?window=all|30d|7d|1d` returns the Stats payload used by
both the web console and the macOS companion.

Relevant fields:

- `totals.context_tier_breakdown.short`
- `totals.context_tier_breakdown.long`
- `by_model[].context_tier_breakdown.short`
- `by_model[].context_tier_breakdown.long`

These are request counts, not token counts. Older consumers that ignore these
fields remain compatible.

## Privacy

Stats reads local histories locally and read-only. It needs no network and
spends no model quota. The optional Codex server-reconciliation line compares
the local total with the ChatGPT usage endpoint; it is advisory and fails
silently offline.
