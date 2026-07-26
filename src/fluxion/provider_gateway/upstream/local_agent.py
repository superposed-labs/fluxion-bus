"""Back a Codex sub-agent thread with a local agent CLI.

This is the "wrap, don't proxy" path. Codex's sub-agent asks for a model
inference; instead of forwarding that to a vendor API we run one of Fluxion's
existing executors (Claude Code, Antigravity, `codex exec`) and render its
output as a Responses event stream. The user's subscription does the work and no
per-token API charge is incurred.

What this can and cannot do, stated plainly because it shapes the whole design:

- Codex renders a real native sub-agent card and right-hand window, and the
  agent's output streams into it live.
- Codex's sub-agent thread executes **no tools**. We emit text only, never
  `function_call` events, so the native trace shows narration rather than file
  reads and commands — those happen inside the local agent's own loop.

The sub-agent thread being inert removes *one* writer from the workspace, but
not all of them. `spawn_agent` does not block — Codex exposes a separate `wait`
tool — so the main Codex agent keeps executing its own tools while a sub-agent
runs. The lock below serializes local agents against each other; it cannot
serialize them against Codex's own process. Concurrent edits from the main agent
remain possible and are a known limitation of this mode.

It lives under `upstream/` because it occupies the upstream slot in the request
path, even though nothing here speaks HTTP.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fluxion.core.models.task import Task
from fluxion.executors.base import Executor, enforces_read_only
from fluxion.executors.prompt_builder import RAW_PROMPT_MODE
from fluxion.provider_gateway.capabilities import ModelCapabilities
from fluxion.provider_gateway.stream import (
    EV_COMPLETED,
    EV_CREATED,
    EV_FAILED,
    EV_OUTPUT_ITEM_ADDED,
    EV_OUTPUT_ITEM_DONE,
    EV_OUTPUT_TEXT_DELTA,
    EV_REASONING_SUMMARY_TEXT_DELTA,
)

log = logging.getLogger(__name__)

# Sentinels distinguishing what a queued chunk is. Reasoning and answer text
# land in different Codex items, so the bridge has to keep them apart.
_DONE = object()
_ANSWER = "answer"
_REASONING = "reasoning"

# Workspace locks are module-level, not per-adapter. Two providers (say a Claude
# one and an Antigravity one) can be pointed at the same repository; if each held
# its own lock they would not exclude each other and would edit the tree
# simultaneously. Keyed by resolved path so "." and an absolute path to the same
# directory share a lock.
_WORKSPACE_LOCKS: dict[Path, asyncio.Lock] = {}


def _workspace_lock(workspace: Path) -> asyncio.Lock:
    return _WORKSPACE_LOCKS.setdefault(_resolve(workspace), asyncio.Lock())


def _resolve(workspace: Path) -> Path:
    try:
        return workspace.resolve()
    except OSError:
        # A path we cannot resolve still needs *a* stable key; falling back to
        # the literal path is better than skipping the lock entirely.
        return workspace


# Classifies the assistant message we emit (`MessagePhase` in
# codex-rs/protocol/src/models.rs, serialized snake_case).
#
# Optional in the protocol — "callers must treat None as phase unknown" — but
# omitting it costs the native sub-agent window its content. Codex's own
# sub-agent threads tag every assistant item, `commentary` for mid-turn
# narration and `final_answer` for the terminal text, and the right-hand window
# renders the `final_answer` one. An untagged item is recorded in the transcript
# and reaches the parent, so the turn looks entirely successful while that pane
# stays blank.
#
# We emit both per turn: narration as it streams, then the terminal answer.
_PHASE_COMMENTARY = "commentary"
_PHASE_FINAL_ANSWER = "final_answer"

# Rough characters-per-token ratio used to size the sub-thread's context.
#
# An estimate is the right tool here, not a compromise. This number feeds
# Codex's auto-compaction threshold (`auto_compact_scope_tokens` vs
# `auto_compact_scope_limit` in core/src/session/turn.rs) — it is a trigger
# input, not a bill. Being 10% off moves compaction slightly; carrying a
# tokenizer dependency to be exact would buy nothing.
#
# The money figure is separate and exact: see `LocalAgentRun.token_usage`.
_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class LocalAgentRun:
    """The outcome of one executor invocation."""

    success: bool
    summary: str
    session_id: str
    changed_files: tuple[str, ...] = ()
    # What the local agent actually consumed, as its own CLI reported it. This
    # is subscription quota, not API tokens, and it is deliberately NOT what we
    # report to Codex — see `_completed`.
    token_usage: Mapping[str, int] = field(default_factory=dict)


@dataclass
class LocalAgentUpstream:
    """Adapts one Fluxion executor to the Responses event protocol."""

    provider_id: str
    executor: Executor
    # Declared per model exactly as an API-backed provider would be, so the
    # router's capability filter needs no special case.
    models: Mapping[str, ModelCapabilities] = field(default_factory=dict)

    def capabilities(self, model: str) -> ModelCapabilities:
        return self.models.get(model, ModelCapabilities())

    async def stream(
        self,
        body: Mapping[str, Any],
        model: str,
        *,
        workspace: Path,
        session_id: str = "",
        request_id: str | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        read_only: bool = False,
    ) -> AsyncIterator[Mapping[str, Any]]:
        """Run the agent and yield Responses events as its output arrives."""
        response_id = request_id or f"resp_{uuid.uuid4().hex[:24]}"
        answer_id = f"msg_{uuid.uuid4().hex[:24]}"
        prompt = extract_prompt(body)
        if not prompt:
            yield _failed(response_id, "no user input found in the request")
            return

        if read_only and not enforces_read_only(self.executor):
            # Refuse rather than downgrade. The role file told the user this
            # agent cannot change anything; running it anyway would break that
            # promise silently, in their real workspace.
            yield _failed(
                response_id,
                f"executor {self.executor.name()!r} cannot run read-only, and this role "
                "requires it. Route this role to an executor that can, or drop the "
                "role's read-only sandbox declaration.",
            )
            return

        async with _workspace_lock(workspace):
            yield _created(response_id, model)

            # Exactly one item is open at a time, and switching channels closes
            # the current one first.
            #
            # This is not tidiness. Codex routes a delta to whichever item is
            # *currently active* — `response.output_text.delta` carries no item
            # id its parser reads (`core/src/session/turn.rs`) — so leaving a
            # reasoning item open while answer text streams would file the
            # answer under the reasoning disclosure.
            open_channel: str | None = None
            open_id = ""
            # Chunks of the item currently open, cleared on every switch.
            buffer: list[str] = []
            # Every answer chunk of the whole run, which is what sizes the
            # sub-thread's context and stands in for a missing summary.
            narration: list[str] = []
            # Reasoning likewise: Codex keeps it in the transcript, so it
            # counts toward the thread's size.
            thought: list[str] = []

            async for channel, chunk, run in self._run(
                prompt, model, workspace, session_id, is_cancelled, read_only
            ):
                if chunk is not None:
                    if channel != open_channel:
                        if open_channel is not None:
                            yield _close(response_id, open_channel, open_id, "".join(buffer))
                        open_channel, open_id, buffer = channel, _fresh_id(), []
                        yield _open(response_id, open_channel, open_id)
                    buffer.append(chunk)
                    if channel == _ANSWER:
                        narration.append(chunk)
                    else:
                        thought.append(chunk)
                    yield _channel_delta(response_id, open_channel, open_id, chunk)
                    continue

                streamed = "".join(narration)
                answer = (run.summary if run is not None else "") or streamed
                if open_channel is not None:
                    yield _close(response_id, open_channel, open_id, "".join(buffer), answer=answer)
                # The terminal answer always gets its own `final_answer` item: a
                # half-written final answer is not something a UI can show, so a
                # lone one would leave the native window blank until it lands.
                yield _item_added(response_id, answer_id, _PHASE_FINAL_ANSWER)
                yield _text_delta(response_id, answer_id, answer)
                yield _item_done(response_id, answer_id, answer, _PHASE_FINAL_ANSWER)

                if run is not None and not run.success:
                    yield _failed(response_id, run.summary or "local agent run failed")
                else:
                    yield _completed(
                        response_id,
                        model,
                        run,
                        _context_usage(body, streamed, "".join(thought)),
                    )

    async def _run(
        self,
        prompt: str,
        model: str,
        workspace: Path,
        session_id: str,
        is_cancelled: Callable[[], bool] | None,
        read_only: bool,
    ) -> AsyncIterator[tuple[str | None, str | None, LocalAgentRun | None]]:
        """Bridge the blocking executor onto the event loop.

        `Executor.execute` is synchronous and can run for minutes, so it goes on
        a worker thread and reports back through a queue. Output is forwarded as
        it arrives rather than at the end — a five-minute silence would trip
        Codex's `stream_idle_timeout_ms`, and a live window is the entire point
        of this mode.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        cancelled = threading.Event()

        task = Task(
            id=f"codex-subagent-{uuid.uuid4().hex[:12]}",
            channel="codex-provider",
            user_id="codex",
            text=prompt,
            workspace=workspace,
            created_at=datetime.now(UTC),
            metadata={
                "source": "provider_gateway",
                "model": model,
                # Codex already supplies the framing: the role's
                # `developer_instructions` arrive in the request and are part of
                # `prompt`. Letting Fluxion prepend its own IM-oriented preamble
                # would duplicate the framing, spend tokens on it, and hand the
                # agent a second, conflicting answer format (FINAL_ANSWER /
                # ACTIONS_JSON) that means nothing on this path.
                "prompt_mode": RAW_PROMPT_MODE,
                # Lets the executor resume its previous session for a follow-up
                # turn on the same sub-thread instead of starting cold.
                "resume_session_id": session_id,
                # Enforced by the executor, because the role file's
                # `sandbox_mode` binds a Codex thread that runs no tools.
                "read_only": read_only,
            },
        )

        def post(item: Any) -> None:
            """Hand an item to the loop thread, tolerating a shut-down loop.

            After a cancelled request the consumer is gone and the loop may
            already be closed, but this thread keeps running until the executor
            notices. Posting into a closed loop raises, so the worker would die
            with a spurious traceback that masks real failures.
            """
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                log.debug("dropping local agent output: event loop is gone")

        def on_output(chunk: str) -> None:
            post((_ANSWER, chunk))

        def on_reasoning(chunk: str) -> None:
            post((_REASONING, chunk))

        def worker() -> None:
            try:
                result = self.executor.execute(
                    task,
                    cancel_requested=cancelled.is_set,
                    stream_output=on_output,
                    stream_reasoning=on_reasoning,
                )
                run = LocalAgentRun(
                    success=result.success,
                    summary=result.summary,
                    session_id=result.executor_session_id,
                    changed_files=tuple(result.changed_files),
                    token_usage=dict(result.token_usage),
                )
            except Exception as err:  # noqa: BLE001 - surfaced as a failed turn
                log.exception("local agent %s raised", self.provider_id)
                run = LocalAgentRun(
                    success=False, summary=f"{type(err).__name__}: {err}", session_id=""
                )
            post((_DONE, run))

        thread = threading.Thread(
            target=worker, name=f"local-agent-{self.provider_id}", daemon=True
        )
        thread.start()

        try:
            while True:
                channel, payload = await queue.get()
                if channel is _DONE:
                    yield None, None, payload
                    return
                if is_cancelled is not None and is_cancelled():
                    cancelled.set()
                yield channel, str(payload), None
        except asyncio.CancelledError:
            # Codex hung up. Tell the executor to stop rather than letting it
            # keep burning the user's subscription on output nobody will read.
            cancelled.set()
            raise


def extract_prompt(body: Mapping[str, Any]) -> str:
    """Build the prompt for the local agent from a Responses request.

    Two parts, and both matter:

    - The role's `developer_instructions`, which Codex sends as `role:
      "developer"` input items (see codex-rs .../world_state/collaboration_mode.rs,
      whose legacy matcher keys on `role == "developer"`). This is what makes an
      "explorer" behave differently from a "reviewer"; dropping it would make
      every role send an identical prompt and differ only by model.
    - The latest user message, which is the task the parent just delegated. For
      a spawned sub-agent this is an `agent_message` item, which carries no
      `role` at all — hence the `None` arm below — and splits its task across
      two content parts (see `_text_of`).

    The rest of the history is deliberately left out. A spawned sub-agent's task
    is that last message, and replaying the parent's whole transcript into a
    fresh agent session buries it. Continuity across turns comes from resuming
    the agent's own session instead (see `Task.metadata["resume_session_id"]`).
    """
    items = body.get("input")
    if isinstance(items, str):
        return items.strip()
    if not isinstance(items, list):
        return ""

    instructions: list[str] = []
    task = ""
    for item in reversed(items):
        if not isinstance(item, Mapping):
            continue
        role = item.get("role")
        text = _text_of(item.get("content"))
        if not text:
            continue
        if role == "developer":
            # Reversed iteration, so prepend to keep the authored order.
            instructions.insert(0, text)
        elif role in (None, "user") and not task:
            task = text

    if not task:
        return ""
    if not instructions:
        return task
    return "\n\n".join([*instructions, task])


def _text_of(content: Any) -> str:
    """Flatten a content array, including the part that carries a spawn payload.

    A spawned sub-agent's task does not arrive as ordinary `input_text`. Codex
    splits an `agent_message` into a plaintext envelope and the payload beside
    it (codex-rs protocol/src/protocol.rs, `to_model_input_item`):

        {"type": "input_text", "text": "Message Type: NEW_TASK\\nTask name: …\\nPayload:\\n"}
        {"type": "encrypted_content", "encrypted_content": "<the task>"}

    Reading only `text` yields the envelope with an empty `Payload:` — the
    sub-agent then runs with nothing but its role instructions and improvises,
    which is exactly what it looks like from the parent: a sub-agent that
    "didn't receive the task".

    The `encrypted_content` name describes the wire field, not the value: the
    CLI passes the `spawn_agent` tool's `message` argument through verbatim
    (codex-rs core/src/tools/handlers/multi_agents_v2.rs,
    `communication_from_tool_message`). If a payload ever does arrive genuinely
    opaque, forwarding it is still strictly better than forwarding nothing.
    """
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if not isinstance(part, Mapping):
            continue
        for key in ("text", "encrypted_content"):
            value = part.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
                break
    return "\n".join(parts).strip()


# ── event constructors ───────────────────────────────────────────────
# Shapes follow codex-rs/core/tests/common/responses.rs, which is what Codex's
# own test server emits and therefore what its parser is written against.


def _created(response_id: str, model: str) -> dict[str, Any]:
    return {
        "type": EV_CREATED,
        "response": {"id": response_id, "model": model, "status": "in_progress"},
    }


def _fresh_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


def _open(response_id: str, channel: str, item_id: str) -> dict[str, Any]:
    if channel == _REASONING:
        return _reasoning_added(response_id, item_id)
    return _item_added(response_id, item_id, _PHASE_COMMENTARY)


def _channel_delta(response_id: str, channel: str, item_id: str, chunk: str) -> dict[str, Any]:
    if channel == _REASONING:
        return _reasoning_delta(response_id, item_id, chunk)
    return _text_delta(response_id, item_id, chunk)


def _close(
    response_id: str, channel: str, item_id: str, text: str, *, answer: str = ""
) -> dict[str, Any]:
    """Commit the open item.

    The deltas already drove the live view; this is what lands in the
    transcript, which is why the reasoning summary is restated here rather than
    left empty.
    """
    if channel == _REASONING:
        return _reasoning_done(response_id, item_id, text)
    # Trim the terminal answer off the narration tail so it is not stored twice
    # and counted twice against the sub-thread's context on a follow-up turn.
    return _item_done(
        response_id, item_id, _narration_only(text, answer) if answer else text, _PHASE_COMMENTARY
    )


def _reasoning_added(response_id: str, item_id: str) -> dict[str, Any]:
    return {
        "type": EV_OUTPUT_ITEM_ADDED,
        "response_id": response_id,
        "output_index": 0,
        "item": {"id": item_id, "type": "reasoning", "summary": []},
    }


def _reasoning_delta(response_id: str, item_id: str, delta: str) -> dict[str, Any]:
    return {
        "type": EV_REASONING_SUMMARY_TEXT_DELTA,
        "response_id": response_id,
        "item_id": item_id,
        "output_index": 0,
        # Codex's parser requires this alongside the delta; one running summary
        # per item is all we produce.
        "summary_index": 0,
        "delta": delta,
    }


def _reasoning_done(response_id: str, item_id: str, text: str) -> dict[str, Any]:
    return {
        "type": EV_OUTPUT_ITEM_DONE,
        "response_id": response_id,
        "output_index": 0,
        "item": {
            "id": item_id,
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": text}] if text else [],
        },
    }


def _narration_only(streamed: str, answer: str) -> str:
    """The streamed text with the terminal answer trimmed off its tail.

    The deltas already carried everything, but the `done` item is what lands in
    the transcript. Trimming here keeps the answer from being stored twice and
    counted twice against the sub-thread's context on a follow-up turn.

    When the agent produced a single message the whole stream *is* the answer;
    there is no narration to keep, and returning it unchanged would duplicate it.
    """
    if not answer or streamed == answer or not streamed.endswith(answer):
        return "" if streamed == answer else streamed
    return streamed[: -len(answer)].rstrip()


def _item_added(response_id: str, item_id: str, phase: str) -> dict[str, Any]:
    return {
        "type": EV_OUTPUT_ITEM_ADDED,
        "response_id": response_id,
        "output_index": 0,
        "item": {
            "id": item_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "phase": phase,
        },
    }


def _text_delta(response_id: str, item_id: str, delta: str) -> dict[str, Any]:
    return {
        "type": EV_OUTPUT_TEXT_DELTA,
        "response_id": response_id,
        "item_id": item_id,
        "output_index": 0,
        "content_index": 0,
        "delta": delta,
    }


def _item_done(response_id: str, item_id: str, text: str, phase: str) -> dict[str, Any]:
    return {
        "type": EV_OUTPUT_ITEM_DONE,
        "response_id": response_id,
        "output_index": 0,
        "item": {
            "id": item_id,
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
            "phase": phase,
        },
    }


def estimate_tokens(text: str) -> int:
    """Approximate token count for threshold purposes only."""
    return max(1, len(text) // _CHARS_PER_TOKEN) if text else 0


def _context_usage(body: Mapping[str, Any], answer: str, reasoning: str = "") -> dict[str, int]:
    """Size of *this sub-thread's* conversation, which is what Codex asked for.

    Explicitly not the local agent's consumption. A Claude Code run burns tokens
    across a dozen internal tool-loop turns; reporting that total would tell
    Codex the sub-thread is at hundreds of thousands of context after a single
    exchange, and it would compact immediately and repeatedly. The two numbers
    answer different questions and must not be conflated.

    Reasoning counts toward the output. Codex stores the reasoning item in the
    sub-thread transcript just like the answer, and raw Claude thinking is often
    longer than the answer it produces — leaving it out would under-report the
    thread's size and delay compaction past the point it was needed.
    """
    input_tokens = estimate_tokens(json.dumps(body.get("input", ""), ensure_ascii=False))
    instructions = body.get("instructions")
    if isinstance(instructions, str):
        input_tokens += estimate_tokens(instructions)
    output_tokens = estimate_tokens(answer) + estimate_tokens(reasoning)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def _completed(
    response_id: str,
    model: str,
    run: LocalAgentRun | None,
    context_usage: Mapping[str, int],
) -> dict[str, Any]:
    return {
        "type": EV_COMPLETED,
        "response": {
            "id": response_id,
            "model": model,
            "status": "completed",
            # Estimated size of this sub-thread, for Codex's compaction maths.
            "usage": dict(context_usage),
            "fluxion": {
                "executor_session_id": run.session_id if run else "",
                "changed_files": list(run.changed_files) if run else [],
                # The exact subscription cost of the run, for Fluxion's usage
                # layer to record with billing_source="subscription".
                "agent_token_usage": dict(run.token_usage) if run else {},
                "estimated_context_usage": True,
            },
        },
    }


def _failed(response_id: str, message: str) -> dict[str, Any]:
    return {
        "type": EV_FAILED,
        "response": {
            "id": response_id,
            "status": "failed",
            "error": {"type": "local_agent_error", "message": message},
        },
    }
