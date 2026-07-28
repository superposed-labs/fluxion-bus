from __future__ import annotations

from pathlib import Path

from PIL import Image

from fluxion.channels.attachments import DownloadedFile, normalize_downloaded_files
from fluxion.core.models.task import Task
from fluxion.executors.prompt_builder import RAW_PROMPT_MODE, AgentPromptBuilder, is_raw_prompt


def _task(tmp_path: Path, **metadata: object) -> Task:
    return Task.create(
        channel="local",
        user_id="local",
        text="fix the parser",
        workspace=tmp_path,
        metadata=dict(metadata),
    )


def test_default_mode_wraps_the_task_in_fluxion_framing(tmp_path):
    prompt = AgentPromptBuilder().build(_task(tmp_path))
    assert "running inside Fluxion" in prompt
    assert "FINAL_ANSWER" in prompt
    assert "fix the parser" in prompt


def test_raw_mode_sends_the_task_and_nothing_else(tmp_path):
    # A host that supplies its own system framing must not be handed Fluxion's
    # preamble on top of it, nor the IM-only FINAL_ANSWER/ACTIONS_JSON contract.
    prompt = AgentPromptBuilder().build(_task(tmp_path, prompt_mode=RAW_PROMPT_MODE))
    assert prompt == "fix the parser"


def test_raw_mode_still_reports_the_workspace_interpreter(tmp_path):
    # Runtime guidance is a fact about this machine, not framing: the caller
    # cannot know it, so dropping it would cost the agent real information.
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").touch()

    prompt = AgentPromptBuilder().build(_task(tmp_path, prompt_mode=RAW_PROMPT_MODE))
    assert prompt.startswith("fix the parser")
    assert str(venv_bin / "python") in prompt
    assert "FINAL_ANSWER" not in prompt


def test_raw_mode_requires_the_exact_marker(tmp_path):
    # A typo must fail closed to the documented default rather than silently
    # stripping the answer contract the IM channels depend on.
    assert not is_raw_prompt(_task(tmp_path, prompt_mode="RAW-ish"))
    assert "FINAL_ANSWER" in AgentPromptBuilder().build(_task(tmp_path, prompt_mode="RAW-ish"))


def test_image_path_is_bridged_only_when_executor_lacks_native_delivery(tmp_path):
    inbox = tmp_path / ".fluxion_inbox" / "abc"
    inbox.mkdir(parents=True)
    path = inbox / "private.png"
    Image.new("RGB", (8, 8)).save(path)
    _, images = normalize_downloaded_files([DownloadedFile(path=path, media_type="image/png")])
    task = Task.create(
        channel="slack",
        user_id="user",
        text="describe it",
        workspace=tmp_path,
        image_attachments=images,
    )

    bridged = AgentPromptBuilder().build(task)
    native = AgentPromptBuilder().build(
        task,
        native_image_media_types={"image/png"},
    )

    assert ".fluxion_inbox/abc/private.png" in bridged
    assert "Do not mention file names" in bridged
    assert "private.png" not in native
    assert native.endswith("User request:\ndescribe it\n")


def test_prepared_attachment_prompt_is_not_appended_twice(tmp_path):
    task = _task(tmp_path, attachment_prompt_prepared=True)
    task.text = "describe it\n\nInternal attachment file(s): one.png"

    prompt = AgentPromptBuilder().build(task)

    assert prompt.count("Internal attachment file(s)") == 1
