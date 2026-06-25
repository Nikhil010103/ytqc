import pytest

from ytqc.llm.client import parse_llm_json


def test_clean_json():
    assert parse_llm_json('{"a": 1}') == {"a": 1}


def test_fenced_json_gemma_style():
    # observed live: gemma4:31b-cloud wraps in ```json fences even with format:json
    raw = '```json\n{"tier_1": "Automobiles", "tier_2": "luxury car review"}\n```'
    assert parse_llm_json(raw)["tier_1"] == "Automobiles"


def test_prose_around_payload():
    raw = 'Here is the analysis:\n{"x": true}\nHope this helps!'
    assert parse_llm_json(raw) == {"x": True}


def test_think_block_stripped():
    raw = '<think>hmm let me reason</think>{"a": 2}'
    assert parse_llm_json(raw) == {"a": 2}


def test_truncated_json_repaired():
    raw = '{"keywords": ["car", "review"], "summary": "A detailed look at'
    out = parse_llm_json(raw)
    assert out["keywords"] == ["car", "review"]


def test_bare_list_wrapped():
    assert parse_llm_json('[1, 2]') == {"items": [1, 2]}


def test_no_json_raises():
    with pytest.raises(ValueError):
        parse_llm_json("I cannot analyze this video.")
