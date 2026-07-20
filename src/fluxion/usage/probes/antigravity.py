from __future__ import annotations

import json
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fluxion.usage.models import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    ProviderUsage,
    UsageWindow,
)
from fluxion.usage.probes import _antigravity_discovery as discovery
from fluxion.usage.probes import _antigravity_mapping as mapping
from fluxion.usage.probes._common import ProbeConfig, _now_iso, logger


class AntigravityUsageProbe:
    """Reads credit balances from the Antigravity IDE's local sidecar.

    The IDE runs a `language_server` sidecar that holds the login and exposes a
    local Connect API (the same one the IDE's quota UI calls). We auto-discover
    its CSRF token (from the process args) and listening port (via lsof), then
    call GetUserStatus. Local-only and read-only; works only while the
    Antigravity IDE is running.

    Surfaces what the IDE's own quota UI shows: the AI Credits pool, grouped
    5h/Weekly quota summary when supported by the sidecar, and legacy per-model
    time-based quota for older sidecars.

    JSON→window mapping lives in ``_antigravity_mapping``; sidecar location in
    ``_antigravity_discovery``. This class is the network/sidecar orchestrator.
    """

    GET_USER_STATUS = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
    RETRIEVE_USER_QUOTA_SUMMARY = (
        "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
    )
    CSRF_HEADER = "x-codeium-csrf-token"

    def __init__(self, config: ProbeConfig) -> None:
        self._config = config

    def provider(self) -> str:
        return "antigravity"

    def probe(self) -> ProviderUsage:
        fallback_reason = ""

        # 1. Prioritize direct cloud API query
        token = self._read_active_token()
        if token:
            try:
                result = self._query_cloud_api(token)
                if result is not None:
                    summary, assist = result
                    windows = mapping.map_quota_summary(summary)
                    if any(window.used_percent is not None for window in windows):
                        paid_tier = assist.get("paidTier")
                        plan = (
                            str(paid_tier.get("name") or "Pro")
                            if isinstance(paid_tier, dict)
                            else "Pro"
                        )
                        if isinstance(paid_tier, dict):
                            for credit in paid_tier.get("availableCredits") or []:
                                if not isinstance(credit, dict):
                                    continue
                                amount = mapping.num(credit.get("creditAmount"))
                                if amount is not None:
                                    windows.append(
                                        UsageWindow(
                                            key="ai_credits",
                                            label="AI Credits",
                                            remaining=amount,
                                        )
                                    )
                                    break
                        return ProviderUsage(
                            provider="antigravity",
                            status=STATUS_OK,
                            account_label=plan,
                            windows=windows,
                            fetched_at=_now_iso(),
                            detail="live",
                            source="cloud",
                        )
                    logger.warning(
                        "Antigravity cloud quota response contained no recognized "
                        "quota buckets; keys=%s; falling back to sidecar",
                        sorted(summary),
                    )
                    fallback_reason = "cloud response contained no recognized quota buckets"
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    fallback_reason = "cloud HTTP 401"
                else:
                    # If it's a transient network/HTTP error other than 401, return error
                    # instead of spawning sidecar to avoid spamming subprocesses when offline
                    return ProviderUsage(
                        provider="antigravity",
                        status=STATUS_ERROR,
                        fetched_at=_now_iso(),
                        detail=f"Cloud API HTTP error {exc.code}",
                    )
            except Exception as exc:
                # Other transient network exceptions (socket timeout etc), proceed to fallback
                fallback_reason = f"cloud request failed: {type(exc).__name__}"
        else:
            fallback_reason = "cloud token unavailable"

        # 2. Fallback to local sidecar query (spawns it on-demand if closed)
        discovered = self._discover()
        spawned_proc = None

        if discovered is None:
            binary = self._config.antigravity_sidecar_path
            if binary and binary.exists() and binary.is_file():
                try:
                    discovered, spawned_proc = self._spawn_sidecar(binary)
                except Exception as exc:
                    return ProviderUsage(
                        provider="antigravity",
                        status=STATUS_UNAVAILABLE,
                        fetched_at=_now_iso(),
                        detail=f"Failed to spawn Antigravity sidecar: {exc}",
                    )

        if discovered is None:
            return ProviderUsage(
                provider="antigravity",
                status=STATUS_UNAVAILABLE,
                fetched_at=_now_iso(),
                detail="Antigravity IDE not running (local sidecar not found).",
            )

        csrf, ports = discovered
        last_err = ""
        try:
            for port in ports[:8]:
                try:
                    data = self._get_user_status(port, csrf)
                except Exception as exc:  # noqa: BLE001 - wrong port/non-HTTP, try next
                    last_err = str(exc)
                    continue
                windows, plan = mapping.map_user_status(data)
                try:
                    quota_summary = self._get_quota_summary(port, csrf)
                    summary_windows = mapping.map_quota_summary(quota_summary)
                    windows = mapping.merge_sidecar_summary(windows, summary_windows)
                except Exception as exc:  # noqa: BLE001 - older sidecars lack this RPC
                    logger.debug("Antigravity sidecar quota summary unavailable: %s", exc)
                if windows:
                    return ProviderUsage(
                        provider="antigravity",
                        status=STATUS_OK,
                        account_label=plan,
                        windows=windows,
                        fetched_at=_now_iso(),
                        detail="live",
                        source="sidecar",
                        source_reason=fallback_reason,
                    )
                last_err = "sidecar returned no credit fields"
            return ProviderUsage(
                provider="antigravity",
                status=STATUS_ERROR if last_err else STATUS_UNAVAILABLE,
                fetched_at=_now_iso(),
                detail=last_err or "sidecar reachable but no quota data",
            )
        finally:
            if spawned_proc is not None:
                self._terminate_process_instance(spawned_proc)

    def _read_active_token(self) -> str | None:
        if "pytest" in sys.modules:
            return None
        # Try macOS Keychain first
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            )
            pwd = out.stdout.strip()
            if pwd.startswith("go-keyring-base64:"):
                import base64

                encoded = pwd.split(":", 1)[1]
                data = json.loads(base64.b64decode(encoded).decode("utf-8"))
                token = data.get("token", {}).get("access_token")
                if token:
                    return str(token)
        except Exception:
            pass

        # Fallback to ~/.gemini/oauth_creds.json
        try:
            creds_path = Path.home() / ".gemini" / "oauth_creds.json"
            if creds_path.is_file():
                creds = json.loads(creds_path.read_text(encoding="utf-8"))
                token = creds.get("access_token")
                if token:
                    return str(token)
        except Exception:
            pass

        return None

    def _query_cloud_api(self, token: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        version = discovery.resolve_version()

        def call_endpoint(endpoint: str) -> dict[str, Any] | None:
            url = f"https://daily-cloudcode-pa.googleapis.com/v1internal:{endpoint}"
            req = urllib.request.Request(
                url,
                data=b"{}",
                method="POST",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": f"antigravity/{version}",
                },
            )
            timeout = min(self._config.http_timeout_sec, 4.0)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                parsed = json.loads(resp.read().decode("utf-8"))
                return parsed if isinstance(parsed, dict) else {}

        try:
            summary = call_endpoint("retrieveUserQuotaSummary")
            if not summary:
                return None
            try:
                assist = call_endpoint("loadCodeAssist")
            except Exception:
                assist = {}
            return summary, assist or {}
        except urllib.error.HTTPError as exc:
            raise exc
        except Exception:
            return None

    def _spawn_sidecar(self, binary: Path) -> tuple[tuple[str, list[int]], subprocess.Popen[str]]:
        import socket
        import time
        import uuid

        port = 0
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
        except Exception:
            port = 50222

        csrf = str(uuid.uuid4())

        proc = subprocess.Popen(
            [
                str(binary),
                "--standalone",
                "--override_ide_name",
                "antigravity",
                "--subclient_type",
                "hub",
                "--override_ide_version",
                discovery.resolve_version(),
                "--override_user_agent_name",
                "antigravity",
                "--https_server_port",
                str(port),
                "--csrf_token",
                csrf,
                "--app_data_dir",
                "antigravity",
                "--api_server_url",
                "https://generativelanguage.googleapis.com",
                "--cloud_code_endpoint",
                "https://daily-cloudcode-pa.googleapis.com",
                "--enable_sidecars",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        start_time = time.monotonic()
        connected = False
        while time.monotonic() - start_time < 4.0:
            if proc.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    connected = True
                    break
            except (TimeoutError, ConnectionRefusedError, OSError):
                time.sleep(0.1)

        if not connected:
            self._terminate_process_instance(proc)
            raise RuntimeError("sidecar process started but did not open port in time")

        return (csrf, [port]), proc

    def _terminate_process_instance(self, proc: subprocess.Popen[str]) -> None:
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.5)
        except Exception:
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _get_user_status(self, port: int, csrf: str) -> dict[str, Any]:
        return self._call_sidecar(port, csrf, self.GET_USER_STATUS)

    def _get_quota_summary(self, port: int, csrf: str) -> dict[str, Any]:
        data = self._call_sidecar(port, csrf, self.RETRIEVE_USER_QUOTA_SUMMARY)
        response = data.get("response")
        return response if isinstance(response, dict) else data

    def _call_sidecar(self, port: int, csrf: str, path: str) -> dict[str, Any]:
        url = f"https://{self._config.antigravity_host}:{port}{path}"
        req = urllib.request.Request(
            url,
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json", self.CSRF_HEADER: csrf},
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # local self-signed sidecar cert
        timeout = min(self._config.http_timeout_sec, 4.0)
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}

    # ── discovery: find the running sidecar's csrf token + ports ───
    def _discover(self) -> tuple[str, list[int]] | None:
        csrf = self._config.antigravity_csrf_token
        ports: list[int] = [self._config.antigravity_port] if self._config.antigravity_port else []
        if csrf and ports:
            return csrf, ports
        pid, found_csrf = discovery.find_process()
        csrf = csrf or found_csrf
        if not csrf:
            return None
        if not ports:
            if pid is None:
                return None
            ports = discovery.listening_ports(pid)
        return (csrf, ports) if ports else None
