"""vendor_cerebras against the local fake chat-completions server of the Foundry tests: Cerebras' model ids, key
handling, prices, the per-model cap sized to the account's credit, and the shared spend ledger."""

import http.client
import os

import pytest

from bca.liveeval import vendor_cerebras as CB
from bca.liveeval import vendor_foundry as F
from bca.liveeval.runner import infra_kind
from bca.liveeval.vendors import VENDORS, make_subject, split_spec
from tests.test_liveeval_vendor_foundry import Fake, serve


@pytest.fixture
def cb(tmp_path, monkeypatch):
    fake = Fake()
    fake.model = "gpt-oss-120b"
    srv = serve(fake)
    monkeypatch.setattr(CB, "HOST", f"127.0.0.1:{srv.server_address[1]}")
    yield fake, tmp_path
    srv.shutdown()


def subj(tmp_path, model="gpt-oss-120b", cap=7.5, **kw):
    s = CB.CerebrasSubject(model, ledger=F.Ledger(tmp_path / "ledger.json", cap=cap), key="cb-test", **kw)
    s.connection = http.client.HTTPConnection
    return s


def test_registry_efforts_and_default_cap():
    assert VENDORS["cerebras"] is CB.CerebrasSubject
    assert split_spec("cerebras:qwen-3.8-27b") == ("cerebras", "qwen-3.8-27b")
    assert CB.CerebrasSubject.efforts == ("low", "medium", "high")
    with pytest.raises(ValueError, match="unknown effort"):
        CB.CerebrasSubject("gpt-oss-120b", effort="max")
    s = make_subject("cerebras:gpt-oss-120b", timeout=180.0)
    assert s.name == "cerebras:gpt-oss-120b" and s.ledger.cap == CB.CAP_USD == 7.5
    assert set(CB.MODELS) == {"gpt-oss-120b", "qwen-3.8-27b"}  # exactly what GET /v1/models listed, 2026-11-11


def test_request_is_ours_priced_and_ledgered(cb):
    fake, tmp = cb
    s = subj(tmp)
    r = s.complete("p" * 100)
    assert r.ok and r.text == "mov rax, 1\nret\n" and r.model == "gpt-oss-120b"
    req = fake.requests[-1]
    assert req["path"] == "/v1/chat/completions" and req["auth"] == "Bearer cb-test"
    body = req["body"]
    assert body["model"] == "gpt-oss-120b" and body["stream"] is True and "reasoning_effort" not in body
    assert set(body) == {"model", "stream", "stream_options", "messages"}
    expected = (1000 * 0.35 + 2000 * 0.75) / 1e6
    assert r.cost_usd == pytest.approx(expected) and s.ledger.spent("cerebras:gpt-oss-120b") == pytest.approx(expected)
    assert s.describe()["cerebras"]["price_per_1m"] == (0.35, 0.35, 0.75)
    s2 = subj(tmp, effort="high")
    s2.complete("p")
    assert fake.requests[-1]["body"]["reasoning_effort"] == "high"


def test_unpriced_model_and_reroute_fail_closed(cb):
    fake, tmp = cb
    r = subj(tmp, model="llama-4-scout").complete("p")
    assert not r.ok and r.error.startswith("cerebras: model") and infra_kind(r.error) == "fatal"
    fake.model = "qwen-3.8-27b"  # the stream names another model than the one asked for
    r = subj(tmp).complete("p")
    assert not r.ok and "rerouted" in r.error


def test_output_cap_is_a_scored_timeout(cb):
    fake, tmp = cb
    fake.finish = "length"
    r = subj(tmp).complete("p")
    assert r.timed_out and not r.ok and r.error == "timeout"
    assert any("Cerebras' serving default" in n for n in r.notes)


def test_key_from_env_or_private_file(tmp_path, monkeypatch):
    s = CB.CerebrasSubject("gpt-oss-120b", ledger=F.Ledger(tmp_path / "l.json"))
    monkeypatch.setenv("CEREBRAS_API_KEY", "from-env")
    assert s._bearer() == "from-env"
    monkeypatch.delenv("CEREBRAS_API_KEY")
    key = tmp_path / "cerebras.key"
    key.write_text("from-file\n")
    os.chmod(key, 0o644)
    monkeypatch.setattr(CB, "KEY_FILE", key)
    s = CB.CerebrasSubject("gpt-oss-120b", ledger=F.Ledger(tmp_path / "l.json"))
    with pytest.raises(F.FoundryError, match="readable by group or others"):
        s._bearer()
    os.chmod(key, 0o600)
    assert s._bearer() == "from-file"
    monkeypatch.setattr(CB, "KEY_FILE", tmp_path / "missing.key")
    s = CB.CerebrasSubject("gpt-oss-120b", ledger=F.Ledger(tmp_path / "l.json"))
    r = s.complete("p")
    assert not r.ok and "no API key" in r.error and infra_kind(r.error) == "fatal"
