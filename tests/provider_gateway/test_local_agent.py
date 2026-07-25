"""Local agent CLI backing a Codex sub-agent thread."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from fluxion.core.models.result import ExecutionResult
from fluxion.executors.prompt_builder import RAW_PROMPT_MODE, AgentPromptBuilder
from fluxion.provider_gateway.capabilities import TOOL_CALLING, ModelCapabilities
from fluxion.provider_gateway.stream import (
    EV_COMPLETED,
    EV_CREATED,
    EV_FAILED,
    EV_OUTPUT_ITEM_ADDED,
    EV_OUTPUT_ITEM_DONE,
    EV_OUTPUT_TEXT_DELTA,
)
from fluxion.provider_gateway.upstream.local_agent import (
    LocalAgentUpstream,
    extract_prompt,
)


class FakeExecutor:
    """Stands in for a real agent CLI, with the same blocking contract."""

    def __init__(self, chunks=("working…", " done"), success=True, raises=None, delay=0.0):
        self.chunks = chunks
        self.success = success
        self.raises = raises
        self.delay = delay
        self.seen_task = None
        self.cancelled_at = None
        self.finished = threading.Event()

    def name(self) -> str:
        return "fake"

    def supports(self, task) -> bool:
        return True

    def execute(self, task, cancel_requested=None, stream_output=None, stream_reasoning=None):
        self.seen_task = task
        try:
            if self.raises:
                raise self.raises
            for index, chunk in enumerate(self.chunks):
                if cancel_requested is not None and cancel_requested():
                    self.cancelled_at = index
                    break
                if stream_output:
                    stream_output(chunk)
                if self.delay:
                    time.sleep(self.delay)
            return ExecutionResult(
                success=self.success,
                summary="summary" if self.success else "it failed",
                stdout="",
                stderr="",
                exit_code=0 if self.success else 1,
                changed_files=["a.py"],
                executor_session_id="sess-123",
                token_usage={"input_tokens": 1200, "output_tokens": 340},
            )
        finally:
            self.finished.set()


def build(executor=None, **kwargs):
    return LocalAgentUpstream(
        provider_id="claude",
        executor=executor or FakeExecutor(),
        models={"opus": ModelCapabilities(frozenset({TOOL_CALLING}))},
        **kwargs,
    )


def run_stream(upstream, body=None, **kwargs):
    async def main():
        return [
            event
            async for event in upstream.stream(
                body or {"input": [{"role": "user", "content": "do the thing"}]},
                "opus",
                workspace=Path("/tmp/ws"),
                **kwargs,
            )
        ]

    return asyncio.run(main())


# ── prompt extraction ────────────────────────────────────────────────
def test_prompt_comes_from_the_last_user_message():
    """Replaying the parent's whole transcript would bury the actual task."""
    body = {
        "input": [
            {"role": "user", "content": "old task"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "the real task"},
        ]
    }
    assert extract_prompt(body) == "the real task"


def test_prompt_handles_structured_content():
    body = {
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "line one"},
                    {"type": "input_text", "text": "line two"},
                ],
            }
        ]
    }
    assert extract_prompt(body) == "line one\nline two"


def test_prompt_handles_a_plain_string_input():
    assert extract_prompt({"input": "just do it"}) == "just do it"


def test_missing_input_yields_no_prompt():
    assert extract_prompt({}) == ""
    assert extract_prompt({"input": []}) == ""


def test_request_without_a_prompt_fails_fast():
    events = run_stream(build(), body={"input": []})
    assert [e["type"] for e in events] == [EV_FAILED]


# ── event sequence ───────────────────────────────────────────────────
def test_emits_a_valid_responses_sequence():
    events = run_stream(build())
    assert [e["type"] for e in events] == [
        EV_CREATED,
        # Narration item: opened first so the native window has somewhere to
        # render while the agent is still working.
        EV_OUTPUT_ITEM_ADDED,
        EV_OUTPUT_TEXT_DELTA,
        EV_OUTPUT_TEXT_DELTA,
        EV_OUTPUT_ITEM_DONE,
        # Answer item.
        EV_OUTPUT_ITEM_ADDED,
        EV_OUTPUT_TEXT_DELTA,
        EV_OUTPUT_ITEM_DONE,
        EV_COMPLETED,
    ]


def _deltas_for(events, item_id):
    return [
        e["delta"] for e in events if e["type"] == EV_OUTPUT_TEXT_DELTA and e["item_id"] == item_id
    ]


def test_output_streams_as_it_arrives():
    """A five-minute silence would trip Codex's stream_idle_timeout_ms."""
    events = run_stream(build(FakeExecutor(chunks=("a", "b", "c"))))
    narration_id = next(e for e in events if e["type"] == EV_OUTPUT_ITEM_ADDED)["item"]["id"]
    assert _deltas_for(events, narration_id) == ["a", "b", "c"]


def test_answer_item_carries_the_executor_summary():
    events = run_stream(build(FakeExecutor(chunks=("hello ", "world"))))
    answer = [e for e in events if e["type"] == EV_OUTPUT_ITEM_DONE][-1]
    assert answer["item"]["content"][0]["text"] == "summary"
    assert answer["item"]["phase"] == "final_answer"


def test_narration_item_drops_the_answer_it_already_streamed():
    """Otherwise the answer is stored twice and counted twice against context."""
    executor = FakeExecutor(chunks=("looking around\n", "summary"))
    events = run_stream(build(executor))
    narration = [e for e in events if e["type"] == EV_OUTPUT_ITEM_DONE][0]
    assert narration["item"]["content"][0]["text"] == "looking around"


def test_single_message_run_leaves_no_narration_behind():
    """The whole stream was the answer, so keeping it would duplicate it."""
    events = run_stream(build(FakeExecutor(chunks=("summary",))))
    narration = [e for e in events if e["type"] == EV_OUTPUT_ITEM_DONE][0]
    assert narration["item"]["content"][0]["text"] == ""


def test_no_function_call_events_are_ever_emitted():
    """The sub-agent thread stays inert; tools run inside the local agent."""
    events = run_stream(build())
    assert not any("function_call" in e["type"] for e in events)


def test_item_and_response_ids_are_consistent():
    events = run_stream(build())
    created = next(e for e in events if e["type"] == EV_CREATED)
    delta = next(e for e in events if e["type"] == EV_OUTPUT_TEXT_DELTA)
    assert delta["response_id"] == created["response"]["id"]


# ── failure and cancellation ─────────────────────────────────────────
def test_failed_run_ends_with_a_failed_event():
    events = run_stream(build(FakeExecutor(success=False)))
    assert events[-1]["type"] == EV_FAILED
    assert "it failed" in events[-1]["response"]["error"]["message"]


def test_executor_exception_becomes_a_failed_event_not_a_crash():
    events = run_stream(build(FakeExecutor(raises=RuntimeError("boom"))))
    assert events[-1]["type"] == EV_FAILED
    assert "boom" in events[-1]["response"]["error"]["message"]


def test_cancellation_reaches_the_executor():
    """Otherwise the agent keeps burning subscription quota on unread output."""
    executor = FakeExecutor(chunks=tuple(str(i) for i in range(200)), delay=0.01)
    upstream = build(executor)

    async def main():
        stream = upstream.stream(
            {"input": [{"role": "user", "content": "go"}]},
            "opus",
            workspace=Path("/tmp/ws"),
        )
        async for event in stream:
            if event["type"] == EV_OUTPUT_TEXT_DELTA:
                await stream.aclose()
                break

    asyncio.run(main())
    assert executor.finished.wait(timeout=5)
    assert executor.cancelled_at is not None


def test_is_cancelled_callback_stops_the_run():
    executor = FakeExecutor(chunks=tuple(str(i) for i in range(200)), delay=0.005)
    events = run_stream(build(executor), is_cancelled=lambda: True)
    assert executor.finished.wait(timeout=5)
    assert executor.cancelled_at is not None
    assert events[-1]["type"] in (EV_COMPLETED, EV_FAILED)


# ── task construction ────────────────────────────────────────────────
def test_task_carries_workspace_model_and_session():
    executor = FakeExecutor()
    run_stream(build(executor), session_id="prev-session")
    task = executor.seen_task
    assert task.workspace == Path("/tmp/ws")
    assert task.text == "do the thing"
    assert task.metadata["model"] == "opus"
    assert task.metadata["resume_session_id"] == "prev-session"


def test_task_asks_for_a_raw_prompt():
    """Codex already sent the role's framing; Fluxion's own must not stack on it.

    Without this the agent receives two system preambles and two conflicting
    answer formats, and pays tokens for the IM-only one on every sub-agent turn.
    """
    executor = FakeExecutor()
    run_stream(build(executor))
    prompt = AgentPromptBuilder().build(executor.seen_task)

    assert executor.seen_task.metadata["prompt_mode"] == RAW_PROMPT_MODE
    assert prompt == executor.seen_task.text
    assert "FINAL_ANSWER" not in prompt


def test_completed_event_reports_the_executor_session():
    """Needed so a follow-up turn on the same sub-thread can resume it."""
    events = run_stream(build())
    completed = events[-1]
    assert completed["response"]["fluxion"]["executor_session_id"] == "sess-123"
    assert completed["response"]["fluxion"]["changed_files"] == ["a.py"]


def test_reported_usage_sizes_the_subthread_not_the_agent_run():
    """Codex uses response.usage for its compaction threshold.

    Reporting the local agent's own consumption would tell Codex the sub-thread
    is at hundreds of thousands of context after one exchange, triggering
    compaction immediately and repeatedly.
    """
    executor = FakeExecutor(chunks=("short answer",))
    executor.huge_usage = {"input_tokens": 500_000, "output_tokens": 40_000}
    completed = run_stream(build(executor))[-1]

    usage = completed["response"]["usage"]
    assert usage["input_tokens"] < 1000
    assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]


def test_reported_usage_is_never_zero():
    """Zero would mean Codex's auto-compaction never fires for this thread."""
    usage = run_stream(build())[-1]["response"]["usage"]
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0


def test_reported_usage_grows_with_the_conversation():
    small = run_stream(build(), body={"input": [{"role": "user", "content": "hi"}]})
    large = run_stream(build(), body={"input": [{"role": "user", "content": "x" * 4000}]})
    assert (
        large[-1]["response"]["usage"]["input_tokens"]
        > small[-1]["response"]["usage"]["input_tokens"]
    )


def test_real_subscription_usage_is_reported_separately_and_exactly():
    """The money figure is exact and lives beside the estimate, not inside it."""
    completed = run_stream(build())[-1]
    fluxion = completed["response"]["fluxion"]
    assert fluxion["agent_token_usage"] == {"input_tokens": 1200, "output_tokens": 340}
    assert fluxion["estimated_context_usage"] is True


def test_the_two_figures_are_not_conflated():
    completed = run_stream(build())[-1]
    reported = completed["response"]["usage"]["input_tokens"]
    actual = completed["response"]["fluxion"]["agent_token_usage"]["input_tokens"]
    assert reported != actual


# ── concurrency ──────────────────────────────────────────────────────
def test_runs_sharing_a_workspace_are_serialized():
    """Two agent CLIs editing one repo corrupt each other undetectably."""
    active = 0
    peak = 0
    lock = threading.Lock()

    class CountingExecutor(FakeExecutor):
        def execute(self, task, cancel_requested=None, stream_output=None, stream_reasoning=None):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            try:
                return super().execute(task, cancel_requested, stream_output)
            finally:
                with lock:
                    active -= 1

    upstream = build(CountingExecutor())

    async def main():
        async def one():
            return [
                e
                async for e in upstream.stream(
                    {"input": [{"role": "user", "content": "go"}]},
                    "opus",
                    workspace=Path("/tmp/shared"),
                )
            ]

        await asyncio.gather(one(), one(), one())

    asyncio.run(main())
    assert peak == 1


def test_developer_instructions_reach_the_local_agent():
    """Without these every role sends an identical prompt, differing only by model."""
    executor = FakeExecutor()
    run_stream(
        build(executor),
        body={
            "input": [
                {"role": "developer", "content": "Review like a code owner."},
                {"role": "user", "content": "check auth.py"},
            ]
        },
    )
    prompt = executor.seen_task.text
    assert "Review like a code owner." in prompt
    assert "check auth.py" in prompt


def test_developer_instructions_precede_the_task():
    executor = FakeExecutor()
    run_stream(
        build(executor),
        body={
            "input": [
                {"role": "developer", "content": "INSTRUCTIONS"},
                {"role": "user", "content": "TASK"},
            ]
        },
    )
    prompt = executor.seen_task.text
    assert prompt.index("INSTRUCTIONS") < prompt.index("TASK")


def test_multiple_developer_items_keep_their_authored_order():
    executor = FakeExecutor()
    run_stream(
        build(executor),
        body={
            "input": [
                {"role": "developer", "content": "FIRST"},
                {"role": "developer", "content": "SECOND"},
                {"role": "user", "content": "TASK"},
            ]
        },
    )
    prompt = executor.seen_task.text
    assert prompt.index("FIRST") < prompt.index("SECOND") < prompt.index("TASK")


def test_developer_instructions_alone_are_not_a_task():
    """Instructions describe how to work; without a task there is nothing to do."""
    events = run_stream(build(), body={"input": [{"role": "developer", "content": "be careful"}]})
    assert events[-1]["type"] == EV_FAILED


def test_two_providers_on_one_workspace_still_serialize():
    """Per-adapter locks would let two providers edit the same repo at once."""
    active = 0
    peak = 0
    lock = threading.Lock()

    class CountingExecutor(FakeExecutor):
        def execute(self, task, cancel_requested=None, stream_output=None, stream_reasoning=None):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            try:
                return super().execute(task, cancel_requested, stream_output)
            finally:
                with lock:
                    active -= 1

    claude = build(CountingExecutor())
    agy = LocalAgentUpstream(
        provider_id="local_agy",
        executor=CountingExecutor(),
        models={"opus": ModelCapabilities(frozenset({TOOL_CALLING}))},
    )

    async def main():
        async def one(upstream):
            return [
                e
                async for e in upstream.stream(
                    {"input": [{"role": "user", "content": "go"}]},
                    "opus",
                    workspace=Path("/tmp/shared-across-providers"),
                )
            ]

        await asyncio.gather(one(claude), one(agy), one(claude))

    asyncio.run(main())
    assert peak == 1


def test_capabilities_come_from_configuration():
    assert build().capabilities("opus").supports(TOOL_CALLING)
    assert not build().capabilities("unconfigured").supports(TOOL_CALLING)


@pytest.mark.parametrize("chunks", [(), ("",)])
def test_runs_with_no_output_still_complete(chunks):
    events = run_stream(build(FakeExecutor(chunks=chunks)))
    assert events[-1]["type"] == EV_COMPLETED


def test_message_items_declare_their_phase():
    """An untagged assistant message leaves the native sub-agent window blank.

    It still lands in the transcript and still reaches the parent thread, so a
    regression here looks like a fully successful turn — the one failure mode no
    other assertion catches. Narration must be `commentary` and arrive first: a
    half-written *final answer* is not something a UI can show, so tagging the
    streaming item `final_answer` leaves the window empty until it completes.
    """
    events = run_stream(build())
    phases = [
        (e["type"], e["item"]["phase"])
        for e in events
        if e["type"] in (EV_OUTPUT_ITEM_ADDED, EV_OUTPUT_ITEM_DONE)
    ]

    assert phases == [
        (EV_OUTPUT_ITEM_ADDED, "commentary"),
        (EV_OUTPUT_ITEM_DONE, "commentary"),
        (EV_OUTPUT_ITEM_ADDED, "final_answer"),
        (EV_OUTPUT_ITEM_DONE, "final_answer"),
    ]


# ── read-only enforcement ────────────────────────────────────────────
class ReadOnlyCapableExecutor(FakeExecutor):
    def enforces_read_only(self) -> bool:
        return True


def test_read_only_role_reaches_a_capable_executor():
    executor = ReadOnlyCapableExecutor()
    events = run_stream(build(executor), read_only=True)
    assert events[-1]["type"] == EV_COMPLETED
    assert executor.seen_task.metadata["read_only"] is True


def test_read_only_role_is_refused_by_an_executor_that_cannot_enforce_it():
    """Downgrading silently would break a promise the role file made to the user."""
    executor = FakeExecutor()  # declares nothing, so it cannot enforce
    events = run_stream(build(executor), read_only=True)

    assert events[-1]["type"] == EV_FAILED
    assert "read-only" in events[-1]["response"]["error"]["message"]
    assert executor.seen_task is None, "the agent must never have started"


def test_writable_roles_are_unaffected():
    executor = FakeExecutor()
    events = run_stream(build(executor))
    assert events[-1]["type"] == EV_COMPLETED
    assert executor.seen_task.metadata["read_only"] is False


# ── reasoning channel ────────────────────────────────────────────────
class ThinkingExecutor(FakeExecutor):
    """Reports working notes alongside its answer, as the Claude executor does."""

    def execute(self, task, cancel_requested=None, stream_output=None, stream_reasoning=None):
        self.seen_task = task
        if stream_reasoning:
            stream_reasoning("Read(README.md)")
        if stream_output:
            stream_output("here is the summary")
        self.finished.set()
        return ExecutionResult(
            success=True,
            summary="here is the summary",
            stdout="",
            stderr="",
            exit_code=0,
            executor_session_id="sess-1",
        )


def _items(events):
    return [
        (e["type"], e["item"]["type"], e["item"].get("phase"))
        for e in events
        if e["type"] in (EV_OUTPUT_ITEM_ADDED, EV_OUTPUT_ITEM_DONE)
    ]


def test_reasoning_becomes_a_reasoning_item_not_answer_text():
    """Codex renders reasoning in its own disclosure; as output text it would
    land in the answer instead."""
    events = run_stream(build(ThinkingExecutor()))

    assert _items(events) == [
        (EV_OUTPUT_ITEM_ADDED, "reasoning", None),
        (EV_OUTPUT_ITEM_DONE, "reasoning", None),
        (EV_OUTPUT_ITEM_ADDED, "message", "commentary"),
        (EV_OUTPUT_ITEM_DONE, "message", "commentary"),
        (EV_OUTPUT_ITEM_ADDED, "message", "final_answer"),
        (EV_OUTPUT_ITEM_DONE, "message", "final_answer"),
    ]


def test_reasoning_deltas_use_the_reasoning_event_type():
    events = run_stream(build(ThinkingExecutor()))
    reasoning = [e for e in events if e["type"] == "response.reasoning_summary_text.delta"]

    assert [e["delta"] for e in reasoning] == ["Read(README.md)"]
    # Codex's parser drops a summary delta that arrives without this.
    assert all(e["summary_index"] == 0 for e in reasoning)


def test_reasoning_item_commits_its_text():
    """The deltas drive the live view; the done item is what the transcript keeps."""
    events = run_stream(build(ThinkingExecutor()))
    done = next(
        e for e in events if e["type"] == EV_OUTPUT_ITEM_DONE and e["item"]["type"] == "reasoning"
    )
    assert done["item"]["summary"] == [{"type": "summary_text", "text": "Read(README.md)"}]


def test_only_one_item_is_open_at_a_time():
    """Codex routes a delta to the *active* item, so an overlap files answer
    text under the reasoning disclosure."""
    events = run_stream(build(ThinkingExecutor()))
    depth = 0
    for kind, _, _ in _items(events):
        depth += 1 if kind == EV_OUTPUT_ITEM_ADDED else -1
        assert depth in (0, 1), "items must never nest"
    assert depth == 0, "every item must be closed"


def test_a_silent_agent_emits_no_reasoning_item():
    events = run_stream(build())
    assert not any(e.get("item", {}).get("type") == "reasoning" for e in events)


def test_reasoning_counts_toward_the_reported_context():
    """Codex keeps the reasoning item in the transcript, and raw thinking is
    often longer than the answer — omitting it would delay compaction."""

    class Verbose(FakeExecutor):
        def execute(self, task, cancel_requested=None, stream_output=None, stream_reasoning=None):
            if stream_reasoning:
                stream_reasoning("t" * 4000)
            if stream_output:
                stream_output("ok")
            self.finished.set()
            return ExecutionResult(
                success=True,
                summary="ok",
                stdout="",
                stderr="",
                exit_code=0,
                executor_session_id="s",
            )

    usage = run_stream(build(Verbose()))[-1]["response"]["usage"]
    assert usage["output_tokens"] > 900, usage
