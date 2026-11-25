import json

import pytest

from broker.retrieval import FailureIndex, redact, tokenize
from broker.retrieval_eval import DATA, load_queries, score

SECRETS = ("EXAMPLE-NOT-A-REAL-TOKEN", "example-password-not-real")


@pytest.fixture(scope="module")
def index():
    return FailureIndex.from_jsonl()


def test_redaction():
    text = "DEBUG request headers: Authorization: Bearer abc.def\nDEBUG env SERVICE_PASSWORD=hunter2 API_KEY: xyz"
    out = redact(text)
    assert "abc.def" not in out and "hunter2" not in out and "xyz" not in out
    assert out.count("[REDACTED]") == 3


def test_secrets_never_indexed_or_returned(index):
    for entry in index.logs:
        assert not any(s in entry.log for s in SECRETS)
    result = index.search("driver too old for CUDA", k=10)
    dumped = json.dumps(result.to_dict())
    assert not any(s in dumped for s in SECRETS)


def test_tokenizer_handles_error_text():
    assert tokenize("Input contains NaN") == ["input", "contain", "nan"]
    assert tokenize("Container exited with code 137 (OOMKilled)") == ["container", "exit", "code", "137", "oom", "kill"]
    assert "permission" in tokenize("PermissionError: [Errno 13]")
    assert "7278" not in tokenize("at step 7278")  # long numbers are noise


def test_boilerplate_lines_are_learned_not_listed(index):
    assert "DEBUG heartbeat ok" not in sum((e.signal for e in index.logs), [])
    assert any(line.startswith("RuntimeError: CUDA error: out of memory") for line in index.logs[0].signal + index.logs[1].signal + index.logs[2].signal + index.logs[3].signal)


def test_given_questions_meet_precision_and_abstain(index):
    s = score(index, load_queries(DATA / "01c_retrieval_queries.jsonl"))
    assert s.p3 >= 0.95 and s.p5 >= 0.95
    assert s.abstain_ok == s.abstain_total and s.false_abstain == 0


def test_held_out_paraphrases(index):
    s = score(index, load_queries(DATA / "holdout_queries.jsonl"))
    assert s.p3 >= 0.9 and s.p5 >= 0.9
    assert s.abstain_ok == s.abstain_total


def test_pasted_log_finds_same_cause(index):
    log = "INFO epoch 3 started\nDEBUG heartbeat ok\nwrite /var/lib/jobs/nlp/out.log: no space left on device\nINFO epoch 4 started"
    result = index.search(log)
    assert {m.id for m in result.matches} <= {"F031", "F032", "F033", "F034", "F035", "F036"}
    assert result.suggested_resolution.startswith("Scratch disk full")


def test_log_without_recorded_fix_borrows_one_from_similar_failures(index):
    result = index.search("Container exited with code 137 (OOMKilled)")
    no_fix = [m for m in result.matches if m.resolution is None]
    assert no_fix  # F911/F912 are found ...
    assert result.suggested_resolution.startswith("Reduce batch size")  # ... and a fix is still suggested


def test_identical_logs_with_different_fixes_are_flagged(index):
    result = index.search("Timed out waiting for response (30000 ms)\nINFO retrying")
    assert {m.id for m in result.matches[:2]} == {"F900", "F901"}
    assert result.ambiguous


def test_off_topic_question_returns_nothing(index):
    result = index.search("how do I book a meeting room")
    assert result.abstained and result.matches == []
