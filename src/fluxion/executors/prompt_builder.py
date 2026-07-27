from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from fluxion.core.models.attachment import Attachment, ImageAttachment
from fluxion.core.models.task import Task

# Set as ``Task.metadata["prompt_mode"]`` by callers that own the prompt in full.
#
# Fluxion's default framing exists for IM channels: it names the runtime, and it
# defines the FINAL_ANSWER/ACTIONS_JSON contract used to lift a user-facing reply
# and file uploads out of the transcript. A host that already sends its own
# system framing — Codex, which supplies each role's ``developer_instructions``
# — gets nothing from that and pays for it twice: once in tokens, and again in a
# conflicting instruction about how to shape the answer.
RAW_PROMPT_MODE = "raw"


def is_raw_prompt(task: Task) -> bool:
    return str(task.metadata.get("prompt_mode", "")).strip() == RAW_PROMPT_MODE


class AgentPromptBuilder:
    def build(
        self,
        task: Task,
        *,
        native_image_media_types: Collection[str] = (),
    ) -> str:
        resumed = bool(str(task.metadata.get("executor_session_id", "")).strip())
        runtime_block = self._format_runtime(task.workspace)
        user_request = self._with_attachments(
            task,
            native_image_media_types={
                media_type.strip().lower() for media_type in native_image_media_types
            },
        )
        if is_raw_prompt(task):
            # Runtime guidance still goes through: it is a fact about this
            # machine's workspace, not framing, and the caller cannot know it.
            return f"{user_request}\n\n{runtime_block}" if runtime_block else user_request
        instruction_block = (
            "Continue this existing executor session and answer the new user request.\n"
            "Use ACTIONS_JSON.upload_files only when the user explicitly asks to send/upload files.\n"
            "Output in this exact structure:\n"
            "FINAL_ANSWER:\n"
            "<your short user-facing answer>\n"
            "ACTIONS_JSON:\n"
            '{"upload_files": []}\n\n'
            if resumed
            else "Complete the user task in the current workspace. "
            "Prefer minimal, safe edits and summarize final outcomes.\n"
            "Use ACTIONS_JSON.upload_files only when the user explicitly asks to send/upload files.\n"
            "At the end, output in this exact structure:\n"
            "FINAL_ANSWER:\n"
            "<your short user-facing answer>\n"
            "ACTIONS_JSON:\n"
            '{"upload_files": []}\n\n'
        )
        return (
            "You are an AI coding agent running inside Fluxion.\n"
            f"{instruction_block}"
            f"Task ID: {task.id}\n"
            f"User ID: {task.user_id}\n"
            f"Workspace: {task.workspace}\n\n"
            f"{runtime_block}"
            f"User request:\n{user_request}\n"
        )

    def _with_attachments(
        self,
        task: Task,
        *,
        native_image_media_types: set[str],
    ) -> str:
        if task.metadata.get("attachment_prompt_prepared"):
            return task.text
        bridged: list[Attachment] = list(task.attachments)
        bridged.extend(
            attachment
            for attachment in task.image_attachments
            if attachment.media_type.lower() not in native_image_media_types
        )
        lines = [
            self._describe_attachment(attachment, workspace=task.workspace)
            for attachment in bridged
        ]
        if lines:
            note = (
                "Internal attachment file(s). Inspect them to answer the user's request. "
                "Do not mention file names, paths, attachment numbers, or delivery details "
                "in the final answer:\n" + "\n".join(lines)
            )
            if task.text.strip():
                return f"{task.text.rstrip()}\n\n{note}"
            return (
                "The user sent file attachment(s) without a text instruction. "
                "Inspect them and respond appropriately.\n\n"
                f"{note}"
            )
        if task.text.strip():
            return task.text
        if task.image_attachments:
            return "Inspect the attached image(s) and respond appropriately."
        return ""

    @staticmethod
    def _describe_attachment(attachment: Attachment, *, workspace: Path) -> str:
        details = attachment.media_type
        if isinstance(attachment, ImageAttachment):
            details += f", {attachment.width}x{attachment.height}"
        return f"{attachment.ordinal}. {attachment.path.relative_to(workspace)} ({details})"

    def _format_runtime(self, workspace: Path) -> str:
        venv_python = workspace / ".venv" / "bin" / "python"
        venv_pip = workspace / ".venv" / "bin" / "pip"
        if not venv_python.exists():
            return ""
        lines = [
            "Runtime guidance:",
            f"- Prefer this Python interpreter for scripts: {venv_python}",
        ]
        if venv_pip.exists():
            lines.append(f"- If dependency install is needed, use: {venv_pip}")
        return "\n".join(lines) + "\n\n"
