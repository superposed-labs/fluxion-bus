"""Agent-path derivation for MCP sub-agent runs.

``task_name`` is free-form human text: it must be slugified into a valid path
segment rather than rejected for containing spaces, uppercase, or hyphens.
"""

from __future__ import annotations

import pytest

from fluxion.subagent import agent_path_for_run


def test_human_readable_task_name_is_slugified():
    path = agent_path_for_run(
        parent_path="/root",
        task_name="Hero asset git-strategy opinion",
        fallback_thread="mcp-fresh:abc",
    )
    assert path == "/root/hero_asset_git_strategy_opinion"


def test_already_clean_task_name_is_unchanged():
    path = agent_path_for_run(
        parent_path="/root",
        task_name="hero_git_strategy_opinion",
        fallback_thread="mcp-fresh:abc",
    )
    assert path == "/root/hero_git_strategy_opinion"


def test_empty_task_name_falls_back_to_thread_slug():
    path = agent_path_for_run(
        parent_path="/root",
        task_name="",
        fallback_thread="mcp-fresh:AB-12",
    )
    assert path == "/root/mcp_fresh_ab_12"


def test_slugified_task_name_is_stable():
    kwargs = dict(parent_path="/root", fallback_thread="t")
    assert agent_path_for_run(task_name="Fix Gateway Retry", **kwargs) == agent_path_for_run(
        task_name="fix gateway retry", **kwargs
    )


def test_reserved_root_segment_still_rejected():
    with pytest.raises(ValueError):
        agent_path_for_run(parent_path="/root", task_name="root", fallback_thread="t")
