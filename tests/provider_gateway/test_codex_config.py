"""Codex-side config generation and installation.

Field names verified against codex-cli-repo at `c8957bbf0f`.
"""

from __future__ import annotations

import tomllib

import pytest

from fluxion.provider_gateway.codex_config import (
    BEGIN_MARKER,
    END_MARKER,
    CodexConfigError,
    apply_plan,
    find_backups,
    is_read_only_role,
    plan_install,
    render_provider_block,
    render_role_file,
    uninstall,
)

TOKEN_COMMAND = "/bin/cat"
TOKEN_ARGS = ("/Users/x/data/provider.token",)


def build_plan(tmp_path, existing: str | None = None, **overrides):
    config_path = tmp_path / "config.toml"
    if existing is not None:
        config_path.write_text(existing)
    kwargs = dict(
        config_path=config_path,
        agents_dir=tmp_path / "agents",
        base_url="http://127.0.0.1:8787/v1",
        token_command=TOKEN_COMMAND,
        token_args=TOKEN_ARGS,
        model="gpt-x",
    )
    kwargs.update(overrides)
    return plan_install(**kwargs)


# ── provider block ───────────────────────────────────────────────────
def test_provider_block_is_valid_toml():
    parsed = tomllib.loads(
        render_provider_block(
            base_url="http://127.0.0.1:8787/v1", token_command=TOKEN_COMMAND, token_args=TOKEN_ARGS
        )
    )
    assert set(parsed["model_providers"]) == {
        "fluxion_auto",
        "fluxion_explorer",
        "fluxion_reviewer",
        "fluxion_worker",
    }


def test_each_role_carries_its_own_route_header():
    """Roles are distinguished by header, not by inventing model names."""
    parsed = tomllib.loads(
        render_provider_block(
            base_url="http://127.0.0.1:8787/v1", token_command=TOKEN_COMMAND, token_args=TOKEN_ARGS
        )
    )
    providers = parsed["model_providers"]
    assert providers["fluxion_reviewer"]["http_headers"]["X-Fluxion-Route"] == "reviewer"
    assert providers["fluxion_explorer"]["http_headers"]["X-Fluxion-Route"] == "explorer"


def test_wire_api_is_responses():
    """Codex's WireApi enum has exactly one variant."""
    parsed = tomllib.loads(
        render_provider_block(
            base_url="http://x/v1", token_command=TOKEN_COMMAND, token_args=TOKEN_ARGS
        )
    )
    assert parsed["model_providers"]["fluxion_auto"]["wire_api"] == "responses"


def test_command_backed_auth_is_used_instead_of_env_key():
    """A Finder-launched Codex App does not inherit the shell environment."""
    parsed = tomllib.loads(
        render_provider_block(
            base_url="http://x/v1", token_command=TOKEN_COMMAND, token_args=TOKEN_ARGS
        )
    )
    provider = parsed["model_providers"]["fluxion_auto"]
    assert provider["auth"]["command"] == TOKEN_COMMAND
    # These three are mutually exclusive with `auth` in Codex's own validation.
    assert "env_key" not in provider
    assert "experimental_bearer_token" not in provider
    assert "requires_openai_auth" not in provider


def test_relative_token_command_is_refused():
    """Codex resolves bare names via PATH, which is nearly empty for a GUI app."""
    with pytest.raises(CodexConfigError, match="absolute path"):
        render_provider_block(base_url="http://x/v1", token_command="fluxion-token")


def test_stream_retry_is_disabled_on_the_codex_side():
    """Two layers retrying independently duplicates output and re-runs tools."""
    parsed = tomllib.loads(
        render_provider_block(
            base_url="http://x/v1", token_command=TOKEN_COMMAND, token_args=TOKEN_ARGS
        )
    )
    assert parsed["model_providers"]["fluxion_auto"]["stream_max_retries"] == 0


def _rendered_block() -> dict:
    return tomllib.loads(
        render_provider_block(
            base_url="http://x/v1", token_command=TOKEN_COMMAND, token_args=TOKEN_ARGS
        )
    )


def test_no_feature_flags_are_written():
    """v1 is already the default, and `[features]` would collide with the user's.

    `Feature::Collab` (TOML key `multi_agent`) is `default_enabled: true`, so
    there is nothing to switch on. Declaring `[features]` here would be a second
    declaration of a table many configs already have, and TOML forbids that —
    Codex would then refuse to load the entire file.
    """
    assert "features" not in _rendered_block()
    assert "[features]" not in render_provider_block(
        base_url="http://x/v1", token_command=TOKEN_COMMAND, token_args=TOKEN_ARGS
    )


def test_install_survives_a_hand_written_features_table(tmp_path):
    """The exact shape that broke a real install."""
    existing = "[features]\nmulti_agent = true\njs_repl = false\n"
    plan = build_plan(tmp_path, existing)

    parsed = tomllib.loads(plan.merged_config)
    assert parsed["features"] == {"multi_agent": True, "js_repl": False}


def test_upgrading_replaces_an_older_managed_block(tmp_path):
    """A left-behind v2 block would re-enable the encrypted protocol."""
    existing = (
        "# >>> fluxion managed block v2 — do not edit inside >>>\n"
        "[features.multi_agent_v2]\n"
        "enabled = true\n"
        "# <<< fluxion managed block v2 <<<\n"
    )
    plan = build_plan(tmp_path, existing)

    assert plan.replaced_existing
    assert "multi_agent_v2" not in plan.merged_config


# ── feature conflicts ────────────────────────────────────────────────
def test_v2_enabled_outside_the_block_is_refused(tmp_path):
    """Under v2 the task arrives encrypted and the agent answers the wrong thing.

    Nothing downstream can detect that, so it has to be caught here.
    """
    with pytest.raises(CodexConfigError, match="multi_agent_v2"):
        build_plan(tmp_path, "[features.multi_agent_v2]\nenabled = true\n")


def test_v2_enabled_as_a_bare_bool_is_refused(tmp_path):
    with pytest.raises(CodexConfigError, match="multi_agent_v2"):
        build_plan(tmp_path, "[features]\nmulti_agent_v2 = true\n")


def test_multi_agent_turned_off_is_refused(tmp_path):
    """No spawn_agent tool means no sub-agent is ever created to route."""
    with pytest.raises(CodexConfigError, match="spawn_agent"):
        build_plan(tmp_path, "[features]\nmulti_agent = false\n")


def test_the_legacy_alias_is_checked_too(tmp_path):
    with pytest.raises(CodexConfigError, match="spawn_agent"):
        build_plan(tmp_path, "[features]\ncollab = false\n")


def test_an_unrelated_features_table_is_left_alone(tmp_path):
    build_plan(tmp_path, "[features]\njs_repl = false\napps = true\n")


def test_no_plain_keys_are_written_under_agents():
    """`[agents]` flattens into a role map, so a scalar there breaks all of Codex.

    Older builds read every key under `[agents]` as a role name, and a boolean
    where an AgentRoleToml belongs fails the whole config load — Codex will not
    start at all, Fluxion or no Fluxion. Observed on codex-cli 0.144.3.
    """
    agents = _rendered_block().get("agents", {})
    scalars = {key: value for key, value in agents.items() if not isinstance(value, dict)}
    assert scalars == {}, f"these would be parsed as roles: {sorted(scalars)}"


# ── role files ───────────────────────────────────────────────────────
def test_role_file_sets_model_provider():
    """Without this line the sub-agent silently inherits the parent's provider."""
    parsed = tomllib.loads(render_role_file("explorer", "gpt-x"))
    assert parsed["model_provider"] == "fluxion_explorer"


def test_role_file_always_has_developer_instructions():
    """Codex refuses to register a standalone role file without it."""
    for role in ("auto", "explorer", "reviewer", "worker"):
        parsed = tomllib.loads(render_role_file(role, "gpt-x"))
        assert parsed["developer_instructions"].strip()


def test_read_only_roles_declare_a_sandbox():
    assert tomllib.loads(render_role_file("explorer", "gpt-x"))["sandbox_mode"] == "read-only"
    assert "sandbox_mode" not in tomllib.loads(render_role_file("worker", "gpt-x"))


# ── install planning ─────────────────────────────────────────────────
def test_plan_produces_parseable_config(tmp_path):
    plan = build_plan(tmp_path)
    tomllib.loads(plan.merged_config)
    assert BEGIN_MARKER in plan.merged_config
    assert END_MARKER in plan.merged_config


def test_existing_content_and_comments_survive(tmp_path):
    """A TOML round-trip would silently drop every comment in the file."""
    existing = '# my notes\nmodel = "gpt-5"\n\n[tui]\nfoo = 1\n'
    plan = build_plan(tmp_path, existing)
    assert "# my notes" in plan.merged_config
    assert "[tui]" in plan.merged_config
    assert tomllib.loads(plan.merged_config)["model"] == "gpt-5"


def test_reinstall_replaces_the_previous_block(tmp_path):
    first = build_plan(tmp_path, "")
    apply_plan(first)
    second = build_plan(tmp_path)
    assert second.replaced_existing
    assert second.merged_config.count(BEGIN_MARKER) == 1


def test_conflicting_hand_written_provider_is_refused(tmp_path):
    """Overwriting one would silently change how existing agents route."""
    existing = '[model_providers.fluxion_reviewer]\nbase_url = "http://mine"\n'
    with pytest.raises(CodexConfigError, match="already defines"):
        build_plan(tmp_path, existing)


def test_unparseable_existing_config_is_refused(tmp_path):
    with pytest.raises(CodexConfigError, match="not valid TOML"):
        build_plan(tmp_path, "this is [not valid")


def test_half_removed_block_is_refused(tmp_path):
    existing = f"{BEGIN_MARKER}\n[model_providers.x]\n"
    with pytest.raises(CodexConfigError, match="no closing marker"):
        build_plan(tmp_path, existing)


# ── apply / uninstall / rollback ─────────────────────────────────────
def test_apply_writes_config_and_role_files(tmp_path):
    plan = build_plan(tmp_path, "")
    apply_plan(plan)
    assert tomllib.loads(plan.config_path.read_text())["model_providers"]
    for path in plan.role_files:
        assert path.exists()


def test_apply_backs_up_the_previous_config(tmp_path):
    plan = build_plan(tmp_path, 'model = "gpt-5"\n')
    backup = apply_plan(plan)
    assert backup is not None
    assert 'model = "gpt-5"' in backup.read_text()
    assert find_backups(plan.config_path) == [backup]


def test_apply_creates_no_backup_when_there_was_no_config(tmp_path):
    assert apply_plan(build_plan(tmp_path)) is None


def test_uninstall_removes_only_the_managed_block(tmp_path):
    plan = build_plan(tmp_path, '# keep me\nmodel = "gpt-5"\n')
    apply_plan(plan)

    assert uninstall(plan.config_path)
    remaining = plan.config_path.read_text()
    assert "# keep me" in remaining
    assert 'model = "gpt-5"' in remaining
    assert BEGIN_MARKER not in remaining
    assert "fluxion_reviewer" not in remaining


def test_uninstall_is_a_no_op_without_a_block(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-5"\n')
    assert not uninstall(path)
    assert path.read_text() == 'model = "gpt-5"\n'


def test_uninstall_on_a_missing_file_is_safe(tmp_path):
    assert not uninstall(tmp_path / "absent.toml")


def test_uninstall_leaves_valid_toml(tmp_path):
    plan = build_plan(tmp_path, "[tui]\nfoo = 1\n")
    apply_plan(plan)
    uninstall(plan.config_path)
    assert tomllib.loads(plan.config_path.read_text())["tui"]["foo"] == 1


def test_write_capable_roles_carry_the_shared_workspace_note():
    # Nothing enforces this — the main Codex agent keeps editing the tree while
    # a sub-agent runs and joins no lock — so advisory text is the only lever,
    # and it is the same one Codex uses for its own built-in worker role.
    worker = render_role_file("worker", "claude-opus-5")
    auto = render_role_file("auto", "claude-opus-5")

    assert "not alone in this repository" in worker
    assert "not alone in this repository" in auto


def test_read_only_roles_omit_the_shared_workspace_note():
    # explorer and reviewer run under a read-only sandbox, so the note would be
    # tokens spent on advice they cannot act on.
    for role in ("explorer", "reviewer"):
        assert "not alone in this repository" not in render_role_file(role, "claude-opus-5")


def test_read_only_roles_match_the_sandboxed_role_files():
    """The gateway enforces this set; a mismatch means the config lies to the user."""
    for role in ("explorer", "reviewer"):
        assert is_read_only_role(role)
        assert 'sandbox_mode = "read-only"' in render_role_file(role, "opus")
    for role in ("auto", "worker"):
        assert not is_read_only_role(role)


def test_write_capable_roles_are_told_the_task_governs():
    """Asked to summarize a README, a worker read its own framing as licence to rewrite it."""
    for role in ("auto", "worker"):
        assert "answer it and change nothing on disk" in render_role_file(role, "opus")
