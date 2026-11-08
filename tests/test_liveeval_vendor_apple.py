"""vendor_apple against a local fake of `fm serve` (plain HTTP, as at the local end of the SSH forward):
the request is ours, the served model is checked, a context overflow is a scored context_limit attempt,
a guardrail 500 is a refusal, and the Mac's governor 503 is a retried infrastructure error."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bca.liveeval.pipelines import call_failure_category
from bca.liveeval.runner import infra_kind
from bca.liveeval.subject import SYSTEM_PROMPT
from bca.liveeval.vendor_apple import AppleSubject
from bca.liveeval.vendors import VENDORS, make_subject, split_spec


class Fake:
    def __init__(self):
        self.model, self.status, self.body, self.refusal, self.requests = "system", 200, b"", None, []


@pytest.fixture
def fake():
    f = Fake()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            f.requests.append({"path": self.path, "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
            if f.status != 200:
                self.send_response(f.status)
                self.send_header("Content-Length", str(len(f.body)))
                self.end_headers()
                self.wfile.write(f.body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def send(o):
                self.wfile.write(b"data: " + json.dumps(o).encode() + b"\n\n")

            delta = {"refusal": f.refusal} if f.refusal else {"content": "mov rax, 1\nret\n"}
            send({"model": f.model, "choices": [{"delta": delta, "finish_reason": None}]})
            send({"model": f.model, "choices": [{"delta": {}, "finish_reason": "stop"}],
                  "usage": {"prompt_tokens": 120, "completion_tokens": 8}})
            self.wfile.write(b"data: [DONE]\n\n")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    f.endpoint = f"http://127.0.0.1:{srv.server_address[1]}"
    yield f
    srv.shutdown()


def test_registry_and_no_effort():
    assert split_spec("apple:system") == ("apple", "system") and VENDORS["apple"] is AppleSubject
    with pytest.raises(ValueError, match="no effort"):
        make_subject("apple:system", timeout=10, effort="high")


def test_request_is_ours_and_free(fake):
    r = AppleSubject("system", endpoint=fake.endpoint).complete("write it")
    assert r.ok and r.text == "mov rax, 1\nret\n" and r.model == "system" and r.cost_usd == 0.0
    req = fake.requests[0]
    assert req["path"] == "/v1/chat/completions"
    assert set(req["body"]) == {"model", "stream", "stream_options", "messages"}
    assert req["body"]["messages"] == [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "write it"}]


def test_context_overflow_is_a_scored_attempt(fake):
    fake.status = 500
    fake.body = b'{"error":{"message":"The session\'s transcript exceeded the model\'s context size.","type":"server_error"}}'
    r = AppleSubject("system", endpoint=fake.endpoint).complete("p")
    assert not r.ok and r.error.startswith("context_limit:") and call_failure_category(r) == "context_limit"


def test_guardrail_500_and_refusal_field_are_refusals(fake):
    fake.status = 500
    fake.body = b'{"error":{"message":"Safety guardrails were triggered.","type":"server_error"}}'
    r = AppleSubject("system", endpoint=fake.endpoint).complete("p")
    assert not r.ok and r.refusal.startswith("guardrail:") and not r.error
    fake.status, fake.refusal = 200, "I can't help with that."
    r = AppleSubject("system", endpoint=fake.endpoint).complete("p")
    assert not r.ok and r.refusal.startswith("refusal:")


def test_governor_503_and_other_500_are_transient(fake):
    fake.status, fake.body = 503, b'{"error":{"message":"memory pressure warn: circuit breaker open"}}'
    r = AppleSubject("system", endpoint=fake.endpoint).complete("p")
    assert not r.ok and call_failure_category(r) == "subject_error" and infra_kind(r.error) == "transient"
    fake.status, fake.body = 500, b'{"error":{"message":"something else"}}'
    r = AppleSubject("system", endpoint=fake.endpoint).complete("p")
    assert call_failure_category(r) == "subject_error" and "something else" in r.error


def test_reroute_is_fatal_and_endpoint_must_be_local(fake):
    fake.model = "pcc"
    r = AppleSubject("system", endpoint=fake.endpoint).complete("p")
    assert r.error.startswith("isolation: model rerouted") and infra_kind(r.error) == "fatal"
    r = AppleSubject("system", endpoint="http://192.168.1.154:8080").complete("p")
    assert r.error.startswith("apple: ") and infra_kind(r.error) == "fatal"


def test_governor_timeout_is_a_timeout(fake):
    fake.status = 503
    fake.body = b'{"error":{"message":"The on-device model did not answer within 120 s.","type":"service_unavailable"}}'
    r = AppleSubject("system", endpoint=fake.endpoint).complete("p")
    assert r.timed_out and not r.ok and call_failure_category(r) == "subject_timeout"


def test_memory_pause_stops_the_run(fake):
    fake.status = 503
    fake.body = (b'{"error":{"message":"fm is paused to protect this Mac: Apple\'s inference service is using 3,187 MB '
                 b'(limit 3,072 MB). Do not retry automatically.","type":"service_unavailable"}}')
    r = AppleSubject("system", endpoint=fake.endpoint).complete("p")
    assert call_failure_category(r) == "subject_error" and infra_kind(r.error) == "fatal"
