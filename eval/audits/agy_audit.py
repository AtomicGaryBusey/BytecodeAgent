"""Audit an Antigravity CLI (`agy`) version the adapter has not seen: one real tool-less call per settings variant
through the adapter's own code path (version pin lifted for this process only), keeping what agy recorded.
Writes <audit>/raw/<variant>.stream.jsonl (agy's stream-json), <variant>.request.json (the decoded model request agy
recorded in its conversation database) and <audit>/summary.json (committed). No credentials are read or copied: agy
authenticates through the keyring, and the private home is deleted by the adapter after the call.
usage: .venv/bin/python -m eval.audits.agy_audit eval/audits/<audit> <agy model> [variant=json-settings ...]"""

import json
import sys
from pathlib import Path

from bca.liveeval import vendor_agy as V

PROMPT = "Reply with exactly: PONG"


def run(audit: Path, model: str, name: str, settings: dict) -> dict:
    raw = audit / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    seen = {}

    class Audit(V.AgySubject):
        audited_versions = ()  # this process only: the point is to look at an unaudited version

        def verify_request(self, p):
            home = self._call.home
            dbs = sorted((home / ".gemini" / "antigravity-cli" / "conversations").glob("*.db"))
            seen["databases"] = len(dbs)
            if len(dbs) == 1:
                seen["requests"] = V.read_requests(dbs[0])
            seen["settings_after"] = json.loads((home / ".gemini" / "antigravity-cli" / "settings.json").read_text())
            super().verify_request(p)

    old = V.AGY_SETTINGS
    V.AGY_SETTINGS = settings
    try:
        s = Audit(model, timeout=180)
        r = s.complete(PROMPT, raw_path=str(raw / f"{name}.stream.jsonl"))
    finally:
        V.AGY_SETTINGS = old
    stream = raw / f"{name}.stream.jsonl"
    events = [json.loads(ln) for ln in stream.read_text().splitlines() if ln.startswith("{")] if stream.exists() else []
    reqs = seen.get("requests") or []
    (raw / f"{name}.request.json").write_text(json.dumps(reqs, indent=1))
    return {
        "variant": name, "settings_written": settings, "settings_after": seen.get("settings_after"),
        "cli_version": s.cli_version, "ok": r.ok, "error": r.error, "answer": r.text, "notes": r.notes,
        "event_types": sorted({str(e.get("event")) for e in events}),
        "step_types": sorted({str((e.get("step_update") or {}).get("step_type")) for e in events
                              if e.get("event") == "step_update"}),
        "init_keys": sorted(next((e.get("init") or {} for e in events if e.get("event") == "init"), {})),
        "init_tools_advertised": len(next(((e.get("init") or {}).get("tools") or [] for e in events
                                           if e.get("event") == "init"), [])),
        "result_keys": sorted(next((e.get("result") or {} for e in events if e.get("event") == "result"), {})),
        "conversation_databases": seen.get("databases"),
        "requests": [{"tools": q["tools"], "model_field_19": q["model"],
                      "system_is_ours": q["system"] == V.AGY_SYSTEM, "system_chars": len(q["system"]),
                      "messages": [{"role": role, "chars": len(text),
                                    "user_turn_matches_wrapper": bool(V.agy_user_re(PROMPT).fullmatch(text))
                                    if role == 1 else None} for role, text in q["messages"]]} for q in reqs],
    }


if __name__ == "__main__":
    audit, model = Path(sys.argv[1]), sys.argv[2]
    variants = dict(a.split("=", 1) for a in sys.argv[3:]) or {"adapter": json.dumps(V.AGY_SETTINGS)}
    out = {"audit": audit.name, "model": model, "prompt": PROMPT,
           "variants": [run(audit, model, n, json.loads(js)) for n, js in variants.items()]}
    (audit / "summary.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))
