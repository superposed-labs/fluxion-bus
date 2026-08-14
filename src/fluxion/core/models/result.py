from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class ExecutionResult:
    success: bool
    summary: str
    stdout: str
    stderr: str
    exit_code: int
    artifacts: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    # Structured file operations parsed from the executor stream (create / edit /
    # overwrite / add / update / delete), used to build a best-effort revert
    # ChangeSet without a workspace snapshot under revert_capture="structured".
    file_operations: list[dict] = field(default_factory=list)
    diff_summary: str = ""
    change_set_file: str = ""
    log_file: str = ""
    executor_session_id: str = ""
    # Model provenance is intentionally split in three. ``effective_model`` is
    # what Fluxion decided before launch; ``resolved_model`` is what the
    # executor runtime actually reported. They differ when a CLI owns its
    # default selection, and keeping both is the only way an MCP caller can
    # explain which quota pool was really billed.
    effective_model: str = ""
    resolved_model: str = ""
    model_resolution_source: str = ""
    # Token usage the executor's own stream reported for this run, keyed with the
    # same names as `fluxion.usage` (input_tokens, output_tokens,
    # cache_creation_tokens, cache_read_tokens). Empty when the executor does not
    # report usage. This is the run's authoritative total as the CLI computed it —
    # do not hand-sum per-message rows, which Claude duplicates 2-4x per turn.
    token_usage: dict[str, int] = field(default_factory=dict)
    duration_sec: float = 0.0
    finished_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # True when the executor returned early (answer ready) while the underlying
    # process is still doing post-answer housekeeping (Antigravity flushes its
    # SQLite trajectory DB and any pending file writes during this tail). The
    # engine must defer the change report (changed_files / change_set /
    # diff_summary) until the executor signals true completion, otherwise it reads
    # an incomplete trajectory / half-written tree. Antigravity-only; claude/codex
    # run to completion before returning and finalize synchronously.
    pending_finalization: bool = False
    # What terminating the run left behind, when the run had to be terminated:
    # {"verified": bool, "remaining": [{"pid", "command"}], "swept": [...]}.
    # Agent CLIs that run terminal commands in a pty put each one in its own
    # session, out of reach of the process-group kill, so "we sent the signal"
    # is not the same as "nothing is still running". Empty for runs that ended
    # on their own — nothing was terminated, so there is nothing to attest.
    process_cleanup: dict = field(default_factory=dict)
