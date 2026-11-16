"""vendor_mistral against the local fake chat-completions server of the Foundry tests: model ids, key handling,
prices booked at the higher published rate, the $0 default cap, and the shared spend ledger."""

import http.client
import os

import pytest

from bca.liveeval import vendor_mistral as CB
from bca.liveeval import vendor_foundry as F
from bca.liveeval.runner import infra_kind
from bca.liveeval.vendors import VENDORS, make_subject, split_spec
from tests.test_liveeval_vendor_foundry import Fake, serve


@pytest.fixture
def cb(tmp_path, monkeypatch):
    fake = Fake()
    fake.model = "mistral-large-4"
    srv = serve(fake)
    monkeypatch.setattr(CB, "HOST", f"127.0.0.1:{srv.server_address[1]}")
    yield fake, tmp_path
    srv.shutdown()


def subj(tmp_path, model="mistral-large-4", cap=7.5, **kw):
    s = CB.MistralSubject(model, ledger=F.Ledger(tmp_path / "ledger.json", cap=cap), key="ms-test", **kw)
    s.connection = http.client.HTTPConnection
    return s


def test_registry_no_effort_and_zero_default_cap():
    assert VENDORS["mistral"] is CB.MistralSubject
    assert split_spec("mistral:mistral-large-4") == ("mistral", "mistral-large-4")
    with pytest.raises(ValueError, match="no effort setting"):
        CB.MistralSubject("mistral-large-4", effort="high")
    s = make_subject("mistral:mistral-large-4", timeout=180.0)
    assert s.name == "mistral:mistral-large-4" and s.ledger.cap == CB.CAP_USD == 0.0  # nothing runs without a cap


def test_request_is_ours_priced_and_ledgered(cb):
    fake, tmp = cb
    s = subj(tmp)
    r = s.complete("p" * 100)
    assert r.ok and r.text == "mov rax, 1\nret\n" and r.model == "mistral-large-4"
    req = fake.requests[-1]
    assert req["path"] == "/v1/chat/completions" and req["auth"] == "Bearer ms-test"
    body = req["body"]
    assert body["model"] == "mistral-large-4" and body["stream"] is True and "reasoning_effort" not in body
    assert set(body) == {"model", "stream", "stream_options", "messages"}
    expected = (1000 * 1.36 + 2000 * 4.18) / 1e6
    assert r.cost_usd == pytest.approx(expected) and s.ledger.spent("mistral:mistral-large-4") == pytest.approx(expected)
    assert s.describe()["mistral"]["price_per_1m"] == (1.36, 0.14, 4.18)


def test_the_default_cap_of_zero_refuses_the_call(cb):
    fake, tmp = cb
    r = subj(tmp, cap=0.0).complete("p")
    assert not r.ok and not fake.requests


def test_unpriced_model_and_reroute_fail_closed(cb):
    fake, tmp = cb
    r = subj(tmp, model="mistral-tiny").complete("p")
    assert not r.ok and r.error.startswith("mistral: model") and infra_kind(r.error) == "fatal"
    fake.model = "mistral-medium-3-5"  # the stream names another model than the one asked for
    r = subj(tmp).complete("p")
    assert not r.ok and "rerouted" in r.error


def test_output_cap_is_a_scored_timeout(cb):
    fake, tmp = cb
    fake.finish = "length"
    r = subj(tmp).complete("p")
    assert r.timed_out and not r.ok and r.error == "timeout"
    assert any("Mistral's serving default" in n for n in r.notes)


def test_key_from_env_or_private_file(tmp_path, monkeypatch):
    s = CB.MistralSubject("mistral-large-4", ledger=F.Ledger(tmp_path / "l.json"))
    monkeypatch.setenv("MISTRAL_API_KEY", "from-env")
    assert s._bearer() == "from-env"
    monkeypatch.delenv("MISTRAL_API_KEY")
    key = tmp_path / "mistral.key"
    key.write_text("from-file\n")
    os.chmod(key, 0o644)
    monkeypatch.setattr(CB, "KEY_FILE", key)
    s = CB.MistralSubject("mistral-large-4", ledger=F.Ledger(tmp_path / "l.json"))
    with pytest.raises(F.FoundryError, match="readable by group or others"):
        s._bearer()
    os.chmod(key, 0o600)
    assert s._bearer() == "from-file"
    monkeypatch.setattr(CB, "KEY_FILE", tmp_path / "missing.key")
    s = CB.MistralSubject("mistral-large-4", ledger=F.Ledger(tmp_path / "l.json"))
    r = s.complete("p")
    assert not r.ok and "no API key" in r.error and infra_kind(r.error) == "fatal"


def test_large_4_thinking_chunks_are_reasoning_not_answer():
    """Mistral Large 4 streams reasoning inside `content` as typed chunks (seen live 2026-11-15)."""
    s = CB.MistralSubject("mistral-large-4", key="k")
    d = s._delta({"content": [{"type": "thinking", "thinking": [{"type": "text", "text": "The user asks"}], "closed": True}]})
    assert "content" not in d and d["reasoning"] == "The user asks"
    d = s._delta({"content": [{"type": "text", "text": "mov rax, 1"}]})
    assert d == {"content": "mov rax, 1"}
    assert s._delta({"content": "ret"}) == {"content": "ret"}
    assert "unknown content chunk" in s._delta({"content": [{"type": "image_url", "url": "x"}]})["_isolation"]
