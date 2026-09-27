"""Canned solutions for the round-2 tasks (fake-subject tests; no model involved).

CORRECT[path][isa][task] is a correct answer for every feasible cell, written by hand. The
structured answers are converted from the stream (assembly) answers by `to_structured`, so
each stream solution below sticks to instructions both structured encoders support (except
dot_i32_simd, which is stream-only). QBE answers are ISA-independent.

WRONG lists deliberately broken answers, each of which must trigger one safety-net category.
"""

import re

# -- x86-64 (GNU as, Intel syntax, noprefix) ---------------------------------------------------

X86 = {
    "fib_rec": """fib_rec:
    cmp rdi, 2
    jl .Lbase
    push rbx
    push r12
    sub rsp, 8
    mov rbx, rdi
    lea rdi, [rbx - 1]
    call fib_rec
    mov r12, rax
    lea rdi, [rbx - 2]
    call fib_rec
    add rax, r12
    add rsp, 8
    pop r12
    pop rbx
    ret
.Lbase:
    mov rax, rdi
    ret
""",
    "map_sum": """map_sum:
    push rbx
    push r12
    push r13
    push r14
    push r15
    mov rbx, rdi
    mov r12, rsi
    mov r13, rdx
    xor r14d, r14d
    xor r15d, r15d
.Lloop:
    cmp r14, r12
    jge .Ldone
    mov rdi, qword ptr [rbx + r14*8]
    call r13
    add r15, rax
    inc r14
    jmp .Lloop
.Ldone:
    mov rax, r15
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbx
    ret
""",
    "sum8": """sum8:
    mov rax, rdi
    lea r10, [rsi + rsi]
    sub rax, r10
    lea r10, [rdx + rdx*2]
    add rax, r10
    lea r10, [rcx*4]
    sub rax, r10
    lea r10, [r8 + r8*4]
    add rax, r10
    imul r10, r9, 6
    sub rax, r10
    imul r10, qword ptr [rsp + 8], 7
    add rax, r10
    mov r10, qword ptr [rsp + 16]
    shl r10, 3
    sub rax, r10
    ret
""",
    "dot_i32_simd": """dot_i32_simd:
    pxor xmm0, xmm0
    xor ecx, ecx
    mov r8, rdx
    and r8, -2
.Lvec:
    cmp rcx, r8
    jge .Ltail
    pmovsxdq xmm1, qword ptr [rdi + rcx*4]
    pmovsxdq xmm2, qword ptr [rsi + rcx*4]
    pmuldq xmm1, xmm2
    paddq xmm0, xmm1
    add rcx, 2
    jmp .Lvec
.Ltail:
    pshufd xmm1, xmm0, 0xee
    paddq xmm0, xmm1
    movq rax, xmm0
    cmp rcx, rdx
    jge .Ldone
    movsxd r9, dword ptr [rsi + rcx*4]
    movsxd r10, dword ptr [rdi + rcx*4]
    imul r9, r10
    add rax, r9
.Ldone:
    ret
""",
    "binary_search": """binary_search:
    xor eax, eax
    lea rcx, [rsi - 1]
.Lloop:
    cmp rax, rcx
    jg .Lnf
    mov r8, rcx
    sub r8, rax
    shr r8, 1
    add r8, rax
    mov r9, qword ptr [rdi + r8*8]
    cmp r9, rdx
    je .Lfound
    jg .Lhigh
    lea rax, [r8 + 1]
    jmp .Lloop
.Lhigh:
    lea rcx, [r8 - 1]
    jmp .Lloop
.Lfound:
    mov rax, r8
    ret
.Lnf:
    mov rax, -1
    ret
""",
    "memmove_i64": """memmove_i64:
    cmp rdi, rsi
    jae .Lback
    xor ecx, ecx
.Lfwd:
    cmp rcx, rdx
    jge .Ldone
    mov rax, qword ptr [rsi + rcx*8]
    mov qword ptr [rdi + rcx*8], rax
    inc rcx
    jmp .Lfwd
.Lback:
    mov rcx, rdx
.Lbl:
    dec rcx
    js .Ldone
    mov rax, qword ptr [rsi + rcx*8]
    mov qword ptr [rdi + rcx*8], rax
    jmp .Lbl
.Ldone:
    xor eax, eax
    ret
""",
    "crc32": """crc32:
    mov eax, -1
    xor ecx, ecx
.Lbyte:
    cmp rcx, rsi
    jge .Lend
    movzx edx, byte ptr [rdi + rcx]
    xor eax, edx
    mov r8d, 8
.Lbit:
    mov edx, eax
    and edx, 1
    neg edx
    and edx, -306674912
    shr eax, 1
    xor eax, edx
    dec r8d
    jnz .Lbit
    inc rcx
    jmp .Lbyte
.Lend:
    not eax
    ret
""",
    "mulhi_u64": """mulhi_u64:
    mov rax, rdi
    mul rsi
    mov rax, rdx
    ret
""",
    "add_overflow": """add_overflow:
    xor eax, eax
    add rdi, rsi
    seto al
    mov qword ptr [rdx], rdi
    ret
""",
    "isqrt": """isqrt:
    mov ecx, -1
    mov rdx, rcx
    imul rdx, rcx
    cmp rdx, rdi
    jbe .Lhi
    xor eax, eax
.Lloop:
    cmp rax, rcx
    jae .Ldone
    mov r8, rcx
    sub r8, rax
    add r8, 1
    shr r8, 1
    add r8, rax
    mov r9, r8
    imul r9, r8
    cmp r9, rdi
    ja .Lless
    mov rax, r8
    jmp .Lloop
.Lless:
    lea rcx, [r8 - 1]
    jmp .Lloop
.Lhi:
    mov rax, rcx
.Ldone:
    ret
""",
    "collatz_max": """collatz_max:
    xor eax, eax
    mov ecx, 1
.Lk:
    cmp rcx, rdi
    jg .Ldone
    mov rdx, rcx
    xor r8d, r8d
.Lstep:
    cmp rdx, 1
    je .Lend
    test dl, 1
    jnz .Lodd
    shr rdx, 1
    jmp .Lcnt
.Lodd:
    lea rdx, [rdx + rdx*2 + 1]
.Lcnt:
    inc r8
    jmp .Lstep
.Lend:
    cmp r8, rax
    cmovg rax, r8
    inc rcx
    jmp .Lk
.Ldone:
    ret
""",
}

# A 4-wide variant with 16-byte unaligned loads (also correct); WRONG uses movdqa instead.
X86_DOT4 = """dot_i32_simd:
    pxor xmm0, xmm0
    xor ecx, ecx
    mov r8, rdx
    and r8, -4
.Lvec:
    cmp rcx, r8
    jge .Lsum
    movdqu xmm1, xmmword ptr [rdi + rcx*4]
    movdqu xmm2, xmmword ptr [rsi + rcx*4]
    pmovsxdq xmm3, xmm1
    pmovsxdq xmm4, xmm2
    pmuldq xmm3, xmm4
    paddq xmm0, xmm3
    pshufd xmm1, xmm1, 0xee
    pshufd xmm2, xmm2, 0xee
    pmovsxdq xmm3, xmm1
    pmovsxdq xmm4, xmm2
    pmuldq xmm3, xmm4
    paddq xmm0, xmm3
    add rcx, 4
    jmp .Lvec
.Lsum:
    pshufd xmm1, xmm0, 0xee
    paddq xmm0, xmm1
    movq rax, xmm0
.Ltail:
    cmp rcx, rdx
    jge .Ldone
    movsxd r9, dword ptr [rsi + rcx*4]
    movsxd r10, dword ptr [rdi + rcx*4]
    imul r9, r10
    add rax, r9
    inc rcx
    jmp .Ltail
.Ldone:
    ret
"""

# -- AArch64 ---------------------------------------------------------------------------------

A64 = {
    "fib_rec": """fib_rec:
    cmp x0, #2
    b.lt .Lbase
    stp x29, x30, [sp, #-32]!
    mov x29, sp
    stp x19, x20, [sp, #16]
    mov x19, x0
    sub x0, x19, #1
    bl fib_rec
    mov x20, x0
    sub x0, x19, #2
    bl fib_rec
    add x0, x0, x20
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #32
.Lbase:
    ret
""",
    "map_sum": """map_sum:
    stp x29, x30, [sp, #-48]!
    mov x29, sp
    stp x19, x20, [sp, #16]
    stp x21, x22, [sp, #32]
    mov x19, x0
    mov x20, x1
    mov x21, x2
    mov x22, #0
.Lloop:
    cbz x20, .Ldone
    ldr x0, [x19], #8
    blr x21
    add x22, x22, x0
    sub x20, x20, #1
    b .Lloop
.Ldone:
    mov x0, x22
    ldp x21, x22, [sp, #32]
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #48
    ret
""",
    "sum8": """sum8:
    sub x0, x0, x1, lsl #1
    add x8, x2, x2, lsl #1
    add x0, x0, x8
    sub x0, x0, x3, lsl #2
    add x8, x4, x4, lsl #2
    add x0, x0, x8
    mov x8, #6
    msub x0, x5, x8, x0
    mov x8, #7
    madd x0, x6, x8, x0
    sub x0, x0, x7, lsl #3
    ret
""",
    "dot_i32_simd": """dot_i32_simd:
    movi v0.2d, #0
    movi v1.2d, #0
    mov x3, #0
    and x4, x2, #-4
.Lvec:
    cmp x3, x4
    b.ge .Ltail
    lsl x5, x3, #2
    ldr q2, [x0, x5]
    ldr q3, [x1, x5]
    smlal v0.2d, v2.2s, v3.2s
    smlal2 v1.2d, v2.4s, v3.4s
    add x3, x3, #4
    b .Lvec
.Ltail:
    add v0.2d, v0.2d, v1.2d
    addp d0, v0.2d
    fmov x6, d0
.Lt:
    cmp x3, x2
    b.ge .Ldone
    ldrsw x7, [x0, x3, lsl #2]
    ldrsw x8, [x1, x3, lsl #2]
    madd x6, x7, x8, x6
    add x3, x3, #1
    b .Lt
.Ldone:
    mov x0, x6
    ret
""",
    "binary_search": """binary_search:
    mov x3, #0
    sub x4, x1, #1
.Lloop:
    cmp x3, x4
    b.gt .Lnf
    sub x5, x4, x3
    add x5, x3, x5, lsr #1
    ldr x6, [x0, x5, lsl #3]
    cmp x6, x2
    b.eq .Lfound
    b.gt .Lhigh
    add x3, x5, #1
    b .Lloop
.Lhigh:
    sub x4, x5, #1
    b .Lloop
.Lfound:
    mov x0, x5
    ret
.Lnf:
    mov x0, #-1
    ret
""",
    "memmove_i64": """memmove_i64:
    cmp x0, x1
    b.hs .Lback
    mov x3, #0
.Lfwd:
    cmp x3, x2
    b.ge .Ldone
    ldr x4, [x1, x3, lsl #3]
    str x4, [x0, x3, lsl #3]
    add x3, x3, #1
    b .Lfwd
.Lback:
    subs x2, x2, #1
    b.lt .Ldone
    ldr x4, [x1, x2, lsl #3]
    str x4, [x0, x2, lsl #3]
    b .Lback
.Ldone:
    mov x0, #0
    ret
""",
    "crc32": """crc32:
    mov w2, #-1
    movz w5, #0x8320
    movk w5, #0xedb8, lsl #16
    mov x3, #0
.Lbyte:
    cmp x3, x1
    b.ge .Lend
    ldrb w4, [x0, x3]
    eor w2, w2, w4
    mov w6, #8
.Lbit:
    and w7, w2, #1
    neg w7, w7
    and w7, w7, w5
    eor w2, w7, w2, lsr #1
    subs w6, w6, #1
    b.ne .Lbit
    add x3, x3, #1
    b .Lbyte
.Lend:
    mvn w0, w2
    ret
""",
    "mulhi_u64": """mulhi_u64:
    umulh x0, x0, x1
    ret
""",
    "add_overflow": """add_overflow:
    adds x3, x0, x1
    cset x0, vs
    str x3, [x2]
    ret
""",
    "isqrt": """isqrt:
    mov x1, #0xffffffff
    mul x2, x1, x1
    cmp x2, x0
    b.ls .Lhi
    mov x3, #0
.Lloop:
    cmp x3, x1
    b.hs .Ldone
    sub x4, x1, x3
    add x4, x4, #1
    add x4, x3, x4, lsr #1
    mul x5, x4, x4
    cmp x5, x0
    b.hi .Lless
    mov x3, x4
    b .Lloop
.Lless:
    sub x1, x4, #1
    b .Lloop
.Lhi:
    mov x0, x1
    ret
.Ldone:
    mov x0, x3
    ret
""",
    "collatz_max": """collatz_max:
    mov x1, #0
    mov x2, #1
.Lk:
    cmp x2, x0
    b.gt .Ldone
    mov x3, x2
    mov x4, #0
.Lstep:
    cmp x3, #1
    b.eq .Lend
    tbnz x3, #0, .Lodd
    lsr x3, x3, #1
    b .Lcnt
.Lodd:
    add x3, x3, x3, lsl #1
    add x3, x3, #1
.Lcnt:
    add x4, x4, #1
    b .Lstep
.Lend:
    cmp x4, x1
    csel x1, x4, x1, gt
    add x2, x2, #1
    b .Lk
.Ldone:
    mov x0, x1
    ret
""",
}

# -- QBE (both ISAs) -------------------------------------------------------------------------

QBE = {
    "fib_rec": """export function l $fib_rec(l %n) {
@start
    %c =w csltl %n, 2
    jnz %c, @base, @rec
@base
    ret %n
@rec
    %a =l sub %n, 1
    %x =l call $fib_rec(l %a)
    %b =l sub %n, 2
    %y =l call $fib_rec(l %b)
    %r =l add %x, %y
    ret %r
}
""",
    "map_sum": """export function l $map_sum(l %a, l %n, l %f) {
@start
    %s =l copy 0
    %i =l copy 0
@loop
    %c =w csltl %i, %n
    jnz %c, @body, @end
@body
    %o =l mul %i, 8
    %p =l add %a, %o
    %v =l loadl %p
    %r =l call %f(l %v)
    %s =l add %s, %r
    %i =l add %i, 1
    jmp @loop
@end
    ret %s
}
""",
    "sum8": """export function l $sum8(l %a, l %b, l %c, l %d, l %e, l %f, l %g, l %h) {
@start
    %t =l mul %b, 2
    %r =l sub %a, %t
    %t =l mul %c, 3
    %r =l add %r, %t
    %t =l mul %d, 4
    %r =l sub %r, %t
    %t =l mul %e, 5
    %r =l add %r, %t
    %t =l mul %f, 6
    %r =l sub %r, %t
    %t =l mul %g, 7
    %r =l add %r, %t
    %t =l mul %h, 8
    %r =l sub %r, %t
    ret %r
}
""",
    "binary_search": """export function l $binary_search(l %a, l %n, l %key) {
@start
    %lo =l copy 0
    %hi =l sub %n, 1
@loop
    %c =w cslel %lo, %hi
    jnz %c, @body, @nf
@body
    %d =l sub %hi, %lo
    %h =l shr %d, 1
    %mid =l add %lo, %h
    %o =l mul %mid, 8
    %p =l add %a, %o
    %v =l loadl %p
    %eq =w ceql %v, %key
    jnz %eq, @found, @cmp
@cmp
    %gt =w csgtl %v, %key
    jnz %gt, @high, @low
@high
    %hi =l sub %mid, 1
    jmp @loop
@low
    %lo =l add %mid, 1
    jmp @loop
@found
    ret %mid
@nf
    ret -1
}
""",
    "memmove_i64": """export function l $memmove_i64(l %dst, l %src, l %n) {
@start
    %lt =w cultl %dst, %src
    jnz %lt, @fwd0, @back
@fwd0
    %i =l copy 0
@fwd
    %c =w csltl %i, %n
    jnz %c, @fbody, @done
@fbody
    %o =l mul %i, 8
    %ps =l add %src, %o
    %v =l loadl %ps
    %pd =l add %dst, %o
    storel %v, %pd
    %i =l add %i, 1
    jmp @fwd
@back
    %j =l sub %n, 1
@bloop
    %c2 =w csgel %j, 0
    jnz %c2, @bbody, @done
@bbody
    %o2 =l mul %j, 8
    %ps2 =l add %src, %o2
    %v2 =l loadl %ps2
    %pd2 =l add %dst, %o2
    storel %v2, %pd2
    %j =l sub %j, 1
    jmp @bloop
@done
    ret 0
}
""",
    "crc32": """export function l $crc32(l %p, l %n) {
@start
    %crc =w copy -1
    %i =l copy 0
@loop
    %c =w csltl %i, %n
    jnz %c, @body, @end
@body
    %q =l add %p, %i
    %b =w loadub %q
    %crc =w xor %crc, %b
    %k =w copy 8
@bit
    %low =w and %crc, 1
    %m =w sub 0, %low
    %x =w and %m, 3988292384
    %s =w shr %crc, 1
    %crc =w xor %s, %x
    %k =w sub %k, 1
    jnz %k, @bit, @next
@next
    %i =l add %i, 1
    jmp @loop
@end
    %r =w xor %crc, -1
    %rl =l extuw %r
    ret %rl
}
""",
    "mulhi_u64": """export function l $mulhi_u64(l %a, l %b) {
@start
    %al =l and %a, 4294967295
    %ah =l shr %a, 32
    %bl =l and %b, 4294967295
    %bh =l shr %b, 32
    %ll =l mul %al, %bl
    %lh =l mul %al, %bh
    %hl =l mul %ah, %bl
    %hh =l mul %ah, %bh
    %t =l shr %ll, 32
    %t =l add %t, %lh
    %tl =l and %t, 4294967295
    %th =l shr %t, 32
    %u =l add %tl, %hl
    %uh =l shr %u, 32
    %r =l add %hh, %th
    %r =l add %r, %uh
    ret %r
}
""",
    "add_overflow": """export function l $add_overflow(l %a, l %b, l %out) {
@start
    %s =l add %a, %b
    storel %s, %out
    %x1 =l xor %s, %a
    %x2 =l xor %s, %b
    %x =l and %x1, %x2
    %o =l shr %x, 63
    ret %o
}
""",
    "isqrt": """export function l $isqrt(l %n) {
@start
    %hi =l copy 4294967295
    %sq =l mul %hi, %hi
    %c =w culel %sq, %n
    jnz %c, @rethi, @init
@init
    %lo =l copy 0
@loop
    %c2 =w cultl %lo, %hi
    jnz %c2, @body, @done
@body
    %d =l sub %hi, %lo
    %d =l add %d, 1
    %h =l shr %d, 1
    %mid =l add %lo, %h
    %m2 =l mul %mid, %mid
    %le =w culel %m2, %n
    jnz %le, @up, @down
@up
    %lo =l copy %mid
    jmp @loop
@down
    %hi =l sub %mid, 1
    jmp @loop
@rethi
    ret %hi
@done
    ret %lo
}
""",
    "collatz_max": """export function l $collatz_max(l %n) {
@start
    %best =l copy 0
    %k =l copy 1
@kloop
    %c =w cslel %k, %n
    jnz %c, @kbody, @done
@kbody
    %x =l copy %k
    %s =l copy 0
@step
    %one =w ceql %x, 1
    jnz %one, @kend, @stepbody
@stepbody
    %odd =l and %x, 1
    %oc =w cnel %odd, 0
    jnz %oc, @odd, @even
@odd
    %x3 =l mul %x, 3
    %x =l add %x3, 1
    jmp @cnt
@even
    %x =l shr %x, 1
@cnt
    %s =l add %s, 1
    jmp @step
@kend
    %gt =w csgtl %s, %best
    jnz %gt, @upd, @next
@upd
    %best =l copy %s
@next
    %k =l add %k, 1
    jmp @kloop
@done
    ret %best
}
""",
}


# -- assembly text -> structured JSON (only the subset used above) ---------------------------

_SIZES = {"byte": 1, "word": 2, "dword": 4, "qword": 8}


def _split_ops(text: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _x86_mem(expr: str, size) -> dict:
    mem: dict = {}
    disp = 0
    for sign, term in re.findall(r"([+-]?)\s*([^+-]+)", expr.replace(" ", "")):
        if "*" in term:
            reg, scale = term.split("*")
            mem["index"], mem["scale"] = reg, int(scale)
        elif re.fullmatch(r"-?(0x[0-9a-fA-F]+|\d+)", term):
            disp += int(sign + term, 0)
        elif "base" not in mem:
            mem["base"] = term
        else:
            mem["index"] = term
    if disp:
        mem["disp"] = disp
    if size:
        mem["size"] = size
    return {"mem": mem}


def _x86_operand(op: str):
    m = re.fullmatch(r"(byte|word|dword|qword) ptr \[(.*)\]", op)
    if m:
        return _x86_mem(m.group(2), _SIZES[m.group(1)])
    m = re.fullmatch(r"\[(.*)\]", op)
    if m:
        return _x86_mem(m.group(1), None)
    if re.fullmatch(r"-?(0x[0-9a-fA-F]+|\d+)", op):
        return {"imm": int(op, 0)}
    return op


def _x86_insn(op: str, ops: list) -> dict:
    if op in ("jmp", "call") or (op.startswith("j") and op != "jmp"):
        return {"op": op, "target": ops[0]}
    if op in ("push", "mul", "div", "idiv"):
        return {"op": op, "src": ops}
    if op in ("pop", "inc", "dec", "neg", "not") or op.startswith("set"):
        return {"op": op, "dst": ops[0]}
    if not ops:
        return {"op": op}
    return {"op": op, "dst": ops[0], "src": ops[1:]}


def _a64_imm(s: str) -> int:
    return int(s.lstrip("#"), 0)


def _a64_operands(parts: list[str]) -> list:
    out: list = []
    i = 0
    while i < len(parts):
        p = parts[i]
        m = re.fullmatch(r"\[(.*)\](!?)", p)
        if m:
            inner = [x.strip() for x in m.group(1).split(",")]
            mem: dict = {"base": inner[0]}
            if len(inner) >= 2:
                if inner[1].startswith("#"):
                    mem["disp"] = _a64_imm(inner[1])
                else:
                    mem["index"] = inner[1]
                    mem["scale"] = 1 << _a64_imm(inner[2].split()[1]) if len(inner) > 2 else 1
            if m.group(2):
                mem["writeback"] = "pre"
            elif i + 1 < len(parts) and parts[i + 1].startswith("#"):
                mem["disp"], mem["writeback"] = _a64_imm(parts[i + 1]), "post"
                i += 1
            out.append({"mem": mem})
        elif re.fullmatch(r"(lsl|lsr|asr|ror) #\S+", p):
            kind, amount = p.split()
            prev = out.pop()
            if isinstance(prev, dict) and "imm" in prev:
                out.append({**prev, kind: _a64_imm(amount)})
            else:
                out.append({"reg": prev, "shift": kind, "amount": _a64_imm(amount)})
        elif p.startswith("#"):
            out.append({"imm": _a64_imm(p)})
        else:
            out.append(p)
        i += 1
    return out


_A64_CONDS = {"eq", "ne", "cs", "hs", "cc", "lo", "mi", "pl", "vs", "vc", "hi", "ls", "ge", "lt", "gt", "le",
              "al", "nv"}


def _a64_insn(op: str, ops: list) -> dict:
    if op in ("cmp", "cmn", "tst", "str", "strb", "strh", "stp"):
        return {"op": op, "src": ops}
    if op == "ldp":
        return {"op": op, "dst": ops[:2], "src": ops[2:]}
    if op in ("b", "bl") or op.startswith("b."):
        return {"op": op, "target": ops[0]}
    if op in ("cbz", "cbnz", "tbz", "tbnz"):
        return {"op": op, "src": ops[:-1], "target": ops[-1]}
    if op in ("br", "blr", "ret"):
        return {"op": op, "src": ops} if ops else {"op": op}
    if op in ("cset", "csetm"):
        return {"op": op, "dst": ops[0], "cond": ops[1]}
    if ops and isinstance(ops[-1], str) and ops[-1] in _A64_CONDS:
        return {"op": op, "dst": ops[0], "src": ops[1:-1], "cond": ops[-1]}
    if not ops:
        return {"op": op}
    return {"op": op, "dst": ops[0], "src": ops[1:]}


def to_structured(isa: str, asm: str) -> dict:
    out = []
    for raw in asm.splitlines():
        line = raw.split("//")[0] if isa == "aarch64" else raw.split("#")[0]
        line = line.strip()
        if not line:
            continue
        if line.endswith(":"):
            out.append({"label": line[:-1]})
            continue
        op, _, rest = line.partition(" ")
        parts = _split_ops(rest)
        if isa == "x86_64":
            out.append(_x86_insn(op, [_x86_operand(p) for p in parts]))
        else:
            out.append(_a64_insn(op, _a64_operands(parts)))
    return {"instructions": out}


STREAM = {"x86_64": X86, "aarch64": A64}


def correct(path: str, isa: str, task: str) -> str:
    """The canned correct response text for a cell."""
    if path == "stream":
        return STREAM[isa][task]
    if path == "structured":
        import json

        return json.dumps(to_structured(isa, STREAM[isa][task]))
    if path == "ir":
        return QBE[task]
    raise ValueError(path)


# -- deliberately wrong answers: (task, path, isa, text, expected category, message fragment) --

WRONG = [
    # rsp is 8 mod 16 at the call: an extra 8-byte adjustment after the five pushes.
    ("map_sum", "stream", "x86_64", X86["map_sum"].replace("    mov rbx, rdi\n", "    sub rsp, 8\n    mov rbx, rdi\n")
     .replace(".Ldone:\n", ".Ldone:\n    add rsp, 8\n"), "misaligned_call", "call r13"),
    # AArch64: the frame is 8 bytes short of 16-byte alignment at blr.
    ("map_sum", "stream", "aarch64", A64["map_sum"].replace("#-48]!", "#-56]!").replace("[sp], #48", "[sp], #56"),
     "misaligned_call", "blr x21"),
    # The recursive call is made from a frame that is not 16-byte aligned (no `sub rsp, 8`).
    ("fib_rec", "stream", "x86_64", X86["fib_rec"].replace("    sub rsp, 8\n", "").replace("    add rsp, 8\n", ""),
     "misaligned_call", "call fib_rec"),
    # x19/x20 used without saving them.
    ("fib_rec", "stream", "aarch64", A64["fib_rec"].replace("    stp x19, x20, [sp, #16]\n", "")
     .replace("    ldp x19, x20, [sp, #16]\n", ""), "abi", "callee-saved register x19"),
    # Iterative fib: correct values, but not recursive.
    ("fib_rec", "stream", "x86_64", """fib_rec:
    xor eax, eax
    mov ecx, 1
.Lloop:
    test rdi, rdi
    jz .Ldone
    lea rdx, [rax + rcx]
    mov rax, rcx
    mov rcx, rdx
    dec rdi
    jmp .Lloop
.Ldone:
    ret
""", "requirement", "recursive"),
    # Scalar dot product: correct values, no SIMD.
    ("dot_i32_simd", "stream", "aarch64", """dot_i32_simd:
    mov x3, #0
    mov x6, #0
.Lt:
    cmp x3, x2
    b.ge .Ldone
    ldrsw x7, [x0, x3, lsl #2]
    ldrsw x8, [x1, x3, lsl #2]
    madd x6, x7, x8, x6
    add x3, x3, #1
    b .Lt
.Ldone:
    mov x0, x6
    ret
""", "requirement", "SIMD"),
    # Vector loop over 4 elements that runs past the end (reads a[n..] on the last iteration).
    ("dot_i32_simd", "stream", "aarch64", A64["dot_i32_simd"].replace("and x4, x2, #-4", "add x4, x2, #3"),
     "oob_read", "ldr q2"),
    # movdqa on 4-byte-aligned data: #GP on hardware, silently accepted by Unicorn.
    ("dot_i32_simd", "stream", "x86_64", X86_DOT4.replace("movdqu", "movdqa"), "crash", "misaligned 16-byte SSE"),
    # hi = n instead of n - 1: reads a[n] (and a[0] when n == 0).
    ("binary_search", "stream", "x86_64", X86["binary_search"].replace("lea rcx, [rsi - 1]", "mov rcx, rsi"),
     "oob_read", "mov r9, qword ptr [rdi + r8*8]"),
    # Stores a zero "terminator" after the copy: writes dst[n] (or dst[-1] after the backward loop).
    ("memmove_i64", "stream", "aarch64", A64["memmove_i64"].replace(".Ldone:\n", ".Ldone:\n    str xzr, [x0, x2, lsl #3]\n"),
     "oob", "str xzr"),
    # Always copies forwards: wrong for dst after src with overlap.
    ("memmove_i64", "ir", "x86_64", QBE["memmove_i64"].replace("jnz %lt, @fwd0, @back", "jnz 1, @fwd0, @back"),
     "wrong_result", "memory ["),
    # The even branch never halves x: endless loop for k = 2.
    ("collatz_max", "stream", "aarch64", A64["collatz_max"].replace("    lsr x3, x3, #1\n", ""), "hang",
     "did not return"),
    # keystone accepts `cmp sil, ch` (encoding it as `cmp sil, bpl`); GNU as refuses it.
    ("crc32", "stream", "x86_64", X86["crc32"].replace("    xor ecx, ecx\n", "    xor ecx, ecx\n    cmp sil, ch\n"),
     "crosscheck", "GNU as"),
    # A malformed line in the middle of the stream: generation is stopped there.
    ("sum8", "stream", "aarch64", A64["sum8"].replace("    mov x8, #6\n", "    mov x8, #6, #7\n"), "syntax",
     "line 8"),
    # Reads the 7th argument from the return-address slot.
    ("sum8", "stream", "x86_64", X86["sum8"].replace("qword ptr [rsp + 8], 7", "qword ptr [rsp], 7"),
     "wrong_result", "sum8("),
    # Keeps the running sum in a caller-saved register across the call: the callee clobbers it.
    ("map_sum", "stream", "aarch64", A64["map_sum"].replace("x22, x22, x0", "x9, x9, x0").replace(
        "mov x22, #0", "mov x9, #0").replace("mov x0, x22", "mov x0, x9"), "wrong_result", "map_sum(a="),
    # Keeps the running sum below rsp (red zone) across the call.
    ("map_sum", "stream", "x86_64", X86["map_sum"].replace("    xor r15d, r15d\n", "    mov qword ptr [rsp - 8], 0\n")
     .replace("    add r15, rax\n", "    add qword ptr [rsp - 8], rax\n").replace("    mov rax, r15\n",
     "    mov rax, qword ptr [rsp - 8]\n"), "wrong_result", "map_sum(a="),
    # 64-bit shift instead of 32-bit: the upper half of crc leaks in.
    ("crc32", "structured", "aarch64", A64["crc32"].replace("mvn w0, w2", "mvn x0, x2"), "wrong_result", "crc32("),
]
