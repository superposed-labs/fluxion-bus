"""Generating and installing the Codex-side configuration.

Codex's config lives in a file the user also edits by hand, so this module never
rewrites it wholesale. Everything Fluxion owns goes between sentinel comments;
install, upgrade, and uninstall operate on that block alone, and the user's
comments, ordering, and unrelated sections come back out byte-identical.

That is also why we do not round-trip through a TOML writer. Parsing to a dict
and re-serializing would silently drop every comment in the file. Instead we
generate text, then *parse the merged result* to prove it is valid TOML before
anything is written.

Field names were verified against codex-cli source at `c8957bbf0f`. **Reading the
newest source is not enough** — the generated file lands on the user's machine,
and their Codex can be any version. We shipped `[agents].enabled`, valid in that
revision, and it made codex-cli 0.144.3 refuse to load config.toml at all: Codex
would not start, with or without Fluxion.

So verify changes here against a *real binary*, without touching the user's
config:

    CODEX_HOME=$(mktemp -d) codex mcp list

Write the rendered block (and any role files) into that directory first. The
command loads and validates the whole config and reports the offending line, so
it doubles as a config linter. Prefer the widely-compatible spelling of an
option over the newest one; a missing optional setting costs a feature, while a
rejected key costs the user their editor.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fluxion.codex_command import resolve_codex_command

# v2 moved the multi-agent switch out of `[agents]`, which older Codex builds
# reject outright. Installs find and replace any earlier version's block.
# v3 switched the multi-agent protocol from v2 to v1 so spawn payloads arrive
# readable; the version bump makes `install-codex-config` replace a v2 block
# rather than leave both in place.
MANAGED_VERSION = 3
BEGIN_MARKER = f"# >>> fluxion managed block v{MANAGED_VERSION} — do not edit inside >>>"
END_MARKER = f"# <<< fluxion managed block v{MANAGED_VERSION} <<<"

# Any version's markers, so uninstall and upgrade can find an older block.
_ANY_BEGIN = "# >>> fluxion managed block v"
_ANY_END = "# <<< fluxion managed block v"

# Rendered as role *names* with a `fluxion_` prefix (see `render_role_file`),
# which keeps them distinct from Codex's built-in roles — `default`, `explorer`,
# and `worker` (core/src/agent/role.rs).
#
# The cost of staying distinct: asking for "a worker sub-agent" in plain language
# selects the *built-in* worker, whose description is richer and matches the
# phrasing better. That runs on the parent session's provider and never reaches
# this gateway — with no error anywhere, since nothing went wrong from Codex's
# point of view. The role has to be named explicitly: "use the fluxion_worker
# role".
#
# Naming ours `worker` outright would override the built-in (same-name user roles
# win in `role.rs`'s merge) and make the plain-language phrasing work — at the
# price of changing every sub-agent that user spawns, everywhere, and replacing
# the built-in's file-ownership guidance. Undecided; keep the prefix for now.
DEFAULT_ROLES = ("auto", "explorer", "reviewer", "worker")

_ROLE_DESCRIPTIONS = {
    "auto": "General-purpose agent; Fluxion picks the model.",
    "explorer": "Read-only explorer routed by Fluxion to a fast, economical model.",
    "reviewer": "Independent correctness and security reviewer.",
    "worker": "Implementation worker whose model is selected by Fluxion.",
}

# Appended to every role's `description`, which is the only text this module
# writes that the *parent* agent ever reads.
#
# Codex renders each role's description into the `agent_type` parameter of the
# parent's `spawn_agent` schema, and uses that same field for its own built-in
# roles to carry coordinator-facing rules — "assign ownership", "spawn multiple
# explorers in parallel", "tell workers they are not alone in the codebase".
# So this is the channel for telling a coordinator how to supervise a role.
# `developer_instructions` reaches the sub-agent, which is the wrong end: a
# sub-agent asked to narrate its progress narrates into a void, because nothing
# it emits mid-turn reaches the parent at all.
#
# It has to be said because this gateway leaves the parent with no interim
# signal whatsoever. A Fluxion role is backed by a local agent CLI that runs for
# minutes and produces nothing until its turn ends, and `wait_agent` is
# edge-triggered on terminal status — it answers with an empty map on timeout.
# "Still working" and "wedged" are therefore indistinguishable from the parent's
# side, and the parent has to guess.
#
# It guessed wrong, in the way that costs the most. Observed 2026-07-30: five
# consecutive `wait_agent` timeouts read as silence, so the parent sent
# `send_input` with `interrupt: true` asking for a progress report — which
# aborted the six-minute turn it had been waiting on (`turn_aborted`, reason
# `interrupted`), restarted from nothing, timed out again 30s later, and was
# closed while `close_agent` still reported `previous_status: "running"`. The
# parent then reported that the sub-agent had produced no progress and no file
# changes: an accurate description with the causality exactly reversed.
#
# Carried by every role, not just the write-capable ones. The blindness is a
# property of the gateway rather than of write capability — a read-only explorer
# backed by the same CLI goes just as quiet for just as long.
_SUPERVISION_NOTE = (
    "Backed by a local agent CLI: one turn takes minutes and reports nothing until it "
    "finishes, so a wait_agent timeout means still working, not stalled. Never send_input "
    "with interrupt=true to ask for progress; that aborts the turn and discards all of "
    "its work. Wait, or do non-overlapping work meanwhile."
)

# `developer_instructions` is REQUIRED for standalone agent role files: without
# it Codex logs `must define developer_instructions` at startup and does not
# register the role at all (core/src/config/agent_roles.rs).
_ROLE_INSTRUCTIONS = {
    "auto": "Complete the delegated task and report what changed.",
    "explorer": (
        "Explore the requested code path and return concise evidence with file and symbol "
        "references. Do not edit files."
    ),
    "reviewer": (
        "Review like a code owner. Prioritize correctness, security, regressions, and missing "
        "tests. Return only evidence-backed findings."
    ),
    "worker": (
        "Implement the bounded task, validate the touched area, and report changed files and "
        "checks."
    ),
}

# Also appended to the write-capable roles.
#
# Their instructions above are written in the imperative of implementation
# ("Implement the bounded task"), and a role's instructions arrive *before* the
# delegated task in the prompt. Asked to "summarize the README", a worker read
# that framing as licence to fix what it found and rewrote the file — a real
# observed failure, not a hypothetical. The delegated task has to win.
_TASK_GOVERNS_NOTE = (
    "The delegated task decides what you do. Follow it exactly and do no more than it "
    "asks: if it asks a question, or asks you to summarize, review, or explain something, "
    "answer it and change nothing on disk. Edit files only when the task actually asks "
    "for a change."
)

# Appended to the write-capable roles only.
#
# Nothing enforces this. Fluxion serializes the local agents it launches against
# each other (upstream/local_agent.py), but `spawn_agent` does not block, so the
# main Codex agent keeps running its own tools in the same tree while a
# sub-agent works — and it is a separate process with no lock to join.
#
# Codex has the same problem among its own sub-agents and answers it the same
# way: its built-in worker role tells the coordinator to assign file ownership
# and tells workers they are "not alone in the codebase"
# (codex-rs/core/src/agent/role.rs). Advisory text is the whole of the available
# mitigation, so we use it rather than leaving the overlap unmentioned.
_SHARED_WORKSPACE_NOTE = (
    "You are not alone in this repository. The agent that delegated this task keeps "
    "working while you do. Stay within the files you were given, never revert an edit "
    "you did not make, and adapt to changes that appear underneath you."
)

_WRITE_CAPABLE_ROLES = ("auto", "worker")

# `sandbox_mode` here configures *Codex's* sandbox, which governs the sub-thread
# — and that thread runs no tools at all in this mode, so on its own the line
# constrains nothing. The tools run inside the local agent CLI, which Fluxion
# launches with edit permission by default.
#
# `read_only_roles()` exists so the gateway can enforce what this declares. Both
# sides must read the same mapping: a role advertised as read-only that can
# still edit the tree is worse than no sandbox line at all, because the user
# reads the config and believes it.
_ROLE_SANDBOX = {"explorer": "read-only", "reviewer": "read-only"}
_ROLE_EFFORT = {"reviewer": "high"}


def read_only_roles() -> frozenset[str]:
    """Roles whose sandbox declaration the gateway has to enforce itself."""
    return frozenset(role for role, mode in _ROLE_SANDBOX.items() if mode == "read-only")


def is_read_only_role(role: str) -> bool:
    return role.strip().lower() in read_only_roles()


class CodexConfigError(RuntimeError):
    """The Codex config cannot be safely modified."""


@dataclass(frozen=True)
class CodexConfigPlan:
    """What an install would write, before anything touches disk."""

    config_path: Path
    agents_dir: Path
    provider_block: str
    role_files: dict[Path, str]
    merged_config: str
    replaced_existing: bool


@dataclass(frozen=True)
class CodexIntegrationFile:
    """One file shown in, and optionally written by, the Preferences workflow."""

    path: Path
    action: str
    content: str
    role: str | None = None
    problem: str | None = None


@dataclass(frozen=True)
class CodexIntegrationPlan:
    """A selective, transactional install or repair plan."""

    mode: str
    model: str
    config: CodexConfigPlan
    files: tuple[CodexIntegrationFile, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "model": self.model,
            "config_path": str(self.config.config_path),
            "agents_dir": str(self.config.agents_dir),
            "files": [
                {
                    "path": str(item.path),
                    "name": item.path.name,
                    "role": item.role,
                    "action": item.action,
                    "problem": item.problem,
                    "content": item.content,
                }
                for item in self.files
            ],
        }


@dataclass(frozen=True)
class CodexIntegrationResult:
    """Outcome of a completed transaction."""

    changed_files: tuple[Path, ...]
    backups: tuple[Path, ...]
    validation_output: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "changed_files": [str(path) for path in self.changed_files],
            "backups": [str(path) for path in self.backups],
            "validation_output": self.validation_output,
        }


def render_provider_block(
    *,
    base_url: str,
    token_command: str,
    token_args: tuple[str, ...] = (),
    roles: tuple[str, ...] = DEFAULT_ROLES,
) -> str:
    """Render the `[model_providers.*]` entries, one per role.

    All point at the same gateway but carry a different `X-Fluxion-Route`
    header. Roles are expressed as separate provider entries rather than
    invented model names because Codex loads context-window and capability
    information from the model name.

    `token_command` is the executable alone; arguments go in `token_args`.
    Codex's `ModelProviderAuthInfo` keeps them in separate fields, so a combined
    "cmd arg" string would have Codex exec a file with a space in its name.

    Deliberately writes no feature flags. Multi-agent v1 — the protocol this
    gateway depends on — is already the default: `Feature::Collab` (TOML key
    `multi_agent`, legacy alias `collab`) is `default_enabled: true`, and
    version selection is `if MultiAgentV2 { V2 } else if !agents_enabled
    { Disabled } else if Collab { V1 }`. So there is nothing to switch on, and
    writing `[features]` here would collide with a hand-written `[features]`
    table — TOML forbids declaring the same table twice, and Codex would refuse
    to load the whole file. `check_feature_conflicts` guards the settings that
    would break routing instead.
    """
    if not token_command.startswith("/"):
        raise CodexConfigError(
            f"token command must be an absolute path, got {token_command!r}. "
            "Codex resolves bare names via PATH, which is nearly empty for a "
            "GUI-launched app."
        )
    if " " in token_command:
        raise CodexConfigError(
            f"token command {token_command!r} contains a space. Pass arguments via "
            "token_args; Codex execs `command` literally rather than through a shell."
        )
    rendered_args = ", ".join(f'"{arg}"' for arg in token_args)

    lines = [BEGIN_MARKER, ""]
    for role in roles:
        lines += [
            f"[model_providers.fluxion_{role}]",
            f'name = "Fluxion {role.title()}"',
            f'base_url = "{base_url}"',
            'wire_api = "responses"',
            # Command-backed bearer token: `env_key` would need an environment
            # variable, which a Finder-launched Codex App does not inherit.
            f'auth = {{ command = "{token_command}", args = [{rendered_args}], '
            "timeout_ms = 5000, refresh_interval_ms = 300000 }",
            f'http_headers = {{ "X-Fluxion-Route" = "{role}" }}',
            "request_max_retries = 1",
            # Fluxion owns stream retry: two layers retrying independently can
            # duplicate output and re-run tool calls.
            "stream_max_retries = 0",
            "stream_idle_timeout_ms = 300000",
            "",
        ]
    lines += [END_MARKER]
    return "\n".join(lines)


def render_role_file(role: str, model: str, *, provider_id: str | None = None) -> str:
    """Render one `.codex/agents/<role>.toml` role layer."""
    provider = provider_id or f"fluxion_{role}"
    description = f"{_ROLE_DESCRIPTIONS.get(role, role)} {_SUPERVISION_NOTE}"
    lines = [
        f'name = "fluxion_{role}"',
        f'description = "{description}"',
        f'model = "{model}"',
        # Without this line the sub-agent silently inherits the parent session's
        # provider and no routing happens at all — the single most important
        # line in the file (core/src/agent/role.rs).
        f'model_provider = "{provider}"',
    ]
    if role in _ROLE_EFFORT:
        lines.append(f'model_reasoning_effort = "{_ROLE_EFFORT[role]}"')
    if role in _ROLE_SANDBOX:
        lines.append(f'sandbox_mode = "{_ROLE_SANDBOX[role]}"')
    instructions = _ROLE_INSTRUCTIONS.get(role, _ROLE_INSTRUCTIONS["auto"])
    if role in _WRITE_CAPABLE_ROLES:
        instructions = f"{instructions}\n\n{_TASK_GOVERNS_NOTE}\n\n{_SHARED_WORKSPACE_NOTE}"
    lines += ['developer_instructions = """', instructions, '"""', ""]
    return "\n".join(lines)


def plan_install(
    *,
    config_path: Path,
    agents_dir: Path,
    base_url: str,
    token_command: str,
    model: str,
    token_args: tuple[str, ...] = (),
    roles: tuple[str, ...] = DEFAULT_ROLES,
) -> CodexConfigPlan:
    """Compute the full result of an install without writing anything."""
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    block = render_provider_block(
        base_url=base_url,
        token_command=token_command,
        token_args=token_args,
        roles=roles,
    )

    body, replaced = _strip_managed_block(existing)
    _reject_conflicting_providers(body, roles)
    check_feature_conflicts(body)
    merged = f"{body.rstrip()}\n\n{block}\n" if body.strip() else f"{block}\n"

    # Prove the result parses before offering to write it. A config file Codex
    # cannot read leaves the user with no working Codex at all.
    try:
        tomllib.loads(merged)
    except tomllib.TOMLDecodeError as err:
        raise CodexConfigError(f"generated config is not valid TOML: {err}") from err

    # Same rule as the merged config above, for the same reason. The role files
    # carry free text — descriptions and instructions — into TOML string
    # literals, so an unescaped quote in one of those constants would land on
    # the user's disk as a file Codex cannot parse. Codex answers that by
    # refusing to register the role, and a role that never registers routes
    # nothing: the sub-agent runs on the parent's provider, silently, with
    # nothing anywhere saying why.
    role_files = {}
    for role in roles:
        rendered = render_role_file(role, model)
        try:
            tomllib.loads(rendered)
        except tomllib.TOMLDecodeError as err:
            raise CodexConfigError(
                f"generated role file for {role!r} is not valid TOML: {err}"
            ) from err
        role_files[agents_dir / f"{role}.toml"] = rendered

    return CodexConfigPlan(
        config_path=config_path,
        agents_dir=agents_dir,
        provider_block=block,
        role_files=role_files,
        merged_config=merged,
        replaced_existing=replaced,
    )


def plan_integration(
    *,
    config_path: Path,
    agents_dir: Path,
    base_url: str,
    token_command: str,
    model: str,
    token_args: tuple[str, ...] = (),
    roles: tuple[str, ...] = DEFAULT_ROLES,
    mode: str = "auto",
) -> CodexIntegrationPlan:
    """Plan the Preferences install/repair workflow without changing disk.

    ``repair`` writes only missing or unreadable role files. ``install`` and
    ``reinstall`` reconcile every generated file. The config is always shown in
    the preview, but is written only when its managed block differs.
    """
    if mode not in {"auto", "install", "repair", "reinstall"}:
        raise CodexConfigError(f"unknown Codex integration mode: {mode}")
    plan = plan_install(
        config_path=config_path,
        agents_dir=agents_dir,
        base_url=base_url,
        token_command=token_command,
        token_args=token_args,
        model=model,
        roles=roles,
    )

    role_statuses: dict[Path, tuple[str, str | None]] = {}
    for path in plan.role_files:
        if not path.exists():
            role_statuses[path] = ("missing", "File is missing.")
            continue
        try:
            tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
            role_statuses[path] = ("corrupt", f"File cannot be read as TOML: {error}")
        else:
            role_statuses[path] = ("healthy", None)

    if mode == "auto":
        existing_config = (
            config_path.read_text(encoding="utf-8", errors="replace")
            if config_path.exists()
            else ""
        )
        has_managed_block = _ANY_BEGIN in existing_config
        if not has_managed_block:
            resolved_mode = "install"
        elif any(status == "corrupt" for status, _ in role_statuses.values()):
            resolved_mode = "corrupt"
        elif any(status == "missing" for status, _ in role_statuses.values()):
            resolved_mode = "missing"
        else:
            resolved_mode = "reinstall"
    elif mode == "repair":
        resolved_mode = (
            "corrupt"
            if any(status == "corrupt" for status, _ in role_statuses.values())
            else "missing"
        )
    else:
        resolved_mode = mode

    current_config = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    files = [
        CodexIntegrationFile(
            path=config_path,
            action=("rewrite" if config_path.exists() else "write")
            if current_config != plan.merged_config
            else "verify",
            content=plan.merged_config,
        )
    ]
    for role, path in ((role, agents_dir / f"{role}.toml") for role in roles):
        content = plan.role_files[path]
        status, problem = role_statuses[path]
        should_write = resolved_mode in {"install", "reinstall"} or status != "healthy"
        action = "keep"
        if should_write:
            action = "rewrite" if path.exists() else "write"
        files.append(
            CodexIntegrationFile(
                path=path,
                role=role,
                action=action,
                content=content,
                problem=problem,
            )
        )
    return CodexIntegrationPlan(
        mode=resolved_mode,
        model=model,
        config=plan,
        files=tuple(files),
    )


def validate_integration_plan(
    plan: CodexIntegrationPlan,
    *,
    codex_command: str | None = None,
    timeout_seconds: float = 20,
) -> str:
    """Validate the exact planned tree with a real Codex binary."""
    command = codex_command or resolve_codex_command()
    if not command:
        raise CodexConfigError(
            "Codex CLI was not found. Install or open Codex once, then try again."
        )
    with tempfile.TemporaryDirectory(prefix="fluxion-codex-validate-") as raw_home:
        home = Path(raw_home)
        target_config = home / "config.toml"
        target_agents = home / "agents"
        target_agents.mkdir(parents=True)
        target_config.write_text(plan.config.merged_config, encoding="utf-8")
        for item in plan.files:
            if item.role is None:
                continue
            source = item.path
            content = item.content
            if item.action == "keep" and source.exists():
                content = source.read_text(encoding="utf-8")
            (target_agents / source.name).write_text(content, encoding="utf-8")

        env = os.environ.copy()
        env["CODEX_HOME"] = str(home)
        try:
            completed = subprocess.run(
                [command, "mcp", "list"],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CodexConfigError(f"Codex CLI validation could not run: {error}") from error
        output = "\n".join(
            part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
        )
        if completed.returncode != 0:
            raise CodexConfigError(
                "Codex CLI rejected the generated configuration"
                + (f":\n{output}" if output else ".")
            )
        return output


def apply_integration_plan(
    plan: CodexIntegrationPlan,
    *,
    validate_with_codex: bool = True,
    codex_command: str | None = None,
) -> CodexIntegrationResult:
    """Apply a Preferences plan atomically and roll every changed file back."""
    validation_output = (
        validate_integration_plan(plan, codex_command=codex_command) if validate_with_codex else ""
    )
    targets = [item for item in plan.files if item.action in {"write", "rewrite"}]
    originals: dict[Path, bytes | None] = {}
    backups: list[Path] = []
    changed: list[Path] = []
    stamp = time.time_ns()
    try:
        for item in targets:
            path = item.path
            original = path.read_bytes() if path.exists() else None
            originals[path] = original
            if original is not None:
                backup = path.with_name(f"{path.name}.fluxion-backup-{stamp}")
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(original)
                backups.append(backup)
            _atomic_write(path, item.content)
            changed.append(path)
    except Exception as error:
        rollback_errors: list[str] = []
        for path, original in reversed(tuple(originals.items())):
            try:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_write_bytes(path, original)
            except OSError as rollback_error:
                rollback_errors.append(f"{path}: {rollback_error}")
        detail = f"; rollback problems: {'; '.join(rollback_errors)}" if rollback_errors else ""
        raise CodexConfigError(
            f"Codex integration failed and was rolled back: {error}{detail}"
        ) from error
    return CodexIntegrationResult(
        changed_files=tuple(changed),
        backups=tuple(backups),
        validation_output=validation_output,
    )


def _atomic_write(path: Path, content: str) -> None:
    _atomic_write_bytes(path, content.encode("utf-8"))


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _strip_managed_block(text: str) -> tuple[str, bool]:
    """Remove a previously installed block, returning the rest untouched."""
    if _ANY_BEGIN not in text:
        return text, False
    lines = text.splitlines()
    kept: list[str] = []
    inside = False
    found = False
    for line in lines:
        if line.startswith(_ANY_BEGIN):
            inside = True
            found = True
            continue
        if line.startswith(_ANY_END):
            inside = False
            continue
        if not inside:
            kept.append(line)
    if inside:
        raise CodexConfigError(
            "the existing Fluxion block has no closing marker; the file was edited by hand. "
            "Remove the block manually and re-run install."
        )
    return "\n".join(kept), found


def _reject_conflicting_providers(body: str, roles: tuple[str, ...]) -> None:
    """Refuse to shadow a provider the user defined themselves.

    Only reached for entries *outside* our managed block, so this cannot fire on
    a re-install of our own configuration.
    """
    try:
        parsed = tomllib.loads(body)
    except tomllib.TOMLDecodeError as err:
        raise CodexConfigError(
            f"the existing {'config'} is not valid TOML, so it cannot be safely modified: {err}"
        ) from err
    declared = parsed.get("model_providers", {})
    clashes = sorted(f"fluxion_{role}" for role in roles if f"fluxion_{role}" in declared)
    if clashes:
        raise CodexConfigError(
            f"config.toml already defines {', '.join(clashes)} outside the Fluxion block. "
            "Rename or remove them first; overwriting a hand-written provider would "
            "silently change how existing agents route."
        )


def check_feature_conflicts(body: str) -> None:
    """Refuse settings that would break sub-agent routing after install.

    Two of Codex's own feature flags decide whether a sub-agent can be served
    from here at all, and neither fails loudly at run time:

    - `multi_agent_v2` selects the v2 protocol, which has the provider encrypt
      the spawn payload (`.with_encrypted()` on the `message` tool parameter).
      The delegated task then reaches a local agent as an opaque blob, and the
      agent — having no task — improvises a plausible report. The parent sees a
      sub-agent that answered the wrong question, with nothing anywhere saying
      why.
    - `multi_agent` (alias `collab`) turned off removes `spawn_agent` entirely,
      so no sub-agent is ever spawned to route.

    Both are left to the user rather than overwritten: they are Codex's own
    settings, and silently flipping them would change how the rest of Codex
    behaves. Failing here is the alternative to failing invisibly later.
    """
    try:
        parsed = tomllib.loads(body)
    except tomllib.TOMLDecodeError:
        # `_reject_conflicting_providers` reports the parse failure with the
        # detail; this check simply has nothing to say about an unparseable file.
        return
    features = parsed.get("features")
    if not isinstance(features, dict):
        return

    v2 = features.get("multi_agent_v2")
    if v2 is True or (isinstance(v2, dict) and v2.get("enabled") is True):
        raise CodexConfigError(
            "config.toml enables features.multi_agent_v2, which routes sub-agent "
            "tasks through an encrypted payload only OpenAI can read. A local "
            "agent would receive an unreadable task and answer the wrong "
            "question. Remove that setting to use the default v1 protocol."
        )
    for key in ("multi_agent", "collab"):
        if features.get(key) is False:
            raise CodexConfigError(
                f"config.toml sets features.{key} = false, which removes the "
                "spawn_agent tool, so no sub-agent is ever created to route. "
                "Remove that setting or set it to true."
            )


def apply_plan(plan: CodexConfigPlan) -> Path | None:
    """Write the plan, backing up an existing config first.

    Returns the backup path, or None when there was nothing to back up.
    """
    backup = None
    if plan.config_path.exists():
        backup = _next_backup_path(plan.config_path)
        backup.write_text(plan.config_path.read_text(encoding="utf-8"), encoding="utf-8")

    plan.config_path.parent.mkdir(parents=True, exist_ok=True)
    plan.config_path.write_text(plan.merged_config, encoding="utf-8")

    plan.agents_dir.mkdir(parents=True, exist_ok=True)
    for path, content in plan.role_files.items():
        path.write_text(content, encoding="utf-8")
    return backup


def uninstall(config_path: Path) -> bool:
    """Remove only the Fluxion block, leaving the rest of the file alone."""
    if not config_path.exists():
        return False
    text = config_path.read_text(encoding="utf-8")
    body, found = _strip_managed_block(text)
    if not found:
        return False
    config_path.write_text(f"{body.rstrip()}\n", encoding="utf-8")
    return True


def find_backups(config_path: Path) -> list[Path]:
    """Existing backups, newest last."""
    return sorted(config_path.parent.glob(f"{config_path.name}.fluxion-backup-*"))


def _next_backup_path(config_path: Path) -> Path:
    import time

    return config_path.with_name(f"{config_path.name}.fluxion-backup-{int(time.time())}")


def diff_preview(before: str, after: str, *, label: str = "after install") -> str:
    """Unified diff of the config change, for confirmation before writing.

    `label` names what the change is, since an uninstall shown as "after install"
    reads as the opposite of what is about to happen.
    """
    import difflib

    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="config.toml (current)",
            tofile=f"config.toml ({label})",
        )
    )
