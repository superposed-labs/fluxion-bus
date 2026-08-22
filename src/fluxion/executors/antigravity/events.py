"""agy's ``--output-format stream-json`` event stream.

`agy --print` on its own prints nothing until the very end and then dumps the
whole answer at once, which is why this executor used to poll agy's private
conversation database to see a run happening. `--output-format stream-json`
replaces that with one JSON object per line on stdout, so both the answer and
the working notes arrive on a channel agy documents.

What the stream still does not carry is thinking: `usage.thinking_tokens`
counts tokens agy never emits any text for. Working notes here are therefore a
feed of tool activity, not a train of thought — the same promise the trajectory
poller made, from a supported source.

Shape, as observed on agy 1.1.14::

    {"event":"init","conversation_id":"…","init":{"cwd":"…","permission_mode":"…"}}
    {"event":"step_update","step_update":{"step_index":3,"state":"ACTIVE",
        "step_type":"tool","tool_name":"view_file","tool_info":{"name":"view_file",
        "parameters":{"AbsolutePath":"…"}}}}
    {"event":"step_update","step_update":{"step_index":4,"state":"DONE",
        "step_type":"agent_response","text_delta":"FINAL_ANSWER:…"}}
    {"event":"result","result":{"status":"SUCCESS","response":"…","usage":{…}}}

`text_delta` chunks are incremental: concatenating every one of them reproduces
`result.response` byte for byte (measured against a live run, 1142 characters
over four chunks). Everything here relies on that, because the answer must grow
monotonically — the executor streams it to channels as a prefix delta.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

_CLIP_LIMIT = 80

# The parameter that says what a tool call is *about*. agy names its path
# parameters the same way in the stream as in the trajectory rows that
# `fluxion.workspace.antigravity_trajectory` already reads, so this reuses that
# key list rather than inventing a second vocabulary.
_PATH_KEYS = ("TargetFile", "AbsolutePath", "FilePath", "Path", "DirectoryPath")
_QUERY_KEYS = ("Query", "Pattern", "Url", "ToolName")


def iter_agy_events(stdout: str) -> list[dict[str, Any]]:
    """Every stream-json event in `stdout`, in order.

    Non-JSON lines are skipped rather than surfaced: once the stream has
    started, anything else on stdout is noise agy wrote around it, and letting
    it through would put raw text into an answer that callers render to end
    users. A truncated trailing line (the run is still in flight) is simply not
    an event yet.
    """
    events: list[dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("event"):
            events.append(value)
    return events


def agy_answer_text(stdout: str) -> str:
    """The model's text, reassembled from the stream.

    Falls back to `stdout` verbatim when there are no events at all — agy
    printing something that is not the event stream (a crash notice before the
    stream opens) is still the most useful thing we have. Once one event has
    landed, only the stream counts.
    """
    events = iter_agy_events(stdout)
    if not events:
        return stdout or ""
    parts = [delta for event in events if (delta := _agent_response_delta(event))]
    if parts:
        return "".join(parts)
    # A turn that produced no deltas can still carry a response on the result
    # event. Reached only when `parts` is empty, so the answer never jumps
    # backwards mid-stream.
    result = _last_result(events)
    return str(result.get("response") or "") if result else ""


def agy_reasoning_text(stdout: str) -> str:
    """The tools the run reached for, one line each.

    Every tool surfaces at least twice — ACTIVE when agy starts it, DONE when it
    lands — carrying the same parameters both times, so lines are keyed by step
    index and rendered once. A step that ends in ERROR gets a second line,
    because a failed tool is the one thing in this feed worth interrupting for
    and nothing else in the run reports it.

    Grows monotonically: the executor streams it as a prefix delta.
    """
    lines: list[str] = []
    seen: set[int] = set()
    failed: set[int] = set()
    for event in iter_agy_events(stdout):
        update = event.get("step_update")
        if not isinstance(update, dict) or update.get("step_type") != "tool":
            continue
        index = update.get("step_index")
        if not isinstance(index, int):
            continue
        described = describe_tool_step(update)
        if not described:
            continue
        if index not in seen:
            seen.add(index)
            lines.append(described)
        if str(update.get("state") or "").upper() == "ERROR" and index not in failed:
            failed.add(index)
            lines.append(f"{described} failed")
    return "\n\n".join(lines)


def describe_tool_step(update: dict[str, Any]) -> str:
    """One readable line for a tool step, e.g. ``view_file(agytest/a.txt)``.

    Commands are rendered as the command line rather than the tool name,
    matching how the trajectory poller rendered them: seeing what actually ran
    is the point.
    """
    info = update.get("tool_info")
    parameters = info.get("parameters") if isinstance(info, dict) else None
    name = str(update.get("tool_name") or "").strip()
    if not isinstance(parameters, dict):
        return name
    command = _first_string(parameters, ("CommandLine",))
    if command:
        return f"$ {_clip(command)}"
    if not name:
        return ""
    subject = _first_string(parameters, _PATH_KEYS)
    if subject:
        # Absolute paths are mostly workspace prefix, and the line is read at a
        # glance in a collapsed disclosure. The last two segments locate the
        # file without pushing the useful part past the clip.
        if subject.startswith("/"):
            subject = "/".join(pathlib.PurePath(subject).parts[-2:])
    else:
        subject = _first_string(parameters, _QUERY_KEYS)
    return f"{name}({_clip(subject)})" if subject else name


def agy_conversation_id(stdout: str) -> str:
    """The conversation agy opened, straight from the stream.

    agy also names it in its log a few seconds into the run, which is where
    this used to be scraped from; the event carries it on the first line.
    """
    for event in iter_agy_events(stdout):
        conversation_id = str(event.get("conversation_id") or "").strip()
        if conversation_id:
            return conversation_id
    return ""


def agy_result_error(stdout: str) -> str:
    """A terminal failure the stream reported, or "" when there is nothing to say.

    agy exits zero and prints an empty answer when a run is cut short — a
    print-timeout, a canceled context, a tool it was not allowed to run — and
    without this the executor reads that as a success whose summary is the
    empty-stdout fallback ("Task completed."), which is a lie the user sees.

    A non-empty response wins over the status: a run that answered and *then*
    tripped over its own housekeeping (agy reports `ERROR`/`context canceled`
    for a turn that killed its own background task, measured) has given the
    caller what it asked for.
    """
    result = _last_result(iter_agy_events(stdout))
    if not result:
        return ""
    status = str(result.get("status") or "").strip().upper()
    if status in ("", "SUCCESS"):
        return ""
    if str(result.get("response") or "").strip():
        return ""
    detail = str(result.get("error") or "").strip()
    reason = f": {detail}" if detail else ""
    return f"AntiGravity ended with status {status} before it answered{reason}"


def _agent_response_delta(event: dict[str, Any]) -> str:
    update = event.get("step_update")
    if not isinstance(update, dict) or update.get("step_type") != "agent_response":
        return ""
    return str(update.get("text_delta") or "")


def _last_result(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        result = event.get("result")
        if isinstance(result, dict):
            return result
    return None


def _first_string(parameters: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = parameters.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().replace("\n", " ")
    return ""


def _clip(subject: str, limit: int = _CLIP_LIMIT) -> str:
    if len(subject) <= limit:
        return subject
    return subject[: limit - 3] + "..."
