---
name: update-model-prices
description: >-
  Refresh the model price table that powers Fluxion's token-usage cost estimates
  — the upstream llm-price-table repo plus the bundled snapshot at
  src/fluxion/usage/model_prices.json — by pulling current prices from
  the official provider pricing pages (Anthropic, OpenAI, Google Gemini). Use
  this whenever the user wants to update, check, or verify model prices; suspects
  a price is stale, wrong, or too high/low; says a provider changed prices or a
  new model version shipped; or asks "are these prices still right" — even if
  they don't name the file. It fetches the official pages (rendering OpenAI's
  JS-heavy page via the browser), reads per-model prices, and proposes
  reviewed edits that follow the file's conventions. It never auto-writes or
  commits — the human approves and merges.
---

# Update model prices

Fluxion estimates the dollar cost of token usage from a hand-maintained price
table. There is no official pricing API, and the CLIs run models newer than any
public price list, so prices are refreshed by a person running this procedure on
demand (a few times a year, when prices change) — **not** by a scheduled scraper.

Your job here is the judgment-heavy part a cron can't do: render the pages,
read the numbers semantically, decide how each model maps into the table, and
hand the human a clear, correct proposal. The mechanical fetching/auditing is
already scripted.

## Where the table lives — edit upstream first

The source of truth is the standalone repo
[superposed-labs/llm-price-table](https://github.com/superposed-labs/llm-price-table)
(its `SCHEMA.md` defines the format). `src/fluxion/usage/model_prices.json` is a
**bundled snapshot** of that file — the offline fallback and release baseline.
At runtime `price_data.load_price_json` picks whichever of the snapshot and the
locally refreshed cache carries the newer top-level `updated_at`, so the daily
background refresh normally delivers a new price within a day even with no
Fluxion change at all.

A price change therefore lands in **two** repos, in this order:

1. Edit `model_prices.json` in the llm-price-table checkout; bump its `updated_at`.
2. Copy that file verbatim over `src/fluxion/usage/model_prices.json`.

Keep the snapshot a byte-for-byte copy and `diff` the two afterwards. Drift is a
silent bug: a family present upstream but missing from the snapshot mispriced
every id that resolved through it (this actually happened with `mythos`, which
fell through to the coarse `claude` fallback at half the real rate).

## The one hard rule

**Propose, never auto-apply.** A wrong number silently corrupts every cost
figure in the dashboard, so a human must verify before anything lands. Produce a
clear summary of proposed changes and stop. Only edit the file if the user
explicitly approves the specific numbers, and **never** `git commit` — committing
is always the user's call. Git history is the price-change log, so it must stay
human-authored.

## Prerequisites

- **Network access** to fetch the pricing pages.
- **A browser tool** (the in-app `mcp__Claude_Browser__*` pane, or
  `mcp__claude-in-chrome__*`; load via ToolSearch if deferred): OpenAI's pricing
  page is a JS-rendered shell — a plain fetch returns an empty page, so it must
  be rendered in a real browser and read with `get_page_text`. Anthropic and
  Google serve prices in plain HTML, so `curl`/WebFetch is enough for those.

## How the table works (read this before proposing edits)

Rates are USD per 1M tokens. Resolution for a given model id cascades:

1. `fast` — tried first, and only for a turn flagged fast (`usage.speed !=
   standard`, i.e. Anthropic Fast mode): exact id, then family. Falls through to
   the standard tables when the model has no fast rate (most don't).
2. `models` — exact model id (highest priority; use for versions that diverge).
3. `families` — case-insensitive **word-boundary** substring (fable, mythos,
   opus, sonnet-5, sonnet, haiku, nano, mini, flash-lite, flash, gemini).
4. `providers` — coarse fallback per provider (codex, claude, antigravity).
5. otherwise `$0` (local/unknown models, surfaced as "uncosted" rather than hidden).

Each entry is a list of dated rates: `{ effective_date, observed, in, out, cw, cw1h, cr, source }`,
optionally plus `context_pricing` (below).

- `in` / `out` — input / output price.
- `cw` — the model's default cache **write** rate. For Anthropic this is the
  5-minute TTL rate (~1.25× input). OpenAI models before GPT-5.6 and Google have
  no additional per-token cache-write fee, so set `cw = in` for them. GPT-5.6
  and later bill both implicit and explicit cache writes at 1.25× input; their
  current cache TTL is at least 30 minutes, so use the documented cache-write
  column rather than assuming `cw = in`.
- `cw1h` — cache **write**, 1-hour TTL (Anthropic **2× input**). The transcript
  records the per-turn 1h/5m token split, so the two are priced separately —
  this matters because Claude Code uses the 1h cache almost exclusively. Omit
  `cw1h` when there's no distinct 1h rate (OpenAI/Google); the resolver falls
  back to `cw`. Current Codex rollout logs do not expose GPT-5.6 cache-write
  tokens, so Fluxion's reconstructed cost is a lower bound until that telemetry
  is available; do not change the documented rate to compensate for missing
  usage data.
- `cr` — cache **read** (the "cached input" column; ~1/10th of input).
- `effective_date` — when this price took effect *as far as we know*. Use the
  provider's announced change date when there is one; otherwise it's a
  best-effort floor = the day you first observed it (older usage then prices at
  the earliest entry via fallback). **Only this field affects cost.**
- `observed` — the day you read the number off the page. Provenance only; the
  pricing logic ignores it.
- `source` — short note on where the number came from.
- `context_pricing` — **optional**, for models whose official page lists distinct
  short/long context tiers: `{ metric, short_max, short: {...}, long: {...} }`.
  `_rate_for_entry` in `history/pricing.py` picks the tier per request from the
  original billed input (`input_tokens_total`), against the provider threshold
  (272k for the GPT-5.4/5.5/5.6 family, 200k for Gemini Pro). Mirror the short
  tier in the top-level `in/out/cw/cr` so a consumer that ignores the block still
  gets a conservative number instead of a null.

Cost is computed **per turn at the rate in effect on that turn's date**, so a
recorded price change splits correctly by period. That only works if you record
changes the right way (below).

## Procedure

### 1. Audit what's there

```bash
python scripts/update_model_prices.py --offline
```

This lists every entry with its `effective_date` age and flags stale ones. It's
your baseline for the diff.

### 2. Fetch the official pages

| Provider | URL | How |
|---|---|---|
| Anthropic | https://docs.claude.com/en/docs/about-claude/pricing | plain fetch (server-rendered) |
| Google Gemini | https://ai.google.dev/gemini-api/docs/pricing | plain fetch (server-rendered) |
| OpenAI | https://platform.openai.com/docs/pricing (redirects to developers.openai.com) | **browser** — navigate, then `get_page_text` |

`scripts/update_model_prices.py` (no `--offline`) fetches and snapshots the two
server-rendered ones for you and prints rough candidates; for OpenAI, drive the
browser yourself. Read the snapshots in `scratch/price-snapshots/`.

### 3. Read prices semantically

Don't trust the script's heuristic extraction — read the rendered page/snapshot
yourself and pull each model's **standard** tier: input, cached input (→ `cr`),
cache write (→ `cw`, where the page lists a column for it), and output. Ignore
batch/flex/Fast-mode tiers and free-tier columns. Where the page has distinct
**short/long context** tiers, read *both* and record them via `context_pricing`.
Note deprecated vs current models — list the current ones (e.g. price the live
Opus, not the deprecated one). For Anthropic also read the **Fast mode** premiums
(→ `fast` section) and the caching multipliers (5-min → `cw`, 1-hour → `cw1h`).

Cross-check the provider's **changelog** as well as the pricing table: it gives
the real announced change date for `effective_date` (which is the only field
that affects cost) instead of a first-observed floor, and it names what moved —
e.g. OpenAI's 2026-07-30 entry stating Luna dropped 80% and Terra 20%.

### 4. Decide family vs exact — the key judgment

- **Family** when a whole tier shares one price across versions. Anthropic does
  this: Opus 4.5–4.8 are all $5/$25, so the `opus` family covers them and a
  future `opus-4-9` resolves without a table edit.
- **Exact model** when versions are repriced. Google does this: Gemini **3**
  Flash is $0.50/$3 but Gemini **3.5** Flash is $1.50/$9 — same "flash" word,
  3× the price. A single family rate would misprice one of them, so pin the
  diverging versions in `models` and let the family track the current/most-likely
  version. Always spot-check: did a newer version of a model change price? If so
  it needs an exact entry.

### 5. Mind the gotchas

- **Word-boundary matching.** Family keys match on a boundary, not raw substring,
  because "mini" is a substring of "ge**mini**" — without the boundary check every
  Gemini model would silently resolve to the `mini` (GPT-5 mini) rate. The
  resolver (`_rates_for` in `history/pricing.py`, re-exported from
  `fluxion.usage.history`) already handles this; just keep family keys
  unambiguous and remember the collision exists.
- **Resolution order.** Families are tried in JSON order, so more specific keys
  must come first: `flash-lite` before `flash` before `gemini` (all three match
  "gemini-2.5-flash-lite"), and `sonnet-5` before `sonnet`.
- **Standard tier only.** Don't mix in batch/flex/Fast-mode rates. Long context
  is *not* excluded — it belongs in `context_pricing`, not in the top-level rate.
- **Cache-write semantics vary by model generation.** `cw` is the model's default
  write rate, not universally "same as input". Don't apply the older OpenAI
  `cw = in` convention to GPT-5.6 or later, which bill writes at 1.25× input.

### 6. Record changes correctly

- A price **changed**: **ADD** a new entry to that rate list with the new
  `effective_date` (the real change date if known, else today) and
  `observed = today`. **Do not edit the old entry's numbers** — overwriting in
  place erases the period split and re-prices all history at the new number.
- A price is **unchanged**: leave the rate entry alone; at most bump the file's
  top-level `updated_at` to record that you re-checked.
- A **new model/version** appears that the family misprices: add an exact
  `models` entry.

### 7. Present the proposal and stop

Show the user a compact summary: for each change, the model/family, old → new
numbers, the source, and the `effective_date` you'd use. Call out anything
uncertain (e.g. a model not listed on the page → which fallback you'd use).
Then ask whether to apply. Apply only the approved edits; leave the commit to
the user.

## Verify after applying (only once the user approves edits)

```bash
diff src/fluxion/usage/model_prices.json ../llm-price-table/model_prices.json
```

```bash
PYTHONPATH=src python -c "from fluxion.usage.history import _rates_for; \
print(_rates_for('codex','gpt-5.6-luna','2026-07-29')); \
print(_rates_for('codex','gpt-5.6-luna','2026-07-31'))"
```

```bash
PYTHONPATH=src python -m pytest tests/test_usage_history.py tests/test_price_data.py -q
```

Confirm the snapshot matches upstream exactly, the JSON still parses, and the
tests pass. Spot-check each changed id **on two dates — one before and one on/
after the new `effective_date`** — to prove the period split works and you added
a rate rather than overwriting one. Also spot-check an id you did *not* touch,
to catch a family edit with wider blast radius than intended.

No restart is needed: the parsed table and the resolver cache are keyed on
`price_data.price_file_stamp`, so a rewritten file is picked up on the next
lookup in a running service.

## Example: a price change done right

> Opus drops from $5/$25 to $4/$20 on 2026-10-01.

Add (don't overwrite) to the `opus` family list:

```json
{ "effective_date": "2026-10-01", "observed": "2026-10-01", "in": 4.0, "out": 20.0, "cw": 5.0, "cr": 0.4, "source": "Anthropic docs" }
```

Turns before Oct 1 keep pricing at $5/$25; turns on/after price at $4/$20 — the
dashboard total reflects both periods automatically.
