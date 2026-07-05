from __future__ import annotations

import fcntl
import json
import os
import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fluxion.scheduler.models import RuleState, ScheduleRule

# Ownership of the three files keeps cross-process access safe without locks:
#   schedules.jsonl       — written by the web CRUD API, read by the daemon
#   scheduler_state.json  — written only by the daemon
#   schedule_runs.jsonl   — appended only by the daemon, read by the web UI
# Definition/state writes use atomic temp+replace so readers never see a
# partial file. The in-process lock guards a single process's read-modify-write.


class ScheduleStore:
    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir
        self._schedules_path = data_dir / "schedules.jsonl"
        self._state_path = data_dir / "scheduler_state.json"
        self._runs_path = data_dir / "schedule_runs.jsonl"
        self._anchor_log_path = data_dir / "anchor_log.jsonl"
        self._lock_path = data_dir / "schedules.lock"
        self._lock = threading.RLock()
        self._ensure()

    @property
    def schedules_path(self) -> Path:
        return self._schedules_path

    def _ensure(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        for path in (self._schedules_path, self._runs_path):
            if not path.exists():
                path.write_text("", encoding="utf-8")
        if not self._state_path.exists():
            self._state_path.write_text("{}", encoding="utf-8")
        self._lock_path.touch(exist_ok=True)

    @contextmanager
    def _process_lock(self):
        with self._lock_path.open("a") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _atomic_write(self, path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    # --- schedule rules ---------------------------------------------------

    def schedules_mtime(self) -> float:
        try:
            return self._schedules_path.stat().st_mtime
        except OSError:
            return -1.0

    def load_rules(self) -> list[ScheduleRule]:
        with self._lock:
            if not self._schedules_path.exists():
                return []
            rules: list[ScheduleRule] = []
            for line in self._schedules_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    if isinstance(raw, dict) and raw.get("id"):
                        rules.append(ScheduleRule.from_dict(raw))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
            return rules

    def save_rules(self, rules: list[ScheduleRule]) -> None:
        with self._lock:
            with self._process_lock():
                self._save_rules_unlocked(rules)

    def _save_rules_unlocked(self, rules: list[ScheduleRule]) -> None:
        lines = [json.dumps(rule.to_dict(), ensure_ascii=False) for rule in rules]
        self._atomic_write(self._schedules_path, "\n".join(lines) + ("\n" if lines else ""))

    def mutate_rules(self, mutate: Callable[[list[ScheduleRule]], list[ScheduleRule]]) -> None:
        """Atomically read-modify-write rules across web, CLI, and desktop processes."""
        with self._lock:
            with self._process_lock():
                self._save_rules_unlocked(mutate(self.load_rules()))

    def get_rule(self, rule_id: str) -> ScheduleRule | None:
        for rule in self.load_rules():
            if rule.id == rule_id:
                return rule
        return None

    def upsert_rule(self, rule: ScheduleRule) -> None:
        def mutate(rules: list[ScheduleRule]) -> list[ScheduleRule]:
            for i, existing in enumerate(rules):
                if existing.id == rule.id:
                    rules[i] = rule
                    return rules
            return [*rules, rule]

        self.mutate_rules(mutate)

    def delete_rule(self, rule_id: str) -> bool:
        deleted = False

        def mutate(rules: list[ScheduleRule]) -> list[ScheduleRule]:
            nonlocal deleted
            kept = [r for r in rules if r.id != rule_id]
            deleted = len(kept) != len(rules)
            return kept

        self.mutate_rules(mutate)
        return deleted

    # --- runtime state ----------------------------------------------------

    def load_state(self) -> dict[str, RuleState]:
        with self._lock:
            try:
                raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            rules = raw.get("rules", {}) if isinstance(raw, dict) else {}
            out: dict[str, RuleState] = {}
            for rule_id, state_raw in rules.items():
                if isinstance(state_raw, dict):
                    out[str(rule_id)] = RuleState.from_dict(state_raw)
            return out

    def save_state(self, state: dict[str, RuleState]) -> None:
        with self._lock:
            payload = {"rules": {rid: st.to_dict() for rid, st in state.items()}}
            self._atomic_write(self._state_path, json.dumps(payload, ensure_ascii=False, indent=2))

    # --- run history ------------------------------------------------------

    def append_run(self, record: dict[str, Any]) -> None:
        with self._lock:
            line = json.dumps(record, ensure_ascii=False)
            with self._runs_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def append_anchor_log(self, record: dict[str, Any]) -> None:
        """Append-only passive trajectory log of per-window anchor state.
        Investigation aid for quota-window anchoring; safe to delete the file
        or this writer once the analysis is done."""
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            line = json.dumps(record, ensure_ascii=False)
            with self._anchor_log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if not self._runs_path.exists():
            return []
        lines = self._runs_path.read_text(encoding="utf-8").splitlines()
        rows: list[dict[str, Any]] = []
        for line in lines[-max(1, limit) :]:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    rows.append(parsed)
            except json.JSONDecodeError:
                continue
        rows.reverse()  # newest first
        return rows
