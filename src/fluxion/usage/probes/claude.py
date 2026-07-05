from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fluxion.usage.models import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    ProviderUsage,
    UsageWindow,
)
from fluxion.usage.probes._common import (
    CLAUDE_OAUTH_BETA,
    CLAUDE_USAGE_URL,
    ProbeConfig,
    _normalize_percent,
    _normalize_reset,
    _now_iso,
)


def _find_first(obj: Any, names: set[str]) -> Any:
    """Depth-first search for the first dict value whose key is in `names`."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in names:
                return value
        for value in obj.values():
            found = _find_first(value, names)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_first(value, names)
            if found is not None:
                return found
    return None


def _find_first_path(obj: Any, names: set[str]) -> list[Any] | None:
    if isinstance(obj, dict):
        for key in obj:
            if key in names:
                return [key]
        for key, value in obj.items():
            found = _find_first_path(value, names)
            if found is not None:
                return [key, *found]
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            found = _find_first_path(value, names)
            if found is not None:
                return [idx, *found]
    return None


def _set_first(obj: Any, names: set[str], value: Any, default_key: str) -> None:
    if not isinstance(obj, dict):
        return
    path = _find_first_path(obj, names)
    if path:
        target = obj
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        return
    access_path = _find_first_path(obj, {"accessToken", "access_token"})
    if access_path:
        target = obj
        for part in access_path[:-1]:
            target = target[part]
        if isinstance(target, dict):
            target[default_key] = value
            return
    obj[default_key] = value


def _coerce_epoch_seconds(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        # Claude Code stores expiresAt in epoch milliseconds.
        return seconds / 1000 if seconds > 10_000_000_000 else seconds
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return _coerce_epoch_seconds(float(raw))
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed.timestamp()
        except ValueError:
            return None
    return None


@dataclass
class _ClaudeCredential:
    access_token: str
    data: Any = None
    source: str = ""  # env | file | keychain
    path: Path | None = None
    account: str = ""  # macOS keychain account


class ClaudeUsageProbe:
    """Reads the subscription 5h/weekly windows from the OAuth usage endpoint.

    Uses the same locally-stored Claude Code OAuth token and request shape that
    the `claude` CLI's `/usage` view uses. Refresh/write-back is opt-in via
    ProbeConfig.claude_auto_refresh.
    """

    def __init__(self, config: ProbeConfig) -> None:
        self._config = config
        self._cred_sub_type = ""
        self._last_refresh_error = ""

    def provider(self) -> str:
        return "claude"

    def probe(self) -> ProviderUsage:
        self._cred_sub_type = ""  # captured from the credential during resolution
        self._last_refresh_error = ""
        credential = self._resolve_credential()
        if not credential.access_token:
            return ProviderUsage(
                provider="claude",
                status=STATUS_UNAVAILABLE,
                fetched_at=_now_iso(),
                detail=(
                    "No Claude OAuth token found. Claude Code stores its login in the "
                    "macOS Keychain — allow Keychain access in Fluxion Preferences (or "
                    "set FLUXION_CLAUDE_USAGE_KEYCHAIN=true) to read it, or provide a "
                    "token via FLUXION_CLAUDE_USAGE_TOKEN. Subscription/login auth "
                    "only, not API keys."
                ),
            )
        if self._should_refresh(credential):
            credential = self._refresh_credential(credential) or credential
        try:
            data = self._fetch(credential.access_token)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                refreshed = self._refresh_credential(credential)
                if refreshed:
                    try:
                        data = self._fetch(refreshed.access_token)
                    except urllib.error.HTTPError as retry_exc:
                        return self._http_error_usage(retry_exc)
                    except Exception as retry_exc:  # noqa: BLE001
                        return ProviderUsage(
                            provider="claude",
                            status=STATUS_ERROR,
                            fetched_at=_now_iso(),
                            detail=f"usage probe failed after token refresh: {retry_exc}",
                        )
                else:
                    detail = "usage endpoint returned HTTP 401"
                    if self._last_refresh_error:
                        detail += f"; token refresh failed: {self._last_refresh_error}"
                    return ProviderUsage(
                        provider="claude",
                        status=STATUS_ERROR,
                        fetched_at=_now_iso(),
                        detail=detail,
                    )
            else:
                return self._http_error_usage(exc)
        except Exception as exc:  # noqa: BLE001 - surface any probe failure as status
            return ProviderUsage(
                provider="claude",
                status=STATUS_ERROR,
                fetched_at=_now_iso(),
                detail=f"usage probe failed: {exc}",
            )

        windows = self._map_windows(data)
        # The usage endpoint omits the plan; the credential carries it (e.g. "pro").
        account = self._cred_sub_type or self._account_label(data)
        if not windows:
            return ProviderUsage(
                provider="claude",
                status=STATUS_UNAVAILABLE,
                account_label=account,
                fetched_at=_now_iso(),
                detail="usage endpoint returned no recognizable windows",
            )
        return ProviderUsage(
            provider="claude",
            status=STATUS_OK,
            account_label=account,
            windows=windows,
            fetched_at=_now_iso(),
        )

    def _http_error_usage(self, exc: urllib.error.HTTPError) -> ProviderUsage:
        hint = " (check FLUXION_CLAUDE_CODE_USER_AGENT)" if exc.code == 429 else ""
        return ProviderUsage(
            provider="claude",
            status=STATUS_ERROR,
            fetched_at=_now_iso(),
            detail=f"usage endpoint returned HTTP {exc.code}{hint}",
        )

    def _fetch(self, token: str) -> dict[str, Any]:
        req = urllib.request.Request(
            CLAUDE_USAGE_URL,
            method="GET",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": CLAUDE_OAUTH_BETA,
                "User-Agent": self._config.claude_user_agent,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self._config.http_timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}

    def _map_windows(self, data: dict[str, Any]) -> list[UsageWindow]:
        windows: list[UsageWindow] = []
        specs = [
            ("5h", "5-hour", {"five_hour", "fiveHour", "5h"}),
            ("7d", "Weekly", {"seven_day", "sevenDay", "7d", "weekly"}),
            ("agent_sdk", "Agent SDK", {"seven_day_oauth_apps", "sevenDayOauthApps"}),
        ]
        for key, label, names in specs:
            obj = data.get(next((n for n in names if n in data), ""), None)
            if not isinstance(obj, dict):
                continue
            used = _normalize_percent(
                _find_first(obj, {"utilization", "used_percent", "percent", "used"})
            )
            resets = _normalize_reset(_find_first(obj, {"resets_at", "reset_at", "resetsAt"}))
            if used is None and resets is None:
                continue
            windows.append(UsageWindow(key=key, label=label, used_percent=used, resets_at=resets))

        return windows

    def _account_label(self, data: dict[str, Any]) -> str:
        label = _find_first(data, {"subscription_type", "subscriptionType", "plan", "plan_type"})
        return str(label) if isinstance(label, str) else ""

    def _resolve_token(self) -> str:
        return self._resolve_credential().access_token

    def _resolve_credential(self) -> _ClaudeCredential:
        if self._config.claude_usage_token:
            return _ClaudeCredential(
                access_token=self._config.claude_usage_token.strip(), source="env"
            )
        credential = self._credential_from_file(self._config.claude_credentials_path)
        if credential.access_token:
            return credential
        # The macOS Keychain is only consulted when explicitly opted in.
        if self._config.claude_use_keychain and sys.platform == "darwin":
            credential = self._credential_from_keychain(self._config.claude_keychain_service)
            if credential.access_token:
                return credential
        return _ClaudeCredential(access_token="")

    def _token_from_file(self, path: Path) -> str:
        return self._credential_from_file(path).access_token

    def _credential_from_file(self, path: Path) -> _ClaudeCredential:
        try:
            if not path.is_file():
                return _ClaudeCredential(access_token="", source="file", path=path)
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _ClaudeCredential(access_token="", source="file", path=path)
        return _ClaudeCredential(
            access_token=self._access_token_from(data),
            data=data,
            source="file",
            path=path,
        )

    def _token_from_keychain(self, service: str) -> str:
        return self._credential_from_keychain(service).access_token

    def _credential_from_keychain(self, service: str) -> _ClaudeCredential:
        # Mirrors how Claude Code reads its own credential on macOS. Single,
        # canonical service name — no scanning.
        account = ""
        try:
            meta_out = subprocess.run(
                ["security", "find-generic-password", "-s", service],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if meta_out.returncode == 0:
                match = re.search(r'"acct"<blob>="([^"]+)"', meta_out.stdout)
                if match:
                    account = match.group(1)
        except Exception:
            pass

        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", service, "-w"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return _ClaudeCredential(access_token="", source="keychain")
        if out.returncode != 0 or not out.stdout.strip():
            return _ClaudeCredential(access_token="", source="keychain")
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            return _ClaudeCredential(
                access_token=out.stdout.strip(), source="keychain", account=account
            )
        return _ClaudeCredential(
            access_token=self._access_token_from(data),
            data=data,
            source="keychain",
            account=account,
        )

    def _access_token_from(self, data: Any) -> str:
        sub = _find_first(data, {"subscriptionType", "subscription_type"})
        if isinstance(sub, str) and sub.strip():
            self._cred_sub_type = sub.strip()
        token = _find_first(data, {"accessToken", "access_token"})
        return token.strip() if isinstance(token, str) else ""

    def _should_refresh(self, credential: _ClaudeCredential) -> bool:
        if not self._config.claude_auto_refresh:
            return False
        if not self._refresh_token_from(credential.data):
            return False
        expires = _find_first(credential.data, {"expiresAt", "expires_at", "expires"})
        expires_at = _coerce_epoch_seconds(expires)
        if expires_at is None:
            return False
        # Refresh one minute early to avoid a token expiring between resolve and fetch.
        return expires_at <= datetime.now(UTC).timestamp() + 60

    def _refresh_credential(self, credential: _ClaudeCredential) -> _ClaudeCredential | None:
        self._last_refresh_error = ""
        if not self._config.claude_auto_refresh:
            self._last_refresh_error = "auto refresh is disabled"
            return None
        if credential.source == "env":
            self._last_refresh_error = "FLUXION_CLAUDE_USAGE_TOKEN cannot be refreshed"
            return None
        refresh_token = self._refresh_token_from(credential.data)
        if not refresh_token:
            self._last_refresh_error = "no refreshToken found in Claude credential"
            return None
        try:
            refreshed = self._fetch_refreshed_tokens(refresh_token)
            updated_data = self._updated_credential_data(credential.data, refreshed)
            updated = _ClaudeCredential(
                access_token=self._access_token_from(updated_data),
                data=updated_data,
                source=credential.source,
                path=credential.path,
            )
            if not updated.access_token:
                self._last_refresh_error = 'refresh response had no "access_token"'
                return None
            if not self._write_credential(updated):
                return None
            return updated
        except Exception as exc:  # noqa: BLE001 - expose refresh failure as probe detail
            self._last_refresh_error = str(exc)
            return None

    def _refresh_token_from(self, data: Any) -> str:
        token = _find_first(data, {"refreshToken", "refresh_token"})
        return token.strip() if isinstance(token, str) else ""

    def _fetch_refreshed_tokens(self, refresh_token: str) -> dict[str, Any]:
        body = json.dumps(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._config.claude_client_id,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self._config.claude_refresh_url,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": self._config.claude_user_agent,
                "anthropic-beta": CLAUDE_OAUTH_BETA,
            },
        )
        with urllib.request.urlopen(req, timeout=self._config.http_timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}

    def _updated_credential_data(self, data: Any, refreshed: dict[str, Any]) -> Any:
        if not isinstance(data, dict):
            data = {}
        updated = json.loads(json.dumps(data))
        access_token = refreshed.get("access_token") or refreshed.get("accessToken")
        if isinstance(access_token, str) and access_token.strip():
            _set_first(
                updated, {"accessToken", "access_token"}, access_token.strip(), "accessToken"
            )
        refresh_token = refreshed.get("refresh_token") or refreshed.get("refreshToken")
        if isinstance(refresh_token, str) and refresh_token.strip():
            _set_first(
                updated, {"refreshToken", "refresh_token"}, refresh_token.strip(), "refreshToken"
            )
        expires_in = refreshed.get("expires_in") or refreshed.get("expiresIn")
        if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
            expires_ms = int((datetime.now(UTC).timestamp() + float(expires_in)) * 1000)
            _set_first(updated, {"expiresAt", "expires_at", "expires"}, expires_ms, "expiresAt")
        return updated

    def _write_credential(self, credential: _ClaudeCredential) -> bool:
        try:
            payload = json.dumps(credential.data, separators=(",", ":"))
            if credential.source == "file" and credential.path:
                credential.path.write_text(payload + "\n", encoding="utf-8")
                return True
            if credential.source == "keychain" and sys.platform == "darwin":
                acct = credential.account or os.environ.get("USER") or ""
                if not acct:
                    try:
                        acct = subprocess.run(
                            ["id", "-un"], capture_output=True, text=True, timeout=2
                        ).stdout.strip()
                    except Exception:
                        acct = "claude"
                out = subprocess.run(
                    [
                        "security",
                        "add-generic-password",
                        "-U",
                        "-s",
                        self._config.claude_keychain_service,
                        "-a",
                        acct,
                        # `-p` passes the token as a command-line argument, where a
                        # same-uid process could observe it via `ps` during the brief
                        # life of this subprocess. Accepted: this path is darwin-only,
                        # and any same-uid attacker can already read the Keychain item
                        # (or the on-disk credential) directly, so there is no added
                        # exposure over what they already have; root is moot for the
                        # same reason. We do NOT pipe the token to the interactive
                        # stdin `-w` prompt instead because that path silently
                        # truncates secrets at 128 bytes (verified), which would
                        # corrupt the real (>128B) credential while reporting success.
                        "-p",
                        payload,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if out.returncode == 0:
                    return True
                self._last_refresh_error = out.stderr.strip() or "failed to update Keychain"
                return False
        except (OSError, subprocess.SubprocessError) as exc:
            self._last_refresh_error = str(exc)
            return False
        self._last_refresh_error = f"unsupported credential source: {credential.source}"
        return False
