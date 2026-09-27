"""Simulate an LLM streaming x86-64 assembly: encode live, abort on a bad line, repair, run.

Every committed instruction is encoded by keystone, checked in-process, and re-assembled by
GNU as (the independent reference); the error for the typo below quotes GNU as's
diagnostic. Needs binutils for x86-64 (x86_64-linux-gnu-as).

Run with: .venv/bin/python -m examples.stream_demo
"""

import random

from bca.isa import X86_64
from bca.stream import LiveRunner, StreamAssembler, trace_program

FIRST_ATTEMPT = """popcount:
    xor eax, eax
top: test rdi, rdi
    jz done          # forward branch: provisional rel32, patched later
    mov rdx, rdi
    andd edx, 1      # typo -> error, generation should stop here
    add rax, rdx
"""
REPAIR = """    and edx, 1
    add rax, rdx
    shr rdi, 1
    jmp top
done: ret
"""


def chunks(text: str, rng: random.Random):
    i = 0
    while i < len(text):
        n = rng.randint(1, 7)
        yield text[i : i + n]
        i += n


def show(events) -> None:
    for e in events:
        if e.kind in ("insn", "patch"):
            note = f"  (pending {e.pending})" if e.pending else ""
            print(f"  {e.kind:5} {e.address:#x}  {e.data.hex():<14} {e.text}{note}")
        else:
            print(f"  {e.kind:5} {e.address:#x}  {e.message or e.text}")


def main() -> None:
    rng = random.Random(1)
    sa = StreamAssembler(X86_64)
    runner = LiveRunner(sa, [0b1011])
    for chunk in chunks(FIRST_ATTEMPT, rng):
        events = sa.feed(chunk)
        show(events)
        runner.advance()
        if sa.halted:
            print(f"-> stop generation; feed back: {sa.halted.message}")
            print(f"   (unconsumed text dropped: {sa.pending_text!r})")
            break
    print(f"-> resume at {sa.retract():#x}")
    for chunk in chunks(REPAIR, rng):
        show(sa.feed(chunk))
        runner.advance()
    prog = sa.finish()
    print(f"ok={prog.ok} bytes={len(prog.code)} live result={runner.ret}")
    ret, steps = trace_program(prog, [0b1011])
    print(f"traced result={ret}; first steps:")
    for step in steps[:6]:
        print(f"  {step.pc:#x} {step.text:<16} {step.changed}")


if __name__ == "__main__":
    main()
