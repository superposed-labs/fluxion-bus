from __future__ import annotations

import json
import logging
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fluxion.config.settings import Settings, env_file_path
from fluxion.executors.antigravity.models import select_antigravity_ping_model
from fluxion.i18n import normalize_locale, t
from fluxion.scheduler import engine
from fluxion.scheduler.messages import build_quota_reset_copy
from fluxion.scheduler.models import (
    ACTION_NOTIFY,
    ACTION_PING,
    TRIGGER_QUOTA_REFRESH,
    RuleState,
    ScheduleRule,
    parse_iso,
)
from fluxion.scheduler.store import ScheduleStore
from fluxion.subagent import SubagentRunner, SubagentRunRequest
from fluxion.usage.collector import SharedUsageClient
from fluxion.usage.models import ProviderUsage
from fluxion.usage.service import UsageService

log = logging.getLogger("fluxion.scheduler")

_PING_PROMPT = (
    "Reply with a brief 'hello'. This is a keep-alive ping to open a quota "
    "window — do not perform any other work."
)

# Passive anchor-log tuning. A window is "anchored" when its reported reset is
# clearly less than a full window-length out (it holds a fixed reset instant);
# "drifting/idle" when reset ≈ now + full window (provider keeps reporting
# "resets a full window from now"). We record on state change or heartbeat.
# A window reports its reset ~a full window from now while drifting (jitter only
# a few seconds), so 30s cleanly separates "drifting" from "anchored" while
# avoiding a long blind spot — a window that anchors at reset (e.g. Antigravity)
# is recognized on the next poll instead of after ~2 minutes of redundant pings.
_ANCHOR_TOLERANCE_SEC = 30
_ANCHOR_LOG_HEARTBEAT_SEC = 600


def _antigravity_ping_model(provider: str, window_key: str, *, command: str = "") -> str | None:
    if provider != "antigravity":
        return None
    return select_antigravity_ping_model(pool_key=window_key, command=command)


# Providers whose 5h window drifts when idle and only anchors after sustained
# in-session activity — these go through the burst state machine. Claude anchors
# normally on first use, so it keeps the simple single-ping path.
_ANCHOR_PROVIDERS = ("codex", "antigravity")


@dataclass
class _AnchorState:
    """In-flight anchoring burst for one quota pool. Lives in memory; a daemon
    restart simply abandons the burst (best-effort, same as the old behavior)."""

    event_id: str
    provider: str
    attempts: int = 0


def _pool_key(provider: str, window_key: str) -> str:
    """A ping is account-wide per quota pool. codex/claude have one pool;
    antigravity has two (Gemini vs External Models), each its own model."""
    if provider == "antigravity":
        key = (window_key or "").lower()
        if "external" in key:
            return "antigravity:external"
        if "gemini" in key:
            return "antigravity:gemini"
    return provider


def _pool_model(pool_key: str, *, command: str = "") -> str | None:
    if pool_key.startswith("antigravity:"):
        return select_antigravity_ping_model(pool_key=pool_key, command=command)
    return None


def _is_five_hour_window(window: Any) -> bool:
    key = (getattr(window, "key", "") or "").lower()
    return "5h" in key or getattr(window, "window_minutes", None) == 300


def _pool_matches_window(pool_key: str, window: Any) -> bool:
    """Does this usage window belong to the pool? For antigravity, match the
    Gemini/External sub-pool by key; for single-pool providers, always yes."""
    if ":" not in pool_key:
        return True
    sub = pool_key.split(":", 1)[1]
    return sub in (getattr(window, "key", "") or "").lower()


def build_usage_service(settings: Settings) -> SharedUsageClient:
    """Build the shared-cache client used by the long-running scheduler."""
    return SharedUsageClient(settings)


def compute_tick(settings: Settings) -> int:
    """Poll cadence. Reading usage is free, so we can poll tightly, but the
    Claude endpoint dislikes hammering — cap at 60s, floor at 15s."""
    return max(15, min(settings.usage_refresh_sec, 60))


def _executor_fingerprint(settings: Any) -> tuple:
    """Settings that the sub-agent executors snapshot at construction. When any
    of these change on a reload, the cached runner must be rebuilt so a fire
    uses the new provider/model/credentials. getattr-with-default keeps this
    safe for the lightweight settings stand-ins used in tests."""
    g = lambda name, default=None: getattr(settings, name, default)  # noqa: E731
    return (
        g("default_executor"),
        tuple(g("enabled_executors", [])),
        g("claude_command"),
        g("claude_provider"),
        g("claude_auth_mode"),
        g("claude_model"),
        g("claude_base_url"),
        g("claude_api_key"),
        g("claude_auth_token"),
        g("codex_sandbox_mode"),
        g("codex_bypass_sandbox"),
        g("codex_skip_git_repo_check"),
        g("antigravity_command"),
        g("antigravity_sandbox"),
        g("task_timeout_sec"),
        str(g("data_dir")),
    )


class SchedulerDaemon:
    def __init__(
        self,
        settings: Settings,
        *,
        runner: SubagentRunner | None = None,
        usage: UsageService | SharedUsageClient | None = None,
        store: ScheduleStore | None = None,
        tick_sec: int | None = None,
        settings_loader=None,
        env_path=None,
    ) -> None:
        self._settings = settings
        self._store = store or ScheduleStore(settings.data_dir)
        self._usage = usage or build_usage_service(settings)
        self._usage_injected = usage is not None
        self._runner = runner
        self._runner_injected = runner is not None
        self._tick_override = tick_sec is not None
        self._tick_sec = tick_sec or compute_tick(settings)
        self._stop = threading.Event()
        self._state = self._store.load_state()
        self._rules: list[ScheduleRule] = []
        self._rules_mtime: float | None = None
        # Hot-reload: watch the .env file so settings edits apply without a
        # daemon restart. `settings_loader` is injectable for tests; production
        # reads the real file via Settings.reload().
        self._settings_loader = settings_loader
        self._env_path = env_path if env_path is not None else env_file_path()
        self._env_mtime = self._read_env_mtime()
        self._exec_fingerprint = _executor_fingerprint(settings)
        # Passive per-window anchor-state trajectory log (investigation aid).
        # Maps (provider, window_key) -> (signature, last_logged_at).
        self._anchor_log_last: dict[tuple[str, str], tuple[Any, datetime]] = {}
        # In-flight anchoring bursts, keyed by quota pool.
        self._anchors: dict[str, _AnchorState] = {}
        # Codex reset-credit tracking, keyed by stable per-credit id and
        # persisted so grants/expiries are detected across daemon restarts.
        self._credit_state_path = settings.data_dir / "credit_state.json"
        (
            self._seen_credit_ids,
            self._notified_expiry_ids,
            self._credit_state_init,
        ) = self._load_credit_state()

    def _read_env_mtime(self) -> float | None:
        if self._env_path is None:
            return None
        try:
            return self._env_path.stat().st_mtime
        except OSError:
            return None

    def _maybe_reload_settings(self) -> None:
        """When the .env file changes, rebuild settings in place so the next
        tick honors the new values. Never raises — a bad reload keeps the
        previous good settings and the daemon keeps running."""
        mtime = self._read_env_mtime()
        if mtime is None or mtime == self._env_mtime:
            return
        self._env_mtime = mtime
        try:
            new_settings = (
                self._settings_loader() if self._settings_loader is not None else Settings.reload()
            )
        except Exception as exc:  # noqa: BLE001 — reload must not kill the daemon
            log.warning("settings reload failed, keeping previous settings: %s", exc)
            return

        self._settings = new_settings
        if not self._usage_injected:
            try:
                self._usage = build_usage_service(new_settings)
            except Exception as exc:  # noqa: BLE001
                log.warning("usage rebuild after reload failed: %s", exc)
        if not self._tick_override:
            self._tick_sec = compute_tick(new_settings)

        new_fp = _executor_fingerprint(new_settings)
        if new_fp != self._exec_fingerprint:
            self._exec_fingerprint = new_fp
            if not self._runner_injected:
                # Drop the cached runner so the next fire rebuilds executors with
                # the new provider/model/credentials. The old gateway's idle
                # daemon worker threads are reclaimed at process exit.
                self._runner = None
                log.info("executor settings changed; runner will be rebuilt")
        log.info("reloaded settings from %s", self._env_path)

    def _ensure_runner(self) -> SubagentRunner:
        # Built lazily so `--once` with no firing rule needs no executors.
        if self._runner is None:
            self._runner = SubagentRunner(self._settings)
        return self._runner

    def _refresh_rules(self) -> list[ScheduleRule]:
        mtime = self._store.schedules_mtime()
        if mtime != self._rules_mtime:
            self._rules = self._store.load_rules()
            self._rules_mtime = mtime
            log.info("loaded %d schedule rule(s)", len(self._rules))

        return list(self._rules)

    def _safe_usage(self):
        try:
            return self._usage.get_usage()
        except Exception as exc:  # noqa: BLE001 — usage must never crash a tick
            log.warning("usage probe failed: %s", exc)
            return []

    def _get_target_provider(self, rule: ScheduleRule) -> str:
        agent = (rule.action.agent or "").strip()
        if not agent or agent == "auto":
            if rule.trigger.type == TRIGGER_QUOTA_REFRESH and rule.trigger.provider:
                return rule.trigger.provider
            return self._settings.default_executor
        return agent

    def _is_provider_exhausted(
        self, provider: str, usage: list[ProviderUsage], now: datetime
    ) -> tuple[bool, str | None]:
        for pu in usage:
            if pu.provider == provider:
                for w in pu.windows:
                    if w.used_percent is not None and w.used_percent >= 99.0:
                        reset_dt = parse_iso(w.resets_at)
                        if reset_dt and reset_dt > now:
                            return True, w.resets_at
                    elif (
                        w.remaining is not None
                        and w.total is not None
                        and w.total > 0
                        and w.remaining <= 0
                    ):
                        reset_dt = parse_iso(w.resets_at)
                        if reset_dt and reset_dt > now:
                            return True, w.resets_at
        return False, None

    def _log_anchor(self, usage: list[ProviderUsage], now: datetime) -> None:
        """Append per-window anchor state (drift vs pinned) to a passive
        trajectory log, on state change or every ~10 min heartbeat. Reading
        usage is free; this never raises into the tick. Investigation aid for
        how/when quota windows anchor and decay across resets."""
        try:
            for pu in usage or []:
                fetched = parse_iso(pu.fetched_at) or now
                for w in getattr(pu, "windows", None) or []:
                    if w.resets_at is None and w.used_percent is None:
                        continue
                    reset = parse_iso(w.resets_at)
                    span = (reset - fetched).total_seconds() if reset else None
                    win_sec = (w.window_minutes or 0) * 60
                    anchored = (
                        span is not None and win_sec > 0 and span < win_sec - _ANCHOR_TOLERANCE_SEC
                    )
                    key = (pu.provider, w.key)
                    sig = (anchored, w.used_percent, w.resets_at if anchored else None)
                    last = self._anchor_log_last.get(key)
                    changed = last is None or last[0] != sig
                    stale = (
                        last is not None
                        and (now - last[1]).total_seconds() >= _ANCHOR_LOG_HEARTBEAT_SEC
                    )
                    if not (changed or stale):
                        continue
                    self._anchor_log_last[key] = (sig, now)
                    self._store.append_anchor_log(
                        {
                            "ts": now.isoformat(),
                            "provider": pu.provider,
                            "key": w.key,
                            "used_percent": w.used_percent,
                            "resets_at": w.resets_at,
                            "fetched_at": pu.fetched_at,
                            "window_minutes": w.window_minutes,
                            "span_sec": round(span) if span is not None else None,
                            "anchored": anchored,
                            "event": "change" if changed else "heartbeat",
                        }
                    )
        except Exception as exc:  # noqa: BLE001 — logging must never crash a tick
            log.debug("anchor log failed: %s", exc)

    # ── anchoring burst state machine ──────────────────────────────
    def _pool_five_hour(
        self, usage: list[ProviderUsage], pool_key: str
    ) -> tuple[Any, str] | tuple[None, None]:
        """The pool's 5h window plus the snapshot's fetched_at, or (None, None)."""
        provider = pool_key.split(":", 1)[0]
        for pu in usage:
            if pu.provider != provider:
                continue
            for w in pu.windows:
                if _is_five_hour_window(w) and _pool_matches_window(pool_key, w):
                    return w, pu.fetched_at
        return None, None

    def _pool_anchored(self, usage: list[ProviderUsage], pool_key: str, now: datetime) -> bool:
        """True when the pool's 5h window holds a fixed reset clearly inside a
        full window (anchored), vs drifting at ~now + full window (idle)."""
        window, fetched_at = self._pool_five_hour(usage, pool_key)
        if window is None:
            return False
        reset = parse_iso(window.resets_at)
        win_sec = (window.window_minutes or 0) * 60
        if reset is None or win_sec <= 0:
            return False
        fetched = parse_iso(fetched_at) or now
        span = (reset - fetched).total_seconds()
        return span < win_sec - _ANCHOR_TOLERANCE_SEC

    def _pool_exhausted(self, usage: list[ProviderUsage], pool_key: str, now: datetime) -> bool:
        """A window in this pool is at its cap (≥99% with a future reset), so a
        ping is futile until it resets — e.g. 5h has room but the weekly is maxed."""
        provider = pool_key.split(":", 1)[0]
        for pu in usage:
            if pu.provider != provider:
                continue
            for w in pu.windows:
                if not _pool_matches_window(pool_key, w):
                    continue
                if w.used_percent is not None and w.used_percent >= 99.0:
                    reset = parse_iso(w.resets_at)
                    if reset and reset > now:
                        return True
        return False

    def _start_anchoring(
        self,
        rule: ScheduleRule,
        state: RuleState,
        now: datetime,
        decision: engine.FireDecision,
        *,
        notify: bool = True,
    ) -> None:
        """Edge entry: arm the burst for this pool (and optionally notify).
        Coalesces 5h+7d (same pool) and re-fires of the same edge into one
        event. Monitor rules notify separately, so they pass notify=False."""
        pool_key = _pool_key(rule.trigger.provider, rule.trigger.window_key)
        if pool_key in self._anchors:
            return
        if notify:
            self._fire(rule, state, now, decision, do_submit=False)
        self._anchors[pool_key] = _AnchorState(
            event_id=uuid4().hex[:12], provider=rule.trigger.provider
        )

    def _advance_anchoring(self, usage: list[ProviderUsage], now: datetime) -> None:
        """One non-blocking step per tick: stop if anchored, skip if exhausted,
        else submit one ping (resuming the event's session). Gives up — with a
        single notification — only after `autoping_max_attempts` pings fail, so a
        run that anchors a ping or two over the typical count stays silent."""
        cap = max(1, int(getattr(self._settings, "autoping_max_attempts", 12)))
        for pool_key in list(self._anchors):
            st = self._anchors[pool_key]
            if self._pool_anchored(usage, pool_key, now):
                del self._anchors[pool_key]
                continue
            if self._pool_exhausted(usage, pool_key, now):
                continue
            if st.attempts >= cap:
                self._notify_anchor_failed(pool_key, st.attempts)
                del self._anchors[pool_key]
                continue
            if self._submit_anchor_ping(pool_key, st.provider, st.event_id):
                st.attempts += 1

    def _submit_anchor_ping(self, pool_key: str, provider: str, event_id: str) -> bool:
        """Submit one keep-alive ping for the pool. The event-scoped thread makes
        the gateway resume the same session across the burst (cheap anchoring)."""
        ping_workspace = self._settings.data_dir / "autoping_workspace"
        ping_workspace.mkdir(parents=True, exist_ok=True)
        safe = pool_key.replace(":", "-")
        model = _pool_model(
            pool_key,
            command=str(getattr(self._settings, "antigravity_command", "") or ""),
        )
        request = SubagentRunRequest(
            agent=provider,
            prompt=[_PING_PROMPT],
            workspace=str(ping_workspace),
            thread=f"autoping-{safe}-{event_id}",
            task_name=f"ping-{safe}",
            profile="inspect",
            mode="read-only",
            session_policy="auto",
            include_subagent_preamble=False,
            model=model,
        )
        record: dict[str, Any] = {
            "schedule_id": f"anchor:{pool_key}",
            "name": f"Auto Ping anchor ({pool_key})",
            "fired_at": datetime.now(UTC).isoformat(),
            "trigger_reason": f"anchoring burst (event {event_id})",
            "action_type": ACTION_PING,
            "agent": provider,
            "task_id": "",
            "run_id": "",
            "accepted": False,
            "error": "",
        }
        if provider == "antigravity" and not model:
            record["error"] = "no live Antigravity model available for quota pool"
            self._store.append_run(record)
            log.warning("anchor ping skipped for pool %s: no live Antigravity model", pool_key)
            return True
        try:
            handle = self._ensure_runner().submit(request)
            record["agent"] = handle.agent
            record["task_id"] = handle.task_id
            record["run_id"] = handle.run_id
            record["accepted"] = bool(handle.accepted)
            self._store.append_run(record)
            return bool(handle.accepted)
        except Exception as exc:  # noqa: BLE001 — a failed ping must not crash the tick
            record["error"] = str(exc)
            self._store.append_run(record)
            log.exception("anchor ping submit failed for pool %s", pool_key)
            return False

    def _notify_anchor_failed(self, pool_key: str, attempts: int) -> None:
        body = t(self._ui_locale(), "autoping.anchor_failed", pool=pool_key, attempts=attempts)
        text = f"⚠️ [Fluxion Auto Ping] {body}"
        if getattr(self._settings, "menu_slack_notify_refresh", False):
            self._notify_slack(text)
        if getattr(self._settings, "menu_telegram_notify_refresh", False):
            self._notify_telegram(text)
        if getattr(self._settings, "menu_qqbot_notify_refresh", False):
            self._notify_qqbot(text)
        if getattr(self._settings, "menu_feishu_notify_refresh", False):
            self._notify_feishu(text)
        if getattr(self._settings, "menu_wechat_notify_refresh", False):
            self._notify_wechat(text)
        if self._should_notify_macos():
            self._notify_macos("Auto Ping Failed", text)

    def tick(self) -> None:
        self._maybe_reload_settings()
        now = datetime.now(UTC)
        rules = self._refresh_rules()
        usage = self._safe_usage()
        self._log_anchor(usage, now)
        active_ids = {rule.id for rule in rules}
        dirty = False

        for rule in rules:
            if not rule.enabled and not getattr(rule, "run_now", False):
                continue
            state = self._state.get(rule.id)
            if state is None:
                state = RuleState()
                self._state[rule.id] = state
            self._roll_daily(state, now)

            decision = engine.evaluate_rule(rule, state, now, usage, self._tick_sec)

            # Always refresh the quota baseline so an edge fires exactly once.
            # (advance_baseline quarantines backward-resets_at glitch samples
            # instead of adopting them.)
            if rule.trigger.type == TRIGGER_QUOTA_REFRESH:
                obs = engine.observe_window(usage, rule.trigger.provider, rule.trigger.window_key)
                if obs is not None:
                    engine.advance_baseline(state, obs)
            state.last_eval_at = now.isoformat()
            dirty = True

            if decision.fire:
                provider = self._get_target_provider(rule)
                if rule.action.type == ACTION_NOTIFY:
                    # Monitor rule: always notify (IM toggles gate the channels),
                    # then ping only if the global Auto Ping switch is on.
                    self._fire(rule, state, now, decision, do_submit=False)
                    if getattr(self._settings, "autoping_enabled", False):
                        if rule.trigger.provider in _ANCHOR_PROVIDERS:
                            self._start_anchoring(rule, state, now, decision, notify=False)
                        elif not self._is_provider_exhausted(provider, usage, now)[0]:
                            self._submit_anchor_ping(provider, provider, uuid4().hex[:12])
                elif rule.action.type == ACTION_PING and rule.trigger.provider in _ANCHOR_PROVIDERS:
                    # Burst path: arm anchoring; pings are driven by
                    # _advance_anchoring below (one per tick until anchored/cap).
                    self._start_anchoring(rule, state, now, decision)
                else:
                    is_exhausted, resets_at = self._is_provider_exhausted(provider, usage, now)
                    if is_exhausted and not getattr(rule, "run_now", False):
                        log.info(
                            "Deferring schedule %s (%s): target provider %s is exhausted%s",
                            rule.id,
                            rule.name,
                            provider,
                            f" until {resets_at}" if resets_at else "",
                        )
                    else:
                        self._fire(rule, state, now, decision)

                # Clear run_now flag if set
                if getattr(rule, "run_now", False):

                    def clear_run_now(
                        rules_list: list[ScheduleRule], _rule_id: str = rule.id
                    ) -> list[ScheduleRule]:
                        for r in rules_list:
                            if r.id == _rule_id:
                                r.run_now = False
                                r.updated_at = datetime.now(UTC).isoformat()
                        return rules_list

                    self._store.mutate_rules(clear_run_now)

        self._advance_anchoring(usage, now)
        self._check_credit_grant(usage)
        self._check_credit_expiry(usage)

        # Drop state for rules that no longer exist.
        for stale_id in [rid for rid in self._state if rid not in active_ids]:
            del self._state[stale_id]
            dirty = True

        if dirty:
            self._store.save_state(self._state)

    def _roll_daily(self, state: RuleState, now: datetime) -> None:
        today = now.date().isoformat()
        if state.runs_today_date != today:
            state.runs_today_date = today
            state.runs_today = 0

    def _load_credit_state(self) -> tuple[set[str], set[str], bool]:
        """Load persisted reset-credit tracking.

        Returns ``(seen_grant_ids, notified_expiry_ids, initialized)``. When the
        file is absent (fresh install) ``initialized`` is False so the first poll
        can seed a baseline without firing a "granted" alert for pre-existing
        credits.
        """
        try:
            raw = json.loads(self._credit_state_path.read_text(encoding="utf-8"))
        except Exception:
            return set(), set(), False
        seen = {str(x) for x in raw.get("seen_grant_ids", [])}
        notified = {str(x) for x in raw.get("notified_expiry_ids", [])}
        return seen, notified, True

    def _save_credit_state(self) -> None:
        payload = {
            "seen_grant_ids": sorted(self._seen_credit_ids),
            "notified_expiry_ids": sorted(self._notified_expiry_ids),
        }
        try:
            self._credit_state_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            log.warning("failed to persist credit state", exc_info=True)

    @staticmethod
    def _codex_credits(usage: list[ProviderUsage]) -> list[dict[str, Any]] | None:
        """Return the per-credit list from the Codex snapshot, or None.

        None means "no identity data to act on" (provider missing/errored, or an
        older snapshot without the `credits` field).
        """
        codex_usage = next((u for u in usage if u.provider == "codex"), None)
        if not codex_usage or codex_usage.status != "ok" or not codex_usage.resets:
            return None
        credits = codex_usage.resets.get("credits")
        if not isinstance(credits, list):
            return None
        return [c for c in credits if isinstance(c, dict) and c.get("id")]

    def _check_credit_grant(self, usage: list[ProviderUsage]) -> None:
        """Notify when Codex grants new reset credits.

        Detection keys on each credit's stable id, not the available count: a
        count delta misses a grant that lands in the same poll a credit is
        consumed or expires, and under-counts simultaneous grants.
        """
        credits = self._codex_credits(usage)
        if credits is None:
            return

        current_ids = {str(c["id"]) for c in credits}

        # Fresh install: seed the baseline silently so pre-existing held credits
        # don't fire a false "granted" alert on first run.
        if not self._credit_state_init:
            self._seen_credit_ids = current_ids
            self._credit_state_init = True
            self._save_credit_state()
            return

        new_credits = [
            c
            for c in credits
            if str(c["id"]) not in self._seen_credit_ids and c.get("status") == "available"
        ]

        # Mark every id we can see as known — including any granted+consumed
        # within one poll — so they never read as "new" on a later tick. Done
        # regardless of the notify setting, matching the prior baseline behavior.
        if not current_ids <= self._seen_credit_ids:
            self._seen_credit_ids |= current_ids
            self._save_credit_state()

        if not new_credits:
            return

        log.info(
            "credit grant detected: +%d new (ids=%s), %d available",
            len(new_credits),
            ", ".join(str(c["id"]) for c in new_credits),
            sum(1 for c in credits if c.get("status") == "available"),
        )
        if not getattr(self._settings, "notify_credit_grant", False):
            return

        notify_slack = getattr(self._settings, "menu_slack_notify_refresh", False)
        notify_telegram = getattr(self._settings, "menu_telegram_notify_refresh", False)
        notify_qqbot = getattr(self._settings, "menu_qqbot_notify_refresh", False)
        notify_feishu = getattr(self._settings, "menu_feishu_notify_refresh", False)
        notify_wechat = getattr(self._settings, "menu_wechat_notify_refresh", False)
        notify_line = getattr(self._settings, "menu_line_notify_refresh", False)
        notify_macos = self._should_notify_macos()
        if not (
            notify_slack
            or notify_telegram
            or notify_qqbot
            or notify_feishu
            or notify_wechat
            or notify_line
            or notify_macos
        ):
            return

        delta = len(new_credits)
        # Currently-available total, for the "now available" tail.
        count = sum(1 for c in credits if c.get("status") == "available")

        msg = f"🎁 *[Fluxion Credit Grant]* Codex granted {delta} reset credit{'s' if delta > 1 else ''} · {count} now available."

        if notify_slack:
            # Format a beautiful Block Kit layout for Slack.
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"🎁 *[Fluxion Credit Grant]*\nCodex granted *{delta}* reset credit{'s' if delta > 1 else ''}.",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Available Resets:*\n✨ `{count}`"},
                    ],
                },
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "⚡ _Fluxion Credit Grant Monitor_"}],
                },
            ]
            self._notify_slack(msg, blocks=blocks)

        if notify_telegram:
            tg_text = (
                "🎁 **[Fluxion Credit Grant]**\n"
                f"Codex granted **{delta}** reset credit{'s' if delta > 1 else ''} · `{count}` now available."
                "\n\n⚡ _Fluxion Credit Grant Monitor_"
            )
            self._notify_telegram(tg_text, kind="credit-grant")

        if notify_qqbot:
            qq_text = (
                "🎁 [Fluxion Credit Grant]\n"
                f"Codex granted {delta} reset credit{'s' if delta > 1 else ''} · {count} now available."
                "\n\n⚡ Fluxion Credit Grant Monitor"
            )
            self._notify_qqbot(qq_text, kind="credit-grant")

        if notify_feishu:
            feishu_text = (
                "🎁 [Fluxion Credit Grant]\n"
                f"Codex granted {delta} reset credit{'s' if delta > 1 else ''} · {count} now available."
                "\n\n⚡ Fluxion Credit Grant Monitor"
            )
            self._notify_feishu(feishu_text, kind="credit-grant")

        if notify_wechat:
            wechat_text = (
                "🎁 [Fluxion Credit Grant]\n"
                f"Codex granted {delta} reset credit{'s' if delta > 1 else ''} · {count} now available."
                "\n\n⚡ Fluxion Credit Grant Monitor"
            )
            self._notify_wechat(wechat_text, kind="credit-grant")

        if notify_line:
            line_text = (
                "🎁 [Fluxion Credit Grant]\n"
                f"Codex granted {delta} reset credit{'s' if delta > 1 else ''} · {count} now available."
                "\n\n⚡ Fluxion Credit Grant Monitor"
            )
            self._notify_line(line_text, kind="credit-grant")

        if notify_macos:
            self._notify_macos(
                "Credit Grant",
                f"Codex granted {delta} reset credit{'s' if delta > 1 else ''} · {count} now available.",
            )

    def _check_credit_expiry(self, usage: list[ProviderUsage]) -> None:
        """Alert before a held reset credit lapses (≤24h out), deduped by id."""
        if not getattr(self._settings, "notify_credit_expiry", False):
            return

        credits = self._codex_credits(usage)
        if credits is None:
            return

        notify_slack = getattr(self._settings, "menu_slack_notify_refresh", False)
        notify_telegram = getattr(self._settings, "menu_telegram_notify_refresh", False)
        notify_qqbot = getattr(self._settings, "menu_qqbot_notify_refresh", False)
        notify_feishu = getattr(self._settings, "menu_feishu_notify_refresh", False)
        notify_wechat = getattr(self._settings, "menu_wechat_notify_refresh", False)
        notify_line = getattr(self._settings, "menu_line_notify_refresh", False)
        notify_macos = self._should_notify_macos()
        if not (
            notify_slack
            or notify_telegram
            or notify_qqbot
            or notify_feishu
            or notify_wechat
            or notify_line
            or notify_macos
        ):
            return

        now_ts = datetime.now(UTC).timestamp()
        current_ids: set[str] = set()
        changed = False

        for cred in credits:
            cid = str(cred["id"])
            current_ids.add(cid)
            if cred.get("status") != "available":
                continue
            exp_dt = parse_iso(cred.get("expires_at"))
            if exp_dt is None:
                continue
            remaining_sec = exp_dt.timestamp() - now_ts
            # Alert once a credit is within 24h of lapsing, and not yet expired.
            if not (0 < remaining_sec <= 86400):
                continue
            if cid in self._notified_expiry_ids:
                continue
            self._notified_expiry_ids.add(cid)
            changed = True

            exp_str = exp_dt.strftime("%b %d")
            hours_left = max(1, int(remaining_sec / 3600))
            log.info("credit expiry warning: id=%s expires in ~%dh (%s)", cid, hours_left, exp_str)
            msg = f"⚠️ *[Fluxion Credit Expiry]* A Codex reset credit is about to expire in {hours_left} hour{'s' if hours_left > 1 else ''}! (Expires {exp_str})"

            if notify_slack:
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"⚠️ *[Fluxion Credit Expiry]*\nA Codex reset credit is about to expire in *{hours_left}* hour{'s' if hours_left > 1 else ''}!",
                        },
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Expiry Date:*\n📅 `{exp_str}`"},
                        ],
                    },
                    {
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": "⚡ _Fluxion Credit Expiry Alert_"}
                        ],
                    },
                ]
                self._notify_slack(msg, blocks=blocks)

            if notify_telegram:
                tg_text = (
                    "⚠️ **[Fluxion Credit Expiry]**\n"
                    f"A Codex reset credit is about to expire in **{hours_left}** hour{'s' if hours_left > 1 else ''}! (Expires `{exp_str}`)"
                    "\n\n⚡ _Fluxion Credit Expiry Alert_"
                )
                self._notify_telegram(tg_text, kind="credit-expiry")

            if notify_qqbot:
                qq_text = (
                    "⚠️ [Fluxion Credit Expiry]\n"
                    f"A Codex reset credit is about to expire in {hours_left} hour{'s' if hours_left > 1 else ''}! (Expires {exp_str})"
                    "\n\n⚡ Fluxion Credit Expiry Alert"
                )
                self._notify_qqbot(qq_text, kind="credit-expiry")

            if notify_feishu:
                feishu_text = (
                    "⚠️ [Fluxion Credit Expiry]\n"
                    f"A Codex reset credit is about to expire in {hours_left} hour{'s' if hours_left > 1 else ''}! (Expires {exp_str})"
                    "\n\n⚡ Fluxion Credit Expiry Alert"
                )
                self._notify_feishu(feishu_text, kind="credit-expiry")

            if notify_wechat:
                wechat_text = (
                    "⚠️ [Fluxion Credit Expiry]\n"
                    f"A Codex reset credit is about to expire in {hours_left} hour{'s' if hours_left > 1 else ''}! (Expires {exp_str})"
                    "\n\n⚡ Fluxion Credit Expiry Alert"
                )
                self._notify_wechat(wechat_text, kind="credit-expiry")

            if notify_line:
                line_text = (
                    "⚠️ [Fluxion Credit Expiry]\n"
                    f"A Codex reset credit is about to expire in {hours_left} hour{'s' if hours_left > 1 else ''}! (Expires {exp_str})"
                    "\n\n⚡ Fluxion Credit Expiry Alert"
                )
                self._notify_line(line_text, kind="credit-expiry")

            if notify_macos:
                self._notify_macos(
                    "Credit Expiring",
                    f"A Codex reset credit is about to expire in {hours_left} hour{'s' if hours_left > 1 else ''}! (Expires {exp_str})",
                )

        # Drop ids for credits that are gone (expired/consumed) to bound growth.
        stale = self._notified_expiry_ids - current_ids
        if stale:
            self._notified_expiry_ids -= stale
            changed = True
        if changed:
            self._save_credit_state()

    def _build_request(self, rule: ScheduleRule) -> SubagentRunRequest:
        action = rule.action
        agent = (action.agent or "").strip()
        if not agent or agent == "auto":
            # For a refresh trigger, call the provider whose quota just reset.
            if rule.trigger.type == TRIGGER_QUOTA_REFRESH and rule.trigger.provider:
                agent = rule.trigger.provider
            else:
                agent = "auto"

        if action.type == ACTION_PING:
            prompt = action.prompt.strip() or _PING_PROMPT
            # Optimize ping: run in an empty directory to avoid scanning the main codebase,
            # use a fresh session to avoid history accumulation, and omit system preamble.
            ping_workspace = self._settings.data_dir / "autoping_workspace"
            ping_workspace.mkdir(parents=True, exist_ok=True)
            return SubagentRunRequest(
                agent=agent,
                prompt=[prompt],
                project=action.project,
                workspace=str(ping_workspace),
                thread=action.thread or "scheduler",
                task_name=action.task_name or f"ping-{rule.id}",
                profile="inspect",
                mode="read-only",
                session_policy="new",
                include_subagent_preamble=False,
                model=_antigravity_ping_model(
                    rule.trigger.provider,
                    rule.trigger.window_key,
                    command=str(getattr(self._settings, "antigravity_command", "") or ""),
                ),
            )

        return SubagentRunRequest(
            agent=agent,
            prompt=[action.prompt],
            project=action.project,
            workspace=action.workspace or ".",
            thread=action.thread or "scheduler",
            task_name=action.task_name or f"sched-{rule.id}",
            profile=action.profile or "inspect",
            mode=action.mode or "read-only",
        )

    def _notify_macos(self, title: str, body: str) -> None:
        """Append a notification record to the JSONL signal file that the macOS
        desktop app watches.  The desktop app picks these up via its cache
        watcher, delivers them through UNUserNotificationCenter, and truncates
        the file.  No-op on non-macOS platforms so the signal file never
        accumulates when no desktop app is running to consume it."""
        if sys.platform != "darwin":
            return
        signal_path = self._settings.data_dir / "macos_notifications.jsonl"
        record = json.dumps(
            {
                "title": title,
                "body": body,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
        )
        try:
            with open(signal_path, "a", encoding="utf-8") as fp:
                fp.write(record + "\n")
            log.info("Queued macOS notification: %s — %s", title, body)
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to write macOS notification signal: %s", exc)

    def _should_notify_macos(self) -> bool:
        return sys.platform == "darwin" and getattr(
            self._settings, "menu_macos_notify_refresh", True
        )

    def _notify_slack(self, text: str, blocks: list[dict[str, Any]] | None = None) -> None:
        token = self._settings.slack_bot_token.strip()
        if not token:
            log.warning("Slack notification skipped: SLACK_BOT_TOKEN is not set.")
            return

        channel = self._settings.scheduler_slack_channel.strip()

        from slack_sdk import WebClient

        client = WebClient(token=token)

        channels_to_notify = []
        if channel:
            channels_to_notify.append(channel)
        else:
            # Auto-discover active DM (IM) channels
            try:
                response = client.conversations_list(types="im")
                if response.get("ok"):
                    for im in response.get("channels", []):
                        if im.get("is_im") and not im.get("is_user_deleted"):
                            im_id = im.get("id")
                            if im_id:
                                channels_to_notify.append(im_id)
            except Exception as exc:
                log.warning(
                    "Failed to auto-discover Slack DM channels via conversations_list: %s", exc
                )

            # If we couldn't discover active DM channels via conversations_list, try allowed users
            if not channels_to_notify and getattr(self._settings, "slack_allowed_users", None):
                log.info(
                    "Attempting to open DM channels for allowed users: %s",
                    self._settings.slack_allowed_users,
                )
                for user_id in self._settings.slack_allowed_users:
                    try:
                        # Try to open/get the DM channel using conversations_open
                        response = client.conversations_open(users=user_id)
                        if response.get("ok"):
                            dm_channel_id = response.get("channel", {}).get("id")
                            if dm_channel_id:
                                channels_to_notify.append(dm_channel_id)
                        else:
                            # Fallback directly to the user_id
                            channels_to_notify.append(user_id)
                    except Exception as exc:
                        log.warning(
                            "conversations_open failed for user %s: %s; falling back to direct user ID routing",
                            user_id,
                            exc,
                        )
                        channels_to_notify.append(user_id)

            # If no DM channels discovered, fall back to slack_channel_workspaces
            if not channels_to_notify and self._settings.slack_channel_workspaces:
                first_ws_channel = next(iter(self._settings.slack_channel_workspaces.keys()))
                channels_to_notify.append(first_ws_channel)

        if not channels_to_notify:
            log.warning("Slack notification skipped: no channel or DM discovered.")
            return

        for ch in channels_to_notify:
            try:
                kwargs = {"channel": ch, "text": text}
                if blocks is not None:
                    kwargs["blocks"] = blocks
                client.chat_postMessage(**kwargs)
                log.info("Sent Slack notification to channel %s: %s", ch, text)
            except Exception as exc:
                log.error("Failed to send Slack notification to %s: %s", ch, exc)

    def _notify_telegram(self, text: str, *, kind: str = "quota-reset") -> None:
        token = self._settings.telegram_bot_token.strip()
        if not token:
            log.warning("Telegram notification skipped: TELEGRAM_BOT_TOKEN is not set.")
            return
        # Telegram bots cannot enumerate who DM'd them, so we deliver to the
        # configured allowlist (for a private chat, chat_id == the user id).
        users = self._settings.telegram_allowed_users
        if not users:
            log.warning(
                "Telegram notification skipped: FLUXION_TELEGRAM_ALLOWED_USERS is empty (no DM target)."
            )
            return

        from fluxion.channels.telegram.markdown import markdown_to_telegram_html
        from fluxion.channels.telegram.telegram_client import TelegramAPIError, TelegramClient

        client = TelegramClient(token)
        # Author notifications in GitHub-Markdown and render to the HTML subset
        # Telegram understands — same path the executor replies use.
        html_text = markdown_to_telegram_html(text)
        sent = 0
        for uid in users:
            try:
                client.send_message(chat_id=uid, text=html_text, parse_mode="HTML")
                sent += 1
            except TelegramAPIError as exc:
                if exc.is_parse_error:
                    # Converted HTML was rejected — send the raw text plainly.
                    try:
                        client.send_message(chat_id=uid, text=text)
                        sent += 1
                        continue
                    except Exception as exc2:
                        log.error("Failed to send Telegram notification to %s: %s", uid, exc2)
                else:
                    log.error("Failed to send Telegram notification to %s: %s", uid, exc)
            except Exception as exc:
                log.error("Failed to send Telegram notification to %s: %s", uid, exc)
        if sent:
            log.info("Sent Telegram %s notification to %d user(s)", kind, sent)

    def _notify_wechat(self, text: str, *, kind: str = "quota-reset") -> None:
        from fluxion.channels.wechat.context_store import ContextTokenStore
        from fluxion.channels.wechat.credential_store import CredentialStore
        from fluxion.channels.wechat.ilink_client import ILinkClient
        from fluxion.channels.wechat.models import MessageItem

        creds = CredentialStore(self._settings.data_dir).load()
        if creds is None:
            log.warning(
                "WeChat notification skipped: credentials not found; run wechat_login first."
            )
            return

        allowed_users = getattr(self._settings, "wechat_allowed_users", set())
        targets = ContextTokenStore(self._settings.data_dir).notification_targets(allowed_users)
        if not targets:
            log.warning(
                "WeChat notification skipped: no user with a saved context token has messaged the bot."
            )
            return

        client = ILinkClient()
        client.apply_credentials(creds)
        sent = 0
        for user_id, context_token in targets:
            try:
                client.send_message(
                    to_user_id=user_id,
                    context_token=context_token,
                    items=[MessageItem.text(text)],
                )
                sent += 1
            except Exception as exc:
                log.error("Failed to send WeChat notification to %s: %s", user_id, exc)
        if sent:
            log.info("Sent WeChat %s notification to %d user(s)", kind, sent)

    def _notify_line(self, text: str, *, kind: str = "quota-reset") -> None:
        token = self._settings.line_channel_access_token.strip()
        if not token:
            log.warning("LINE notification skipped: LINE_CHANNEL_ACCESS_TOKEN is not set.")
            return
        users = self._settings.line_allowed_users
        if not users:
            log.warning("LINE notification skipped: FLUXION_LINE_ALLOWED_USERS is empty.")
            return

        import json
        import urllib.error
        import urllib.request

        from fluxion.renderers.line_renderer import clip_text, markdown_to_plain_text

        # LINE standard text bubbles do not render markdown
        plain_text = clip_text(markdown_to_plain_text(text))

        sent = 0
        for uid in users:
            url = "https://api.line.me/v2/bot/message/push"
            payload = {"to": uid, "messages": [{"type": "text", "text": plain_text}]}
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp.read()
                sent += 1
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8")
                log.error("Failed to send LINE notification to %s: %s %s", uid, exc.code, err_body)
            except Exception as exc:
                log.error("Failed to send LINE notification to %s: %s", uid, exc)
        if sent:
            log.info("Sent LINE %s notification to %d user(s)", kind, sent)

    def _notify_qqbot(self, text: str, *, kind: str = "quota-reset") -> None:
        app_id = self._settings.qqbot_app_id.strip()
        client_secret = self._settings.qqbot_client_secret.strip()
        if not app_id or not client_secret:
            log.warning("QQ notification skipped: QQBOT_APP_ID or QQBOT_CLIENT_SECRET is not set.")
            return
        users = self._settings.qqbot_allowed_users
        if not users:
            log.warning("QQ notification skipped: FLUXION_QQBOT_ALLOWED_USERS is empty.")
            return

        from fluxion.channels.qqbot.qqbot_client import QQBotAPIError, QQBotClient
        from fluxion.channels.qqbot.token_manager import QQBotTokenManager
        from fluxion.renderers.line_renderer import clip_text, markdown_to_plain_text

        client = QQBotClient(
            QQBotTokenManager(app_id, client_secret),
            sandbox=getattr(self._settings, "qqbot_sandbox", False),
        )
        plain_text = clip_text(markdown_to_plain_text(text))

        sent = 0
        for uid in users:
            try:
                client.send_c2c_text(uid, plain_text)
                sent += 1
            except QQBotAPIError as exc:
                log.error("Failed to send QQ notification to %s: %s", uid, exc)
            except Exception as exc:
                log.error("Failed to send QQ notification to %s: %s", uid, exc)
        if sent:
            log.info("Sent QQ %s notification to %d user(s)", kind, sent)

    def _notify_feishu(self, text: str, *, kind: str = "quota-reset") -> None:
        app_id = self._settings.feishu_app_id.strip()
        app_secret = self._settings.feishu_app_secret.strip()
        if not app_id or not app_secret:
            log.warning(
                "Feishu notification skipped: FEISHU_APP_ID or FEISHU_APP_SECRET is not set."
            )
            return
        users = self._settings.feishu_allowed_users
        if not users:
            log.warning("Feishu notification skipped: FLUXION_FEISHU_ALLOWED_USERS is empty.")
            return

        from fluxion.channels.feishu.feishu_client import FeishuAPIError, FeishuClient
        from fluxion.renderers.line_renderer import clip_text, markdown_to_plain_text

        client = FeishuClient(app_id, app_secret)
        plain_text = clip_text(markdown_to_plain_text(text))

        sent = 0
        for uid in users:
            try:
                client.send_text(uid, plain_text, receive_id_type="open_id")
                sent += 1
            except FeishuAPIError as exc:
                log.error("Failed to send Feishu notification to %s: %s", uid, exc)
            except Exception as exc:
                log.error("Failed to send Feishu notification to %s: %s", uid, exc)
        if sent:
            log.info("Sent Feishu %s notification to %d user(s)", kind, sent)

    def _ui_locale(self) -> str:
        return normalize_locale(getattr(self._settings, "ui_locale", "en"))

    def _fire(
        self,
        rule: ScheduleRule,
        state: RuleState,
        now: datetime,
        decision: engine.FireDecision,
        *,
        do_submit: bool = True,
    ) -> None:
        reason = decision.reason
        log.info("firing schedule %s (%s): %s", rule.id, rule.name, reason)

        # Send quota-reset notifications to whichever channels are enabled.
        notify_slack = getattr(self._settings, "menu_slack_notify_refresh", False)
        notify_telegram = getattr(self._settings, "menu_telegram_notify_refresh", False)
        notify_qqbot = getattr(self._settings, "menu_qqbot_notify_refresh", False)
        notify_feishu = getattr(self._settings, "menu_feishu_notify_refresh", False)
        notify_wechat = getattr(self._settings, "menu_wechat_notify_refresh", False)
        notify_line = getattr(self._settings, "menu_line_notify_refresh", False)
        notify_macos = self._should_notify_macos()
        if rule.trigger.type == "quota_refresh" and (
            notify_slack
            or notify_telegram
            or notify_qqbot
            or notify_feishu
            or notify_wechat
            or notify_line
            or notify_macos
        ):
            copy = build_quota_reset_copy(rule, decision, self._ui_locale())
            if notify_slack:
                self._notify_slack(copy.slack_fallback, blocks=copy.slack_blocks)
            if notify_telegram:
                self._notify_telegram(copy.telegram)
            if notify_qqbot:
                self._notify_qqbot(copy.plain)
            if notify_feishu:
                self._notify_feishu(copy.plain)
            if notify_wechat:
                self._notify_wechat(copy.plain)
            if notify_line:
                self._notify_line(copy.plain)
            if notify_macos:
                self._notify_macos(copy.macos_title, copy.macos_body)
        # Notify-only mode: the burst state machine sends the reset notification
        # once at event start, then submits pings itself (silently).
        if not do_submit:
            return
        record: dict[str, Any] = {
            "schedule_id": rule.id,
            "name": rule.name,
            "fired_at": now.isoformat(),
            "trigger_reason": reason,
            "action_type": rule.action.type,
            "agent": "",
            "task_id": "",
            "run_id": "",
            "accepted": False,
            "error": "",
        }
        try:
            request = self._build_request(rule)
            handle = self._ensure_runner().submit(request)
            record["agent"] = handle.agent
            record["task_id"] = handle.task_id
            record["run_id"] = handle.run_id
            record["accepted"] = handle.accepted
            # Count the fire only once it was actually accepted into the queue.
            state.last_fired_at = now.isoformat()
            state.runs_today += 1
        except Exception as exc:  # noqa: BLE001 — record and keep the loop alive
            record["error"] = str(exc)
            log.exception("schedule %s failed to fire", rule.id)
        self._store.append_run(record)

    def run_once(self) -> None:
        self.tick()

    def run_forever(self) -> None:
        log.info("fluxion-scheduler started (tick=%ss)", self._tick_sec)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 — a bad tick must not kill the daemon
                log.exception("scheduler tick failed")
            self._stop.wait(self._tick_sec)
        log.info("fluxion-scheduler stopped")

    def stop(self) -> None:
        self._stop.set()
