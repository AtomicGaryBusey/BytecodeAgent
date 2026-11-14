"""vendor_muse against a fake `muse` binary that speaks Muse Code 1.4.15's `exec --json` event log: the private config
home and the appended system prompt, the streamed answer, and every fail-closed check."""

import stat

import pytest

from bca.liveeval.vendor_muse import MUSE_FLAGS, MuseSubject

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
if a == ["--version"]:  # the real launcher self-updates on any invocation, the version probe included (11-13)
    if os.environ.get("MUSE_NO_AUTO_UPDATE") != "1":
        print("muse: updating to 1.4.20", file=sys.stderr); sys.exit(1)
    print("Muse Code 1.4.15 (1.4.15-R8339.1)"); sys.exit(0)
arg = lambda k: a[a.index(k) + 1]
mode = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mode")).read().strip()
assert os.path.isfile(os.path.join(os.environ["XDG_CONFIG_HOME"], "muse", "auth.json"))
assert "expert low-level programmer" in open(os.environ["TBH_EVAL_APPEND_SYSTEM_PROMPT_FILE"]).read()
assert os.environ.get("MUSE_NO_AUTO_UPDATE") == "1"
prompt = open(arg("--prompt-file")).read()
def emit(pt, payload): print(json.dumps({"schema_version": 1, "payload_type": pt, "payload": payload}), flush=True)
emit("run.model.configured", {"model_id": arg("--model") if mode != "reroute" else "muse-spark-1.2"})
emit("turn.input.user", {"prompt": prompt if mode != "badprompt" else "x"})
emit("task.lifecycle.proposed", {"event": {"task_kind": "model.meta.response" if mode != "tool" else "tool.shell"}})
if mode in ("resample", "otherfail"):
    emit("run.output.delta", {"text": "mov rax, 1\n.Ldone:"})
    emit("task.lifecycle.failed", {"event": {"reason": "stream guard resample (foreign-script) [foreign-script-guard]"
                                             if mode == "resample" else "provider error"}})
    if mode == "otherfail":
        emit("run.terminal.completed", {"terminal": "completed", "text": ""}); sys.exit(0)
    emit("task.lifecycle.proposed", {"event": {"task_kind": "model.meta.response"}})
for t in ("mov rax, 1\n", "ret"):
    emit("run.output.delta", {"text": t})
emit("task.lifecycle.completed", {"event": {"kind": "completed"}})
emit("run.usage", {"calls": 1, "models": [{"input_tokens": 18000, "output_tokens": 40, "cached_tokens": 0}]})
emit("run.terminal.completed", {"terminal": "completed", "text": "mov rax, 1\nret"})
if mode == "fail":
    print("muse: error: provider refused the request", file=sys.stderr); sys.exit(2)
'''


@pytest.fixture
def fake(tmp_path):
    exe = tmp_path / "muse"
    exe.write_text(FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    real = tmp_path / "realconfig"
    (real / "muse").mkdir(parents=True)
    (real / "muse" / "auth.json").write_text('{"schema_version": 1, "providers": {}}')

    def make(mode="plain", model="muse-spark-1.3-contributor", **kw):
        (tmp_path / "mode").write_text(mode)
        s = MuseSubject(model, binary=str(exe), timeout=30, **kw)
        s.home_real = real
        return s
    return make


def test_answer_usage_model_and_flags(fake):
    s = fake()
    r = s.complete("write sum_to")
    assert r.ok and r.text == "mov rax, 1\nret" and r.model == "muse-spark-1.3-contributor"
    assert r.model_source == "reported" and r.usage["input_tokens"] == 18000
    argv = s.argv("p", __import__("pathlib").Path(s.home_real))
    assert all(f in argv for f in MUSE_FLAGS) and "--reasoning-effort" not in argv


@pytest.mark.parametrize("mode,needle", [("tool", "non-model task"), ("reroute", "instead of"),
                                         ("badprompt", "not the task prompt")])
def test_fail_closed(fake, mode, needle):
    r = fake(mode).complete("p")
    assert not r.ok and r.error.startswith("isolation:") and needle in r.error


def test_a_failed_run_is_an_error_not_an_answer(fake):
    r = fake("fail").complete("p")
    assert not r.ok and "provider refused" in r.error


def test_effort_is_passed_only_when_asked(fake, tmp_path):
    s = fake(effort="high")
    assert s.argv("p", tmp_path)[-2:] == ["--reasoning-effort", "high"]


def test_a_guard_resample_drops_the_discarded_sample(fake):
    seen = []
    r = fake("resample").complete("p", on_text=lambda t: seen.append(t))
    assert r.ok and r.text == "mov rax, 1\nret" and seen == ["mov rax, 1\nret"]
    assert r.usage["guard_resamples"] == 1 and any("resampled" in n for n in r.notes)


def test_any_other_failed_model_task_is_an_error(fake):
    r = fake("otherfail").complete("p")
    assert not r.ok and "provider error" in r.error
