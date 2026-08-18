from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Any

from fluxion.usage.models import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    ProviderUsage,
    UsageWindow,
)
from fluxion.usage.probes._common import (
    CODEX_DEFAULT_BASE_URL,
    ProbeConfig,
    _normalize_percent,
    _normalize_reset,
    _now_iso,
)
from fluxion.utils.logger import get_logger

logger = get_logger("fluxion.usage.probes.codex")


class CodexUsageProbe:
    """Reports Codex quota, preferring the live `/wham/usage` endpoint (the same
    data the Codex app shows) and falling back to the rate-limit snapshot the
    CLI writes into its session rollout logs.

    - `live`: GET the ChatGPT backend usage endpoint with the locally-stored
      OAuth token (read-only). Real-time, like the Claude probe.
    - `logs`: read the most recent `rate_limits` record from
      ~/.codex/sessions/.../rollout-*.jsonl. No network, but reflects the last
      Codex run (so it can be stale).

    Mode is `auto` (live, then logs), `live`, or `logs`.
    """

    def __init__(self, config: ProbeConfig) -> None:
        self._config = config

    def provider(self) -> str:
        return "codex"

    def probe(self) -> ProviderUsage:
        mode = (self._config.codex_usage_mode or "auto").strip().lower()
        live_err = ""
        if mode in ("auto", "live"):
            live, live_err = self._probe_live()
            if live is not None:
                return live
            if mode == "live":
                return ProviderUsage(
                    provider="codex",
                    status=STATUS_ERROR if live_err else STATUS_UNAVAILABLE,
                    fetched_at=_now_iso(),
                    detail=live_err or "live usage unavailable",
                )
        usage = self._probe_logs()
        if usage.status == STATUS_OK and live_err:
            usage.detail = f"{usage.detail}; live fetch failed: {live_err}"
        return usage

    # ── live: ChatGPT backend /wham/usage ──────────────────────────
    def _probe_live(self) -> tuple[ProviderUsage | None, str]:
        creds = self._read_auth()
        if creds is None:
            return None, "no Codex ChatGPT OAuth token in auth.json"
        access_token, account_id = creds
        base = (self._config.codex_usage_base_url or CODEX_DEFAULT_BASE_URL).rstrip("/")
        url = f"{base}/wham/usage"
        credits_url = f"{base}/wham/rate-limit-reset-credits"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": self._config.codex_user_agent,
            "Accept": "application/json",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        # The reset-credit endpoint is independent of the quota endpoint. Start
        # it on a daemon thread while the required quota request runs here. Once
        # quota is ready, give credits only a short grace period: a slow optional
        # endpoint must never hold the main quota snapshot for a full HTTP
        # timeout. A later refresh can recover credits, while UsageService keeps
        # the last confirmed value in the meantime.
        resets_result: Queue[tuple[dict[str, Any] | None, Exception | None]] = Queue(maxsize=1)

        def fetch_resets() -> None:
            try:
                resets_result.put((self._http_get_json(credits_url, headers), None))
            except Exception as exc:  # noqa: BLE001 - reported on the quota thread below
                resets_result.put((None, exc))

        Thread(target=fetch_resets, name="codex-reset-credits", daemon=True).start()
        try:
            data = self._http_get_json(url, headers)
        except urllib.error.HTTPError as exc:
            return None, f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - degrade to fallback on any failure
            return None, str(exc)

        windows = self._map_live_windows(data)
        if not windows:
            return None, "no rate-limit windows in response"
        plan = data.get("plan_type")
        rate_limit = data.get("rate_limit")
        limit_reached = None
        detail = "live"
        if isinstance(rate_limit, dict):
            if isinstance(rate_limit.get("limit_reached"), bool):
                limit_reached = rate_limit["limit_reached"]
            elif isinstance(rate_limit.get("allowed"), bool):
                # Older payloads may expose only the inverse availability flag.
                limit_reached = not rate_limit["allowed"]
            if limit_reached:
                detail = "live · limit reached"

        # Map rate-limit reset credits. A successful empty response is an
        # explicit count of zero; None is reserved for request failure so the
        # service can retain the last confirmed value without confusing the UI.
        resets_payload = None
        resets_fetch_failed = False
        try:
            # Both requests normally finish together. The small grace period
            # absorbs ordinary response-order jitter without letting this
            # optional endpoint dictate the refresh latency.
            grace = min(0.35, max(0.05, self._config.http_timeout_sec * 0.1))
            credits_data, credits_error = resets_result.get(timeout=grace)
            if credits_error is not None:
                raise credits_error
            if isinstance(credits_data, dict):
                avail_count = credits_data.get("available_count", 0)
                credits_list = credits_data.get("credits", [])
                expiries = []
                credits_out = []
                now_ts = datetime.now(UTC).timestamp()
                for cred in credits_list:
                    if not isinstance(cred, dict):
                        continue
                    cid = cred.get("id")
                    status = cred.get("status")
                    expires_at = cred.get("expires_at")
                    # Per-credit identity drives grant/expiry detection downstream
                    # (the count alone can't survive a same-poll grant+consume).
                    if isinstance(cid, str):
                        credits_out.append(
                            {
                                "id": cid,
                                "status": status,
                                "granted_at": cred.get("granted_at"),
                                "expires_at": expires_at,
                            }
                        )
                    if status == "available" and isinstance(expires_at, str):
                        try:
                            expiry_str = expires_at.replace("Z", "+00:00")
                            expiry_ts = datetime.fromisoformat(expiry_str).timestamp()
                            remaining_ms = int(max(0.0, expiry_ts - now_ts) * 1000)
                            expiries.append(remaining_ms)
                        except Exception:
                            pass
                expiries.sort()
                resets_payload = {
                    "count": avail_count,
                    "expiries": expiries,
                    "credits": credits_out,
                }
        except Empty:
            resets_fetch_failed = True
            logger.warning("Codex reset-credit fetch did not finish with the quota request")
        except Exception as exc:  # noqa: BLE001 - quota remains usable without this add-on
            resets_fetch_failed = True
            logger.warning("Codex reset-credit fetch failed: %s", exc)

        return (
            ProviderUsage(
                provider="codex",
                status=STATUS_OK,
                account_label=str(plan) if isinstance(plan, str) else "",
                windows=windows,
                fetched_at=_now_iso(),
                detail=detail,
                limit_reached=limit_reached,
                resets=resets_payload,
                resets_fetch_failed=resets_fetch_failed,
            ),
            "",
        )

    def _http_get_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        req = urllib.request.Request(url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=self._config.http_timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}

    def _read_auth(self) -> tuple[str, str | None] | None:
        try:
            data = json.loads(self._config.codex_auth_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        tokens = data.get("tokens")
        if not isinstance(tokens, dict):
            return None
        access = tokens.get("access_token")
        if not isinstance(access, str) or not access.strip():
            return None
        account_id = tokens.get("account_id")
        account = account_id.strip() if isinstance(account_id, str) and account_id.strip() else None
        return access.strip(), account

    def _map_live_windows(self, data: dict[str, Any]) -> list[UsageWindow]:
        rate_limit = data.get("rate_limit")
        if not isinstance(rate_limit, dict):
            return []
        windows: list[UsageWindow] = []
        specs = [
            ("primary_window", "5h", "5-hour"),
            ("secondary_window", "7d", "Weekly"),
        ]
        for source_key, fallback_key, fallback_label in specs:
            obj = rate_limit.get(source_key)
            if not isinstance(obj, dict):
                continue
            used = _normalize_percent(obj.get("used_percent"))
            resets = self._resolve_live_reset(obj)
            window_minutes = self._window_minutes(obj.get("limit_window_seconds"))
            key, label = self._window_identity(window_minutes, fallback_key, fallback_label)
            if used is None and resets is None:
                continue
            windows.append(
                UsageWindow(
                    key=key,
                    label=label,
                    used_percent=used,
                    resets_at=resets,
                    window_minutes=window_minutes,
                )
            )
        # Map Codex credits if present
        credits = data.get("credits")
        if isinstance(credits, dict) and (
            credits.get("has_credits") or credits.get("balance", "0") != "0"
        ):
            try:
                bal_str = credits.get("balance")
                if bal_str:
                    bal_val = float(bal_str)
                    windows.append(
                        UsageWindow(key="ai_credits", label="AI Credits", remaining=bal_val)
                    )
            except Exception:
                pass

        return windows

    @staticmethod
    def _window_minutes(seconds: Any) -> int | None:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            return None
        return int(seconds) // 60 or None

    @staticmethod
    def _window_identity(
        window_minutes: int | None, fallback_key: str, fallback_label: str
    ) -> tuple[str, str]:
        """Name Codex windows by duration, not their response position.

        Historically ``primary`` meant 5 hours and ``secondary`` meant one
        week. Codex can now omit the 5-hour limit and return the weekly limit
        as ``primary``, so the field position is only a compatibility fallback
        for older payloads that do not include a duration.
        """
        if window_minutes == 300:
            return "5h", "5-hour"
        if window_minutes == 10080:
            return "7d", "Weekly"
        # Derive from actual duration when it doesn't match a known value,
        # so a 30-day free-tier window is not silently labelled "5h".
        if window_minutes is not None and window_minutes > 0:
            if window_minutes % 1440 == 0:
                days = window_minutes // 1440
                return f"{days}d", f"{days}-day"
            if window_minutes % 60 == 0:
                hours = window_minutes // 60
                return f"{hours}h", f"{hours}-hour"
            return f"{window_minutes}m", f"{window_minutes}-minute"
        return fallback_key, fallback_label

    @staticmethod
    def _resolve_live_reset(obj: dict[str, Any]) -> str | None:
        reset_at = obj.get("reset_at")
        if isinstance(reset_at, (int, float)) and not isinstance(reset_at, bool) and reset_at > 0:
            return _normalize_reset(reset_at)
        after = obj.get("reset_after_seconds")
        if isinstance(after, (int, float)) and not isinstance(after, bool):
            return (datetime.now(UTC) + timedelta(seconds=float(after))).isoformat()
        return None

    # ── fallback: rollout-log snapshot ─────────────────────────────
    def _probe_logs(self) -> ProviderUsage:
        record = self._latest_rate_limit_record()
        if record is None:
            return ProviderUsage(
                provider="codex",
                status=STATUS_UNAVAILABLE,
                fetched_at=_now_iso(),
                detail=(
                    "No Codex rate-limit data found. Sign in to Codex, or run a "
                    "task so the CLI records its quota windows."
                ),
            )
        rate_limits, record_ts = record
        windows = self._map_log_windows(rate_limits)
        if not windows:
            return ProviderUsage(
                provider="codex",
                status=STATUS_UNAVAILABLE,
                fetched_at=_now_iso(),
                detail="Codex reported no rate-limit windows.",
            )
        plan = rate_limits.get("plan_type")
        return ProviderUsage(
            provider="codex",
            status=STATUS_OK,
            account_label=str(plan) if isinstance(plan, str) else "",
            windows=windows,
            fetched_at=record_ts or _now_iso(),
            detail="from last Codex run",
        )

    def _map_log_windows(self, rate_limits: dict[str, Any]) -> list[UsageWindow]:
        windows: list[UsageWindow] = []
        specs = [("primary", "5h", "5-hour"), ("secondary", "7d", "Weekly")]
        for source_key, fallback_key, fallback_label in specs:
            obj = rate_limits.get(source_key)
            if not isinstance(obj, dict):
                continue
            used = _normalize_percent(obj.get("used_percent"))
            resets = _normalize_reset(obj.get("resets_at"))
            window_minutes = obj.get("window_minutes")
            normalized_minutes = window_minutes if isinstance(window_minutes, int) else None
            key, label = self._window_identity(normalized_minutes, fallback_key, fallback_label)
            if used is None and resets is None:
                continue
            windows.append(
                UsageWindow(
                    key=key,
                    label=label,
                    used_percent=used,
                    resets_at=resets,
                    window_minutes=normalized_minutes,
                )
            )
        return windows

    def _latest_rate_limit_record(self) -> tuple[dict[str, Any], str] | None:
        sessions_dir = self._config.codex_sessions_dir
        if not sessions_dir.is_dir():
            return None
        files = sorted(
            sessions_dir.rglob("rollout-*.jsonl"),
            key=lambda p: self._safe_mtime(p),
            reverse=True,
        )
        for path in files[: max(1, self._config.codex_scan_files)]:
            found = self._scan_file(path)
            if found is not None:
                return found
        return None

    @staticmethod
    def _safe_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _scan_file(self, path: Path) -> tuple[dict[str, Any], str] | None:
        latest: tuple[dict[str, Any], str] | None = None
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or "rate_limits" not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = event.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    rate_limits = payload.get("rate_limits")
                    if isinstance(rate_limits, dict):
                        ts = event.get("timestamp")
                        latest = (rate_limits, ts if isinstance(ts, str) else "")
        except OSError:
            return None
        return latest


@dataclass
class CodexAccountUsage:
    """Server-side Codex token activity for account-level reconciliation."""

    lifetime_tokens: int | None = None
    daily_usage_buckets: dict[str, int] = field(default_factory=dict)
    fetched_at: str = ""


class CodexAccountUsageProbe:
    """Reads authoritative account token totals from the ChatGPT Codex backend.

    The profile endpoint only exposes lifetime and daily total-token counts. It
    intentionally cannot replace rollout parsing for model/input/cache detail;
    it is used to measure how much of the account activity is represented by
    the local, classifiable rollout history.
    """

    def __init__(self, config: ProbeConfig) -> None:
        self._config = config

    def probe(self) -> CodexAccountUsage | None:
        quota_probe = CodexUsageProbe(self._config)
        creds = quota_probe._read_auth()
        if creds is None:
            return None
        access_token, account_id = creds
        base = (self._config.codex_usage_base_url or CODEX_DEFAULT_BASE_URL).rstrip("/")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": self._config.codex_user_agent,
            "Accept": "application/json",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        data = quota_probe._http_get_json(f"{base}/wham/profiles/me", headers)
        stats = data.get("stats")
        if not isinstance(stats, dict):
            return None
        lifetime = stats.get("lifetime_tokens")
        buckets: dict[str, int] = {}
        raw_buckets = stats.get("daily_usage_buckets")
        if isinstance(raw_buckets, list):
            for bucket in raw_buckets:
                if not isinstance(bucket, dict):
                    continue
                start_date = bucket.get("start_date")
                tokens = bucket.get("tokens")
                if (
                    isinstance(start_date, str)
                    and start_date
                    and isinstance(tokens, (int, float))
                    and not isinstance(tokens, bool)
                ):
                    buckets[start_date[:10]] = buckets.get(start_date[:10], 0) + int(tokens)
        return CodexAccountUsage(
            lifetime_tokens=int(lifetime)
            if isinstance(lifetime, (int, float)) and not isinstance(lifetime, bool)
            else None,
            daily_usage_buckets=buckets,
            fetched_at=_now_iso(),
        )
