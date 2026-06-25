"""Tool JSON-schema generation + argument coercion for the chat agent.

Schemas are auto-generated from each tool's Python signature (so they never
drift), and incoming LLM args are aliased + coerced before binding (gemma
passes "2 lanes" for an int, "true" for a bool, a comma-string for a list,
and invents synonym kwargs). Pattern lifted from the mirrors chatbot
(_py_type_to_json / _coerce_value / _sanitize_kwargs). Every function here is
TOTAL — it never raises — so a surprising annotation degrades to a string
rather than crashing agent startup or a turn.
"""
from __future__ import annotations

import inspect
import logging
import re
import typing
from typing import Any, Callable

log = logging.getLogger("ytqc.agent.schema")

# The first parameter of every tool is the injected AgentContext — it is bound
# by the registry and must never appear in the schema the model sees.
CTX_PARAM = "ctx"

# LLM synonym kwargs → real parameter names (fallback only: a key the function
# actually accepts is never aliased away).
ALIASES: dict[str, str] = {
    "file": "path", "csv": "path", "filepath": "path", "file_path": "path",
    "input": "path", "input_file": "path",
    "id": "ids", "channel": "ids", "channels": "ids", "video": "ids", "videos": "ids",
    "num_lanes": "lanes", "tabs": "lanes", "browser_lanes": "lanes",
    "threads": "workers", "analysis_workers": "workers",
    "run": "run_id", "run_identifier": "run_id",
    "filter": "only", "filter_by": "only",
    "pages": "channel_pages", "sample_k": "channel_pages",
    "n": "limit", "first": "limit", "max_items": "limit",
    "type": "item_type", "kind": "item_type",
}


def py_type_to_json(ann: Any) -> dict:
    """Map a Python annotation to a JSON-schema fragment."""
    origin = typing.get_origin(ann)
    args = typing.get_args(ann)
    # Optional[X] / X | None → unwrap to X
    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return py_type_to_json(non_none[0])
    if ann is int:
        return {"type": "integer"}
    if ann is float:
        return {"type": "number"}
    if ann is bool:
        return {"type": "boolean"}
    if ann is str:
        return {"type": "string"}
    if origin in (list, typing.List):
        item = args[0] if args else str
        return {"type": "array", "items": py_type_to_json(item)}
    if origin is dict or ann is dict:
        return {"type": "object"}
    return {"type": "string"}


def build_tool_schema(fn: Callable) -> dict:
    """Build the OpenAI tool envelope from a tool function's signature.
    The first parameter (ctx) is skipped; the docstring's first paragraph is
    the description; params without a default are required."""
    try:
        sig = inspect.signature(fn)
        hints = typing.get_type_hints(fn)
    except Exception as exc:                       # never let one bad tool break startup
        log.warning("schema introspection failed for %s: %s", getattr(fn, "__name__", fn), exc)
        sig, hints = None, {}

    props: dict[str, dict] = {}
    required: list[str] = []
    if sig is not None:
        for i, (pname, p) in enumerate(sig.parameters.items()):
            if i == 0 and pname == CTX_PARAM:
                continue
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            props[pname] = py_type_to_json(hints.get(pname, str))
            if p.default is inspect.Parameter.empty:
                required.append(pname)

    doc = inspect.getdoc(fn) or ""
    description = doc.split("\n\n", 1)[0].strip().replace("\n", " ")
    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def coerce_value(value: Any, annotation: Any) -> Any:
    """Best-effort coerce an LLM-supplied value to its annotated type. Never
    raises — on failure the original value passes through so the tool's own
    validation produces the error message."""
    if value is None:
        return value
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    wants_list = origin in (list, typing.List) or any(
        typing.get_origin(a) in (list, typing.List) for a in args
    )
    if wants_list and isinstance(value, str):
        s = value.strip()
        if s.startswith("["):
            try:
                import json
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except Exception:
                pass
        return [p.strip() for p in re.split(r"[,\n;]+", s) if p.strip()]

    target: set = set()
    if annotation in (int, float, bool, str):
        target.add(annotation)
    if origin is typing.Union:
        for a in args:
            if a in (int, float, bool, str):
                target.add(a)

    if isinstance(value, str):
        if bool in target:                          # check bool before int (True/False words)
            low = value.strip().lower()
            if low in ("true", "yes", "y", "1", "on"):
                return True
            if low in ("false", "no", "n", "0", "off", ""):
                return False
        if int in target and not isinstance(value, bool):
            m = re.search(r"-?\d+", value)
            if m:
                try:
                    return int(m.group())
                except ValueError:
                    pass
        if float in target:
            m = re.search(r"-?\d+(?:\.\d+)?", value)
            if m:
                try:
                    return float(m.group())
                except ValueError:
                    pass
    return value


def sanitize_kwargs(fn: Callable, raw: dict) -> tuple[dict, list[str]]:
    """Alias synonym keys onto real params, drop unknown keys, coerce values
    to annotated types. Returns (clean_kwargs, dropped_keys)."""
    try:
        sig = inspect.signature(fn)
        accepted = set(sig.parameters.keys()) - {CTX_PARAM}
    except (TypeError, ValueError):
        return dict(raw), []
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}

    clean: dict = {}
    dropped: list[str] = []
    for k, v in (raw or {}).items():
        target = k if k in accepted else ALIASES.get(k, k)
        if target in accepted and target not in clean:
            ann = hints.get(target)
            clean[target] = coerce_value(v, ann) if ann is not None else v
        else:
            dropped.append(k)
    return clean, dropped
