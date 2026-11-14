"""Write <audit>/summary.json from the captures in <audit>/raw/ (recorder.py's reqNNN.json plus the CLI's own event
log out.jsonl). The summary is what an adapter's audit claims rest on and is committed; raw/ is not (it holds the
vendor's own system prompt). usage: .venv/bin/python -m eval.audits.summarize eval/audits/<audit>"""

import hashlib
import json
import sys
from pathlib import Path

from bca.liveeval.subject import SYSTEM_PROMPT


def summarize(audit: Path) -> dict:
    raw = audit / "raw"
    reqs = [json.loads(p.read_text()) for p in sorted(raw.glob("req*.json"))]
    out = []
    for r in reqs:
        b = r["body"] if isinstance(r["body"], dict) else {}
        ins = b.get("instructions") or ""
        item = {"method": r["method"], "path": r["path"], "body_keys": sorted(b)}
        if r["method"] == "POST":
            item.update({
                "model": b.get("model"),
                "tools_field_present": "tools" in b,
                "tool_count": len(b.get("tools") or []),
                "reasoning": b.get("reasoning"),
                "max_output_tokens": b.get("max_output_tokens"),
                "instructions_chars": len(ins),
                "instructions_sha256": hashlib.sha256(ins.encode()).hexdigest(),
                "instructions_sections": [ln[2:] for ln in ins.splitlines() if ln.startswith("# ")],
                "our_system_prompt_is_tail": ins.rstrip().endswith(SYSTEM_PROMPT.strip()),
                "input": [{"role": i.get("role") or i.get("type"), "chars": len(json.dumps(i.get("content")))}
                          for i in b.get("input") or []],
            })
        out.append(item)
    events = raw / "out.jsonl"
    types = sorted({json.loads(ln).get("payload_type") for ln in events.read_text().splitlines()}) if events.exists() else []
    return {"audit": audit.name, "requests": out, "event_types": types}


if __name__ == "__main__":
    a = Path(sys.argv[1])
    s = summarize(a)
    (a / "summary.json").write_text(json.dumps(s, indent=1) + "\n")
    print(json.dumps(s, indent=1)[:1500])
