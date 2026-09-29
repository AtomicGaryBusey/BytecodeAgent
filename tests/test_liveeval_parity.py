"""Cross-vendor parity: refusals, repair-prompt text, timeouts, setup failures and summaries do not
depend on which CLI (or which text delivery) a subject uses."""

import json
import sys
import textwrap

from bca.liveeval import runner
from bca.liveeval.pipelines import attempt
from bca.liveeval.subject import CLAUDE_FLAGS, ClaudeSubject, FakeSubject, Response, Subject
from bca.liveeval.summarize import _cost, _cost_text, _model_label
from bca.liveeval.tasks import TASKS
from bca.liveeval.vendor_base import CliSubject, IsolationError, Parsed

SUM = next(t for t in TASKS if t.name == "sum_to")


def _claude_stream(tmp_path, events):
    exe = tmp_path / "claude"
    exe.write_text(f"#!{sys.executable}\nimport json\nfor e in {events!r}:\n    print(json.dumps(e), flush=True)\n")
    exe.chmod(0o755)
    return str(exe)


def _delta(t):
    return {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}}


def _start(m):
    return {"type": "stream_event", "event": {"type": "message_start", "message": {"model": m}}}


REFUSAL_DELTA = {"type": "stream_event", "event": {"type": "message_delta", "delta": {
    "stop_reason": "refusal", "stop_details": {"type": "refusal", "category": "cyber", "explanation": "blocked"}}}}


def test_claude_never_falls_back_silently():
    i = CLAUDE_FLAGS.index("--settings")
    assert json.loads(CLAUDE_FLAGS[i + 1]) == {"switchModelsOnFlag": False}


def test_claude_refusal_is_a_scored_refusal_and_discards_partial_text(tmp_path):
    events = [{"type": "system", "subtype": "init", "model": "claude-fable-5-1"}, _start("claude-fable-5-1"),
              _delta("sum_to:\n    xor eax, eax\n"), REFUSAL_DELTA,
              {"type": "system", "subtype": "model_refusal_no_fallback", "original_model": "claude-fable-5-1",
               "api_refusal_category": "cyber", "api_refusal_explanation": "blocked"},
              _start("<synthetic>"),
              {"type": "result", "subtype": "success", "is_error": False, "stop_reason": "refusal", "usage": {}}]
    subj = ClaudeSubject(binary=_claude_stream(tmp_path, events), timeout=30)
    for path in ("stream", "structured", "ir"):
        a = attempt(subj, path, "x86_64", SUM, "p")
        assert a.category == "refusal" and "cyber" in a.failure, (path, a.category)
        assert a.response.model == "claude-fable-5-1"  # <synthetic> is not a model
        assert not runner.is_infra_error(a)  # the model's own outcome: scored like any failure


def test_claude_fallback_is_flagged_if_it_ever_happens(tmp_path):
    events = [_start("claude-fable-5-1"), _start("claude-opus-4-8"), _delta("x"),
              {"type": "system", "subtype": "model_refusal_fallback", "original_model": "claude-fable-5-1",
               "fallback_model": "claude-opus-4-8", "api_refusal_category": "cyber"},
              {"type": "result", "subtype": "success", "is_error": False, "usage": {}}]
    r = ClaudeSubject(binary=_claude_stream(tmp_path, events), timeout=30).complete("p")
    assert r.refusal.startswith("cyber") and "fallback model claude-opus-4-8" in r.refusal


BAD = "sum_to:\n    xor eax, eax\n    bogus rax\n    ret\n"


def test_stream_repair_text_is_the_same_for_deltas_and_one_piece():
    streamed = attempt(FakeSubject([[BAD[i:i + 5] for i in range(0, len(BAD), 5)]]), "stream", "x86_64", SUM, "p")
    whole = attempt(FakeSubject([BAD]), "stream", "x86_64", SUM, "p")
    assert streamed.category == whole.category == "syntax"
    assert streamed.code_text == whole.code_text == "sum_to:\n    xor eax, eax\n    bogus rax\n"
    assert streamed.failure == whole.failure


def test_timeout_shows_no_partial_text_to_the_repair_round():
    for path in ("stream", "structured", "ir"):
        a = attempt(FakeSubject([{"chunks": ["sum_to:\n    xor eax, eax\n"], "timeout": True}]), path, "x86_64",
                    SUM, "p")
        assert a.category == "subject_timeout" and a.code_text == "", path


class _BrokenSetup(CliSubject):
    vendor, binary_name, efforts = "broken", "true", ()

    def default_home(self):
        return self._home

    def argv(self, prompt, home):
        raise IsolationError("isolation: cannot switch the tools off")


def test_setup_failure_is_a_fatal_isolation_error_not_a_crash(tmp_path):
    subj = _BrokenSetup.__new__(_BrokenSetup)
    subj._home = tmp_path
    CliSubject.__init__(subj, "m", home_real=tmp_path)
    r = subj.complete("p")
    assert not r.ok and r.error.startswith("isolation:") and runner.infra_kind(r.error) == "fatal"
    assert r.model_source == "requested"


def test_summary_labels_unreported_models_and_missing_cost():
    assert _model_label({"model": "gpt-6-luna", "model_source": "requested"}) == "gpt-6-luna (requested; not reported)"
    assert _model_label({"model": "claude-opus-5-5", "model_source": "reported"}) == "claude-opus-5-5"
    assert _model_label({"model": "claude-opus-5-5"}) == "claude-opus-5-5"  # records from before the field
    assert _cost_text(_cost([{"cost_usd": None}, {}])) == "n/a"
    assert _cost_text(_cost([{"cost_usd": 0.5}, {"cost_usd": None}])) == "$0.50"


def test_antigravity_quota_is_fatal():
    err = 'AGY_ERROR: {"code":429,"status":"RESOURCE_EXHAUSTED","message":"You have exhausted your quota on this model."}'
    assert runner.infra_kind(err) == "fatal"
    # capacity / per-minute 429s are transient (agy also retries them itself)
    assert runner.infra_kind('AGY_ERROR: {"code":429,"message":"exhausted your capacity; quota will reset after 7s"}') == "transient"
    assert runner.infra_kind("AGY_ERROR: {\"code\":503}") == "transient"


def test_parsed_and_response_defaults():
    assert Parsed().model_reported is False and Parsed().refusal == ""
    assert Response("x").model_source == "reported" and Response("x").refusal == ""
    assert textwrap and Subject  # imported names used


def test_antigravity_is_registered_and_its_auth_errors_are_fatal():
    from bca.liveeval.vendors import AgySubject, VENDORS, split_spec

    assert VENDORS["agy"] is AgySubject and split_spec("agy:gemini-3.8-flash-high") == ("agy", "gemini-3.8-flash-high")
    for err in ("You have exhausted your quota on this model.", "Error: not signed in",
                "No authentication methods available"):
        assert runner.infra_kind(err) == "fatal", err
    assert runner.infra_kind('AGY_ERROR: {"code":429,"status":"RESOURCE_EXHAUSTED","message":"slow down"}') == "transient"


def test_keyring_vendor_never_touches_the_real_home(tmp_path):
    from bca.liveeval.vendor_base import CredHome

    real = tmp_path / "real"
    h = CredHome(real, (), (), ())
    assert h.sync_back() == []
    h.cleanup()
    assert not real.exists()


def test_claude_server_side_fallback_is_flagged(tmp_path):
    events = [_start("claude-fable-5-1"), _delta("x"),
              {"type": "result", "subtype": "success", "is_error": False,
               "usage": {"iterations": [{"type": "fallback_message", "model": "claude-opus-4-8"}]}}]
    r = ClaudeSubject(binary=_claude_stream(tmp_path, events), timeout=30).complete("p")
    assert r.model == "claude-opus-4-8" and "fallback" in r.refusal


def test_refusal_then_same_model_retry_is_still_a_refusal(tmp_path):
    """Claude Code retries once after a refusal with an injected nudge: that answer is not scored."""
    events = [_start("claude-fable-5-1"), REFUSAL_DELTA, _start("claude-fable-5-1"), _delta("sum_to:\n    ret\n"),
              {"type": "result", "subtype": "success", "is_error": False, "usage": {}}]
    a = attempt(ClaudeSubject(binary=_claude_stream(tmp_path, events), timeout=30), "structured", "x86_64", SUM, "p")
    assert a.category == "refusal"


# -- credential safety (CredHome) ---------------------------------------------------------------

import fcntl  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

from bca.liveeval.vendor_base import CredHome, credentials_degraded  # noqa: E402


def _home(tmp_path, data):
    real = tmp_path / "real"
    (real / "credentials").mkdir(parents=True, exist_ok=True)
    (real / "credentials" / "c.json").write_text(json.dumps(data))
    return real


REL = ("credentials/c.json",)


def test_blanked_or_broken_credentials_are_never_copied_back(tmp_path):
    good = {"access_token": "a" * 20, "refresh_token": "r" * 20, "expires_at": 1}
    assert credentials_degraded(json.dumps(good).encode(), json.dumps({**good, "access_token": ""}).encode())
    assert credentials_degraded(json.dumps(good).encode(), b'{"access_token": "x"')
    assert not credentials_degraded(json.dumps(good).encode(), json.dumps({**good, "access_token": "b" * 20}).encode())
    real = _home(tmp_path, good)
    h = CredHome(real, REL, REL, REL)
    (h.path / REL[0]).write_text(json.dumps({**good, "access_token": "", "refresh_token": ""}))
    assert h.sync_back(True) == []
    h.cleanup()
    assert json.loads((real / REL[0]).read_text()) == good


def test_a_valid_refresh_during_a_failed_call_is_kept(tmp_path):
    """With rotating refresh tokens the old one is revoked once used: keep the new one even if the call failed."""
    good = {"access_token": "a" * 20, "refresh_token": "r" * 20}
    real = _home(tmp_path, good)
    h = CredHome(real, REL, REL, REL)
    new = {**good, "access_token": "b" * 20, "refresh_token": "s" * 20}
    (h.path / REL[0]).write_text(json.dumps(new))
    assert h.sync_back(False) == [REL[0]]
    h.cleanup()
    assert json.loads((real / REL[0]).read_text()) == new


def test_status_code_patterns_need_an_error_context():
    for transient in ("retrying after 401 ms", "stream disconnected at byte 4040", "rate limited, 403 requests left"):
        assert runner.infra_kind(transient) == "transient", transient
    for fatal in ("API Error: 401 {\"type\":\"error\"}", "unexpected status 403 Forbidden", "HTTP 401 Unauthorized",
                  '{"code":401,"message":"bad token"}'):
        assert runner.infra_kind(fatal) == "fatal", fatal


def test_refresh_leader_serialises_calls_near_expiry(tmp_path):
    good = {"access_token": "a" * 20, "refresh_token": "r" * 20, "expires_at": time.time() + 30}
    real = _home(tmp_path, good)

    def expiry(rel, data):
        return json.loads(data)["expires_at"]

    leader = CredHome(real, REL, REL, REL, expiry=expiry, horizon=300)
    assert leader.leader
    started = threading.Event()

    def second():
        h = CredHome(real, REL, REL, REL, expiry=expiry, horizon=300)
        started.set()
        h.sync_back(True)
        h.cleanup()

    t = threading.Thread(target=second)
    t.start()
    assert not started.wait(0.5)  # blocked while the leader's call runs
    refreshed = {**good, "access_token": "b" * 20, "expires_at": time.time() + 900}
    (leader.path / REL[0]).write_text(json.dumps(refreshed))
    assert leader.sync_back(True) == [REL[0]]
    leader.cleanup()
    assert started.wait(5)  # the next call starts with the refreshed token
    t.join(5)
    assert json.loads((real / REL[0]).read_text())["access_token"] == "b" * 20


def test_fresh_tokens_do_not_serialise(tmp_path):
    good = {"access_token": "a" * 20, "expires_at": time.time() + 3600}
    real = _home(tmp_path, good)
    a = CredHome(real, REL, REL, REL, expiry=lambda r, d: json.loads(d)["expires_at"], horizon=300)
    b = CredHome(real, REL, REL, REL, expiry=lambda r, d: json.loads(d)["expires_at"], horizon=300)
    assert not a.leader and not b.leader
    for h in (a, b):
        h.sync_back(True)
        h.cleanup()
    assert fcntl
