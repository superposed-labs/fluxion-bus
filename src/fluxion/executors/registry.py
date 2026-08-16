"""Construction of the executor set from settings.

Extracted because three callers now need it — the Slack/IM gateway, the MCP
sub-agent surface, and the provider gateway — and the first two had already
drifted into identical copies. A fourth copy would eventually disagree with the
others about, say, a new Claude flag, and the bug would only show up on whichever
surface was forgotten.
"""

from __future__ import annotations

from fluxion.config.settings import Settings
from fluxion.executors.antigravity.executor import AntiGravityExecutor
from fluxion.executors.base import Executor
from fluxion.executors.claude.executor import ClaudeExecutor
from fluxion.executors.codex.executor import CodexExecutor


def executor_read_only_support() -> dict[str, bool]:
    """Which executors can honor a read-only task, without building any.

    Whether an executor can promise read-only is a property of the CLI it
    drives, not of how this machine configured it, so it is readable from the
    classes. Callers that only need the capability — the preferences state,
    which must stay cheap — should not have to load Settings to find out.
    """
    return {
        "codex": CodexExecutor.enforces_read_only(),
        "claude": ClaudeExecutor.enforces_read_only(),
        "antigravity": AntiGravityExecutor.enforces_read_only(),
    }


def build_all_executors(settings: Settings) -> dict[str, Executor]:
    """Every executor Fluxion knows how to run, regardless of what is enabled."""
    logs_dir = settings.data_dir / "logs"
    return {
        "codex": CodexExecutor(
            timeout_sec=settings.task_timeout_sec,
            skip_git_repo_check=settings.codex_skip_git_repo_check,
            sandbox_mode=settings.codex_sandbox_mode,
            bypass_sandbox=settings.codex_bypass_sandbox,
            max_structured_uploads=settings.artifact_max_files,
            logs_dir=logs_dir,
        ),
        "claude": ClaudeExecutor(
            timeout_sec=settings.task_timeout_sec,
            command=settings.claude_command,
            provider=settings.claude_provider,
            auth_mode=settings.claude_auth_mode,
            model=settings.claude_model,
            base_url=settings.claude_base_url,
            api_key=settings.claude_api_key,
            auth_token=settings.claude_auth_token,
            permission_mode=settings.claude_permission_mode,
            use_bare_mode=settings.claude_use_bare_mode,
            append_system_prompt=settings.claude_append_system_prompt,
            allowed_tools=settings.claude_allowed_tools,
            max_turns=settings.claude_max_turns,
            max_structured_uploads=settings.artifact_max_files,
            logs_dir=logs_dir,
        ),
        "antigravity": AntiGravityExecutor(
            timeout_sec=settings.task_timeout_sec,
            command=settings.antigravity_command,
            sandbox=settings.antigravity_sandbox,
            dangerously_skip_permissions=settings.antigravity_dangerously_skip_permissions,
            print_timeout_sec=settings.antigravity_print_timeout_sec,
            logs_dir=logs_dir,
            max_structured_uploads=settings.artifact_max_files,
        ),
    }


def build_enabled_executors(settings: Settings) -> dict[str, Executor]:
    """The executors the user has enabled, falling back to all of them.

    An empty selection means "no preference expressed", not "run nothing" — a
    typo in FLUXION_ENABLED_EXECUTORS should not silently leave the user with a
    gateway that cannot execute anything.
    """
    all_executors = build_all_executors(settings)
    selected = {
        name: all_executors[name] for name in settings.enabled_executors if name in all_executors
    }
    return selected or all_executors
