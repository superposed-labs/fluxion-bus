from __future__ import annotations

from pathlib import Path

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
    def build(self, task: Task) -> str:
        resumed = bool(str(task.metadata.get("executor_session_id", "")).strip())
        runtime_block = self._format_runtime(task.workspace)
        if is_raw_prompt(task):
            # Runtime guidance still goes through: it is a fact about this
            # machine's workspace, not framing, and the caller cannot know it.
            return f"{task.text}\n\n{runtime_block}" if runtime_block else task.text
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
            f"User request:\n{task.text}\n"
        )

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
