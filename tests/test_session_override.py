from __future__ import annotations

import tempfile
from pathlib import Path

from fluxion.channels.control_formatter import render_control_response
from fluxion.core.engine import GatewayCore
from fluxion.core.router import TaskRouter
from fluxion.core.session_manager import SessionManager
from fluxion.core.storage import JsonlStorage


def test_session_executor_override():
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = JsonlStorage(Path(tmp_dir))
        manager = SessionManager(storage=storage)

        convo_key = "slack:C123:T456"
        channel = "slack"
        user_id = "U123"

        # 1. No override initially
        assert (
            manager.get_executor_override(
                conversation_key=convo_key, channel=channel, user_id=user_id
            )
            is None
        )

        # 2. Set override
        manager.set_executor_override(
            conversation_key=convo_key,
            channel=channel,
            user_id=user_id,
            executor_name="claude",
        )
        assert (
            manager.get_executor_override(
                conversation_key=convo_key, channel=channel, user_id=user_id
            )
            == "claude"
        )

        # 3. Reload from storage
        manager2 = SessionManager(storage=storage)
        assert (
            manager2.get_executor_override(
                conversation_key=convo_key, channel=channel, user_id=user_id
            )
            == "claude"
        )

        # 4. Reset
        manager2.reset(conversation_key=convo_key, channel=channel, user_id=user_id)
        assert (
            manager2.get_executor_override(
                conversation_key=convo_key, channel=channel, user_id=user_id
            )
            is None
        )

        # 5. Reload again to verify reset is persisted
        manager3 = SessionManager(storage=storage)
        assert (
            manager3.get_executor_override(
                conversation_key=convo_key, channel=channel, user_id=user_id
            )
            is None
        )


def test_session_model_override_persists_and_resets():
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = JsonlStorage(Path(tmp_dir))
        manager = SessionManager(storage=storage)

        convo_key = "slack:C123:T456"
        channel = "slack"
        user_id = "U123"

        assert (
            manager.get_model_override(
                conversation_key=convo_key,
                channel=channel,
                user_id=user_id,
                executor_name="Codex",
            )
            is None
        )

        manager.set_model_override(
            conversation_key=convo_key,
            channel=channel,
            user_id=user_id,
            executor_name="Codex",
            model="gpt-5.4-mini",
        )
        assert (
            manager.get_model_override(
                conversation_key=convo_key,
                channel=channel,
                user_id=user_id,
                executor_name="codex",
            )
            == "gpt-5.4-mini"
        )

        manager2 = SessionManager(storage=storage)
        assert (
            manager2.get_model_override(
                conversation_key=convo_key,
                channel=channel,
                user_id=user_id,
                executor_name="codex",
            )
            == "gpt-5.4-mini"
        )

        manager2.reset(conversation_key=convo_key, channel=channel, user_id=user_id)
        assert (
            manager2.get_model_override(
                conversation_key=convo_key,
                channel=channel,
                user_id=user_id,
                executor_name="codex",
            )
            is None
        )

        manager3 = SessionManager(storage=storage)
        assert (
            manager3.get_model_override(
                conversation_key=convo_key,
                channel=channel,
                user_id=user_id,
                executor_name="codex",
            )
            is None
        )


def test_reset_command_returns_english_messages():
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = JsonlStorage(Path(tmp_dir))
        manager = SessionManager(storage=storage)
        gateway = GatewayCore(
            router=TaskRouter(executors={}, default_executor="codex"),
            storage=storage,
            sessions=manager,
            artifact_max_files=10,
            worker_count=1,
            max_pending_per_user=5,
            max_retries=0,
            retry_backoff_sec=1,
            change_detection="off",
            revert_capture="structured",
        )

        empty_response = gateway.handle_control_command(
            text="reset",
            user_id="U123",
            convo_key="slack:C123:T456",
            channel="slack",
        )
        assert (
            render_control_response(empty_response, channel="cli")
            == "[Fluxion] No conversation memory to clear for this session."
        )

        manager.set_executor_override(
            conversation_key="slack:C123:T456",
            channel="slack",
            user_id="U123",
            executor_name="claude",
        )
        cleared_response = gateway.handle_control_command(
            text="reset",
            user_id="U123",
            convo_key="slack:C123:T456",
            channel="slack",
        )
        assert (
            render_control_response(cleared_response, channel="cli")
            == "[Fluxion] Conversation memory and thread overrides cleared."
        )
