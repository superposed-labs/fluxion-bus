"""Shared allowlist rejection copy for IM channel adapters."""

from __future__ import annotations

import platform

from fluxion.i18n import t


def format_allowlist_rejection(
    *,
    reason: str,
    user_id: str,
    id_label: str,
    env_key: str,
    locale: str = "en",
    macos_pending_location: str | None = None,
) -> str:
    """Build a user-facing allowlist rejection message.

    The gateway knows which OS it is running on, so macOS-only Preferences
    guidance should be emitted only on macOS instead of using vague conditional
    copy in the IM message.
    """
    is_allowlist = "allowlist" in reason.lower()
    if not is_allowlist:
        return t(locale, "allowlist.rejected.generic", reason=reason)
    lines = [t(locale, "allowlist.rejected.allowlist")]
    if not user_id:
        return "\n".join(lines)

    lines.extend(
        [
            "",
            t(locale, "allowlist.ask_admin", id_label=id_label, env_key=env_key),
            user_id,
            "",
        ]
    )
    if macos_pending_location and platform.system() == "Darwin":
        lines.append(t(locale, "allowlist.macos_pending", location=macos_pending_location))
    lines.append(t(locale, "allowlist.env_reload"))
    return "\n".join(lines)
