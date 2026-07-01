"""The agent multi-hop loop: dispatch, role:tool messages, hop cap, error
recovery, temperature escalation. Hermetic via FakeAgentLLM + a fake registry."""
import pytest

from ytqc.agent.loop import MAX_TOOL_HOPS, run_turn
from tests.fakes import FakeAgentLLM


class FakeRegistry:
    def __init__(self, results=None):
        self.calls = []
        self._results = results or {}
    def schemas(self):
        return [{"type": "function", "function": {"name": "run_qc", "parameters": {}}}]
    def dispatch(self, name, args):
        self.calls.append((name, args))
        return self._results.get(name, {"ok": True, "echo": args})


def _msgs():
    return [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]


def test_final_answer_no_tools():
    llm = FakeAgentLLM([{"content": "Hello there!"}])
    text, msgs = run_turn(llm, FakeRegistry(), _msgs())
    assert text == "Hello there!"
    assert msgs[-1] == {"role": "assistant", "content": "Hello there!"}


def test_single_tool_then_final():
    reg = FakeRegistry(results={"run_qc": {"run_id": "R1", "items": 2}})
    llm = FakeAgentLLM([
        {"tool_calls": [{"name": "run_qc", "arguments": {"path": "x.csv"}}]},
        {"content": "Done — run R1 covered 2 items."},
    ])
    text, msgs = run_turn(llm, reg, _msgs())
    assert reg.calls == [("run_qc", {"path": "x.csv"})]
    assert text == "Done — run R1 covered 2 items."
    # a role:tool message carrying the result was inserted before the final answer
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert tool_msgs and "R1" in tool_msgs[0]["content"]


def test_bad_json_args_degrade_to_empty():
    reg = FakeRegistry()
    llm = FakeAgentLLM([
        {"tool_calls": [{"name": "run_qc", "arguments": "{not valid json"}]},
        {"content": "ok"},
    ])
    run_turn(llm, reg, _msgs())
    assert reg.calls == [("run_qc", {})]      # malformed args → {}


def test_hop_cap_forces_final_answer():
    # always returns a tool call → loop must stop at MAX_TOOL_HOPS and force final
    script = [{"tool_calls": [{"name": "run_qc", "arguments": {}}]} for _ in range(MAX_TOOL_HOPS)]
    script.append({"content": "forced final after cap"})
    reg = FakeRegistry()
    llm = FakeAgentLLM(script)
    text, _ = run_turn(llm, reg, _msgs())
    assert len(reg.calls) == MAX_TOOL_HOPS      # no more than the cap
    assert text == "forced final after cap"


def test_unknown_tool_does_not_crash_loop():
    # registry returns an error dict; loop should keep going to a final answer
    reg = FakeRegistry(results={"mystery": {"error": "unknown tool"}})
    llm = FakeAgentLLM([
        {"tool_calls": [{"name": "mystery", "arguments": {}}]},
        {"content": "handled the error"},
    ])
    text, msgs = run_turn(llm, reg, _msgs())
    assert text == "handled the error"
    assert any(m.get("role") == "tool" and "error" in m["content"] for m in msgs)


def test_interrupt_mid_tool_appends_cancelled_result_then_reraises():
    # Ctrl-C during run_qc must NOT leave a dangling tool_call: run_turn appends a
    # cancelled tool-result (valid OpenAI sequence) and re-raises so the next turn
    # knows nothing is running (regression for the "already started" hallucination).
    class _InterruptingRegistry(FakeRegistry):
        def dispatch(self, name, args):
            raise KeyboardInterrupt
    reg = _InterruptingRegistry()
    llm = FakeAgentLLM([{"tool_calls": [{"name": "run_qc", "arguments": {"path": "x.csv"}}]}])
    msgs = _msgs()
    with pytest.raises(KeyboardInterrupt):
        run_turn(llm, reg, msgs)
    # the assistant tool-call turn has a matching tool result (no dangling call)
    assert msgs[-2]["role"] == "assistant" and msgs[-2].get("tool_calls")
    assert msgs[-1]["role"] == "tool"
    assert msgs[-1]["tool_call_id"] == msgs[-2]["tool_calls"][0]["id"]
    assert "cancelled" in msgs[-1]["content"]


def test_interrupt_cancels_all_pending_tool_calls_in_turn():
    # two tool_calls in one assistant turn; interrupt on the first must still leave
    # BOTH with tool results so the sequence is valid.
    class _InterruptingRegistry(FakeRegistry):
        def dispatch(self, name, args):
            raise KeyboardInterrupt
    reg = _InterruptingRegistry()
    llm = FakeAgentLLM([{"tool_calls": [
        {"name": "run_qc", "arguments": {}},
        {"name": "inspect_input", "arguments": {}},
    ]}])
    msgs = _msgs()
    with pytest.raises(KeyboardInterrupt):
        run_turn(llm, reg, msgs)
    tool_ids = {m["tool_call_id"] for m in msgs if m["role"] == "tool"}
    call_ids = {c["id"] for c in msgs[-3]["tool_calls"]} if msgs[-3].get("tool_calls") else set()
    # every tool_call in the assistant turn has a matching cancelled result
    assert call_ids and call_ids.issubset(tool_ids)


def test_temperature_escalation_on_transient_failure():
    # first create() raises → escalation retries at the next ladder temp
    llm = FakeAgentLLM([{"content": "recovered"}], raise_times=1)
    text, _ = run_turn(llm, FakeRegistry(), _msgs())
    assert text == "recovered"
    assert llm.temps[:2] == [0.1, 0.3]      # climbed the ladder


def test_status_callback_pauses_for_long_running_tool():
    seen = []
    reg = FakeRegistry()
    llm = FakeAgentLLM([
        {"tool_calls": [{"name": "run_qc", "arguments": {}}]},   # long-running
        {"content": "done"},
    ])
    run_turn(llm, reg, _msgs(), on_status=lambda s: seen.append(s))
    assert None in seen      # spinner was paused (None) for the long-running tool


def test_on_event_emits_tool_block_sequence():
    reg = FakeRegistry(results={"inspect_input": {"total": 3}})
    llm = FakeAgentLLM([
        {"tool_calls": [{"name": "inspect_input", "arguments": {"path": "x.csv"}}]},
        {"content": "found 3"},
    ])
    events = []
    run_turn(llm, reg, _msgs(), on_event=lambda kind, **d: events.append((kind, d)))
    kinds = [k for k, _ in events]
    # think_start → think_stop → tool_start → tool_end → (final hop) think_start → think_stop
    assert kinds[0] == "think_start"
    assert "tool_start" in kinds and "tool_end" in kinds
    ts = next(d for k, d in events if k == "tool_start")
    assert ts["name"] == "inspect_input" and ts["args"] == {"path": "x.csv"}
    te = next(d for k, d in events if k == "tool_end")
    assert te["name"] == "inspect_input" and te["result"] == {"total": 3}


def test_on_event_optional_does_not_break_plain_calls():
    # passing neither on_event nor on_status must still work (back-compat)
    llm = FakeAgentLLM([{"content": "hi"}])
    text, _ = run_turn(llm, FakeRegistry(), _msgs())
    assert text == "hi"


def test_run_turn_propagates_keyboard_interrupt_from_tool():
    class KbReg(FakeRegistry):
        def dispatch(self, name, args):
            raise KeyboardInterrupt
    llm = FakeAgentLLM([{"tool_calls": [{"name": "run_qc", "arguments": {}}]},
                        {"content": "unreached"}])
    with pytest.raises(KeyboardInterrupt):
        run_turn(llm, KbReg(), _msgs())
