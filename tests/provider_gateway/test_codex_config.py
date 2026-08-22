"""Codex-side config generation and installation.

Field names verified against codex-cli-repo at `c8957bbf0f`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from fluxion.provider_gateway.codex_config import (
    BEGIN_MARKER,
    END_MARKER,
    CodexConfigError,
    apply_integration_plan,
    apply_plan,
    find_backups,
    is_read_only_role,
    parse_codex_version,
    plan_install,
    plan_integration,
    render_provider_block,
    render_role_file,
    supports_role_model_provider,
    uninstall,
    validate_integration_plan,
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


def build_integration_plan(tmp_path, existing: str | None = None, **overrides):
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
    return plan_integration(**kwargs)


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


def test_integration_plan_exposes_backend_generated_preview(tmp_path):
    plan = build_integration_plan(tmp_path)
    preview = plan.as_dict()

    assert preview["mode"] == "install"
    assert preview["files"][0]["content"] == plan.config.merged_config
    assert {item["role"] for item in preview["files"][1:]} == {
        "auto",
        "explorer",
        "reviewer",
        "worker",
    }


def test_repair_only_writes_missing_or_corrupt_roles(tmp_path):
    installed = build_integration_plan(tmp_path)
    apply_integration_plan(installed, validate_with_codex=False)
    auto = installed.config.agents_dir / "auto.toml"
    worker = installed.config.agents_dir / "worker.toml"
    auto.unlink()
    worker.write_text("not valid [toml", encoding="utf-8")

    repair = build_integration_plan(tmp_path, mode="auto")
    actions = {item.role: item.action for item in repair.files if item.role}

    assert repair.mode == "corrupt"
    assert actions == {
        "auto": "write",
        "explorer": "keep",
        "reviewer": "keep",
        "worker": "rewrite",
    }


def test_transaction_backs_up_every_replaced_file(tmp_path):
    first = build_integration_plan(tmp_path)
    apply_integration_plan(first, validate_with_codex=False)
    second = build_integration_plan(tmp_path, model="gpt-y", mode="reinstall")

    result = apply_integration_plan(second, validate_with_codex=False)

    assert len(result.changed_files) == 4
    assert len(result.backups) == 4
    assert {path.name.split(".fluxion-backup-")[0] for path in result.backups} == {
        "auto.toml",
        "explorer.toml",
        "reviewer.toml",
        "worker.toml",
    }


def test_transaction_rolls_back_all_files_after_write_failure(tmp_path, monkeypatch):
    first = build_integration_plan(tmp_path)
    apply_integration_plan(first, validate_with_codex=False)
    before = {
        path: path.read_bytes() for path in [first.config.config_path, *first.config.role_files]
    }
    second = build_integration_plan(tmp_path, model="gpt-y", mode="reinstall")
    real_write = __import__(
        "fluxion.provider_gateway.codex_config", fromlist=["_atomic_write"]
    )._atomic_write
    writes = 0

    def fail_second_write(path, content):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated disk failure")
        real_write(path, content)

    monkeypatch.setattr("fluxion.provider_gateway.codex_config._atomic_write", fail_second_write)
    with pytest.raises(CodexConfigError, match="rolled back"):
        apply_integration_plan(second, validate_with_codex=False)

    assert {path: path.read_bytes() for path in before} == before


def test_validation_uses_an_isolated_codex_home(tmp_path, monkeypatch):
    plan = build_integration_plan(tmp_path)
    observed = {}

    def fake_run(command, **kwargs):
        class Completed:
            returncode = 0
            stdout = "No MCP servers configured"
            stderr = ""

        # The capability probe runs first and carries no CODEX_HOME: it asks the
        # binary what it is, not what it makes of a config.
        if command[1] == "--version":
            Completed.stdout = "codex-cli 0.148.0-alpha.15"
            return Completed()

        observed["command"] = command
        observed["home"] = Path(kwargs["env"]["CODEX_HOME"])
        assert (observed["home"] / "config.toml").exists()
        assert (observed["home"] / "agents" / "worker.toml").exists()
        return Completed()

    monkeypatch.setattr("fluxion.provider_gateway.codex_config.subprocess.run", fake_run)

    output = validate_integration_plan(plan, codex_command="/usr/bin/codex")

    assert observed["command"] == ["/usr/bin/codex", "mcp", "list"]
    assert output == "No MCP servers configured"


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


# ── supervision note ─────────────────────────────────────────────────
# It rides on `description` rather than `developer_instructions` because only
# `description` reaches the parent: Codex renders it into the `agent_type`
# parameter of the parent's `spawn_agent` schema. Instructions reach the
# sub-agent, and the sub-agent is not the one making the mistake.
def test_every_role_tells_the_parent_a_wait_timeout_is_not_a_stall():
    """A parent read five `wait_agent` timeouts as silence and interrupted the turn."""
    for role in ("auto", "explorer", "reviewer", "worker"):
        description = tomllib.loads(render_role_file(role, "opus"))["description"]
        assert "wait_agent timeout means still working, not stalled" in description


def test_every_role_warns_the_parent_off_interrupting_for_progress():
    """`interrupt=true` aborts the turn; asking for a report this way destroys it."""
    for role in ("auto", "explorer", "reviewer", "worker"):
        description = tomllib.loads(render_role_file(role, "opus"))["description"]
        assert "Never send_input with interrupt=true" in description


def test_the_supervision_note_does_not_reach_the_sub_agent():
    """Instructions are prepended to every turn, and the sub-agent cannot act on this.

    Nothing a sub-agent emits mid-turn reaches its parent, so telling the
    sub-agent about `wait_agent` would be tokens spent on advice for someone
    else — and would invite it to narrate progress into a void.
    """
    for role in ("auto", "explorer", "reviewer", "worker"):
        instructions = tomllib.loads(render_role_file(role, "opus"))["developer_instructions"]
        assert "wait_agent" not in instructions
        assert "interrupt" not in instructions


def test_each_role_keeps_its_own_description(tmp_path):
    """The note is appended to the role's identity, not a replacement for it."""
    assert (
        "Read-only explorer" in tomllib.loads(render_role_file("explorer", "opus"))["description"]
    )
    assert (
        "Implementation worker" in tomllib.loads(render_role_file("worker", "opus"))["description"]
    )


# ── generated role files must parse ──────────────────────────────────
def test_every_generated_role_file_is_valid_toml():
    """Descriptions and instructions are free text inside TOML string literals.

    An unescaped quote in one of those constants produces a role file Codex
    cannot parse, so it declines to register the role — and an unregistered role
    routes nothing: the sub-agent quietly runs on the parent's own provider.
    """
    for role in ("auto", "explorer", "reviewer", "worker"):
        parsed = tomllib.loads(render_role_file(role, "gpt-x"))
        assert parsed["name"] == f"fluxion_{role}"


def test_planning_refuses_a_role_file_that_would_not_parse(tmp_path, monkeypatch):
    """Proven before anything is written, like the merged config."""
    monkeypatch.setattr(
        "fluxion.provider_gateway.codex_config._SUPERVISION_NOTE",
        'an unescaped " breaks the literal',
    )
    with pytest.raises(CodexConfigError, match="role file"):
        build_plan(tmp_path)


# ── Codex builds where a role may not choose its provider ─────────────
#
# Codex 1a6e07a4fe (#39299) restricted agent roles to a bounded override
# allowlist that excludes `model_provider`, so a child agent always inherits the
# parent session's provider. Bisected independently across 79 real sub-agent
# rollouts: every spawn up to codex-cli 0.148.0-alpha.15 recorded
# `model_provider = fluxion_worker`, and every spawn from 0.149.0-alpha.4.1 on
# records `openai`, with the config byte-identical across the boundary.
def test_the_last_working_codex_is_still_supported():
    assert supports_role_model_provider((0, 148, 0)) is True


def test_the_first_broken_codex_is_not():
    assert supports_role_model_provider((0, 149, 0)) is False


def test_later_codex_versions_stay_unsupported():
    """The override was removed on purpose, so newer is not a fix."""
    assert supports_role_model_provider((0, 150, 3)) is False
    assert supports_role_model_provider((1, 0, 0)) is False


def test_an_unreadable_version_is_reported_as_unknown_not_guessed():
    """Refusing on a parse failure would break builds that are fine; assuming
    support would restore exactly the silence this check exists to end."""
    assert supports_role_model_provider(None) is None


def test_prerelease_suffixes_are_dropped_rather_than_ordered():
    """The regression shipped in the alphas, so 0.149.0-alpha.4.1 and 0.149.0
    are the same build family here."""
    assert parse_codex_version("codex-cli 0.149.0-alpha.4.1") == (0, 149, 0)
    assert parse_codex_version("codex-cli 0.148.0-alpha.15") == (0, 148, 0)
    assert parse_codex_version("codex-cli 0.149.0") == (0, 149, 0)


def test_unparseable_version_output_yields_none():
    assert parse_codex_version("not a version") is None


def _fake_codex(monkeypatch, version_output, *, on_mcp_list=None):
    def fake_run(command, **kwargs):
        class Completed:
            returncode = 0
            stdout = version_output if command[1] == "--version" else "No MCP servers configured"
            stderr = ""

        if command[1] != "--version" and on_mcp_list is not None:
            on_mcp_list()
        return Completed()

    monkeypatch.setattr("fluxion.provider_gateway.codex_config.subprocess.run", fake_run)


def test_install_is_refused_on_a_codex_that_would_not_route(tmp_path, monkeypatch):
    """The config parses fine on 0.149 — that is the whole problem. Refusing
    here is the alternative to billing the user's OpenAI account in silence."""
    plan = build_integration_plan(tmp_path)
    _fake_codex(monkeypatch, "codex-cli 0.149.0-alpha.4.1")

    with pytest.raises(CodexConfigError, match="does not let an agent role choose"):
        validate_integration_plan(plan, codex_command="/usr/bin/codex")


def test_the_refusal_names_the_version_and_a_way_forward(tmp_path, monkeypatch):
    plan = build_integration_plan(tmp_path)
    _fake_codex(monkeypatch, "codex-cli 0.149.0-alpha.4.1")

    with pytest.raises(CodexConfigError) as error:
        validate_integration_plan(plan, codex_command="/usr/bin/codex")

    message = str(error.value)
    assert "0.149.0" in message
    assert "run_subagent" in message


def test_a_supported_codex_still_validates_normally(tmp_path, monkeypatch):
    plan = build_integration_plan(tmp_path)
    _fake_codex(monkeypatch, "codex-cli 0.148.0-alpha.15")

    assert validate_integration_plan(plan, codex_command="/usr/bin/codex")


def test_an_unknown_version_does_not_block_the_install(tmp_path, monkeypatch):
    plan = build_integration_plan(tmp_path)
    _fake_codex(monkeypatch, "some other tool")

    assert validate_integration_plan(plan, codex_command="/usr/bin/codex")


def test_the_escape_hatch_skips_the_capability_check_only(tmp_path, monkeypatch):
    """Still validated by the real binary; the user has just accepted that
    sub-agents will not route."""
    plan = build_integration_plan(tmp_path)
    reached = []
    _fake_codex(
        monkeypatch,
        "codex-cli 0.149.0-alpha.4.1",
        on_mcp_list=lambda: reached.append(True),
    )

    validate_integration_plan(plan, codex_command="/usr/bin/codex", allow_unsupported=True)

    assert reached == [True]


def test_apply_refuses_before_writing_anything(tmp_path, monkeypatch):
    plan = build_integration_plan(tmp_path)
    _fake_codex(monkeypatch, "codex-cli 0.149.0-alpha.4.1")

    with pytest.raises(CodexConfigError, match="does not let an agent role choose"):
        apply_integration_plan(plan, codex_command="/usr/bin/codex")

    assert not (tmp_path / "agents" / "worker.toml").exists()
