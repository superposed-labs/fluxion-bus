"""Locating the Antigravity IDE's local ``language_server`` sidecar.

Stateless helpers for the probe: read the app version and find the running
sidecar's PID, CSRF token, and listening ports. No probe state; the probe's
own discovery/spawn methods (which tests monkeypatch) stay on the class.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def resolve_version() -> str:
    # Default fallback version
    fallback_version = "2.1.4"
    plist_path = Path("/Applications/Antigravity.app/Contents/Info.plist")
    if plist_path.is_file():
        try:
            import plistlib

            with open(plist_path, "rb") as f:
                plist = plistlib.load(f)
                version = plist.get("CFBundleShortVersionString")
                if version:
                    return str(version)
        except Exception:
            pass
    return fallback_version


def find_process() -> tuple[int | None, str]:
    try:
        out = subprocess.run(
            ["ps", "-axww", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None, ""
    for line in out.splitlines():
        if (
            "language_server" in line
            and "override_ide_name antigravity" in line
            and "--csrf_token" in line
        ):
            try:
                pid = int(line.split(None, 1)[0])
            except ValueError:
                pid = None
            match = re.search(r"--csrf_token\s+(\S+)", line)
            return pid, (match.group(1) if match else "")
    return None, ""


def listening_ports(pid: int) -> list[int]:
    try:
        out = subprocess.run(
            ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    ports: list[int] = []
    for match in re.finditer(r"127\.0\.0\.1:(\d+)", out):
        port = int(match.group(1))
        if port not in ports:
            ports.append(port)
    return ports
