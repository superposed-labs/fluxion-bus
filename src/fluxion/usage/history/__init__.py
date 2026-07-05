"""Historical token-usage analytics, reconstructed from local histories.

This is a *separate* data path from `usage/probes`. The probes report live
quota windows (the % of a 5h/weekly limit that's left); this package parses the
local histories written by Claude Code, Codex, and Antigravity and rolls them
up into cumulative analytics — total tokens, sessions, active days, streaks,
peak hour, per-model and per-day breakdowns — i.e. the data behind a "token
usage" console. It needs no network and spends no quota.

Token counting mirrors how `ccusage` reads the same files:
  - one record per *assistant* turn, taking `message.usage`;
  - de-duplicated globally by `requestId` + `message.id` so a forked/resumed
    session that copies prior turns can't double-count them;
  - total = input + output + cache_creation + cache_read.

Repeated scans are cheap: each file's parsed turns are cached and only re-read
when its mtime/size changes.

Layout (one concern per module):
  - ``entry``         the ``UsageEntry`` record + cache (de)serialization
  - ``pricing``       dated rate resolution + per-turn cost
  - ``parsing``       per-provider history readers + the file cache walk
  - ``antigravity_db`` SQLite + protobuf decoding for Antigravity stores
  - ``aggregate``     roll-up of entries into the console payload
  - ``service``       persisted scan cache + ``UsageHistoryService``

The public surface is re-exported here so ``from fluxion.usage.history import
...`` keeps working.
"""

from __future__ import annotations

from fluxion.usage.history import pricing  # noqa: F401 - exposed for tests to patch
from fluxion.usage.history.aggregate import (
    WINDOW_DAYS,
    _Bucket,
    _streaks,
    aggregate,
)
from fluxion.usage.history.antigravity_db import (
    _antigravity_entry_from_blob,
    _decode_proto,
    _parse_antigravity_db,
    _proto_bytes,
    _proto_int,
    _proto_message,
    _read_varint,
    _sqlite_signature,
)
from fluxion.usage.history.entry import (
    UsageEntry,
    _entry_from_cache,
    _entry_to_cache,
    _int,
    _parse_ts,
)
from fluxion.usage.history.parsing import (
    ANTIGRAVITY_CONVERSATIONS_DIRS,
    CLAUDE_PROJECTS_DIR,
    CODEX_SESSIONS_DIR,
    _claude_entry_from_line,
    _collect_files,
    _parse_claude_file,
    _parse_codex_file,
    collect_antigravity_entries,
    collect_claude_entries,
    collect_codex_entries,
)
from fluxion.usage.history.pricing import (
    _FALLBACK_PRICES,
    _context_tier_for_entry,
    _entry_cost,
    _family_match,
    _load_prices,
    _pick_rate,
    _rates_for,
    _resolve_fast,
)
from fluxion.usage.history.service import (
    _CACHE_VERSION,
    UsageHistoryService,
    _load_cache,
    _parse_usage_date,
    _save_cache,
    compute_usage_stats,
)

__all__ = [
    "UsageEntry",
    "UsageHistoryService",
    "aggregate",
    "collect_antigravity_entries",
    "collect_claude_entries",
    "collect_codex_entries",
    "compute_usage_stats",
]
