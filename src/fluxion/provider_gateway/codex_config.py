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

import tomllib
from dataclasses import dataclass
from pathlib import Path

# v2 moved the multi-agent switch out of `[agents]`, which older Codex builds
# reject outright. Installs find and replace any earlier version's block.
MANAGED_VERSION = 2
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


def render_provider_block(
    *,
    base_url: str,
    token_command: str,
    token_args: tuple[str, ...] = (),
    roles: tuple[str, ...] = DEFAULT_ROLES,
) -> str:
    """Render the `[model_providers.*]` entries plus the `[agents]` settings.

    One provider entry per role, all pointing at the same gateway but carrying a
    different `X-Fluxion-Route` header. Roles are expressed as separate provider
    entries rather than invented model names because Codex loads context-window
    and capability information from the model name.

    `token_command` is the executable alone; arguments go in `token_args`.
    Codex's `ModelProviderAuthInfo` keeps them in separate fields, so a combined
    "cmd arg" string would have Codex exec a file with a space in its name.
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
    lines += [
        # Multi-agent is `default_enabled: false` (features/src/lib.rs), so it has
        # to be switched on or `spawn_agent` never appears.
        #
        # Written under `[features]`, never as `[agents].enabled`. `AgentsToml`
        # carries `#[serde(flatten)] roles: BTreeMap<String, AgentRoleToml>`, so
        # in older Codex builds *every* key under `[agents]` is read as a role
        # name — `enabled = true` becomes a role called "enabled" whose value is
        # a boolean, and the whole config fails to load with
        # "invalid type: boolean `true`, expected struct AgentRoleToml".
        # That breaks Codex entirely, not just Fluxion's part of it.
        #
        # Newer builds do accept `[agents].enabled`, and their own comment says a
        # `features.multi_agent_v2` setting takes precedence — so this form is
        # both the portable one and the authoritative one. Verified against
        # codex-cli 0.144.3.
        "[features.multi_agent_v2]",
        "enabled = true",
        "max_concurrent_threads_per_session = 6",
        "",
        END_MARKER,
    ]
    return "\n".join(lines)


def render_role_file(role: str, model: str, *, provider_id: str | None = None) -> str:
    """Render one `.codex/agents/<role>.toml` role layer."""
    provider = provider_id or f"fluxion_{role}"
    lines = [
        f'name = "fluxion_{role}"',
        f'description = "{_ROLE_DESCRIPTIONS.get(role, role)}"',
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
    merged = f"{body.rstrip()}\n\n{block}\n" if body.strip() else f"{block}\n"

    # Prove the result parses before offering to write it. A config file Codex
    # cannot read leaves the user with no working Codex at all.
    try:
        tomllib.loads(merged)
    except tomllib.TOMLDecodeError as err:
        raise CodexConfigError(f"generated config is not valid TOML: {err}") from err

    return CodexConfigPlan(
        config_path=config_path,
        agents_dir=agents_dir,
        provider_block=block,
        role_files={agents_dir / f"{role}.toml": render_role_file(role, model) for role in roles},
        merged_config=merged,
        replaced_existing=replaced,
    )


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


def diff_preview(before: str, after: str) -> str:
    """Unified diff of the config change, for confirmation before writing."""
    import difflib

    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="config.toml (current)",
            tofile="config.toml (after install)",
        )
    )
