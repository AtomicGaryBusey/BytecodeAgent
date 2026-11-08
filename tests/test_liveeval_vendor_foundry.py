"""vendor_foundry against a local fake of the Foundry chat completions endpoint (plain HTTP) and a faked
`az`: the request is ours, the deployment is read and recorded, reroutes and tool calls fail closed, safety
blocks are refusals, and the per-deployment spend cap holds."""

import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bca.liveeval import vendor_foundry as F
from bca.liveeval.runner import infra_kind
from bca.liveeval.vendors import VENDORS, make_subject, split_spec

DEP = "mai-thinking-1"


class Fake:
    def __init__(self):
        self.model = "mai-thinking-1"
        self.chunks = ["mov rax, 1\n", "ret\n"]
        self.finish = "stop"
        self.usage = {"prompt_tokens": 1000, "completion_tokens": 2000,
                      "prompt_tokens_details": {"cached_tokens": 0}}
        self.status = 200
        self.error_event = None
        self.tool_call = False
        self.requests = []


def serve(fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            fake.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
            if fake.status != 200:
                data = json.dumps({"error": {"code": "content_filter", "message": "blocked"}}).encode()
                self.send_response(fake.status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def send(obj):
                self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")

            for c in fake.chunks:
                send({"model": fake.model, "choices": [{"delta": {"content": c}, "finish_reason": None}]})
            if fake.tool_call:
                send({"model": fake.model, "choices": [{"delta": {"tool_calls": [{"id": "x"}]}}]})
            if fake.error_event:
                send({"error": fake.error_event})
            else:
                send({"model": fake.model, "choices": [{"delta": {}, "finish_reason": fake.finish}],
                      "usage": fake.usage})
            self.wfile.write(b"data: [DONE]\n\n")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = Fake()
    srv = serve(fake)
    host = f"127.0.0.1:{srv.server_address[1]}"
    calls = []

    def az(*args, timeout=60.0):
        calls.append(args)
        if args[:2] == ("account", "get-access-token"):
            return {"accessToken": "tok123", "expires_on": 9e9}
        if args[:3] == ("cognitiveservices", "account", "show"):
            return {"properties": {"endpoint": "https://wrong.cognitiveservices.azure.com/", "endpoints": {
                "Azure MAI Model Inference API v1.0": f"https://{host}/", "AI Foundry API": f"https://{host}/"}}}
        if args[:4] == ("cognitiveservices", "account", "deployment", "show"):
            return {"sku": {"name": fake_sku[0]}, "properties": {
                "model": {"name": fake_model[0], "version": "2026-06-01", "format": "Microsoft"},
                "raiPolicyName": "Microsoft.DefaultV2"}}
        raise AssertionError(args)

    fake_sku, fake_model = ["GlobalStandard"], ["MAI-Thinking-1"]
    monkeypatch.setattr(F, "_az", az)
    yield fake, tmp_path, fake_sku, fake_model, calls
    srv.shutdown()


def subj(tmp_path, cap=25.0, **kw):
    s = F.FoundrySubject(DEP, account="acct", resource_group="rg",
                         ledger=F.Ledger(tmp_path / "ledger.json", cap=cap), **kw)
    s.connection = http.client.HTTPConnection
    return s


def test_registry_spec_and_efforts():
    assert split_spec("foundry:mai-thinking-1") == ("foundry", DEP) and VENDORS["foundry"] is F.FoundrySubject
    with pytest.raises(ValueError, match="unknown effort"):
        make_subject("foundry:x", timeout=10, effort="max")
    assert "reasoning_effort" not in F.FoundrySubject(DEP).request_body("p")
    assert F.FoundrySubject(DEP, effort="low").request_body("p")["reasoning_effort"] == "low"


def test_request_is_ours_and_answer_streams(env):
    fake, tmp, *_ = env
    s = subj(tmp)
    r = s.complete("write it")
    assert r.ok and r.text == "mov rax, 1\nret\n" and r.model == "mai-thinking-1"
    req = fake.requests[0]
    assert req["path"] == "/mai/v1/chat/completions" and req["auth"] == "Bearer tok123"
    assert set(req["body"]) == {"model", "stream", "stream_options", "messages"}
    assert req["body"]["model"] == DEP and [m["role"] for m in req["body"]["messages"]] == ["system", "user"]
    d = s.describe()["foundry"]
    assert d["sku"] == "GlobalStandard" and d["content_filter"] == "Microsoft.DefaultV2" and d["model"] == DEP


def test_cost_is_priced_and_ledgered(env):
    fake, tmp, *_ = env
    s = subj(tmp)
    r = s.complete("p")
    # 1000 input at $2/M + 2000 output at $8/M
    assert r.cost_usd == pytest.approx(0.002 + 0.016)
    assert s.ledger.spent(DEP) == pytest.approx(0.018)
    fake.usage = {"prompt_tokens": 1000, "completion_tokens": 0, "prompt_tokens_details": {"cached_tokens": 800}}
    assert s.complete("p").cost_usd == pytest.approx((200 * 2 + 800 * 0.2) / 1e6)


def test_spend_cap_refuses_before_calling_and_is_fatal(env):
    fake, tmp, *_ = env
    s = subj(tmp, cap=0.5)  # below one call's worst case (about $0.59)
    r = s.complete("p")
    assert not r.ok and "foundry spend cap reached" in r.error and infra_kind(r.error) == "fatal"
    assert fake.requests == []


def test_no_usage_is_charged_worst_case(env):
    fake, tmp, *_ = env
    fake.error_event = {"type": "ServerError", "message": "boom"}
    s = subj(tmp)
    r = s.complete("p")
    assert not r.ok and "foundry stream error" in r.error
    assert r.cost_usd == pytest.approx(F.worst_case(DEP))


@pytest.mark.parametrize("how", ["event", "finish", "http400"])
def test_safety_blocks_are_refusals(env, how):
    fake, tmp, *_ = env
    if how == "event":
        fake.error_event = {"type": "SafetyBlockedError", "message": "blocked"}
    elif how == "finish":
        fake.finish = "content_filter"
    else:
        fake.status = 400
    r = subj(tmp).complete("p")
    assert not r.ok and r.refusal.startswith("content_filter") and not r.error


def test_reroute_tool_call_and_length_fail_closed(env):
    fake, tmp, *_ = env
    fake.model = "gpt-6-luna"
    r = subj(tmp).complete("p")
    assert r.error.startswith("isolation: model rerouted") and infra_kind(r.error) == "fatal"
    fake.model, fake.tool_call = "mai-thinking-1", True
    assert subj(tmp).complete("p").error == "isolation: the model emitted a tool call"
    fake.tool_call, fake.finish = False, "length"
    assert subj(tmp).complete("p").error.startswith("isolation: the answer reached the model's output cap")


def test_unpriced_model_and_wrong_sku_are_fatal(env):
    fake, tmp, sku, model, _ = env
    model[0] = "Mistral-Large-3"
    r = subj(tmp).complete("p")
    assert "no route and price" in r.error and infra_kind(r.error) == "fatal" and fake.requests == []
    model[0], sku[0] = "MAI-Thinking-1", "DataZoneStandard"
    r = subj(tmp).complete("p")
    assert "only GlobalStandard" in r.error and infra_kind(r.error) == "fatal"


def test_missing_config_is_fatal(tmp_path, monkeypatch):
    monkeypatch.delenv("BCA_FOUNDRY_ACCOUNT", raising=False)
    r = F.FoundrySubject(DEP, ledger=F.Ledger(tmp_path / "l.json")).complete("p")
    assert "BCA_FOUNDRY_ACCOUNT" in r.error and infra_kind(r.error) == "fatal"


def test_early_abort_is_killed_not_failed(env):
    fake, tmp, *_ = env
    r = subj(tmp).complete("p", on_text=lambda t: False)
    assert r.ok and r.killed and r.text == "mov rax, 1\n"


def test_a_rejected_request_is_not_charged(env):
    fake, tmp, *_ = env
    fake.status = 429  # rate limited: Azure generated and billed nothing
    s = subj(tmp)
    r = s.complete("p")
    assert not r.ok and "HTTP 429" in r.error and infra_kind(r.error) == "transient"
    assert r.cost_usd == 0 and s.ledger.spent(DEP) == 0
