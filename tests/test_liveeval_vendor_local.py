"""local:<model> (bca.liveeval.vendor_local) against a fake Ollama server: the request we send, the checks
we make on what served it, reasoning handling, early abort, timeouts and error classification."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bca.liveeval.runner import infra_kind
from bca.liveeval.subject import SYSTEM_PROMPT
from bca.liveeval.vendor_local import LocalSubject, _ThinkFilter
from bca.liveeval.vendors import make_subject, split_spec

MODEL = "qwen3:30b-a3b-instruct-2507-q8_0"
DIGEST = "528dfe43328ba6235a38e89f6e8ead082a70eda6b45bcae9bbdeba0f38ac3f9b"


class Fake:
    def __init__(self):
        self.version = "0.30.10"
        self.chunks = ["mov rax, 1\n", "ret\n"]
        self.served = MODEL
        self.digest_after = DIGEST
        self.ctx_after = 65536
        self.loaded = False
        self.requests = []
        self.warmups = 0
        self.delay = 0.0
        self.final = {"prompt_eval_count": 300, "eval_count": 10}
        self.thinking = None


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
            if self.path == "/api/version":
                return self._json({"version": fake.version})
            if self.path == "/api/tags":
                return self._json({"models": [{"name": MODEL, "digest": DIGEST, "details": {
                    "quantization_level": "Q8_0", "parameter_size": "30.5B", "family": "qwen3moe"}}]})
            if self.path == "/api/ps":
                ms = [{"name": MODEL, "digest": fake.digest_after, "context_length": fake.ctx_after}] if fake.loaded else []
                return self._json({"models": ms})
            self._json({"error": "nope"}, 404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            if self.path == "/api/show":
                if body["model"] != MODEL:
                    return self._json({"error": f"model '{body['model']}' not found"}, 404)
                return self._json({"model_info": {"qwen3moe.context_length": 262144},
                                   "parameters": "temperature 0.7\ntop_p 0.8"})
            if self.path == "/api/generate":
                fake.warmups += 1
                fake.loaded = True
                return self._json({"done": True})
            if self.path == "/api/chat":
                fake.requests.append(body)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                try:
                    if fake.thinking:
                        self.wfile.write((json.dumps({"model": fake.served, "message": {
                            "role": "assistant", "content": "", "thinking": fake.thinking}}) + "\n").encode())
                    for c in fake.chunks:
                        time.sleep(fake.delay)
                        self.wfile.write((json.dumps({"model": fake.served, "message": {
                            "role": "assistant", "content": c}, "done": False}) + "\n").encode())
                        self.wfile.flush()
                    self.wfile.write((json.dumps({"model": fake.served, "done": True, "done_reason": "stop",
                                                  **fake.final}) + "\n").encode())
                except OSError:
                    pass
                return None
            self._json({"error": "nope"}, 404)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = Fake()
    srv = serve(fake)
    yield fake, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def subj(url, **kw):
    return LocalSubject(MODEL, url=url, timeout=30, **kw)


def test_spec_and_registry(env):
    assert split_spec("local:qwen2.5:14b") == ("local", "qwen2.5:14b")
    with pytest.raises(ValueError, match="no effort"):
        make_subject("local:qwen2.5:14b", timeout=10, effort="high")


def test_request_is_ours_and_answer_streams(env, tmp_path):
    fake, url = env
    s = subj(url)
    seen = []
    raw = tmp_path / "raw.jsonl"
    r = s.complete("PROMPT", lambda t: seen.append(t) or True, raw_path=str(raw))
    assert r.ok and r.text == "mov rax, 1\nret\n" and seen == ["mov rax, 1\n", "ret\n"]
    body = fake.requests[0]
    assert body["messages"] == [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "PROMPT"}]
    assert body["stream"] is True and "tools" not in body and "format" not in body
    assert body["options"] == {"num_ctx": 65536}  # min(native 262144, LOCAL_MAX_CTX); sampling left to the model
    assert r.model == MODEL and r.model_source == "reported" and r.usage["eval_count"] == 10
    assert fake.warmups == 1 and raw.read_text().count("\n") == 3
    d = s.describe()
    assert d["effort"] == "model-default" and d["cli_version"] == "ollama 0.30.10"
    assert d["local"]["digest"] == DIGEST and "temperature 0.7" in d["local"]["sampling"]
    s.complete("again")
    assert fake.warmups == 1  # resident with our context: no reload


def test_warm_up_happens_before_the_clock(env):
    fake, url = env
    r = subj(url).complete("x")
    assert r.ok and fake.warmups == 1 and fake.loaded


@pytest.mark.parametrize("change,msg", [
    (lambda f: setattr(f, "served", "qwen2.5:14b"), "model rerouted"),
    (lambda f: setattr(f, "digest_after", "0" * 64), "digest changed"),
    (lambda f: setattr(f, "ctx_after", 4096), "context 4096"),
    (lambda f: setattr(f, "final", {"prompt_eval_count": 65000, "eval_count": 600}), "context window"),
])
def test_fails_closed_on_what_served_the_call(env, change, msg):
    fake, url = env
    change(fake)
    r = subj(url).complete("x")
    assert not r.ok and r.error.startswith("isolation:") and msg in r.error
    assert infra_kind(r.error) == "fatal"


def test_unaudited_server_version_is_fatal(env):
    fake, url = env
    fake.version = "0.31.0"
    r = subj(url).complete("x")
    assert not r.ok and "not an audited version" in r.error and infra_kind(r.error) == "fatal"


def test_unknown_model_and_dead_server_are_fatal(env):
    fake, url = env
    r = LocalSubject("nope:1b", url=url).complete("x")
    assert "not found" in r.error and infra_kind(r.error) == "fatal"
    r = LocalSubject(MODEL, url="http://127.0.0.1:9").complete("x")
    assert "local server unreachable" in r.error and infra_kind(r.error) == "fatal"


def test_early_abort_stops_reading(env):
    fake, url = env
    fake.chunks = ["line1\n", "line2\n", "line3\n"]
    r = subj(url).complete("x", lambda t: False)
    assert r.ok and r.killed and r.text == "line1\n"


def test_timeout(env):
    fake, url = env
    fake.delay = 0.6
    r = LocalSubject(MODEL, url=url).complete("x", timeout=0.5)
    assert not r.ok and r.timed_out and r.error == "timeout" and infra_kind("timeout") == "transient"


def test_reasoning_is_recorded_not_scored(env):
    fake, url = env
    fake.thinking = "let me think"
    fake.chunks = ["<think>more", " thought</think>\n", "ret\n"]
    r = subj(url).complete("x")
    assert r.ok and r.text == "ret\n"
    assert any("reasoning not scored" in n and "let me think" in n and "more thought" in n for n in r.notes)


def test_think_filter_passes_plain_text_through():
    f = _ThinkFilter()
    assert f.feed("<th") == "" and f.feed("i") == "" and f.feed("x") == "<thix" and f.feed("y") == "y"
    f = _ThinkFilter()
    assert f.feed("<think>a</think>b") == "b" and f.thinking == "a"
    f = _ThinkFilter()
    assert f.feed("<think>never closed") == "" and f.flush() == "" and f.thinking == "never closed"


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
    assert all(r.wall_s < 0.7 for r in out)  # the clock starts once the call holds the server


def test_parity_registration_and_a_real_cell(env, tmp_path):
    # registered like the vendor adapters, and a whole round-1 stream cell runs through the runner
    from bca.liveeval.runner import run_cell
    from bca.liveeval.tasks import TASKS
    from bca.liveeval.vendors import VENDORS, all_efforts

    fake, url = env
    assert VENDORS["local"] is LocalSubject and "model-default" not in all_efforts()
    task = next(t for t in TASKS if t.name == "sum_to")
    fake.chunks = ["sum_to:\n", "    xor eax, eax\n", "top: test rdi, rdi\n", "    jle done\n", "    add rax, rdi\n",
                   "    dec rdi\n", "    jmp top\n", "done: ret\n"]
    s = subj(url)
    rec = run_cell(s, task, "stream", "x86_64", tmp_path, max_attempts=1, meta=s.describe())
    assert rec["attempts"] == 1 and rec["pass_at_1"] and rec.get("vendor") == "local" and rec["local"]["digest"] == DIGEST
    assert fake.requests and fake.requests[0]["messages"][0]["content"] == SYSTEM_PROMPT
