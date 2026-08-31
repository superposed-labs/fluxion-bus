"""Small JSON CLI used by the macOS Preferences workspace page.

The desktop app delegates validation and atomic persistence to the Python
service instead of maintaining a second, subtly different path policy in Swift.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from fluxion.config.settings import Settings
from fluxion.workspace import WorkspaceAccessService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Fluxion workspace access")
    parser.add_argument("--json", action="store_true", help="emit JSON (the default)")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="list effective and App-managed workspaces")

    add = commands.add_parser("add", help="add an App-managed workspace")
    add.add_argument("--path", required=True)
    add.add_argument("--key", default="")
    add.add_argument("--access", default="read-write")
    add.add_argument("--default-executor", default="")
    add.add_argument("--description", default="")

    update = commands.add_parser("update", help="update an App-managed workspace")
    update.add_argument("entry_id")
    update.add_argument("--path")
    update.add_argument("--key")
    update.add_argument("--access")
    update.add_argument("--default-executor")
    update.add_argument("--description")

    delete = commands.add_parser("delete", help="delete an App-managed workspace")
    delete.add_argument("entry_id")

    approve = commands.add_parser("approve", help="approve one exact pending request")
    approve.add_argument("request_id")
    approve.add_argument("--path")
    approve.add_argument("--mode")
    approve.add_argument("--client-id")

    allow_project = commands.add_parser(
        "allow-project", help="create persistent project access from one exact request"
    )
    allow_project.add_argument("request_id")
    allow_project.add_argument("--path")
    allow_project.add_argument("--mode")
    allow_project.add_argument("--client-id")
    allow_project.add_argument("--access")
    allow_project.add_argument("--key", default="")
    allow_project.add_argument("--default-executor", default="")
    allow_project.add_argument("--description", default="")

    deny = commands.add_parser("deny", help="deny a pending request")
    deny.add_argument("request_id")
    return parser


def _run(args: argparse.Namespace) -> dict[str, Any]:
    service = WorkspaceAccessService(Settings.reload())
    if args.command == "list":
        return service.list_workspaces()
    if args.command == "add":
        return service.create_entry(
            path=args.path,
            key=args.key,
            access=args.access,
            default_executor=args.default_executor,
            description=args.description,
        )
    if args.command == "update":
        return service.update_entry(
            args.entry_id,
            path=args.path,
            key=args.key,
            access=args.access,
            default_executor=args.default_executor,
            description=args.description,
        )
    if args.command == "delete":
        return service.delete_entry(args.entry_id)
    if args.command == "approve":
        return service.approve_request(
            args.request_id,
            path=args.path,
            mode=args.mode,
            client_id=args.client_id,
        )
    if args.command == "allow-project":
        return service.allow_request_as_project(
            args.request_id,
            path=args.path,
            mode=args.mode,
            client_id=args.client_id,
            access=args.access,
            key=args.key,
            default_executor=args.default_executor,
            description=args.description,
        )
    if args.command == "deny":
        return service.deny_request(args.request_id)
    raise ValueError(f"Unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _run(args)
    except Exception as error:
        result = {"success": False, "error": str(error)}
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
