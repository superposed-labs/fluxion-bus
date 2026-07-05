"""Pure JSON → :class:`UsageWindow` transforms for the Antigravity probe.

These functions hold no I/O and no probe state; they are split out of
``antigravity.py`` so the probe class stays a thin orchestrator. The probe's
network/sidecar methods (which tests monkeypatch) remain on the class.
"""

from __future__ import annotations

from typing import Any

from fluxion.usage.models import UsageWindow
from fluxion.usage.probes._common import ProbeConfig


def num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def matches(label: str, wanted: tuple[str, ...]) -> bool:
    if not wanted:
        return True
    low = label.lower()
    return any(w.lower() in low for w in wanted)


def map_quota_summary(summary: dict[str, Any]) -> list[UsageWindow]:
    windows: list[UsageWindow] = []
    buckets: list[Any] = []
    groups = summary.get("groups")
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("buckets"), list):
                buckets.extend(group["buckets"])
    if not buckets and isinstance(summary.get("buckets"), list):
        buckets.extend(summary["buckets"])

    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        bucket_id = str(bucket.get("bucketId") or "")
        window_type = str(bucket.get("window") or "")
        description = str(bucket.get("description") or "")
        frac = num(bucket.get("remainingFraction"))
        reset = bucket.get("resetTime")

        if frac is None:
            continue

        used = round(min(100.0, max(0.0, 100.0 * (1 - frac))), 1)
        is_gemini = "gemini" in bucket_id.lower()
        description_lower = description.lower()
        is_weekly = (
            "weekly" in bucket_id.lower()
            or window_type.lower() == "weekly"
            or (
                not window_type
                and (
                    " day" in description_lower
                    or " days" in description_lower
                    or "7d" in description_lower
                )
            )
        )

        if is_gemini:
            key = "Gemini (Weekly)" if is_weekly else "Gemini (5h)"
            label = "Gemini Models (Weekly)" if is_weekly else "Gemini Models (5h)"
        else:
            key = "External Models (Weekly)" if is_weekly else "External Models (5h)"
            label = "Claude/GPT Models (Weekly)" if is_weekly else "Claude/GPT Models (5h)"

        windows.append(
            UsageWindow(
                key=key,
                label=label,
                used_percent=used,
                resets_at=reset if isinstance(reset, str) and reset else None,
                # Carry the window length so idle-vs-anchored detection works for
                # Antigravity too: like Codex, a full/idle window reports
                # resetTime = now + window (drifting), an active one a fixed
                # instant. The raw `window` field is "5h" or "weekly".
                window_minutes=10080 if is_weekly else 300,
            )
        )

    windows = _dedupe_quota_summary_windows(windows)

    # Sort windows consistently: 5h -> Weekly
    order = {
        "Gemini (5h)": 1,
        "External Models (5h)": 2,
        "Gemini (Weekly)": 3,
        "External Models (Weekly)": 4,
    }
    windows.sort(key=lambda w: order.get(w.key, 99))
    return windows


def _dedupe_quota_summary_windows(windows: list[UsageWindow]) -> list[UsageWindow]:
    """Collapse duplicate cloud summary buckets without merging distinct windows.

    Starter accounts can return one bucket per underlying model even though
    each bucket maps to the same Gemini/External quota row. Keep separate
    Gemini vs External and 5h vs Weekly rows, but merge exact logical repeats.
    """

    by_key: dict[str, UsageWindow] = {}
    order: list[str] = []
    for window in windows:
        key = window.key
        if key not in by_key:
            by_key[key] = window
            order.append(key)
            continue

        current = by_key[key]
        used_values = [v for v in [current.used_percent, window.used_percent] if v is not None]
        used = max(used_values) if used_values else None

        resets = sorted(
            reset
            for reset in [current.resets_at, window.resets_at]
            if isinstance(reset, str) and reset
        )
        by_key[key] = UsageWindow(
            key=current.key,
            label=current.label,
            used_percent=used,
            resets_at=resets[0] if resets else None,
            window_minutes=current.window_minutes or window.window_minutes,
            remaining=current.remaining,
            total=current.total,
        )

    return [by_key[key] for key in order]


def merge_sidecar_summary(
    legacy_windows: list[UsageWindow],
    summary_windows: list[UsageWindow],
    group_models: bool,
) -> list[UsageWindow]:
    if not summary_windows:
        return legacy_windows

    credits = [window for window in legacy_windows if window.key == "ai_credits"]
    if group_models:
        return credits + summary_windows

    # Individual-model mode keeps its existing 5h detail, but the new
    # sidecar summary is the only authoritative local source for Weekly.
    weekly = [window for window in summary_windows if "Weekly" in window.key]
    return legacy_windows + weekly


def map_user_status(data: dict[str, Any], config: ProbeConfig) -> tuple[list[UsageWindow], str]:
    user_status = data.get("userStatus")
    if not isinstance(user_status, dict):
        return [], ""
    plan_info = (user_status.get("planStatus") or {}).get("planInfo") or {}
    plan_name = plan_info.get("planName") if isinstance(plan_info, dict) else ""

    windows: list[UsageWindow] = []

    # AI Credits — the GOOGLE_ONE_AI overage pool the IDE shows up top.
    user_tier = user_status.get("userTier") or {}
    for credit in user_tier.get("availableCredits") or []:
        if not isinstance(credit, dict):
            continue
        amount = num(credit.get("creditAmount"))
        if amount is None:
            continue
        windows.append(UsageWindow(key="ai_credits", label="AI Credits", remaining=amount))
        break  # primary credit pool only

    # Per-model time-based quota — quotaInfo.{remainingFraction, resetTime}.
    configs = (user_status.get("cascadeModelConfigData") or {}).get("clientModelConfigs") or []
    if config.antigravity_group_models:
        gemini_cfg = None
        third_party_cfg = None
        for cfg in configs:
            if not isinstance(cfg, dict):
                continue
            label = str(cfg.get("label") or "")
            if not label:
                continue
            quota = cfg.get("quotaInfo") or {}
            if not isinstance(quota, dict):
                continue
            frac = quota.get("remainingFraction")
            reset = quota.get("resetTime")
            has_reset = isinstance(reset, str) and bool(reset)
            if (frac is None or num(frac) is None) and not has_reset:
                continue

            if "gemini" in label.lower():
                if gemini_cfg is None:
                    gemini_cfg = cfg
            else:
                if third_party_cfg is None:
                    third_party_cfg = cfg

        if gemini_cfg:
            quota = gemini_cfg.get("quotaInfo") or {}
            frac = num(quota.get("remainingFraction"))
            reset = quota.get("resetTime")
            used = (
                100.0
                if frac is None and isinstance(reset, str) and reset
                else None
                if frac is None
                else round(min(100.0, max(0.0, 100.0 * (1 - frac))), 1)
            )
            windows.append(
                UsageWindow(
                    key="Gemini",
                    label="Gemini",
                    used_percent=used,
                    resets_at=reset if isinstance(reset, str) and reset else None,
                )
            )

        if third_party_cfg:
            quota = third_party_cfg.get("quotaInfo") or {}
            frac = num(quota.get("remainingFraction"))
            used = None if frac is None else round(min(100.0, max(0.0, 100.0 * (1 - frac))), 1)
            reset = quota.get("resetTime")
            windows.append(
                UsageWindow(
                    key="External Models",
                    label="External Models",
                    used_percent=used,
                    resets_at=reset if isinstance(reset, str) and reset else None,
                )
            )
    else:
        wanted = config.antigravity_models
        for cfg in configs:
            if not isinstance(cfg, dict):
                continue
            label = str(cfg.get("label") or "")
            if not label or not matches(label, wanted):
                continue
            quota = cfg.get("quotaInfo") or {}
            frac = num(quota.get("remainingFraction"))
            used = None if frac is None else round(min(100.0, max(0.0, 100.0 * (1 - frac))), 1)
            reset = quota.get("resetTime")
            windows.append(
                UsageWindow(
                    key=label,
                    label=label,
                    used_percent=used,
                    resets_at=reset if isinstance(reset, str) and reset else None,
                )
            )
    return windows, str(plan_name or "")
