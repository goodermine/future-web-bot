import json
from types import SimpleNamespace

import pytest

import processor


def test_lexical_scores_are_bounded_ints():
    for text in ["", "hello", "PANIC!!! " * 50]:
        scores = processor.score_lexical(text)
        assert set(scores) == {"intensity", "immediacy", "scale"}
        assert all(isinstance(v, int) and 1 <= v <= 100 for v in scores.values())


def test_lexical_orders_texts_sensibly():
    hot = processor.score_lexical("BREAKING: global markets collapsing right now, terror and panic!!!")
    calm = processor.score_lexical("The town library will extend its hours next year.")
    assert hot["intensity"] > calm["intensity"]
    assert hot["immediacy"] > calm["immediacy"]
    assert hot["scale"] > calm["scale"]


def test_validate_scores_clamps_and_requires_all_dims():
    assert processor.validate_scores({"intensity": 150, "immediacy": 0, "scale": 50.4}) == {
        "intensity": 100, "immediacy": 1, "scale": 50}
    with pytest.raises(processor.ScoringError):
        processor.validate_scores({"intensity": 5})


class FakeScorer:
    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def score(self, text):
        self.calls += 1
        if self.fail:
            raise processor.ScoringError("boom")
        return {"intensity": 99, "immediacy": 98, "scale": 97}


def test_prefilter_only_sends_charged_text():
    scorer = FakeScorer()
    records = [
        {"headline": "Catastrophe unfolding now, terrifying panic worldwide!!!", "raw_text": ""},
        {"headline": "Library hours", "raw_text": "The library opens at nine."},
    ]
    out = processor.process_records(records, "claude", min_intensity=40, scorer=scorer)
    assert scorer.calls == 1
    assert out[0]["method"] == "claude" and out[0]["intensity"] == 99
    assert out[1]["method"] == "lexical"


def test_claude_failure_falls_back_to_lexical():
    out = processor.process_records([{"headline": "Disaster!!! panic!!!"}], "claude", 0, FakeScorer(fail=True))
    assert out[0]["method"] == "lexical"


def test_claude_scorer_parses_structured_response():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(stop_reason="end_turn", content=[
            SimpleNamespace(type="text", text=json.dumps({"intensity": 80, "immediacy": 70, "scale": 60}))])

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    scorer = processor.ClaudeScorer(client=client)
    assert scorer.score("text") == {"intensity": 80, "immediacy": 70, "scale": 60}
    assert captured["output_config"]["format"]["schema"] == processor.SCORE_SCHEMA
    assert captured["model"] == processor.CLAUDE_MODEL


def test_claude_scorer_refusal_raises():
    create = lambda **_: SimpleNamespace(stop_reason="refusal", content=[])
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    with pytest.raises(processor.ScoringError):
        processor.ClaudeScorer(client=client).score("x")
