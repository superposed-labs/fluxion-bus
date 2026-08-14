from __future__ import annotations

import json
import pathlib
import re
import shlex
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ClaudeEventCapture:
    is_stream_json: bool = False
    final_message: str = ""
    session_id: str = ""
    resolved_model: str = ""
    changed_files: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    operations: list[dict[str, Any]] = field(default_factory=list)
    # Token totals for the whole run, taken from the terminal `result` event.
    token_usage: dict[str, int] = field(default_factory=dict)


def extract_claude_stream_message(stdout: str) -> str:
    """Return the latest user-facing Claude stream-json answer, if complete."""
    message = ""
    for event in _iter_jsonl_events(stdout):
        for text in _assistant_text_blocks(event):
            if _has_final_answer_marker(text):
                message = text.strip()
        result = event.get("result")
        if isinstance(result, str) and _has_final_answer_marker(result):
            message = result.strip()
    return message


def extract_claude_stream_text(stdout: str) -> str:
    """Return all assistant text so far, with no FINAL_ANSWER gate.

    ``extract_claude_stream_message`` deliberately withholds output until a
    message carries the marker, because under Fluxion's own prompt contract
    everything before it is scratch work an IM user should not see.

    A caller running with ``RAW_PROMPT_MODE`` never gets that marker, so the
    gated reader returns "" for the entire run — the consumer would see silence
    and then a single blob, or nothing at all. Here the narration is the point:
    it is what keeps a live window live and a stream from idling out.

    Two event shapes are read, and both are needed:

    - ``assistant`` events, which arrive only once a whole message is finished.
      On their own they make a live view lurch: nothing for twenty seconds, then
      the entire answer at once.
    - ``stream_event`` text deltas from ``--include-partial-messages``, which
      arrive token by token while the message is still being written.

    The two overlap by design: a partial run is held in ``pending`` and dropped
    the moment the finished message supersedes it, so text is never counted
    twice and the returned string only ever grows — which is what the caller's
    ``current[sent_len:]`` delta arithmetic depends on.
    """
    parts: list[str] = []
    pending = ""
    for event in _iter_jsonl_events(stdout):
        blocks = _assistant_text_blocks(event)
        if blocks:
            parts.extend(blocks)
            pending = ""
            continue
        pending += _partial_text_delta(event)
    return "\n\n".join([*parts, pending] if pending else parts)


def _partial_text_delta(event: dict[str, Any]) -> str:
    """Text from one ``--include-partial-messages`` chunk, if it carries any.

    Deliberately narrow: thinking blocks and tool-call arguments stream through
    the same event type with a different delta type, and neither belongs in the
    user-visible answer.
    """
    if event.get("type") != "stream_event":
        return ""
    inner = event.get("event")
    if not isinstance(inner, dict) or inner.get("type") != "content_block_delta":
        return ""
    delta = inner.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return ""
    return str(delta.get("text") or "")


def extract_claude_stream_reasoning(stdout: str) -> str:
    """The agent's visible working — its thinking, and the tools it reached for.

    Separate from `extract_claude_stream_text` because the consumer renders it
    somewhere else. Codex shows reasoning in the collapsed disclosure above a
    sub-agent's answer; sending this as ordinary output text would dump the
    scratch work into the answer instead.

    Tool calls are rendered as one short line each rather than forwarded as
    structured calls. The sub-agent thread that displays this executes no tools,
    so a real `function_call` would be an instruction to run something; a line of
    prose is a description of something that already ran here.

    Grows monotonically for the same reason as the text reader: an in-flight
    `thinking_delta` run is held in `pending` and dropped once the finished
    message supersedes it, so nothing is counted twice.
    """
    parts: list[str] = []
    pending = ""
    for event in _iter_jsonl_events(stdout):
        if event.get("type") == "stream_event":
            pending += _partial_thinking_delta(event)
            continue
        thinking = _assistant_thinking_blocks(event)
        if thinking:
            parts.extend(thinking)
            pending = ""
        for tool in _tool_uses(event):
            described = describe_tool_use(tool)
            if described:
                parts.append(described)
    return "\n\n".join([*parts, pending] if pending else parts)


def _partial_thinking_delta(event: dict[str, Any]) -> str:
    inner = event.get("event")
    if not isinstance(inner, dict) or inner.get("type") != "content_block_delta":
        return ""
    delta = inner.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "thinking_delta":
        return ""
    return str(delta.get("thinking") or "")


def _assistant_thinking_blocks(event: dict[str, Any]) -> list[str]:
    if event.get("type") != "assistant":
        return []
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    texts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            text = str(block.get("thinking") or "").strip()
            if text:
                texts.append(text)
    return texts


# What to show for each tool, chosen so one line says what the agent touched.
# A tool that is not listed still gets its name, which beats silence.
_TOOL_SUBJECT = {
    "read": "file_path",
    "edit": "file_path",
    "write": "file_path",
    "notebookedit": "notebook_path",
    "grep": "pattern",
    "glob": "pattern",
    "bash": "command",
    "webfetch": "url",
}


def describe_tool_use(tool: dict[str, Any]) -> str:
    """One readable line for a tool call, e.g. ``Read(README.md)``."""
    name = str(tool.get("name") or "").strip()
    if not name:
        return ""
    tool_input = tool.get("input")
    key = _TOOL_SUBJECT.get(name.lower())
    subject = ""
    if key and isinstance(tool_input, dict):
        subject = str(tool_input.get(key) or "").strip().replace("\n", " ")
    if not subject:
        return name
    if key and key.endswith("_path") and subject.startswith("/"):
        # Absolute paths are mostly workspace prefix, and the line is read at a
        # glance in a collapsed disclosure. The last two segments locate the file
        # without pushing the useful part past the truncation limit.
        subject = "/".join(pathlib.PurePath(subject).parts[-2:])
    if len(subject) > 80:
        subject = subject[:77] + "..."
    return f"{name}({subject})"


def parse_claude_stream_events(stdout: str, *, workspace: Path) -> ClaudeEventCapture:
    events = _load_jsonl_events(stdout)
    if not events:
        return ClaudeEventCapture()

    workspace = workspace.resolve()
    changed_files: OrderedDict[str, None] = OrderedDict()
    final_message = ""
    session_id = ""
    resolved_model = ""
    token_usage: dict[str, int] = {}
    shell_mutation_without_paths = False
    # Edits/creates are recorded as they stream; Write is deferred until its
    # tool_result lands, because only the result text distinguishes a freshly
    # created file ("File created successfully") from an overwrite — and that
    # decides whether a revert may safely delete the file.
    operations: list[dict[str, Any]] = []
    pending_writes: dict[str, dict[str, Any]] = {}
    write_results: dict[str, str] = {}

    for event in events:
        current_session = str(event.get("session_id") or "").strip()
        if current_session:
            session_id = current_session
        current_model = str(event.get("model") or "").strip()
        if current_model:
            resolved_model = current_model

        result = event.get("result")
        if isinstance(result, str) and result.strip():
            final_message = result.strip()

        # The terminal `result` event carries Claude's own total for the run.
        # Take it rather than summing per-message usage: Claude re-logs the same
        # turn 2-4x as a streaming artifact, so hand-summing inflates the figure
        # two to three times over.
        if event.get("type") == "result":
            usage = _token_usage(event.get("usage"))
            if usage:
                token_usage = usage

        for text in _assistant_text_blocks(event):
            if _has_final_answer_marker(text):
                final_message = text.strip()

        for tool_use_id, result_text in _tool_results(event):
            if tool_use_id in pending_writes:
                write_results[tool_use_id] = result_text

        for tool in _tool_uses(event):
            name = str(tool.get("name") or "").strip().lower()
            tool_input = tool.get("input")
            if not isinstance(tool_input, dict):
                continue

            if name == "write":
                rel_path = _normalize_workspace_path(
                    tool_input.get("file_path"), workspace=workspace
                )
                if rel_path:
                    changed_files.setdefault(rel_path, None)
                    pending_writes[str(tool.get("id") or "")] = {
                        "path": rel_path,
                        "content": tool_input.get("content"),
                    }
                continue

            if name in {"edit", "multiedit"}:
                rel_path = _normalize_workspace_path(
                    tool_input.get("file_path"), workspace=workspace
                )
                if rel_path:
                    changed_files.setdefault(rel_path, None)
                    edits = _claude_edits(name, tool_input)
                    if edits:
                        operations.append({"op": "edit", "path": rel_path, "edits": edits})
                continue

            if name in {"notebookedit", "notebook_edit"}:
                rel_path = _normalize_workspace_path(
                    tool_input.get("notebook_path"), workspace=workspace
                )
                if rel_path:
                    changed_files.setdefault(rel_path, None)
                    # Notebook edits mutate structured JSON cells, not plain text
                    # fragments, so they are not stream-revertible.
                    operations.append({"op": "update", "path": rel_path})
                continue

            if name == "bash":
                command = str(tool_input.get("command") or "").strip()
                if not _command_is_clearly_mutating(command):
                    continue
                paths = _paths_from_shell_command(command, workspace=workspace)
                if paths:
                    for path in paths:
                        changed_files.setdefault(path, None)
                else:
                    shell_mutation_without_paths = True

    for write_id, info in pending_writes.items():
        result_text = write_results.get(write_id, "")
        op = "create" if _is_create_result(result_text) else "overwrite"
        operations.append({"op": op, "path": info["path"], "content": info["content"]})

    risk_flags: list[str] = []
    if shell_mutation_without_paths:
        risk_flags.extend(["shell_side_effects_detected", "changed_files_may_be_incomplete"])

    return ClaudeEventCapture(
        is_stream_json=True,
        final_message=final_message,
        session_id=session_id,
        resolved_model=resolved_model,
        changed_files=list(changed_files.keys()),
        risk_flags=risk_flags,
        operations=operations,
        token_usage=token_usage,
    )


def _token_usage(raw: Any) -> dict[str, int]:
    """Normalize Claude's usage block to the names `fluxion.usage` uses.

    Only non-zero values are kept, so an executor that reports nothing is
    distinguishable from one that genuinely used zero tokens.
    """
    if not isinstance(raw, dict):
        return {}
    mapping = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "cache_creation_input_tokens": "cache_creation_tokens",
        "cache_read_input_tokens": "cache_read_tokens",
    }
    usage: dict[str, int] = {}
    for source, target in mapping.items():
        value = raw.get(source)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            usage[target] = value
    return usage


def _is_create_result(result_text: str) -> bool:
    return "file created successfully" in (result_text or "").lower()


def _claude_edits(name: str, tool_input: dict[str, Any]) -> list[dict[str, Any]]:
    raw_edits: list[Any]
    if name == "multiedit":
        raw_edits = tool_input.get("edits") if isinstance(tool_input.get("edits"), list) else []
    else:
        raw_edits = [tool_input]
    edits: list[dict[str, Any]] = []
    for raw in raw_edits:
        if not isinstance(raw, dict):
            continue
        old = raw.get("old_string")
        new = raw.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            continue
        edits.append({"old": old, "new": new, "replace_all": bool(raw.get("replace_all"))})
    return edits


def _load_jsonl_events(stdout: str) -> list[dict[str, Any]]:
    lines = [line for line in (stdout or "").splitlines() if line.strip()]
    if not lines:
        return []

    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return []
        if not isinstance(value, dict):
            return []
        events.append(value)
    return events


def _iter_jsonl_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _assistant_text_blocks(event: dict[str, Any]) -> list[str]:
    if event.get("type") != "assistant":
        return []
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = str(block.get("text") or "").strip()
            if text:
                texts.append(text)
    return texts


def _tool_uses(event: dict[str, Any]) -> list[dict[str, Any]]:
    if event.get("type") != "assistant":
        return []
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        block for block in content if isinstance(block, dict) and block.get("type") == "tool_use"
    ]


def _tool_results(event: dict[str, Any]) -> list[tuple[str, str]]:
    if event.get("type") != "user":
        return []
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    results: list[tuple[str, str]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        tool_use_id = str(block.get("tool_use_id") or "")
        if not tool_use_id:
            continue
        results.append((tool_use_id, _tool_result_text(block.get("content"))))
    return results


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return " ".join(part for part in parts if part)
    return ""


def _has_final_answer_marker(text: str) -> bool:
    return bool(re.search(r"(?m)^\s*FINAL_ANSWER:?\s*$", text or ""))


def _normalize_workspace_path(raw_path: Any, *, workspace: Path) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return ""
    try:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = workspace / path
        resolved = path.resolve(strict=False)
        rel = resolved.relative_to(workspace)
    except Exception:
        return ""
    value = rel.as_posix()
    if not value or value.startswith("../"):
        return ""
    return value


def _paths_from_shell_command(command: str, *, workspace: Path) -> list[str]:
    text = _unwrap_shell_command(command)
    try:
        tokens = shlex.split(text)
    except ValueError:
        return []
    if not tokens:
        return []

    paths: OrderedDict[str, None] = OrderedDict()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        command_name = Path(token).name
        if command_name in {"rm", "touch", "mkdir", "rmdir", "cp", "mv", "ln"}:
            index = _collect_command_paths(tokens, index + 1, workspace=workspace, paths=paths)
            continue
        if token in {">", ">>"} and index + 1 < len(tokens):
            rel_path = _normalize_workspace_path(tokens[index + 1], workspace=workspace)
            if rel_path:
                paths.setdefault(rel_path, None)
            index += 2
            continue
        match = re.match(r"^>{1,2}(.+)$", token)
        if match:
            rel_path = _normalize_workspace_path(match.group(1), workspace=workspace)
            if rel_path:
                paths.setdefault(rel_path, None)
        index += 1
    return list(paths.keys())


def _collect_command_paths(
    tokens: list[str],
    index: int,
    *,
    workspace: Path,
    paths: OrderedDict[str, None],
) -> int:
    while index < len(tokens):
        token = tokens[index]
        if token in {"&&", ";", "|", "||"}:
            return index + 1
        if token.startswith("-"):
            index += 1
            continue
        rel_path = _normalize_workspace_path(token, workspace=workspace)
        if rel_path:
            paths.setdefault(rel_path, None)
        index += 1
    return index


def _command_is_clearly_mutating(command: str) -> bool:
    text = _unwrap_shell_command(command)
    if not text:
        return False
    lowered = text.lower()
    patterns = [
        r"(^|[;&|]\s*)(rm|mv|cp|touch|mkdir|rmdir|ln)\b",
        r"(^|[;&|]\s*)(sed|perl)\s+[^;&|]*\s-[^\s;&|]*i",
        r"(^|[;&|]\s*)git\s+(apply|checkout|clean|mv|rm|reset|restore)\b",
        r"(^|[;&|]\s*)python[0-9.]*\s+-c\s+['\"][^'\"]*(write_text|open\([^)]*,\s*['\"]w|unlink\()",
        r"(^|[;&|]\s*)node\s+-e\s+['\"][^'\"]*(writefile|rmsync|unlinksync)",
    ]
    if any(re.search(pattern, lowered) for pattern in patterns):
        return True
    return bool(re.search(r"(^|[^<])>>?\s*[^&\s]", text))


def _unwrap_shell_command(command: str) -> str:
    text = (command or "").strip()
    if not text:
        return ""
    try:
        parts = shlex.split(text)
    except ValueError:
        return text
    if (
        len(parts) >= 3
        and Path(parts[0]).name in {"sh", "bash", "zsh"}
        and parts[1]
        in {
            "-c",
            "-lc",
        }
    ):
        return parts[2]
    return text
