"""Interactive CLI login for WeChat iLink.

Usage::

    python -m fluxion.channels.wechat.wechat_login [--data-dir DATA_DIR]

The tool requests a QR code from iLink, prints it to the terminal, waits for
the user to scan and confirm in WeChat, then persists the credentials to
``$FLUXION_DATA_DIR/wechat_credentials.json``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fluxion.channels.wechat.credential_store import CredentialStore
from fluxion.channels.wechat.ilink_client import ILinkClient


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Bind a WeChat account to Fluxion via iLink QR-code login.",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="",
        help="Fluxion data directory (default: read from FLUXION_DATA_DIR or ./data)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON events to stdout instead of printing human-readable text and ASCII QR code.",
    )
    args = parser.parse_args(argv)

    # Resolve data dir
    if args.data_dir:
        data_dir = Path(args.data_dir).expanduser().resolve()
    else:
        import os

        raw = os.environ.get("FLUXION_DATA_DIR", "").strip()
        if raw:
            data_dir = Path(raw).expanduser().resolve()
        else:
            workspace_root = os.environ.get("FLUXION_WORKSPACE_ROOT", "").strip()
            if workspace_root:
                data_dir = Path(workspace_root).expanduser().resolve() / "data"
            else:
                data_dir = Path.cwd() / "data"

    store = CredentialStore(data_dir)
    existing = store.load()
    if existing is not None and not args.json:
        print(f"[Fluxion] Existing credentials found (bot_id={existing.ilink_bot_id}).")
        answer = input("Overwrite? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("[Fluxion] Aborted.")
            sys.exit(0)

    if not args.json:
        print("[Fluxion] Starting WeChat iLink QR-code login...")
        print("[Fluxion] Please scan the QR code below with WeChat.\n")

    client = ILinkClient()
    try:
        creds = client.login_interactive(json_mode=args.json)
    except RuntimeError as exc:
        if args.json:
            import json

            print(json.dumps({"event": "failed", "reason": str(exc)}), flush=True)
        else:
            print(f"\n[Fluxion] Login failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        if args.json:
            import json

            print(json.dumps({"event": "failed", "reason": "Canceled by user"}), flush=True)
        else:
            print("\n[Fluxion] Login canceled.")
        sys.exit(130)

    store.save(creds)
    if not args.json:
        print("\n[Fluxion] Login successful!")
        print(f"  bot_id:  {creds.ilink_bot_id}")
        print(f"  baseurl: {creds.baseurl}")
        print(f"  saved:   {store.path}")
        print("\nYou can now start Fluxion with FLUXION_WECHAT_ENABLED=true.")


if __name__ == "__main__":
    main()
