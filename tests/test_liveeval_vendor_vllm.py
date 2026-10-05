"""vllm:<model> (bca.liveeval.vendor_vllm) against a fake vLLM OpenAI-compatible server: the request we send,
the checks on what served it, reasoning handling, the context-window check, early abort, timeouts and error
classification."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bca.liveeval.runner import infra_kind
from bca.liveeval.subject import SYSTEM_PROMPT
from bca.liveeval.vendor_vllm import VllmSubject
from bca.liveeval.vendors import VENDORS, make_subject, split_spec

MODEL = "Aleph-Alpha/Kolibri-1"


class Fake:
    def __init__(self):
        self.version = "0.29.0"
        self.chunks = ["mov rax, 1\n", "ret\n"]
        self.reasoning = None  # streamed as reasoning_content deltas first
        self.served = MODEL
        self.finish = "stop"
        self.tool_call = False
        self.requests = []
        self.delay = 0.0


def serve(fake: Fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/version":
                return self._json({"version": fake.version})
            if self.path == "/v1/models":
                return self._json({"data": [{"id": MODEL, "root": "/models/kolibri", "max_model_len": 65536}]})
            self._json({"error": "nope"}, 404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            if self.path != "/v1/chat/completions":
                return self._json({"error": "nope"}, 404)
            fake.requests.append(body)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def send(delta, finish=None, usage=None):
                ev = {"model": fake.served, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                if usage:
                    ev = {"model": fake.served, "choices": [], "usage": usage}
                self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
                self.wfile.flush()

            try:
                if fake.reasoning:
                    send({"reasoning_content": fake.reasoning})
                if fake.tool_call:
                    send({"tool_calls": [{"index": 0, "function": {"name": "f", "arguments": "{}"}}]})
                for c in fake.chunks:
                    time.sleep(fake.delay)
                    send({"content": c})
                send({}, finish=fake.finish)
                send({}, usage={"prompt_tokens": 300, "completion_tokens": 10})
                self.wfile.write(b"data: [DONE]\n\n")
            except OSError:
                pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def env():
    fake = Fake()
    srv = serve(fake)
    yield fake, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def subj(url, **kw):
    return VllmSubject(MODEL, url=url, timeout=30, **kw)


def test_spec_registry_and_efforts():
    assert split_spec("vllm:Aleph-Alpha/Kolibri-1") == ("vllm", MODEL)
    assert VENDORS["vllm"] is VllmSubject
    with pytest.raises(ValueError, match="unknown effort"):
        make_subject("vllm:x", timeout=10, effort="xhigh")
    assert "chat_template_kwargs" not in VllmSubject(MODEL).request_body("p")
    assert VllmSubject(MODEL, effort="low").request_body("p")["chat_template_kwargs"] == {"reasoning_effort": "low"}
    assert VllmSubject(MODEL, effort="none").request_body("p")["chat_template_kwargs"] == {
        "reasoning_effort": "none", "enable_thinking": False}


def test_request_is_ours_and_answer_streams(env, tmp_path):
    fake, url = env
    s = subj(url)
    seen = []
    raw = tmp_path / "raw.txt"
    r = s.complete("PROMPT", lambda t: seen.append(t) or True, raw_path=str(raw))
    assert r.ok and r.text == "mov rax, 1\nret\n" and seen == ["mov rax, 1\n", "ret\n"]
    body = fake.requests[0]
    assert body["messages"] == [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "PROMPT"}]
    assert body["stream"] is True and set(body) == {"model", "stream", "stream_options", "messages"}  # no tools/sampling
    assert r.model == MODEL and r.model_source == "reported" and r.usage["completion_tokens"] == 10
    assert r.usage["finish_reason"] == "stop" and "[DONE]" in raw.read_text()
    d = s.describe()
    assert d["effort"] == "model-default" and d["cli_version"] == "vllm 0.29.0" and d["vllm"]["max_model_len"] == 65536


@pytest.mark.parametrize("change,msg", [
    (lambda f: setattr(f, "served", "other/model"), "model rerouted"),
    (lambda f: setattr(f, "finish", "length"), "context window"),
    (lambda f: setattr(f, "tool_call", True), "tool call"),
])
def test_fails_closed_on_what_served_the_call(env, change, msg):
    fake, url = env
    change(fake)
    r = subj(url).complete("x")
    assert not r.ok and r.error.startswith("isolation:") and msg in r.error and infra_kind(r.error) == "fatal"


def test_unaudited_version_unknown_model_dead_server_are_fatal(env):
    fake, url = env
    r = VllmSubject("nope/model", url=url).complete("x")
    assert "not served" in r.error and infra_kind(r.error) == "fatal"
    fake.version = "0.30.0"
    r = subj(url).complete("x")
    assert "not an audited version" in r.error and infra_kind(r.error) == "fatal"
    r = VllmSubject(MODEL, url="http://127.0.0.1:9").complete("x")
    assert "unreachable" in r.error and infra_kind(r.error) == "fatal"


def test_reasoning_is_recorded_not_scored(env):
    fake, url = env
    fake.reasoning = "let me think"
    fake.chunks = ["<think>more</think>\n", "ret\n"]
    r = subj(url).complete("x")
    assert r.ok and r.text == "ret\n" and any("reasoning not scored" in n for n in r.notes)


def test_effort_none_with_reasoning_fails_closed(env):
    fake, url = env
    fake.reasoning = "thinking anyway"
    r = subj(url, effort="none").complete("x")
    assert not r.ok and "did not honour" in r.error and infra_kind(r.error) == "fatal"


def test_early_abort_and_timeout(env):
    fake, url = env
    fake.chunks = ["line1\n", "line2\n", "line3\n"]
    r = subj(url).complete("x", lambda t: False)
    assert r.ok and r.killed and r.text == "line1\n"
    fake.delay = 0.6
    r = VllmSubject(MODEL, url=url).complete("x", timeout=0.5)
    assert not r.ok and r.timed_out and r.error == "timeout" and infra_kind("timeout") == "transient"


def test_calls_are_serialised(env):
    fake, url = env
    fake.delay = 0.2
    s = subj(url)
    out = []
    ts = [threading.Thread(target=lambda: out.append(s.complete("x"))) for _ in range(2)]
    t0 = time.perf_counter()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert all(r.ok for r in out) and time.perf_counter() - t0 >= 0.75
    assert all(r.wall_s < 0.7 for r in out)


def test_a_real_cell_runs_through_the_runner(env, tmp_path):
    from bca.liveeval.runner import run_cell
    from bca.liveeval.tasks import TASKS

    fake, url = env
    task = next(t for t in TASKS if t.name == "sum_to")
    fake.chunks = ["sum_to:\n", "    xor eax, eax\n", "top: test rdi, rdi\n", "    jle done\n", "    add rax, rdi\n",
                   "    dec rdi\n", "    jmp top\n", "done: ret\n"]
    s = subj(url)
    rec = run_cell(s, task, "stream", "x86_64", tmp_path, max_attempts=1, meta=s.describe())
    assert rec["attempts"] == 1 and rec["pass_at_1"] and rec.get("vendor") == "vllm"
