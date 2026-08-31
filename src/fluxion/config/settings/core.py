from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fluxion.config.settings.env import (
    _load_dotenv,
    _load_dotenv_file,
    env_file_path,
)
from fluxion.config.settings.models import ProjectConfig, WorkspaceAuthorization
from fluxion.config.settings.parsing import (
    _first_containing_root,
    _is_git_repo,
    _is_within,
    _merge_allowed_workspaces,
    _parse_bool,
    _parse_change_detection_mode,
    _parse_channel_workspaces,
    _parse_claude_auth_mode,
    _parse_claude_provider,
    _parse_codex_sandbox_mode,
    _parse_codex_usage_mode,
    _parse_enabled_executors,
    _parse_int,
    _parse_paths,
    _parse_projects,
    _parse_revert_capture_mode,
    _parse_status_updates,
    _parse_usage_providers,
    _resolve_data_dir,
)
from fluxion.i18n import normalize_locale, normalize_locale_mode


@dataclass(frozen=True)
class Settings:
    fluxion_env: str
    workspace_root: Path
    allowed_workspaces: list[Path]
    trusted_workspace_roots: list[Path]
    denied_workspaces: list[Path]
    write_allowed_workspaces: list[Path]
    workspace_discovery: bool
    projects: dict[str, ProjectConfig]
    slack_allow_channels: bool
    slack_require_mention_in_channels: bool
    slack_channel_workspaces: dict[str, Path]
    slack_allowed_users: set[str]
    data_dir: Path
    default_executor: str
    enabled_executors: list[str]
    task_timeout_sec: int
    worker_count: int
    workspace_lock_timeout_sec: int
    max_pending_per_user: int
    max_retries: int
    retry_backoff_sec: int
    codex_skip_git_repo_check: bool
    codex_sandbox_mode: str
    codex_bypass_sandbox: bool
    claude_command: str
    claude_provider: str
    claude_auth_mode: str
    claude_model: str
    claude_base_url: str
    claude_api_key: str
    claude_auth_token: str
    claude_permission_mode: str
    claude_use_bare_mode: bool
    claude_append_system_prompt: str
    claude_allowed_tools: str
    claude_max_turns: int
    antigravity_command: str
    antigravity_sandbox: bool
    antigravity_dangerously_skip_permissions: bool
    antigravity_print_timeout_sec: int
    artifact_max_files: int
    change_detection: str
    revert_capture: str
    change_set_max_file_bytes: int
    change_set_max_total_bytes: int
    mcp_status_max_wait_ms: int
    mcp_authorization_wait_ms: int
    status_updates: set[str]
    upload_log_on_success: bool
    locale_mode: str
    ui_locale: str
    slack_typing_heartbeat_sec: int
    slack_running_update_sec: int
    slack_enabled: bool
    slack_bot_token: str
    slack_app_token: str
    slack_signing_secret: str
    usage_panel_enabled: bool
    usage_refresh_sec: int
    autoping_enabled: bool
    autoping_max_attempts: int
    usage_providers: list[str]
    claude_code_user_agent: str
    claude_usage_token: str
    claude_usage_keychain: bool
    claude_usage_auto_refresh: bool
    codex_usage_mode: str
    codex_usage_base_url: str
    codex_history_reconciliation: bool
    scheduler_enabled: bool
    scheduler_tick_sec: int
    menu_slack_notify_refresh: bool
    menu_telegram_notify_refresh: bool
    menu_qqbot_notify_refresh: bool
    menu_feishu_notify_refresh: bool
    menu_wechat_notify_refresh: bool
    menu_line_notify_refresh: bool
    menu_macos_notify_refresh: bool
    notify_credit_grant: bool
    notify_credit_expiry: bool
    scheduler_slack_channel: str
    wechat_enabled: bool
    wechat_default_workspace: str
    wechat_allowed_users: set[str]
    wechat_message_max_chars: int
    wechat_typing_heartbeat_sec: int
    telegram_enabled: bool
    telegram_bot_token: str
    telegram_default_workspace: str
    telegram_allowed_users: set[str]
    line_enabled: bool
    line_channel_secret: str
    line_channel_access_token: str
    line_default_workspace: str
    line_allowed_users: set[str]
    line_tunnel_name: str
    qqbot_enabled: bool
    qqbot_app_id: str
    qqbot_client_secret: str
    qqbot_sandbox: bool
    qqbot_transport: str
    qqbot_default_workspace: str
    qqbot_allowed_users: set[str]
    qqbot_allow_group_chat: bool
    feishu_enabled: bool
    feishu_app_id: str
    feishu_app_secret: str
    feishu_default_workspace: str
    feishu_allowed_users: set[str]
    feishu_allow_group_chat: bool
    inbox_ttl_hours: int

    @classmethod
    def reload(cls) -> Settings:
        """Re-read the active .env file (file values overriding os.environ) and
        rebuild settings, so a long-running daemon can pick up edits without a
        restart. Unlike load(), the file wins over the pre-existing process
        environment — the file is the source of truth the settings UI writes."""
        path = env_file_path()
        if path is not None:
            _load_dotenv_file(path, override=True)
        return cls.load()

    @classmethod
    def load(cls) -> Settings:
        _load_dotenv()
        workspace_root = (
            Path(os.environ.get("FLUXION_WORKSPACE_ROOT", str(Path.cwd()))).expanduser().resolve()
        )
        allowed_workspaces = _parse_paths(os.environ.get("FLUXION_ALLOWED_WORKSPACES", "")) or [
            workspace_root
        ]
        trusted_workspace_roots = _parse_paths(
            os.environ.get("FLUXION_TRUSTED_WORKSPACE_ROOTS", "")
        )
        denied_workspaces = _parse_paths(os.environ.get("FLUXION_DENIED_WORKSPACES", ""))
        write_allowed_workspaces = _parse_paths(
            os.environ.get("FLUXION_WRITE_ALLOWED_WORKSPACES", "")
        )
        projects = _parse_projects(
            raw=os.environ.get("FLUXION_PROJECTS", ""),
            file_path=os.environ.get("FLUXION_PROJECTS_FILE", ""),
        )
        allowed_workspaces = _merge_allowed_workspaces(
            allowed_workspaces,
            [project.workspace for project in projects.values()],
        )
        slack_channel_workspaces = _parse_channel_workspaces(
            os.environ.get("FLUXION_SLACK_CHANNEL_WORKSPACES", "")
        )
        data_dir = _resolve_data_dir(
            os.environ.get("FLUXION_DATA_DIR", "data"),
            workspace_root=workspace_root,
        )
        allowed_users_raw = os.environ.get("FLUXION_SLACK_ALLOWED_USERS", "")
        slack_allowed_users = {x.strip() for x in allowed_users_raw.split(",") if x.strip()}
        return cls(
            fluxion_env=os.environ.get("FLUXION_ENV", "dev"),
            workspace_root=workspace_root,
            allowed_workspaces=allowed_workspaces,
            trusted_workspace_roots=trusted_workspace_roots,
            denied_workspaces=denied_workspaces,
            write_allowed_workspaces=write_allowed_workspaces,
            workspace_discovery=_parse_bool(
                os.environ.get("FLUXION_WORKSPACE_DISCOVERY"), default=False
            ),
            projects=projects,
            slack_allow_channels=_parse_bool(
                os.environ.get("FLUXION_SLACK_ALLOW_CHANNELS", ""),
                default=False,
            ),
            slack_require_mention_in_channels=_parse_bool(
                os.environ.get("FLUXION_SLACK_REQUIRE_MENTION_IN_CHANNELS", ""),
                default=True,
            ),
            slack_channel_workspaces=slack_channel_workspaces,
            slack_allowed_users=slack_allowed_users,
            data_dir=data_dir,
            default_executor=os.environ.get("FLUXION_DEFAULT_EXECUTOR", "codex"),
            enabled_executors=_parse_enabled_executors(
                os.environ.get("FLUXION_ENABLED_EXECUTORS", "claude,codex,antigravity")
            ),
            task_timeout_sec=_parse_int(os.environ.get("FLUXION_TASK_TIMEOUT_SEC"), default=1800),
            # Runs execute concurrently across workers. Writes to the *same*
            # workspace stay serialized regardless — that is the workspace lock's
            # job, not the worker count's — so this only decides how much
            # unrelated work can proceed at once. Each worker can drive a full
            # agent CLI, so raise it against available CPU and provider quota.
            worker_count=max(1, _parse_int(os.environ.get("FLUXION_WORKER_COUNT"), default=3)),
            # How long a workspace-write run waits for another run to release the
            # same workspace before giving up. Defaults to the task budget: a
            # holder can never legitimately outlive its own timeout.
            workspace_lock_timeout_sec=max(
                1,
                _parse_int(
                    os.environ.get("FLUXION_WORKSPACE_LOCK_TIMEOUT_SEC"),
                    default=_parse_int(os.environ.get("FLUXION_TASK_TIMEOUT_SEC"), default=1800),
                ),
            ),
            # Kept above worker_count on purpose: at parity there is no queue at
            # all, so the first submission past a full set of busy workers is
            # rejected rather than waiting a moment for one to free up.
            max_pending_per_user=max(
                1, _parse_int(os.environ.get("FLUXION_MAX_PENDING_PER_USER"), default=5)
            ),
            max_retries=max(0, _parse_int(os.environ.get("FLUXION_MAX_RETRIES"), default=1)),
            retry_backoff_sec=max(
                1, _parse_int(os.environ.get("FLUXION_RETRY_BACKOFF_SEC"), default=2)
            ),
            codex_skip_git_repo_check=_parse_bool(
                os.environ.get("FLUXION_CODEX_SKIP_GIT_REPO_CHECK"), default=True
            ),
            codex_sandbox_mode=_parse_codex_sandbox_mode(
                os.environ.get("FLUXION_CODEX_SANDBOX_MODE", "workspace-write")
            ),
            codex_bypass_sandbox=_parse_bool(
                os.environ.get("FLUXION_CODEX_BYPASS_SANDBOX"), default=False
            ),
            claude_command=os.environ.get("FLUXION_CLAUDE_COMMAND", "").strip(),
            claude_provider=_parse_claude_provider(
                os.environ.get("FLUXION_CLAUDE_PROVIDER", "official")
            ),
            claude_auth_mode=_parse_claude_auth_mode(
                os.environ.get("FLUXION_CLAUDE_AUTH_MODE", "login")
            ),
            claude_model=os.environ.get("FLUXION_CLAUDE_MODEL", ""),
            claude_base_url=os.environ.get("FLUXION_CLAUDE_BASE_URL", "").strip(),
            claude_api_key=os.environ.get("FLUXION_CLAUDE_API_KEY", "").strip(),
            claude_auth_token=os.environ.get("FLUXION_CLAUDE_AUTH_TOKEN", "").strip(),
            claude_permission_mode=os.environ.get("FLUXION_CLAUDE_PERMISSION_MODE", "acceptEdits"),
            claude_use_bare_mode=_parse_bool(
                os.environ.get("FLUXION_CLAUDE_USE_BARE_MODE"), default=False
            ),
            claude_append_system_prompt=os.environ.get("FLUXION_CLAUDE_APPEND_SYSTEM_PROMPT", ""),
            claude_allowed_tools=os.environ.get("FLUXION_CLAUDE_ALLOWED_TOOLS", "Bash,Read,Edit"),
            claude_max_turns=max(
                0, _parse_int(os.environ.get("FLUXION_CLAUDE_MAX_TURNS"), default=0)
            ),
            antigravity_command=os.environ.get("FLUXION_ANTIGRAVITY_COMMAND", "").strip(),
            antigravity_sandbox=_parse_bool(
                os.environ.get("FLUXION_ANTIGRAVITY_SANDBOX"), default=False
            ),
            antigravity_dangerously_skip_permissions=_parse_bool(
                os.environ.get("FLUXION_ANTIGRAVITY_DANGEROUSLY_SKIP_PERMISSIONS"),
                default=False,
            ),
            antigravity_print_timeout_sec=max(
                1,
                _parse_int(
                    os.environ.get("FLUXION_ANTIGRAVITY_PRINT_TIMEOUT_SEC"),
                    default=1800,
                ),
            ),
            artifact_max_files=_parse_int(os.environ.get("FLUXION_ARTIFACT_MAX_FILES"), default=8),
            change_detection=_parse_change_detection_mode(
                os.environ.get("FLUXION_CHANGE_DETECTION", "off")
            ),
            revert_capture=_parse_revert_capture_mode(
                os.environ.get("FLUXION_REVERT_CAPTURE", "structured")
            ),
            change_set_max_file_bytes=max(
                0,
                _parse_int(
                    os.environ.get("FLUXION_CHANGE_SET_MAX_FILE_BYTES"),
                    default=1_000_000,
                ),
            ),
            change_set_max_total_bytes=max(
                0,
                _parse_int(
                    os.environ.get("FLUXION_CHANGE_SET_MAX_TOTAL_BYTES"),
                    default=20_000_000,
                ),
            ),
            mcp_status_max_wait_ms=max(
                1_000,
                _parse_int(
                    os.environ.get("FLUXION_MCP_STATUS_MAX_WAIT_MS"),
                    default=60_000,
                ),
            ),
            # How long an MCP run_subagent call waits in place for the user to
            # answer a workspace approval it just raised. The user is normally
            # at the keyboard when they trigger a task, so absorbing that click
            # turns the common case into one successful call instead of relying
            # on the caller to come back for it. 0 disables the inline wait and
            # returns the pending rejection immediately.
            mcp_authorization_wait_ms=min(
                300_000,
                max(
                    0,
                    _parse_int(
                        os.environ.get("FLUXION_MCP_AUTHORIZATION_WAIT_MS"),
                        default=60_000,
                    ),
                ),
            ),
            status_updates=_parse_status_updates(
                os.environ.get("FLUXION_STATUS_UPDATES", "RUNNING,FAILED,CANCELED")
            ),
            upload_log_on_success=_parse_bool(
                os.environ.get("FLUXION_UPLOAD_LOG_ON_SUCCESS"), default=False
            ),
            locale_mode=normalize_locale_mode(os.environ.get("FLUXION_LOCALE_MODE", "auto")),
            ui_locale=normalize_locale(os.environ.get("FLUXION_UI_LOCALE", "en")),
            slack_typing_heartbeat_sec=max(
                0, _parse_int(os.environ.get("FLUXION_SLACK_TYPING_HEARTBEAT_SEC"), default=6)
            ),
            slack_running_update_sec=max(
                0, _parse_int(os.environ.get("FLUXION_SLACK_RUNNING_UPDATE_SEC"), default=30)
            ),
            slack_enabled=_parse_bool(os.environ.get("FLUXION_SLACK_ENABLED"), default=True),
            slack_bot_token=os.environ.get("SLACK_BOT_TOKEN", ""),
            slack_app_token=os.environ.get("SLACK_APP_TOKEN", ""),
            slack_signing_secret=os.environ.get("SLACK_SIGNING_SECRET", ""),
            usage_panel_enabled=_parse_bool(
                os.environ.get("FLUXION_USAGE_PANEL_ENABLED"), default=True
            ),
            usage_refresh_sec=max(
                30, _parse_int(os.environ.get("FLUXION_USAGE_REFRESH_SEC"), default=60)
            ),
            autoping_enabled=_parse_bool(os.environ.get("FLUXION_AUTOPING_ENABLED"), default=False),
            autoping_max_attempts=max(
                1, _parse_int(os.environ.get("FLUXION_AUTOPING_MAX_ATTEMPTS"), default=12)
            ),
            usage_providers=_parse_usage_providers(
                os.environ.get("FLUXION_USAGE_PROVIDERS", "claude,codex,antigravity")
            ),
            claude_code_user_agent=os.environ.get(
                "FLUXION_CLAUDE_CODE_USER_AGENT", "claude-code/2.0.0"
            ).strip()
            or "claude-code/2.0.0",
            claude_usage_token=os.environ.get("FLUXION_CLAUDE_USAGE_TOKEN", "").strip(),
            claude_usage_keychain=_parse_bool(
                os.environ.get("FLUXION_CLAUDE_USAGE_KEYCHAIN"), default=False
            ),
            claude_usage_auto_refresh=_parse_bool(
                os.environ.get("FLUXION_CLAUDE_USAGE_AUTO_REFRESH"), default=False
            ),
            codex_usage_mode=_parse_codex_usage_mode(
                os.environ.get("FLUXION_CODEX_USAGE_MODE", "auto")
            ),
            codex_usage_base_url=os.environ.get("FLUXION_CODEX_USAGE_BASE_URL", "").strip(),
            codex_history_reconciliation=_parse_bool(
                os.environ.get("FLUXION_CODEX_HISTORY_RECONCILIATION"), default=False
            ),
            scheduler_enabled=_parse_bool(
                os.environ.get("FLUXION_SCHEDULER_ENABLED"), default=True
            ),
            scheduler_tick_sec=max(
                0, _parse_int(os.environ.get("FLUXION_SCHEDULER_TICK_SEC"), default=0)
            ),
            menu_slack_notify_refresh=_parse_bool(
                os.environ.get("FLUXION_MENU_SLACK_NOTIFY_REFRESH"), default=False
            ),
            menu_telegram_notify_refresh=_parse_bool(
                os.environ.get("FLUXION_MENU_TELEGRAM_NOTIFY_REFRESH"), default=False
            ),
            menu_qqbot_notify_refresh=_parse_bool(
                os.environ.get("FLUXION_MENU_QQBOT_NOTIFY_REFRESH"), default=False
            ),
            menu_feishu_notify_refresh=_parse_bool(
                os.environ.get("FLUXION_MENU_FEISHU_NOTIFY_REFRESH"), default=False
            ),
            menu_wechat_notify_refresh=_parse_bool(
                os.environ.get("FLUXION_MENU_WECHAT_NOTIFY_REFRESH"), default=False
            ),
            menu_line_notify_refresh=_parse_bool(
                os.environ.get("FLUXION_MENU_LINE_NOTIFY_REFRESH"), default=False
            ),
            menu_macos_notify_refresh=_parse_bool(
                os.environ.get("FLUXION_MENU_MACOS_NOTIFY_REFRESH"), default=True
            ),
            notify_credit_grant=_parse_bool(
                os.environ.get("FLUXION_NOTIFY_CREDIT_GRANT"), default=False
            ),
            notify_credit_expiry=_parse_bool(
                os.environ.get("FLUXION_NOTIFY_CREDIT_EXPIRY"), default=False
            ),
            scheduler_slack_channel=os.environ.get("FLUXION_SCHEDULER_SLACK_CHANNEL", "").strip(),
            wechat_enabled=_parse_bool(os.environ.get("FLUXION_WECHAT_ENABLED"), default=False),
            wechat_default_workspace=os.environ.get("FLUXION_WECHAT_DEFAULT_WORKSPACE", "").strip(),
            wechat_allowed_users={
                x.strip()
                for x in os.environ.get("FLUXION_WECHAT_ALLOWED_USERS", "").split(",")
                if x.strip()
            },
            wechat_message_max_chars=max(
                100,
                _parse_int(os.environ.get("FLUXION_WECHAT_MESSAGE_MAX_CHARS"), default=4096),
            ),
            wechat_typing_heartbeat_sec=max(
                0,
                _parse_int(os.environ.get("FLUXION_WECHAT_TYPING_HEARTBEAT_SEC"), default=8),
            ),
            telegram_enabled=_parse_bool(os.environ.get("FLUXION_TELEGRAM_ENABLED"), default=False),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_default_workspace=os.environ.get(
                "FLUXION_TELEGRAM_DEFAULT_WORKSPACE", ""
            ).strip(),
            telegram_allowed_users={
                x.strip()
                for x in os.environ.get("FLUXION_TELEGRAM_ALLOWED_USERS", "").split(",")
                if x.strip()
            },
            line_enabled=_parse_bool(os.environ.get("FLUXION_LINE_ENABLED"), default=False),
            line_channel_secret=os.environ.get("LINE_CHANNEL_SECRET", "").strip(),
            line_channel_access_token=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip(),
            line_default_workspace=os.environ.get("FLUXION_LINE_DEFAULT_WORKSPACE", "").strip(),
            line_allowed_users={
                x.strip()
                for x in os.environ.get("FLUXION_LINE_ALLOWED_USERS", "").split(",")
                if x.strip()
            },
            line_tunnel_name=os.environ.get("FLUXION_LINE_TUNNEL_NAME", "fluxion-line").strip(),
            qqbot_enabled=_parse_bool(os.environ.get("FLUXION_QQBOT_ENABLED"), default=False),
            qqbot_app_id=os.environ.get("QQBOT_APP_ID", "").strip(),
            qqbot_client_secret=os.environ.get("QQBOT_CLIENT_SECRET", "").strip(),
            qqbot_sandbox=_parse_bool(os.environ.get("FLUXION_QQBOT_SANDBOX"), default=False),
            qqbot_transport=(
                os.environ.get("FLUXION_QQBOT_TRANSPORT", "websocket").strip().lower()
                or "websocket"
            ),
            qqbot_default_workspace=os.environ.get("FLUXION_QQBOT_DEFAULT_WORKSPACE", "").strip(),
            qqbot_allowed_users={
                x.strip()
                for x in os.environ.get("FLUXION_QQBOT_ALLOWED_USERS", "").split(",")
                if x.strip()
            },
            qqbot_allow_group_chat=_parse_bool(
                os.environ.get("FLUXION_QQBOT_ALLOW_GROUP_CHAT"), default=True
            ),
            feishu_enabled=_parse_bool(os.environ.get("FLUXION_FEISHU_ENABLED"), default=False),
            feishu_app_id=os.environ.get("FEISHU_APP_ID", "").strip(),
            feishu_app_secret=os.environ.get("FEISHU_APP_SECRET", "").strip(),
            feishu_default_workspace=os.environ.get("FLUXION_FEISHU_DEFAULT_WORKSPACE", "").strip(),
            feishu_allowed_users={
                x.strip()
                for x in os.environ.get("FLUXION_FEISHU_ALLOWED_USERS", "").split(",")
                if x.strip()
            },
            feishu_allow_group_chat=_parse_bool(
                os.environ.get("FLUXION_FEISHU_ALLOW_GROUP_CHAT"), default=True
            ),
            inbox_ttl_hours=max(
                0, _parse_int(os.environ.get("FLUXION_INBOX_TTL_HOURS"), default=24)
            ),
        )

    def validate(self, *, require_slack: bool | None = None) -> None:
        del require_slack
        # Channel-specific credentials are validated by the gateway when it
        # starts each adapter, so one incomplete chat integration cannot prevent
        # the rest of the gateway from running.
        self._validate_claude_settings()

    def _validate_claude_settings(self) -> None:
        if self.claude_provider == "third_party" and not self.claude_base_url:
            raise ValueError(
                "FLUXION_CLAUDE_BASE_URL is required when FLUXION_CLAUDE_PROVIDER=third_party."
            )
        if self.claude_auth_mode == "api_key" and not self.claude_api_key:
            raise ValueError(
                "FLUXION_CLAUDE_API_KEY is required when FLUXION_CLAUDE_AUTH_MODE=api_key."
            )
        if self.claude_auth_mode == "auth_token" and not self.claude_auth_token:
            raise ValueError(
                "FLUXION_CLAUDE_AUTH_TOKEN is required when FLUXION_CLAUDE_AUTH_MODE=auth_token."
            )
        if self.claude_provider == "third_party" and self.claude_auth_mode == "login":
            raise ValueError(
                "FLUXION_CLAUDE_AUTH_MODE=login is only supported with FLUXION_CLAUDE_PROVIDER=official."
            )

    def resolve_project(self, project_key: str | None) -> ProjectConfig | None:
        key = (project_key or "").strip()
        if not key:
            return None
        project = self.projects.get(key)
        if project is None:
            allowed = ", ".join(sorted(self.projects)) or "(none configured)"
            raise ValueError(f"Unknown Fluxion project: {key}. Configured projects: {allowed}")
        return project

    def resolve_workspace(self, raw_workspace: str | None) -> Path:
        if raw_workspace:
            path = Path(raw_workspace).expanduser()
            if not path.is_absolute():
                path = (self.workspace_root / path).resolve()
            else:
                path = path.resolve()
        else:
            path = self.workspace_root

        if not path.exists() or not path.is_dir():
            raise ValueError(f"Workspace does not exist or is not a directory: {path}")
        if not any(_is_within(path, root) for root in self.allowed_workspaces):
            roots = ", ".join(str(p) for p in self.allowed_workspaces)
            raise ValueError(f"Workspace is not in allowed roots: {roots}")
        return path

    def default_channel_workspace(self) -> str | None:
        """Return the implicit workspace for IM channels.

        Installed desktop builds use the managed checkout as
        ``FLUXION_WORKSPACE_ROOT`` while ``FLUXION_ALLOWED_WORKSPACES`` points
        at the user's project. An empty per-channel default should therefore
        target the first allowed workspace instead of the install directory.
        """
        if self.allowed_workspaces:
            return str(self.allowed_workspaces[0])
        return None

    def resolve_run_workspace(
        self,
        *,
        raw_workspace: str | None,
        project_key: str | None = None,
        mode: str = "read-only",
    ) -> Path:
        return self.authorize_run_workspace(
            raw_workspace=raw_workspace,
            project_key=project_key,
            mode=mode,
        ).require_allowed()

    def authorize_run_workspace(
        self,
        *,
        raw_workspace: str | None,
        project_key: str | None = None,
        mode: str = "read-only",
    ) -> WorkspaceAuthorization:
        # Workspace authorization is a first-class service.  Keep this
        # compatibility method so existing channels and callers retain their
        # public Settings API while MCP/Web runners can share a hot-loaded
        # service instance.  The service reads the managed JSON file on every
        # authorization and reloads legacy settings when available.
        from fluxion.workspace.access import WorkspaceAccessService

        return WorkspaceAccessService(
            self,
            settings_loader=type(self).reload,
        ).authorize_run_workspace(
            raw_workspace=raw_workspace,
            project_key=project_key,
            mode=mode,
            client_id="settings",
            request_if_denied=False,
        )

    def _resolve_workspace_path(self, raw_workspace: str | None, *, base: Path) -> Path:
        if raw_workspace:
            path = Path(raw_workspace).expanduser()
            if not path.is_absolute():
                path = (base / path).resolve()
            else:
                path = path.resolve()
        else:
            path = base
        return path

    def _authorize_workspace_path(
        self,
        *,
        path: Path,
        mode: str,
        project: ProjectConfig | None,
    ) -> WorkspaceAuthorization:
        if not path.exists() or not path.is_dir():
            return WorkspaceAuthorization(
                allowed=False,
                reason=f"Workspace does not exist or is not a directory: {path}",
                policy="missing",
                workspace=path,
            )
        denied = _first_containing_root(path, self.denied_workspaces)
        if denied is not None:
            return WorkspaceAuthorization(
                allowed=False,
                reason=f"Workspace is denied by FLUXION_DENIED_WORKSPACES: {denied}",
                policy="denied",
                workspace=path,
            )
        autoping_dir = self.data_dir / "autoping_workspace"
        if path == autoping_dir or _is_within(path, autoping_dir):
            return WorkspaceAuthorization(
                allowed=True,
                reason="Workspace is the managed Auto Ping workspace",
                policy="autoping",
                workspace=path,
            )
        if project is not None:
            return WorkspaceAuthorization(
                allowed=True,
                reason=f"Workspace allowed by registered project: {project.key}",
                policy="project",
                workspace=path,
            )
        allowed = _first_containing_root(path, self.allowed_workspaces)
        if allowed is not None:
            return WorkspaceAuthorization(
                allowed=True,
                reason=f"Workspace allowed by FLUXION_ALLOWED_WORKSPACES: {allowed}",
                policy="allowed-workspace",
                workspace=path,
            )
        if mode == "workspace-write":
            write_allowed = _first_containing_root(path, self.write_allowed_workspaces)
            if write_allowed is not None:
                return WorkspaceAuthorization(
                    allowed=True,
                    reason=f"Workspace write allowed by FLUXION_WRITE_ALLOWED_WORKSPACES: {write_allowed}",
                    policy="write-allowed-workspace",
                    workspace=path,
                )
            return WorkspaceAuthorization(
                allowed=False,
                reason=(
                    "Workspace-write runs require a registered project, "
                    "FLUXION_ALLOWED_WORKSPACES, or FLUXION_WRITE_ALLOWED_WORKSPACES. "
                    f"Requested workspace: {path}"
                ),
                policy="write-not-authorized",
                workspace=path,
            )
        trusted = _first_containing_root(path, self.trusted_workspace_roots)
        if trusted is not None:
            if self.workspace_discovery and _is_git_repo(path):
                return WorkspaceAuthorization(
                    allowed=True,
                    reason=f"Read-only workspace allowed by trusted Git root: {trusted}",
                    policy="trusted-git-read",
                    workspace=path,
                )
            return WorkspaceAuthorization(
                allowed=False,
                reason=(
                    "Workspace is under FLUXION_TRUSTED_WORKSPACE_ROOTS but is not "
                    "an allowed Git repo for discovery. Set FLUXION_WORKSPACE_DISCOVERY=true "
                    f"and use a Git repository root. Requested workspace: {path}"
                ),
                policy="trusted-root-not-discovered",
                workspace=path,
            )
        roots = ", ".join(str(p) for p in self.allowed_workspaces)
        trusted_roots = ", ".join(str(p) for p in self.trusted_workspace_roots)
        return WorkspaceAuthorization(
            allowed=False,
            reason=(
                f"Workspace is not authorized: {path}. "
                f"Allowed roots: {roots or '(none)'}. "
                f"Trusted roots: {trusted_roots or '(none)'}."
            ),
            policy="not-authorized",
            workspace=path,
        )

    def resolve_workspace_for_event(
        self,
        *,
        channel_id: str,
        channel_type: str,
        raw_workspace: str | None,
    ) -> Path:
        # Channel mode uses fixed workspace mapping and ignores per-message override.
        if channel_type in {"channel", "group"} and self.slack_allow_channels:
            mapped = self.slack_channel_workspaces.get(channel_id)
            if mapped is None:
                raise ValueError(
                    "channel is not mapped to a workspace. Configure FLUXION_SLACK_CHANNEL_WORKSPACES."
                )
            return self.resolve_workspace(str(mapped))
        return self.resolve_workspace(raw_workspace or self.default_channel_workspace())
