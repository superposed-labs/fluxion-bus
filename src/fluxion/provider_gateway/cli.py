"""`fluxion-provider` command line.

Commands that touch the user's Codex configuration always show a diff and ask
before writing, and always leave a backup. Getting this wrong breaks the user's
Codex install, not just Fluxion's feature.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path

from fluxion.config.settings import load_dotenv
from fluxion.provider_gateway import codex_catalog, codex_config
from fluxion.provider_gateway.auth import check_bind, load_or_create_token
from fluxion.provider_gateway.codex_config import CodexConfigError
from fluxion.provider_gateway.config import (
    ConfigError,
    GatewaySettings,
    RoutingConfig,
)
from fluxion.provider_gateway.model_catalog import (
    describe_missing,
    verify_configured_models,
)
from fluxion.provider_gateway.sticky import StickyStore
from fluxion.utils import macos_notify

_STARTER_CONFIG = {
    "version": 1,
    "providers": [
        {
            "id": "local_claude",
            # Backed by the Claude Code CLI on the user's own subscription
            # rather than a metered API. Capability flags are granted
            # automatically for local agents — see config.py.
            "protocol": "local_agent",
            "executor": "claude",
            "enabled": True,
            # Where the agent runs when a request carries no workspace. Codex
            # normally reports its git repo root, so this is only the fallback;
            # leave it empty to have such requests refused rather than run in
            # the wrong repository.
            "default_workspace": "",
            "models": [{"id": "opus", "capabilities": {"max_context_tokens": 200000}}],
        }
    ],
    "policies": {
        "balanced": {"candidates": ["local_claude:opus"]},
    },
    "routes": {
        "auto": "balanced",
        "explorer": "balanced",
        "reviewer": "balanced",
        "worker": "balanced",
        "compaction": "balanced",
    },
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    # Every FLUXION_PROVIDER_* setting is documented as an environment variable,
    # and users put those in `.env` alongside the rest of Fluxion's config. Read
    # it here rather than only where `Settings` happens to be constructed: an
    # unattended `check-models` that ignores `.env` silently uses defaults, so a
    # setting the user believes is on is simply not.
    load_dotenv()
    try:
        return args.handler(args)
    except (ConfigError, CodexConfigError) as err:
        return _error(str(err))


# ── commands ─────────────────────────────────────────────────────────
def _init(args: argparse.Namespace) -> int:
    settings = GatewaySettings.load()
    token = load_or_create_token(settings.token_file)
    print(f"token: {settings.token_file} (mode 600)")

    config_path = settings.config_file
    if config_path.exists():
        # Never overwrite: this file holds the user's provider and policy work.
        print(f"routing config already exists, left untouched: {config_path}")
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(_STARTER_CONFIG, indent=2) + "\n", encoding="utf-8")
        print(f"routing config: {config_path}")
        print("  It routes every role to the local Claude Code CLI. Adjust models,")
        print("  add roles, or set default_workspace before starting.")
        print("  For the fuller shape — several executors, per-role policies —")
        print("  see config/provider_routes.example.json.")

    print(f"\nToken (for reference, do not paste into config files): {token[:6]}…")
    print("Next: `fluxion-provider print-codex-config` to see the Codex side.")
    return 0


def _serve(args: argparse.Namespace) -> int:
    from fluxion.provider_gateway.app import main as serve_main

    return serve_main()


def _doctor(args: argparse.Namespace) -> int:
    settings = GatewaySettings.load()
    problems: list[str] = []
    notes: list[str] = []

    try:
        check_bind(settings.host, token=str(settings.token_file))
        notes.append(f"bind {settings.host}:{settings.port} is safe")
    except Exception as err:  # noqa: BLE001 - reported, not raised
        problems.append(str(err))

    if settings.token_file.exists():
        mode = settings.token_file.stat().st_mode & 0o777
        if mode & 0o077:
            problems.append(f"token file {settings.token_file} is mode {mode:o}; should be 600")
        else:
            notes.append(f"token file {settings.token_file} (mode {mode:o})")
    else:
        problems.append(f"token file missing: {settings.token_file} — run `init`")

    if _port_in_use(settings.host, settings.port):
        problems.append(f"port {settings.port} is already in use; another gateway may be running")
    else:
        notes.append(f"port {settings.port} is free")

    try:
        routing = RoutingConfig.load(settings.config_file)
        notes.append(f"routing config parsed: {len(routing.providers)} provider(s)")
        from fluxion.config.settings import Settings
        from fluxion.executors.base import enforces_read_only
        from fluxion.executors.registry import build_all_executors

        executors = build_all_executors(Settings.load())
        for provider_id, spec in routing.providers.items():
            if not spec.enabled:
                notes.append(f"  {provider_id}: disabled")
                continue
            if spec.executor not in executors:
                problems.append(
                    f"  {provider_id}: executor {spec.executor!r} is not registered "
                    f"(available: {', '.join(sorted(executors))})"
                )
                continue
            notes.append(f"  {provider_id}: local agent via {spec.executor!r}")
        problems.extend(_read_only_problems(routing, executors, enforces_read_only))
        model_problems, model_notes = _model_catalog_report(routing)
        problems.extend(model_problems)
        notes.extend(model_notes)
    except ConfigError as err:
        problems.append(str(err))

    for note in notes:
        print(f"ok   {note}")
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    return 1 if problems else 0


def _check_models(args: argparse.Namespace) -> int:
    """Verify configured model ids against the CLIs' own catalogs.

    Separate from `doctor` so it can run unattended. `doctor` reports a bound
    port as a problem — correct when you are about to start a gateway, wrong for
    a scheduled check, where a gateway already running is the normal case and
    would make every run exit non-zero.
    """
    settings = GatewaySettings.load()
    if not settings.config_file.exists():
        # Nothing is configured, so there is nothing to verify. Exiting zero is
        # the whole point of this branch: most installs never set up the provider
        # gateway, and telling them daily that something is wrong trains everyone
        # to ignore the one run that matters. `doctor` still reports a missing
        # config as a problem — there you are about to start a gateway.
        print(f"ok   no routing config at {settings.config_file}; nothing to check")
        return 0

    routing = RoutingConfig.load(settings.config_file)
    # The user's own Codex catalog override is a separate subject, but it has to
    # be settled first: a `model_catalog_json` *is* the model list Codex serves,
    # so a stale one makes the id verification below report configured models as
    # missing. In `refresh` mode that would mean a spurious routing finding on
    # every run that repaired a snapshot.
    catalog_problems, notes = codex_catalog.report(settings.codex_catalog_drift)
    routing_problems, routing_notes = _model_catalog_report(routing)
    notes.extend(routing_notes)
    problems = routing_problems + catalog_problems
    for note in notes:
        print(f"ok   {note}")
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    # Tied to the routing findings specifically: printing it for a stale catalog
    # would send the user to edit the wrong file.
    if routing_problems:
        print(
            "\nA retired model id fails every turn on its route, and a policy's "
            "`fallback` does not cover it: the gateway makes one attempt per turn "
            "by design. Edit the routing config and restart the gateway.",
            file=sys.stderr,
        )
    if getattr(args, "notify", False):
        _notify_findings(problems, bool(routing_problems))
    return 1 if problems else 0


_NOTIFY_KEY = "check-models"


def _notify_findings(problems: list[str], has_routing_problem: bool) -> None:
    """Hand the findings to the desktop app, at most once per finding per day.

    Naming which of the two subjects fired matters more than listing every line:
    a retired model id and a stale catalog snapshot are fixed in different files,
    and a notification that sends the user to the wrong one wastes the trip.
    """
    data_dir = _data_dir()
    if not problems:
        macos_notify.clear_throttle(data_dir, _NOTIFY_KEY)
        return
    title = (
        "Fluxion: model id retired"
        if has_routing_problem
        else "Fluxion: Codex model catalog is stale"
    )
    body = "\n".join(problems[:3])
    fingerprint = hashlib.sha256("\n".join(sorted(problems)).encode()).hexdigest()[:16]
    result = macos_notify.queue_throttled(
        data_dir, _NOTIFY_KEY, title, body, fingerprint=fingerprint
    )
    # Name the file. Only the desktop app's own data directory is watched, and a
    # CLI run from a second checkout resolves a different one — the notification
    # would then sit in a file nothing consumes, with the check itself looking
    # entirely healthy. Printing the path puts that in the log of every run.
    print(f"ok   notification {result}: {data_dir / macos_notify.FILENAME}")


def _data_dir() -> Path:
    """Where the notification signal file lives.

    Falls back to the environment alone: a broken or half-written settings file
    must not stop a check from reporting, and this needs one path, not the whole
    settings object.
    """
    try:
        from fluxion.config.settings import Settings

        return Settings.load().data_dir
    except Exception:  # noqa: BLE001
        return Path(os.environ.get("FLUXION_DATA_DIR", "data")).expanduser()


def _install_codex_catalog(args: argparse.Namespace) -> int:
    """Create the local catalog override that keeps a v2 model on protocol v1.

    The counterpart to `refresh-codex-catalog`, which maintains an override but
    cannot create one. Without this the only route was to hand-build a snapshot
    from Codex's cache, which is a lot of file surgery to ask of someone whose
    actual problem is that their sub-agent came back with the wrong answer.
    """
    home = codex_catalog.codex_home()
    config_path = home / "config.toml"
    catalog_path = (
        Path(args.catalog).expanduser()
        if args.catalog
        else (home / "model-catalogs" / "multiagent-v1.json")
    )

    cache_path = home / "models_cache.json"
    if not cache_path.exists():
        # Not the same as "nothing needs pinning": with no catalog to copy there
        # is nothing to decide from, and saying otherwise would tell a user whose
        # sub-agents cannot work that they already can.
        return _error(
            f"{cache_path} does not exist, so there is no catalog to derive from. "
            "Codex writes it on its own — start a Codex session, then run this again."
        )

    slugs = tuple(args.model) if args.model else codex_catalog.models_needing_pin(home)
    if not slugs:
        print(
            f"no model in {cache_path} declares multi-agent v2, so nothing needs "
            "pinning: sub-agent tasks already reach a local agent as readable text. "
            "If one did not, the cause is elsewhere — check the role name first "
            "(`fluxion_worker`, not `worker`).",
            file=sys.stderr,
        )
        return 1

    try:
        snapshot = codex_catalog.build_snapshot(home, slugs)
    except codex_catalog.CatalogError as err:
        return _error(str(err))

    before = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    after = codex_catalog.plan_config_line(before, catalog_path)
    print(f"Pinning to multi-agent v1: {', '.join(slugs)}")
    print(f"Catalog snapshot: {catalog_path} ({len(json.loads(snapshot)['models'])} models)")
    print()
    print(codex_config.diff_preview(before, after) or "(no change to config.toml)")
    print(
        "\nThis file becomes your whole model list — it replaces the server's rather\n"
        "than extending it, so it needs re-deriving when models change upstream.\n"
        "`fluxion-provider check-models` reports when that happens."
    )

    if not args.yes and not _confirm("\nApply these changes?"):
        print("aborted; nothing was written")
        return 1

    backup = ""
    if before:
        backup_path = config_path.with_name(f"{config_path.name}.fluxion-backup-{int(time.time())}")
        shutil.copy2(config_path, backup_path)
        backup = str(backup_path)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(snapshot, encoding="utf-8")
    config_path.write_text(after, encoding="utf-8")

    if backup:
        print(f"backup: {backup}")
    print(f"installed: {catalog_path}")
    print(
        "Start a NEW Codex session to pick this up — the protocol version is fixed "
        "when a thread starts, so switching inside an existing one changes nothing."
    )
    print("verify with: codex debug models")
    return 0


def _refresh_codex_catalog(args: argparse.Namespace) -> int:
    """Re-derive the local Codex catalog snapshot on demand.

    The manual half of the daily check: same detection, but it writes regardless
    of `FLUXION_PROVIDER_CODEX_CATALOG_DRIFT`, because running this command *is*
    the decision that setting otherwise defers.
    """
    home = codex_catalog.codex_home()
    catalog_path = codex_catalog.find_override(home)
    if catalog_path is None:
        print(
            f"no `{codex_catalog.CONFIG_KEY}` in {home / 'config.toml'}; nothing to refresh",
            file=sys.stderr,
        )
        return 1
    try:
        drift = codex_catalog.inspect(home)
    except codex_catalog.CatalogError as err:
        print(f"FAIL {err}", file=sys.stderr)
        return 1
    if drift is None:
        print(f"FAIL {catalog_path} or {home / 'models_cache.json'} is missing", file=sys.stderr)
        return 1

    if args.check:
        problems, notes = codex_catalog.report("warn", home)
        for note in notes:
            print(f"ok   {note}")
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        return 1 if problems else 0

    try:
        messages = codex_catalog.refresh(drift)
    except (codex_catalog.CatalogError, OSError) as err:
        print(f"FAIL {err}", file=sys.stderr)
        return 1
    for message in messages:
        print(f"ok   {message}")
    print("verify with: codex debug models")
    return 0


def _model_catalog_report(routing: RoutingConfig) -> tuple[list[str], list[str]]:
    """Model-existence findings as `(problems, notes)`.

    Only ids a *readable* catalog fails to list become problems. An unreachable
    catalog is reported as a note: a CLI that is missing, slow, or newly
    upgraded must not be able to condemn a working configuration.
    """
    verification = verify_configured_models(routing)
    problems = [describe_missing(routing, candidate) for candidate in verification.missing]
    notes = list(verification.catalog_notes)
    if verification.verified:
        notes.append(f"{len(verification.verified)} configured model(s) confirmed live")
    for candidate, reason in verification.unverified:
        notes.append(f"  {candidate}: not verified — {reason}")
    return problems, notes


def _read_only_problems(routing, executors, enforces_read_only) -> list[str]:
    """Roles whose declared read-only sandbox nothing can actually enforce.

    The role file tells the user the agent cannot change anything, and at request
    time the gateway refuses rather than break that promise. Reporting it here
    means the user finds out while editing config, not when a sub-agent they were
    counting on dies mid-task.
    """
    problems: list[str] = []
    for role, policy_id in sorted(routing.routes.items()):
        if not codex_config.is_read_only_role(role):
            continue
        policy = routing.policies.get(policy_id)
        if policy is None:
            continue
        for candidate in policy.ordered_candidates():
            provider_id = candidate.split(":", 1)[0]
            spec = routing.providers.get(provider_id)
            if spec is None or not spec.enabled:
                continue
            executor = executors.get(spec.executor)
            if executor is not None and not enforces_read_only(executor):
                problems.append(
                    f"  role {role!r} is read-only but routes to {candidate!r}, and executor "
                    f"{spec.executor!r} cannot enforce read-only; those turns will be refused"
                )
    return problems


def _print_codex_config(args: argparse.Namespace) -> int:
    settings = GatewaySettings.load()
    command, command_args = _token_command(args, settings)
    block = codex_config.render_provider_block(
        base_url=_gateway_url(settings),
        token_command=command,
        token_args=command_args,
    )
    print("# Add to ~/.codex/config.toml\n")
    print(block)
    print("\n# Role files (one per role), e.g. .codex/agents/explorer.toml:\n")
    print(codex_config.render_role_file("explorer", args.model))
    return 0


def _install_codex_config(args: argparse.Namespace) -> int:
    settings = GatewaySettings.load()
    config_path = Path(args.codex_config).expanduser()
    command, command_args = _token_command(args, settings)
    plan = codex_config.plan_install(
        config_path=config_path,
        agents_dir=Path(args.agents_dir).expanduser(),
        base_url=_gateway_url(settings),
        token_command=command,
        token_args=command_args,
        model=args.model,
    )

    before = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    diff = codex_config.diff_preview(before, plan.merged_config)
    print(diff or "(no change to config.toml)")
    print("\nRole files to write:")
    for path in plan.role_files:
        print(f"  {path}")

    if not args.yes and not _confirm("\nApply these changes?"):
        print("aborted; nothing was written")
        return 1

    backup = codex_config.apply_plan(plan)
    if backup:
        print(f"backup: {backup}")
    print(f"installed into {config_path}")
    if plan.replaced_existing:
        print("(replaced a previous Fluxion block)")
    return 0


def _uninstall_codex_config(args: argparse.Namespace) -> int:
    config_path = Path(args.codex_config).expanduser()
    if not args.yes and not _confirm(f"Remove the Fluxion block from {config_path}?"):
        print("aborted")
        return 1
    if codex_config.uninstall(config_path):
        print(f"removed the Fluxion block from {config_path}")
        print("Role files under the agents directory were left in place.")
        return 0
    print("no Fluxion block found; nothing to do")
    return 0


def _rollback_codex_config(args: argparse.Namespace) -> int:
    config_path = Path(args.codex_config).expanduser()
    backups = codex_config.find_backups(config_path)
    if not backups:
        return _error(f"no backups found next to {config_path}")
    chosen = Path(args.backup).expanduser() if args.backup else backups[-1]
    if not chosen.exists():
        return _error(f"backup not found: {chosen}")
    if not args.yes and not _confirm(f"Restore {chosen} over {config_path}?"):
        print("aborted")
        return 1
    shutil.copyfile(chosen, config_path)
    print(f"restored {config_path} from {chosen}")
    return 0


def _routes(args: argparse.Namespace) -> int:
    settings = GatewaySettings.load()
    store = StickyStore(
        settings.token_file.parent / "sticky.db", ttl_seconds=settings.sticky_ttl_seconds
    )
    try:
        if getattr(args, "prune", False):
            removed = store.purge_expired()
            print(f"removed {removed} expired route(s)")
            return 0

        routes = store.list_routes()
        if not routes:
            print("no sticky routes recorded")
            return 0
        for route in routes:
            pin = " [pinned]" if route.pinned else ""
            # Thread ids are shown truncated and prompts never are: this command
            # is for operators checking routing, not for reading conversations.
            #
            # Ingresses with no thread concept (Anthropic keys on a session id)
            # print "-" here; the truncated route_key on the left is their
            # per-conversation handle, and it identifies the row without
            # exposing the client's own id.
            conversation = (route.thread_id or "-")[:12]
            # Whether the *agent* can be resumed is a different question from
            # which provider the route points at, and it is the one that decides
            # if the next turn keeps its context. A route can be remembered
            # perfectly while the session behind it is gone.
            session = "resumable" if route.executor_session_id else "cold"
            print(
                f"{route.route_key[:12]}  {route.ingress:<9} {route.candidate_id:<40} "
                f"role={route.route_hint:<11} conv={conversation:<13} {session}{pin}"
            )
        return 0
    finally:
        store.close()


# ── helpers ──────────────────────────────────────────────────────────
def _gateway_url(settings: GatewaySettings) -> str:
    return f"http://{settings.host}:{settings.port}/v1"


def _token_command(
    args: argparse.Namespace, settings: GatewaySettings
) -> tuple[str, tuple[str, ...]]:
    """The executable and its arguments, kept separate.

    Codex execs `command` directly rather than through a shell, so an argument
    folded into the command string becomes part of the filename it looks for.
    """
    if args.token_command:
        return str(Path(args.token_command).expanduser().resolve()), ()
    # `cat <token file>` satisfies Codex's command-backed auth without shipping
    # another binary.
    cat = shutil.which("cat") or "/bin/cat"
    return cat, (str(settings.token_file.resolve()),)


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex((host if host != "0.0.0.0" else "127.0.0.1", port)) == 0


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        # Refuse rather than assume yes: this writes to the user's Codex config.
        print(f"{prompt} [not a terminal; pass --yes to proceed non-interactively]")
        return False
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fluxion-provider",
        description="Run and configure the Fluxion Provider Gateway.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex"))

    def add_codex_paths(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--codex-config", default=str(codex_home / "config.toml"))
        sub.add_argument("--agents-dir", default=str(codex_home / "agents"))
        sub.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")

    init_parser = subparsers.add_parser("init", help="Create the token and a starter config.")
    init_parser.set_defaults(handler=_init)

    serve_parser = subparsers.add_parser("serve", help="Run the gateway.")
    serve_parser.set_defaults(handler=_serve)

    doctor_parser = subparsers.add_parser("doctor", help="Check the local setup.")
    doctor_parser.set_defaults(handler=_doctor)

    check_models_parser = subparsers.add_parser(
        "check-models",
        help="Verify configured model ids still exist in the CLIs' catalogs "
        "(safe to run on a schedule while the gateway is up).",
    )
    check_models_parser.add_argument(
        "--notify",
        action="store_true",
        help="Deliver findings as a macOS notification through the Fluxion "
        "desktop app, at most once a day per unchanged finding.",
    )
    check_models_parser.set_defaults(handler=_check_models)

    install_catalog_parser = subparsers.add_parser(
        "install-codex-catalog",
        help="Pin a v2 model's sub-agent protocol to v1 with a local Codex model "
        "catalog, so delegated tasks reach a local agent as readable text.",
    )
    install_catalog_parser.add_argument(
        "--model",
        action="append",
        default=[],
        help="Model to pin (repeatable). Defaults to every model declaring v2.",
    )
    install_catalog_parser.add_argument(
        "--catalog",
        default="",
        help="Where to write the snapshot (default: ~/.codex/model-catalogs/multiagent-v1.json).",
    )
    install_catalog_parser.add_argument("--yes", action="store_true")
    install_catalog_parser.set_defaults(handler=_install_codex_catalog)

    refresh_catalog_parser = subparsers.add_parser(
        "refresh-codex-catalog",
        help="Re-derive a local Codex model catalog override from Codex's own "
        "fresh cache, keeping the multi-agent protocol pins.",
    )
    refresh_catalog_parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift and exit non-zero instead of writing.",
    )
    refresh_catalog_parser.set_defaults(handler=_refresh_codex_catalog)

    print_parser = subparsers.add_parser(
        "print-codex-config", help="Print the Codex-side config without modifying anything."
    )
    print_parser.add_argument("--model", default="REPLACE_WITH_A_REAL_MODEL_ID")
    print_parser.add_argument("--token-command", default="")
    print_parser.set_defaults(handler=_print_codex_config)

    install_parser = subparsers.add_parser(
        "install-codex-config", help="Install the Fluxion block into ~/.codex/config.toml."
    )
    install_parser.add_argument("--model", default="REPLACE_WITH_A_REAL_MODEL_ID")
    install_parser.add_argument("--token-command", default="")
    add_codex_paths(install_parser)
    install_parser.set_defaults(handler=_install_codex_config)

    uninstall_parser = subparsers.add_parser(
        "uninstall-codex-config", help="Remove only the Fluxion block."
    )
    add_codex_paths(uninstall_parser)
    uninstall_parser.set_defaults(handler=_uninstall_codex_config)

    rollback_parser = subparsers.add_parser(
        "rollback-codex-config", help="Restore a config.toml backup."
    )
    rollback_parser.add_argument("--backup", default="", help="Defaults to the newest backup.")
    add_codex_paths(rollback_parser)
    rollback_parser.set_defaults(handler=_rollback_codex_config)

    routes_parser = subparsers.add_parser("routes", help="Show recorded sticky routes.")
    routes_parser.add_argument(
        "--prune",
        action="store_true",
        help="Delete expired routes instead of listing. Nothing expires on its own.",
    )
    routes_parser.set_defaults(handler=_routes)

    return parser.parse_args(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
