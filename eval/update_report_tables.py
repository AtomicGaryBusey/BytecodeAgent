"""Replace the generated tables inside a report with fresh generator output, leaving the prose alone.
A table is matched by its header's first two columns; the k-th such table in the report takes the k-th such table
in the generator output. Tables the generator does not print are left untouched.
usage: .venv/bin/python eval/update_report_tables.py <report.md> <generator-output.md>"""
import collections
import sys


def tables(lines):
    """[(start, end, key)] for each run of lines starting with '|'."""
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith("|"):
            j = i
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            key = tuple(c.strip() for c in lines[i].split("|")[1:3])
            out.append((i, j, key))
            i = j
        else:
            i += 1
    return out


report, gen = sys.argv[1], sys.argv[2]
rl, gl = open(report).read().split("\n"), open(gen).read().split("\n")
src = collections.defaultdict(list)
for s, e, k in tables(gl):
    src[k].append(gl[s:e])
seen, n = collections.Counter(), 0
for s, e, k in reversed(tables(rl)):  # back to front, so earlier indices stay valid
    pass
plan = []
for s, e, k in tables(rl):
    if seen[k] < len(src[k]):
        plan.append((s, e, src[k][seen[k]]))
    seen[k] += 1
for s, e, new in reversed(plan):
    if rl[s:e] != new:
        n += 1
        print(f"  table at line {s + 1}: {e - s - 2} rows -> {len(new) - 2} rows")
    rl[s:e] = new
open(report, "w").write("\n".join(rl))
print(f"{report}: {n} of {len(plan)} generated tables changed")
