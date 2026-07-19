"""Reading local provider histories into :class:`UsageEntry` streams.

One parser per provider (Claude transcripts, Codex rollout logs, Antigravity
SQLite stores) plus the mtime/size-incremental file cache that makes repeated
scans cheap. Antigravity's binary decoding lives in ``antigravity_db``; this
module only orchestrates the walk + cache.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from fluxion.usage.history.antigravity_db import (
    _parse_antigravity_db,
    _sqlite_signature,
)
from fluxion.usage.history.entry import (
    UsageEntry,
    _entry_from_cache,
    _entry_to_cache,
    _int,
    _parse_ts,
)

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
ANTIGRAVITY_CONVERSATIONS_DIRS = (
    Path.home() / ".gemini" / "antigravity" / "conversations",
    Path.home() / ".gemini" / "antigravity-cli" / "conversations",
)


def _dedupe_codex_paths(paths: Iterable[Path], sessions_dir: Path) -> list[Path]:
    """Choose one physical file for each Codex rollout.

    Archiving normally moves a rollout unchanged, but the active and archive
    paths can briefly coexist. Prefer the more complete copy, then the active
    copy when sizes tie. Codex rollout basenames contain the stable session ID.
    """
    by_name: dict[str, list[Path]] = {}
    for path in paths:
        by_name.setdefault(path.name, []).append(path)

    def rank(path: Path) -> tuple[int, bool]:
        try:
            size = path.stat().st_size
        except OSError:
            size = -1
        return size, path == sessions_dir or sessions_dir in path.parents

    return [max(candidates, key=rank) for candidates in by_name.values()]


def _claude_entry_from_line(line: str) -> UsageEntry | None:
    """Parse one transcript line into a UsageEntry, or None if it isn't a
    token-bearing assistant turn."""
    line = line.strip()
    if not line or '"usage"' not in line:
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict) or event.get("type") != "assistant":
        return None
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    model = message.get("model")
    # Claude Code stamps locally-injected placeholder turns (error stubs, system
    # notices) with model "<synthetic>" and zero usage; they aren't real API
    # calls, so skip them — same as ccusage — to keep counts honest.
    if model == "<synthetic>":
        return None
    ts = _parse_ts(event.get("timestamp"))
    if ts is None:
        return None

    message_id = message.get("id")
    request_id = event.get("requestId")
    dedup_key = (
        f"{request_id}:{message_id}"
        if message_id or request_id
        else str(event.get("uuid") or id(event))
    )
    # `cache_creation` breaks the write down by TTL; the 1h tokens cost 2x input
    # vs 1.25x for the 5m, so capture the 1h portion for separate pricing.
    cache_detail = usage.get("cache_creation")
    cache_1h = (
        _int(cache_detail.get("ephemeral_1h_input_tokens")) if isinstance(cache_detail, dict) else 0
    )
    # `speed` is expected to mark Fast-mode turns (premium pricing); treat
    # anything that isn't the default "standard" as fast, permissively, rather
    # than miss the premium. UNVERIFIED + rare: Fast mode is a metered add-on
    # that draws from pay-as-you-go *usage credits* (not the subscription) and
    # must be enabled via `/usage-credits`, so a plain subscription user can't
    # produce a fast turn at all. It also never reaches these transcripts when
    # run in the desktop/web app — so this only ever fires for an in-terminal
    # `/fast` turn by a credits-enabled user, and the exact non-standard `speed`
    # value is a best guess until one is observed. (When it does fire, that cost
    # is real metered spend, the one exception to "subscription isn't billed".)
    speed = usage.get("speed")
    is_fast = isinstance(speed, str) and speed.lower() not in ("", "standard")
    return UsageEntry(
        provider="claude",
        ts=ts,
        model=str(model) if isinstance(model, str) and model else "unknown",
        session_id=str(event.get("sessionId") or ""),
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
        cache_creation_tokens=_int(usage.get("cache_creation_input_tokens")),
        cache_read_tokens=_int(usage.get("cache_read_input_tokens")),
        dedup_key=str(dedup_key),
        billed_input_tokens_total=_int(usage.get("input_tokens"))
        + _int(usage.get("cache_read_input_tokens")),
        cache_creation_1h_tokens=cache_1h,
        is_fast=is_fast,
    )


# A line parser consumes complete decoded lines and produces entries, resuming
# (and updating) its carry-over `state` so the tail of an append-only file can
# be parsed without re-reading the head. `path` is passed for name-derived ids.
LineParser = Callable[[Path, Iterable[str], dict[str, Any]], list[UsageEntry]]


def _iter_complete_lines(
    handle: Any, offset_holder: list[int], tail_holder: list[bytes]
) -> Iterator[str]:
    """Yield decoded lines from a binary handle, one per terminating newline.

    `offset_holder[0]` is advanced to the byte position just past each complete
    line. A trailing line with no newline is not yielded here — it is stashed in
    `tail_holder[0]` so the committed offset never lands mid-record. The next
    scan re-reads it from the same offset once the writer terminates the line."""
    for raw in handle:
        if not raw.endswith(b"\n"):
            tail_holder[0] = raw
            break
        offset_holder[0] += len(raw)
        yield raw.decode("utf-8", errors="replace")


def _parse_incremental(
    path: Path, start_offset: int, state: dict[str, Any], parser: LineParser
) -> tuple[list[UsageEntry], list[UsageEntry], int]:
    """Parse `path` from `start_offset` to EOF.

    Returns ``(committed, tail, offset)``: entries from complete (newline-ended)
    lines, entries from a trailing unterminated line, and the byte offset just
    past the last complete line. `state` is mutated in place by the committed
    lines only. Callers must cache `committed` but not `tail` — the tail's bytes
    are not covered by `offset`, so it is re-read (and re-emitted) on the next
    scan once the writer terminates the line."""
    offset_holder = [start_offset]
    tail_holder: list[bytes] = [b""]
    try:
        with path.open("rb") as handle:
            if start_offset:
                handle.seek(start_offset)
            committed = parser(
                path, _iter_complete_lines(handle, offset_holder, tail_holder), state
            )
            tail_bytes = tail_holder[0]
    except OSError:
        return [], [], start_offset
    tail: list[UsageEntry] = []
    if tail_bytes:
        # Parse the unterminated record against a throwaway state copy so the
        # committed state stays clean for when the line is later terminated.
        tail = parser(path, iter([tail_bytes.decode("utf-8", errors="replace")]), dict(state))
    return committed, tail, offset_holder[0]


def _collect_files(
    roots: Path | Iterable[Path],
    pattern: str,
    parser: LineParser,
    provider: str,
    *,
    cache: dict[str, Any] | None = None,
) -> list[UsageEntry]:
    """Walk `roots` for files matching `pattern`, parsing each into UsageEntry.

    Each provider gets its own bucket under `cache["files"][provider]` so a file
    is re-parsed only when its mtime/size changed and the stale-row sweep for one
    provider can't evict another's. The buckets are keyed by absolute path.

    Transcript files are append-only, so when a cached file has only grown we
    `seek` to the last parsed line boundary and parse just the new bytes (with
    the parser's carry-over state restored), instead of re-reading the whole
    file. Any other change (shrink, in-place rewrite, or a pre-offset cache row)
    falls back to a full re-parse from the start."""
    if isinstance(roots, Path):
        roots = (roots,)
    else:
        roots = tuple(roots)

    paths: list[Path] = []
    for root in roots:
        if root.is_dir():
            paths.extend(root.rglob(pattern))

    bucket: dict[str, Any] = (
        cache.setdefault("files", {}).setdefault(provider, {}) if cache is not None else {}
    )
    seen_paths: set[str] = set()
    entries: list[UsageEntry] = []

    if provider == "codex":
        final_paths = _dedupe_codex_paths(paths, roots[0])
        # Basename → cached-path index, built once instead of scanning the
        # whole bucket (with a Path() per probe) for every rollout — that was
        # O(files²) and dominated a no-change scan. final_paths has one path
        # per basename, so each bucket entry is consulted at most once.
        cached_by_name: dict[str, list[str]] = {}
        if cache is not None:
            for key in bucket:
                cached_by_name.setdefault(Path(key).name, []).append(key)
        for chosen in final_paths:
            chosen_key = str(chosen)
            if cache is not None:
                matching_old_keys = [
                    key for key in cached_by_name.get(chosen.name, ()) if key != chosen_key
                ]
                for old_key in matching_old_keys:
                    old_cached = bucket.pop(old_key)
                    bucket.setdefault(chosen_key, old_cached)

        paths = final_paths

    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        key = str(path)
        seen_paths.add(key)
        cached = bucket.get(key)

        # Unchanged: serve every entry straight from the cache, no file IO. The
        # trailing unterminated record (`tail`) is kept separately because it is
        # not covered by `offset`; it is still served here but re-parsed (not
        # reused) the moment the file grows.
        if (
            isinstance(cached, dict)
            and cached.get("mtime") == stat.st_mtime
            and cached.get("size") == stat.st_size
        ):
            for d in list(cached.get("entries", [])) + list(cached.get("tail", [])):
                e = _entry_from_cache(provider, d)
                if e is not None:
                    entries.append(e)
            continue

        # Append-only fast path: the file only grew, so resume from the cached
        # offset/state and parse only the appended bytes.
        prev_cached_entries: list[dict[str, Any]] = []
        state: dict[str, Any] = {}
        start_offset = 0
        if (
            isinstance(cached, dict)
            and "offset" in cached
            and stat.st_size > cached.get("size", -1)
        ):
            start_offset = int(cached["offset"])
            prev_cached_entries = cached.get("entries", []) or []
            state = dict(cached.get("state") or {})
            for d in prev_cached_entries:
                e = _entry_from_cache(provider, d)
                if e is not None:
                    entries.append(e)

        committed, tail, end_offset = _parse_incremental(path, start_offset, state, parser)
        entries.extend(committed)
        entries.extend(tail)
        if cache is not None:
            # The tail is stored apart from `entries`: it sits after `offset`, so
            # the next grown scan re-reads (and replaces) it — keeping it out of
            # `entries` is what prevents a double count there.
            bucket[key] = {
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "offset": end_offset,
                "state": state,
                "entries": prev_cached_entries + [_entry_to_cache(e) for e in committed],
                "tail": [_entry_to_cache(e) for e in tail],
            }

    # Drop cache rows for files that no longer exist.
    if cache is not None:
        for stale in set(bucket) - seen_paths:
            bucket.pop(stale, None)

    return entries


def collect_claude_entries(
    projects_dir: Path = CLAUDE_PROJECTS_DIR,
    *,
    cache: dict[str, Any] | None = None,
) -> list[UsageEntry]:
    """Read every Claude Code transcript under `projects_dir` (incrementally
    cached), returning all assistant turns."""
    return _collect_files(projects_dir, "*.jsonl", _claude_line_parser, "claude", cache=cache)


def collect_codex_entries(
    sessions_dir: Path = CODEX_SESSIONS_DIR,
    *,
    archived_sessions_dir: Path | None = None,
    cache: dict[str, Any] | None = None,
) -> list[UsageEntry]:
    """Read Codex rollouts from active and archived session directories."""
    if archived_sessions_dir is None:
        archived_sessions_dir = sessions_dir.parent / "archived_sessions"

    roots = [sessions_dir]
    if archived_sessions_dir:
        roots.append(archived_sessions_dir)

    return _collect_files(roots, "rollout-*.jsonl", _codex_line_parser, "codex", cache=cache)


def collect_antigravity_entries(
    conversations_dirs: Iterable[Path] = ANTIGRAVITY_CONVERSATIONS_DIRS,
    *,
    cache: dict[str, Any] | None = None,
) -> list[UsageEntry]:
    """Read Antigravity's per-conversation SQLite stores.

    `gen_metadata.data` contains the same protobuf usage metadata returned by
    the sidecar's GetCascadeTrajectoryGeneratorMetadata endpoint. The database
    is opened read-only and the incremental-cache signature includes SQLite's
    WAL/SHM sidecars because active conversations may not checkpoint promptly.
    """
    bucket: dict[str, Any] = (
        cache.setdefault("files", {}).setdefault("antigravity", {}) if cache is not None else {}
    )
    seen_paths: set[str] = set()
    entries: list[UsageEntry] = []
    for root in conversations_dirs:
        if not root.is_dir():
            continue
        for path in root.glob("*.db"):
            key = str(path)
            seen_paths.add(key)
            signature = _sqlite_signature(path)
            cached = bucket.get(key)
            if isinstance(cached, dict) and cached.get("signature") == signature:
                for d in cached.get("entries", []):
                    entry = _entry_from_cache("antigravity", d)
                    if entry is not None:
                        entries.append(entry)
                continue
            file_entries = _parse_antigravity_db(path)
            entries.extend(file_entries)
            if cache is not None:
                bucket[key] = {
                    "signature": signature,
                    "entries": [_entry_to_cache(entry) for entry in file_entries],
                }
    if cache is not None:
        for stale in set(bucket) - seen_paths:
            bucket.pop(stale, None)
    return entries


def _claude_line_parser(
    path: Path, lines: Iterable[str], state: dict[str, Any]
) -> list[UsageEntry]:
    """Claude turns are self-contained per line, so there is no carry-over
    `state` to resume — the tail parses identically to a full file."""
    out: list[UsageEntry] = []
    for line in lines:
        e = _claude_entry_from_line(line)
        if e is not None:
            out.append(e)
    return out


def _parse_claude_file(path: Path) -> list[UsageEntry]:
    committed, tail, _ = _parse_incremental(path, 0, {}, _claude_line_parser)
    return committed + tail


def _codex_line_parser(path: Path, lines: Iterable[str], state: dict[str, Any]) -> list[UsageEntry]:
    """Parse a Codex rollout log. Each `token_count` event carries that turn's
    `last_token_usage` (summing unique cumulative states reproduces the
    session's cumulative total);
    the model comes from the most recent `turn_context` and the session id from
    `session_meta`. Codex reports input *including* the re-read cache, so the
    cached portion is mapped to cache_read for parity with Claude.

    Some Codex versions emit the same token-count event twice. Those duplicates
    have identical `total_token_usage`, so skip repeated cumulative states.

    Forking a session copies the parent rollout's entire history into the new
    file with every timestamp rewritten to the fork instant, so per-file dedup
    keys would count the whole parent history again — dated "today". Instead,
    turns are keyed by session *lineage* plus a digest of the cumulative+turn
    usage: the replayed copies collide with the parent's keys and aggregate
    once, while post-fork turns diverge in cumulative totals and keep their own
    keys. The lineage resolves transitively to the root of a fork chain because
    the replay preserves every ancestor's `session_meta` line in order (each
    carrying its own `forked_from_id`), so by the first `token_count` the
    lineage has walked fork → parent → … → root; the fork's own
    `forked_from_id` only covers the stretch before the first replayed meta
    (verified against Codex Desktop 0.144.2 fork-of-fork rollouts). Rollouts
    without `total_token_usage` fall back to the per-file sequence key.

    Note: Codex `/fast` mode sends a premium `service_tier` ("priority") and
    bills credits at 2.5x standard for GPT-5.5 / 2x for GPT-5.4 (per
    developers.openai.com/codex/speed), but the CLI deliberately omits
    `service_tier` from the rollout, so fast turns are indistinguishable from
    standard here and get the standard rate — a known undercount (up to 2.5x)
    with no signal to correct it (verified in the Codex source: TurnContextItem
    serialises model/effort/mode but not tier).

    State that spans lines — the current session id / model, the running event
    sequence, and the last cumulative-total signature — is restored from and
    written back to `state` so an appended tail resumes exactly where the
    previous parse stopped."""
    out: list[UsageEntry] = []
    session_id = str(state.get("session_id", ""))
    lineage = str(state.get("lineage", ""))
    model = str(state.get("model", "unknown"))
    seq = int(state.get("seq", 0))
    previous_total_signature: str | None = state.get("prev_sig")
    pending_compaction = bool(state.get("pending_compaction"))
    for line in lines:
        if (
            '"token_count"' not in line
            and '"turn_context"' not in line
            and '"session_meta"' not in line
            and '"compacted"' not in line
            and '"context_compacted"' not in line
        ):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        ptype = payload.get("type") or event.get("type")

        if ptype == "session_meta":
            sid = payload.get("id") or event.get("id")
            if isinstance(sid, str) and sid:
                session_id = sid
            fork_parent = payload.get("forked_from_id")
            lineage = fork_parent if isinstance(fork_parent, str) and fork_parent else session_id
        elif ptype == "turn_context":
            m = payload.get("model")
            if isinstance(m, str) and m:
                model = m
        elif ptype == "compacted" or event.get("type") == "compacted":
            pending_compaction = True
        elif ptype == "context_compacted":
            pending_compaction = True
        elif ptype == "token_count":
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            last = info.get("last_token_usage")
            if not isinstance(last, dict):
                continue
            total = info.get("total_token_usage")
            total_signature = (
                json.dumps(total, sort_keys=True, separators=(",", ":"))
                if isinstance(total, dict)
                else None
            )
            if total_signature is not None and total_signature == previous_total_signature:
                continue
            ts = _parse_ts(event.get("timestamp"))
            if ts is None:
                continue
            previous_total_signature = total_signature
            input_total = _int(last.get("input_tokens"))
            cached = _int(last.get("cached_input_tokens"))
            output = _int(last.get("output_tokens"))
            total_tokens = _int(last.get("total_tokens"))
            # Compaction spends tokens but some rollouts expose only the total,
            # leaving input/output/cache at zero. Keep that cost in the ledger by
            # treating the opaque spend as billed input: that is the closest
            # available approximation for the hidden summarization request.
            if pending_compaction and not (input_total or cached or output) and total_tokens > 0:
                input_total = total_tokens
            if total_signature is not None and lineage:
                turn_signature = json.dumps(last, sort_keys=True, separators=(",", ":"))
                digest = hashlib.sha1(f"{total_signature}|{turn_signature}".encode()).hexdigest()[
                    :20
                ]
                dedup_key = f"codex:{lineage}:{digest}"
            else:
                dedup_key = f"codex:{path.name}:{seq}"
            out.append(
                UsageEntry(
                    provider="codex",
                    ts=ts,
                    model=model,
                    session_id=session_id or path.stem,
                    input_tokens=max(0, input_total - cached),
                    output_tokens=output,
                    # Codex 0.142.3 rollout TokenUsage omits GPT-5.6's API-level
                    # cache_write_tokens field. Keep the existing numeric shape
                    # for aggregation, but the Web UI treats GPT-5.6 cost as a
                    # lower bound instead of interpreting this as an observed 0.
                    cache_creation_tokens=0,
                    cache_read_tokens=cached,
                    dedup_key=dedup_key,
                    billed_input_tokens_total=input_total,
                )
            )
            seq += 1
            pending_compaction = False

    state["session_id"] = session_id
    state["lineage"] = lineage
    state["model"] = model
    state["seq"] = seq
    state["prev_sig"] = previous_total_signature
    state["pending_compaction"] = pending_compaction
    return out


def _parse_codex_file(path: Path) -> list[UsageEntry]:
    committed, tail, _ = _parse_incremental(path, 0, {}, _codex_line_parser)
    return committed + tail
