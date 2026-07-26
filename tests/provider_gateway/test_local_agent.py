"""Local agent CLI backing a Codex sub-agent thread."""

from __future__ import annotations

import asyncio
import json
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
    has_unreadable_task,
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
CONVERSATION = {
    "input": [
        {"role": "user", "content": "the codeword is DURIAN-7"},
        {"role": "assistant", "content": "OK"},
        {"role": "user", "content": "what was the codeword?"},
    ]
}


def test_a_resumed_turn_sends_only_the_last_user_message():
    """The agent's own session already holds the rest; replaying it would bury
    the actual task and pay for the history again every turn."""
    assert extract_prompt(CONVERSATION, resuming=True) == "what was the codeword?"


def test_a_cold_start_keeps_the_history():
    """The failure this prevents is a sub-agent that forgets mid-thread.

    Codex resends the whole sub-thread every turn, so when there is no session
    to resume — a first turn, a first run that died before reporting a session
    id, a candidate switch, an expired sticky row — the context is right there
    in the request. Sending only the last message would ask "what was the
    codeword?" of an agent that never heard it, and nothing would report an
    error.
    """
    prompt = extract_prompt(CONVERSATION, resuming=False)
    assert "User: the codeword is DURIAN-7" in prompt
    assert "Assistant: OK" in prompt
    assert prompt.endswith("what was the codeword?")


def test_the_replayed_history_is_marked_as_background():
    """Unlabelled, the transcript reads as more instructions and the sub-agent
    redoes the first task."""
    assert "Earlier in this conversation:" in extract_prompt(CONVERSATION, resuming=False)


def test_one_agent_turn_is_replayed_once():
    """Each turn comes back as a commentary item and a final-answer item, which
    are identical whenever the answer arrived in one piece."""
    body = {
        "input": [
            {"role": "user", "content": "ping"},
            {"role": "assistant", "content": "pong"},
            {"role": "assistant", "content": "pong"},
            {"role": "user", "content": "again"},
        ]
    }
    assert extract_prompt(body, resuming=False).count("Assistant: pong") == 1


def test_a_first_turn_replays_nothing():
    body = {"input": [{"role": "user", "content": "the only task"}]}
    assert extract_prompt(body, resuming=False) == "the only task"


def test_codex_injected_context_is_not_replayed():
    """Codex prepends its host's plugin list and environment to the first user
    turn. Replaying it describes Codex's host, not the conversation."""
    body = {
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "<recommended_plugins>\n- Box\n</recommended_plugins>",
                    },
                    {
                        "type": "input_text",
                        "text": "<environment_context>\n  <shell>zsh</shell>\n</environment_context>",
                    },
                ],
            },
            {"role": "user", "content": "first task"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "second task"},
        ]
    }
    prompt = extract_prompt(body, resuming=False)

    assert "recommended_plugins" not in prompt
    assert "environment_context" not in prompt
    assert "User: first task" in prompt


def test_a_task_that_looks_like_host_context_survives():
    """The envelope filter must never reach the delegated task itself."""
    body = {"input": [{"role": "user", "content": "<task>ship it</task>"}]}
    assert extract_prompt(body, resuming=False) == "<task>ship it</task>"


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
    assert extract_prompt(body, resuming=True) == "line one\nline two"


def test_a_spawned_subagent_receives_its_task():
    """The spawn payload rides beside the envelope, not inside its text.

    Shape copied from codex-rs core/tests/suite/subagent_notifications.rs. Read
    the `text` parts alone and the task is gone: the agent gets an envelope
    ending in an empty `Payload:` plus its role instructions, and improvises a
    plausible-looking report — which reaches the parent as a sub-agent that
    never got the task.
    """
    body = {
        "input": [
            {"role": "developer", "content": "Complete the delegated task."},
            {
                "type": "agent_message",
                "author": "/root",
                "recipient": "/root/worker",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Message Type: NEW_TASK\nTask name: /root/worker\nSender: /root\nPayload:\n",
                    },
                    {
                        "type": "encrypted_content",
                        "encrypted_content": "summarize this week's news",
                    },
                ],
            },
        ]
    }
    prompt = extract_prompt(body, resuming=True)

    assert "summarize this week's news" in prompt
    assert prompt.startswith("Complete the delegated task.")


# ── the encrypted-task guard ─────────────────────────────────────────
# Captured from codex-cli 0.145.0 with a v2 parent model (gpt-5.6-terra),
# truncated in the middle. A Fernet token: base64url of a 0x80 version byte,
# timestamp, IV, ciphertext, and HMAC.
V2_TOKEN = "gAAAAABqZeSZCF2oN519yW3OMqrNjtC6Yns0R5outXAUJbj5MczsEGHKv9sCdpJWnyinVd-_JW_XTrTt" + (
    "x" * 60
)


def spawn(payload):
    return {
        "input": [
            {"role": "developer", "content": "Complete the delegated task."},
            {
                "type": "agent_message",
                "content": [
                    {"type": "input_text", "text": "Message Type: NEW_TASK\nPayload:\n"},
                    {"type": "encrypted_content", "encrypted_content": payload},
                ],
            },
        ]
    }


def test_an_encrypted_task_is_detected():
    assert has_unreadable_task(spawn(V2_TOKEN))


def test_a_v1_payload_is_not_mistaken_for_ciphertext():
    """v1 passes the spawn_agent `message` argument through verbatim under the
    same wire field name. Refusing those would break the protocol that works."""
    assert not has_unreadable_task(spawn("summarize this week's news"))


def test_a_v1_spawn_carries_no_such_part_at_all():
    """Measured: a v1 parent sends the task as a plain user message."""
    body = {"input": [{"role": "user", "content": [{"type": "input_text", "text": "go"}]}]}
    assert not has_unreadable_task(body)


def test_prose_that_happens_to_start_like_a_token_is_kept():
    """The pattern needs an unbroken base64url run; prose has spaces."""
    assert not has_unreadable_task(spawn("gAAAAA is a strange way to open a task but " * 4))


def test_an_encrypted_task_fails_the_turn_instead_of_running():
    """This is the failure the guard exists for. Handed ciphertext as its task,
    an agent has no task at all, so it improvises — and the parent shows a
    sub-agent that confidently answered a question nobody asked, with nothing
    anywhere reporting an error.
    """
    executor = FakeExecutor()
    events = run_stream(build(executor), body=spawn(V2_TOKEN))

    assert [e["type"] for e in events] == [EV_FAILED]
    assert executor.seen_task is None


def test_the_refusal_names_the_fix():
    """A user who hits this needs to know it is the parent's model, not their
    task, and which way out exists."""
    events = run_stream(build(), body=spawn(V2_TOKEN))
    message = json.dumps(events[0])
    assert "encrypted" in message
    assert "gpt-5.6-luna" in message


def test_prompt_handles_a_plain_string_input():
    assert extract_prompt({"input": "just do it"}, resuming=True) == "just do it"


def test_missing_input_yields_no_prompt():
    assert extract_prompt({}, resuming=False) == ""
    assert extract_prompt({"input": []}, resuming=False) == ""


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


def test_a_trailing_newline_does_not_defeat_the_trim():
    """The shape that actually reaches us, and the one that broke.

    A CLI writes its answer to stdout with a trailing newline; the executor
    strips it before putting it in `summary`. Compared raw, a single "\\n" made
    both the equality and the suffix test miss, so the answer was committed
    twice — visible in Codex 0.146.0 as the same text in the collapsed work
    section and again as the final answer.
    """
    events = run_stream(build(FakeExecutor(chunks=("summary\n",))))
    narration = [e for e in events if e["type"] == EV_OUTPUT_ITEM_DONE][0]
    assert narration["item"]["content"][0]["text"] == ""


def test_narration_survives_a_newline_before_the_answer():
    events = run_stream(build(FakeExecutor(chunks=("looking around\n", "summary\n"))))
    narration = [e for e in events if e["type"] == EV_OUTPUT_ITEM_DONE][0]
    assert narration["item"]["content"][0]["text"] == "looking around"


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
    assert task.metadata["executor_session_id"] == "prev-session"


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


def test_the_resume_key_is_the_one_executors_read():
    """A renamed key is ignored in silence, and the silence is the problem.

    Every executor — and the prompt builder, which uses it to tell a fresh task
    from a continued one — reads `executor_session_id`. The gateway once wrote
    `resume_session_id` instead: nothing raised, every turn succeeded, and the
    agent simply started cold each time while the gateway trimmed the history
    it would otherwise have resent, believing the session was being resumed.
    """
    from fluxion.executors.antigravity import executor as antigravity_executor
    from fluxion.executors.claude import executor as claude_executor
    from fluxion.executors.codex import executor as codex_executor

    executor = FakeExecutor()
    run_stream(build(executor), session_id="prev-session")

    assert executor.seen_task.metadata["executor_session_id"] == "prev-session"
    for module in (claude_executor, codex_executor, antigravity_executor):
        source = Path(module.__file__).read_text()
        assert 'metadata.get("executor_session_id"' in source, module.__name__
