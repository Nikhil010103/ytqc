"""Schema generation + argument coercion (pure, hermetic)."""
from typing import Optional

from ytqc.agent.schema import (build_tool_schema, coerce_value, py_type_to_json,
                               sanitize_kwargs)
from ytqc.agent.tools import run_qc, show_results


def test_run_qc_schema_strips_ctx_and_marks_all_optional():
    s = build_tool_schema(run_qc)["function"]
    assert s["name"] == "run_qc"
    props = s["parameters"]["properties"]
    assert "ctx" not in props                       # injected param hidden from the model
    assert "path" in props and "lanes" in props
    assert props["lanes"] == {"type": "integer"}
    assert props["no_comments"] == {"type": "boolean"}
    assert s["parameters"]["required"] == []        # every run_qc param has a default
    assert s["description"]                          # from the docstring


def test_required_includes_undefaulted_params():
    s = build_tool_schema(show_results)["function"]
    # show_results(ctx, run_id=None, only=None) — all defaulted → none required
    assert s["parameters"]["required"] == []
    assert set(s["parameters"]["properties"]) == {"run_id", "only"}


def test_py_type_to_json_unwraps_optional_and_lists():
    assert py_type_to_json(int) == {"type": "integer"}
    assert py_type_to_json(bool) == {"type": "boolean"}
    assert py_type_to_json(Optional[str]) == {"type": "string"}
    assert py_type_to_json(list[str]) == {"type": "array", "items": {"type": "string"}}


def test_coerce_value_numbers_bools_lists():
    assert coerce_value("2 lanes", int) == 2
    assert coerce_value("30 days", int) == 30
    assert coerce_value("true", bool) is True
    assert coerce_value("no", bool) is False
    assert coerce_value("a, b ,c", list[str]) == ["a", "b", "c"]
    assert coerce_value('["x","y"]', list[str]) == ["x", "y"]


def test_coerce_value_never_raises_on_garbage():
    assert coerce_value("not-a-number", int) == "not-a-number"   # passes through


def test_sanitize_aliases_and_drops_unknown():
    clean, dropped = sanitize_kwargs(run_qc, {"file": "x.csv", "tabs": "2", "bogus": 1})
    assert clean["path"] == "x.csv"        # file → path alias
    assert clean["lanes"] == 2             # tabs → lanes alias + int coercion
    assert "bogus" in dropped


def test_sanitize_prefers_real_param_over_alias():
    clean, _ = sanitize_kwargs(run_qc, {"path": "real.csv", "file": "ignored.csv"})
    assert clean["path"] == "real.csv"
