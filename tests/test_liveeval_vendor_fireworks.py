"""vendor_fireworks against the local fake chat-completions server of the Foundry tests: Fireworks' model ids,
key handling, prices and the shared spend ledger."""

import http.client
import os

import pytest

from bca.liveeval import vendor_fireworks as FW
from bca.liveeval import vendor_foundry as F
from bca.liveeval.runner import infra_kind
from bca.liveeval.vendors import VENDORS, make_subject, split_spec
from tests.test_liveeval_vendor_foundry import Fake, serve

FULL = "accounts/fireworks/models/glm-5p3"


@pytest.fixture
def fw(tmp_path, monkeypatch):
    fake = Fake()
    fake.model = FULL
    srv = serve(fake)
    monkeypatch.setattr(FW, "HOST", f"127.0.0.1:{srv.server_address[1]}")
    yield fake, tmp_path
    srv.shutdown()


def subj(tmp_path, model="glm-5p3", cap=25.0, **kw):
    s = FW.FireworksSubject(model, ledger=F.Ledger(tmp_path / "ledger.json", cap=cap), key="fw-test", **kw)
    s.connection = http.client.HTTPConnection
    return s


def test_registry_and_efforts():
    assert split_spec("fireworks:glm-5p3") == ("fireworks", "glm-5p3") and VENDORS["fireworks"] is FW.FireworksSubject
    with pytest.raises(ValueError, match="unknown effort"):
        make_subject("fireworks:glm-5p3", timeout=10, effort="ultra")
    assert FW.FireworksSubject(FULL).model_id == "glm-5p3"


def test_request_is_ours_priced_and_ledgered(fw):
    fake, tmp = fw
    s = subj(tmp, effort="high")
    r = s.complete("p")
    assert r.ok and r.text == "mov rax, 1\nret\n"
    req = fake.requests[0]
    assert req["path"] == "/inference/v1/chat/completions" and req["auth"] == "Bearer fw-test"
    assert set(req["body"]) == {"model", "stream", "stream_options", "messages", "reasoning_effort"}
    assert req["body"]["model"] == FULL and req["body"]["reasoning_effort"] == "high"
    assert r.cost_usd == pytest.approx((1000 * 1.40 + 2000 * 4.40) / 1e6)
    assert s.ledger.spent("fireworks:glm-5p3") == pytest.approx(r.cost_usd)
    assert s.describe()["fireworks"]["model"] == FULL


def test_reroute_unpriced_and_cap_fail_closed(fw):
    fake, tmp = fw
    fake.model = "accounts/fireworks/models/other"
    assert subj(tmp).complete("p").error.startswith("isolation: model rerouted")
    r = subj(tmp, model="kimi-k3p1-pro").complete("p")
    assert "has no price here" in r.error and infra_kind(r.error) == "fatal"
    r = subj(tmp, cap=0.1).complete("p")
    assert "fireworks spend cap reached" in r.error and infra_kind(r.error) == "fatal"


def test_key_file_must_be_private(tmp_path, monkeypatch):
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    kf = tmp_path / "fw.key"
    kf.write_text("fw-secret\n")
    os.chmod(kf, 0o644)
    monkeypatch.setattr(FW, "KEY_FILE", kf)
    s = FW.FireworksSubject("glm-5p3", ledger=F.Ledger(tmp_path / "l.json"))
    r = s.complete("p")
    assert "chmod 600" in r.error and "fw-secret" not in r.error and infra_kind(r.error) == "fatal"
    os.chmod(kf, 0o600)
    assert s._bearer() == "fw-secret"
    monkeypatch.setattr(FW, "KEY_FILE", tmp_path / "missing.key")
    r = FW.FireworksSubject("glm-5p3", ledger=F.Ledger(tmp_path / "l.json")).complete("p")
    assert "no API key" in r.error and infra_kind(r.error) == "fatal"


def test_display_name_accepted_only_where_listed():
    assert FW.FireworksSubject("qwen3p8-max").setup()["served_as"] == ("Qwen 3.8 Max",)
    assert FW.FireworksSubject("glm-5p3").setup()["served_as"] == ()


def test_display_name_passes_the_stream_check_only_where_listed(fw):
    fake, tmp = fw
    fake.model = "Qwen 3.8 Max"
    assert subj(tmp, model="qwen3p8-max").complete("p").ok
    assert subj(tmp).complete("p").error.startswith("isolation: model rerouted")  # glm lists no display name


def test_no_usage_is_booked_from_the_streamed_deltas(fw):
    fake, tmp = fw
    fake.usage = None  # as on a timeout: the stream ends without a usage report
    s = subj(tmp)
    r = s.complete("p" * 100)
    expected = ((50 + 2000) * 1.40 + int(2 * 1.25 + 2048) * 4.40) / 1e6
    assert r.cost_usd == pytest.approx(expected, abs=1e-6) and expected < s._worst("glm-5p3") / 10
    assert s.ledger.spent("fireworks:glm-5p3") == pytest.approx(expected, abs=1e-6)
    assert any("estimate from the streamed deltas" in n for n in r.notes)


def test_output_cap_is_a_scored_timeout(fw):
    from bca.liveeval.pipelines import call_failure_category
    fake, tmp = fw
    fake.finish = "length"
    r = subj(tmp).complete("p")
    assert r.timed_out and not r.ok and call_failure_category(r) == "subject_timeout"
    assert any("output cap reached: 2000" in n for n in r.notes)
