#!/usr/bin/env python3
"""Decode-speed benchmark for the Flash-Next endpoint. Streams so TTFT and decode
rate are measured separately. Fixed prompts, temperature 0, thinking off, so
MTP-on vs MTP-off is a like-for-like comparison. Usage: decode-bench.py LABEL"""
import json, sys, time, urllib.request, concurrent.futures as cf
URL = __import__("os").environ.get("BENCH_URL", "http://127.0.0.1:8110/v1/chat/completions")  # the vLLM listener
PROMPTS = [
  "Write a Python class implementing an LRU cache with get and put in O(1). Include docstrings.",
  "Write a C function that parses an IPv4 dotted-quad string into a uint32_t, with error handling.",
  "Explain how TCP congestion control works (slow start, congestion avoidance, fast retransmit) in about 400 words.",
  "Write a bash script that finds the 10 largest files under a directory and prints them human-readably.",
]
def run(prompt, max_tokens=512):
    body = {"model": "gym-eval", "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
            "temperature": 0, "stream": True, "stream_options": {"include_usage": True},
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    t0 = time.time(); ttft = None; ntok = None
    with urllib.request.urlopen(req, timeout=900) as r:
        for line in r:
            line = line.strip()
            if not line.startswith(b"data: ") or line == b"data: [DONE]": continue
            d = json.loads(line[6:])
            if d.get("choices") and d["choices"][0]["delta"].get("content") and ttft is None: ttft = time.time() - t0
            if d.get("usage"): ntok = d["usage"]["completion_tokens"]
    total = time.time() - t0
    return ttft, ntok, ntok / (total - ttft)
label = sys.argv[1] if len(sys.argv) > 1 else "run"
run("Say hi.", 8)  # warm-up
single = [run(p) for p in PROMPTS]
for (ttft, n, r), p in zip(single, PROMPTS):
    print(f"[{label}] single  ttft={ttft:.2f}s tokens={n} decode={r:.1f} tok/s  :: {p[:40]}")
avg = sum(x[2] for x in single) / len(single)
with cf.ThreadPoolExecutor(2) as ex:
    t0 = time.time(); res = list(ex.map(run, PROMPTS)); wall = time.time() - t0
agg = sum(x[1] for x in res) / wall
print(f"[{label}] SUMMARY single-stream mean decode {avg:.1f} tok/s | concurrency-2 aggregate {agg:.1f} tok/s, per-stream {sum(x[2] for x in res)/len(res):.1f} tok/s")
