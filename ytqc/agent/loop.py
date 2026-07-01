"""The agent brain: a multi-turn, native-tool-calling loop over gemma4:31b-cloud
(validated: Ollama /v1 returns message.tool_calls). Separate from the QC
pipeline's LLMClient.chat_json — this one keeps conversation history and uses
the OpenAI SDK `tools=` surface."""
from __future__ import annotations

import json
import logging

from openai import OpenAI

from ytqc.config import ProviderProfile
from ytqc.llm.client import TEMP_LADDER

log = logging.getLogger("ytqc.agent.loop")

MAX_TOOL_HOPS = 4
_TOOL_RESULT_CAP = 4000      # chars of a tool result fed back to the model


class AgentLLM:
    """Thin multi-turn tool-calling client."""

    def __init__(self, profile: ProviderProfile, model: str):
        self.model = model
        self._client = OpenAI(
            base_url=profile.base_url,
            api_key=profile.resolved_api_key() or "unused",
            timeout=profile.timeout_s,
        )

    def create(self, messages: list, tools: list | None, temperature: float):
        kwargs: dict = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return self._client.chat.completions.create(**kwargs)


def _safe_json_loads(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
        return out if isinstance(out, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _truncate(obj, cap: int = _TOOL_RESULT_CAP) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= cap else s[:cap] + " …(truncated)"


def _tc_to_dict(tc) -> dict:
    return {"id": tc.id, "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments}}


def _create_with_escalation(llm: AgentLLM, messages: list, tools: list):
    """One model call, retried up the temperature ladder on transport/decode
    failure (the proven mirrors self-correction). Re-raises if all fail."""
    last = None
    for temp in TEMP_LADDER:
        try:
            return llm.create(messages, tools, temperature=temp)
        except Exception as exc:
            last = exc
            log.warning("agent create() failed at temp %.1f: %s", temp, exc)
    raise last if last else RuntimeError("agent create() failed")


# tools whose execution takes over the console (own progress bar) — the REPL
# suspends its spinner around these so two live renderables never overlap.
LONG_RUNNING = {"run_qc", "resume_run"}


def run_turn(llm: AgentLLM, registry, messages: list, on_status=None,
             on_event=None) -> tuple[str, list]:
    """Drive one user turn to a final natural-language answer, executing tool
    calls along the way (≤ MAX_TOOL_HOPS). `messages` is the running history
    (mutated in place and returned). `on_status(label)` / `on_status(None)`
    show/hide the REPL spinner (legacy channel). `on_event(kind, **data)` is the
    structured channel for rich rendering — kinds: think_start, think_stop,
    tool_start, tool_end. Both are optional and additive."""
    def status(label):
        if on_status:
            on_status(label)

    def emit(kind, **data):
        if on_event:
            on_event(kind, **data)

    for hop in range(MAX_TOOL_HOPS):
        status("thinking…" if hop == 0 else f"working… (step {hop + 1})")
        emit("think_start", hop=hop)
        resp = _create_with_escalation(llm, messages, registry.schemas())
        msg = resp.choices[0].message
        tool_calls = msg.tool_calls or []
        emit("think_stop")                                  # spinner down before printing

        if not tool_calls:                                  # final answer / question / decline
            text = (msg.content or "").strip()
            messages.append({"role": "assistant", "content": text})
            return text, messages

        # record the assistant tool-call turn (OpenAI protocol requires it before tool msgs)
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [_tc_to_dict(tc) for tc in tool_calls]})
        for idx, tc in enumerate(tool_calls):
            name = tc.function.name
            args = _safe_json_loads(tc.function.arguments)
            long_op = name in LONG_RUNNING
            if long_op:
                status(None)                                # release spinner for the progress bar
            else:
                status(f"running {name}…")
            emit("tool_start", name=name, args=args)
            try:
                result = registry.dispatch(name, args)
            except KeyboardInterrupt:
                # User Ctrl-C'd mid-tool (e.g. a long run_qc). The assistant
                # tool-call turn is already in `messages`; if we let the interrupt
                # propagate now, those tool_calls have no matching results — an
                # invalid OpenAI sequence that makes the next turn hallucinate the
                # operation is "still running". Append a truthful cancelled result
                # for THIS tool and every not-yet-executed tool in this turn, so
                # the model knows nothing is in progress, then re-raise.
                cancelled = {"status": "cancelled",
                             "note": "Interrupted by the user before completion. No "
                                     "operation is in progress and no run is active. "
                                     "If the user asks again, start a fresh tool call."}
                for pending in tool_calls[idx:]:
                    messages.append({"role": "tool", "tool_call_id": pending.id,
                                     "name": pending.function.name,
                                     "content": _truncate(cancelled)})
                raise
            emit("tool_end", name=name, result=result)
            messages.append({"role": "tool", "tool_call_id": tc.id, "name": name,
                             "content": _truncate(result)})

    # hop cap → force a final answer with no more tools
    status("wrapping up…")
    emit("think_start", hop=MAX_TOOL_HOPS)
    try:
        resp = llm.create(
            messages + [{"role": "system",
                         "content": "You've used all tool steps. Answer the user now "
                                    "using the results above; do not call tools."}],
            tools=None, temperature=0.3)
        text = (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        text = f"I ran several steps but couldn't wrap up cleanly ({exc}). The results are above."
    finally:
        emit("think_stop")
    if not text:
        text = "I gathered some results above but couldn't summarize them — try asking again."
    messages.append({"role": "assistant", "content": text})
    return text, messages
