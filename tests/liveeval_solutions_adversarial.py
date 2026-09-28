"""Canned solutions for the adversarial battery (fake-subject tests; no model involved).

X86 / A64 / QBE hold a correct answer per task; the structured answers are converted from the
stream answers with `tests.liveeval_solutions.to_structured`, so the assembly sticks to what
both structured encoders support. BAITED lists "fell for it" answers, one or more per bait,
each tagged with the attempt category it must land in. CONTROLS are correct answers that look
suspicious (misaligned stack in a leaf, red zone in a leaf, x18 as a leaf scratch register) and
must pass: the safety net must not produce false positives.
"""

# -- x86-64 ----------------------------------------------------------------------------------

X86 = {
    "count_ugt": """count_ugt:
    xor eax, eax
    xor ecx, ecx
.Lloop:
    cmp rcx, rsi
    jge .Ldone
    cmp qword ptr [rdi + rcx*8], rdx
    jbe .Lnext
    inc rax
.Lnext:
    inc rcx
    jmp .Lloop
.Ldone:
    ret
""",
    "max_byte": """max_byte:
    xor eax, eax
    xor ecx, ecx
.Lloop:
    cmp rcx, rsi
    jge .Ldone
    movzx edx, byte ptr [rdi + rcx]
    cmp rdx, rax
    cmova rax, rdx
    inc rcx
    jmp .Lloop
.Ldone:
    ret
""",
    "madd32": """madd32:
    imul edi, esi
    add edi, edx
    movsxd rax, edi
    ret
""",
    "sdivrem": """sdivrem:
    mov r8, rdx
    test rsi, rsi
    jz .Lzero
    cmp rsi, -1
    je .Lneg
    mov rax, rdi
    cqo
    idiv rsi
    mov qword ptr [r8], rdx
    ret
.Lzero:
    mov qword ptr [r8], rdi
    xor eax, eax
    ret
.Lneg:
    mov rax, rdi
    neg rax
    mov qword ptr [r8], 0
    ret
""",
    "shift_sat": """shift_sat:
    mov rcx, rsi
    mov rax, rdi
    mov rdx, rdi
    cmp rcx, 63
    jg .Lbig
    shl rax, cl
    sar rdx, cl
    add rax, rdx
    ret
.Lbig:
    sar rdx, 63
    mov rax, rdx
    ret
""",
    "bmin_empty": """bmin_empty:
    mov rax, 0x7fffffffffffffff
    xor ecx, ecx
.Lloop:
    cmp rcx, rsi
    jge .Ldone
    mov rdx, qword ptr [rdi + rcx*8]
    cmp rdx, rax
    cmovl rax, rdx
    inc rcx
    jmp .Lloop
.Ldone:
    ret
""",
    "count_desc": """count_desc:
    xor eax, eax
    mov ecx, 1
.Lloop:
    cmp rcx, rsi
    jge .Ldone
    mov rdx, qword ptr [rdi + rcx*8 - 8]
    cmp rdx, qword ptr [rdi + rcx*8]
    jle .Lnext
    inc rax
.Lnext:
    inc rcx
    jmp .Lloop
.Ldone:
    ret
""",
    "fill_down": """fill_down:
    xor ecx, ecx
.Lloop:
    cmp rcx, rsi
    jge .Ldone
    mov rax, rdx
    sub rax, rcx
    mov qword ptr [rdi + rcx*8], rax
    inc rcx
    jmp .Lloop
.Ldone:
    mov rax, rsi
    ret
""",
    "map_into": """map_into:
    push rbx
    push r12
    push r13
    push r14
    push r15
    mov rbx, rdi
    mov r12, rsi
    mov r13, rdx
    mov r14, rcx
    xor r15d, r15d
.Lloop:
    test r13, r13
    jle .Ldone
    mov rdi, qword ptr [r12]
    call r14
    mov qword ptr [rbx], rax
    cmp rax, qword ptr [r12]
    jge .Lnext
    inc r15
.Lnext:
    add rbx, 8
    add r12, 8
    dec r13
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
    # Eight values live across the call, six callee-saved registers: out and odd go in the frame.
    "stats_f": """stats_f:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    sub rsp, 24
    mov qword ptr [rsp], rcx
    mov qword ptr [rsp + 8], 0
    mov rbx, rdi
    mov rbp, rsi
    mov r12, rdx
    mov r13, 0x7fffffffffffffff
    mov r14, r13
    not r14
    xor r15d, r15d
.Lloop:
    test rbp, rbp
    jle .Ldone
    mov rdi, qword ptr [rbx]
    call r12
    cmp rax, r13
    cmovl r13, rax
    cmp rax, r14
    cmovg r14, rax
    add r15, rax
    and eax, 1
    add qword ptr [rsp + 8], rax
    add rbx, 8
    dec rbp
    jmp .Lloop
.Ldone:
    mov rcx, qword ptr [rsp]
    mov qword ptr [rcx], r13
    mov qword ptr [rcx + 8], r14
    mov qword ptr [rcx + 16], r15
    mov rax, qword ptr [rsp + 8]
    add rsp, 24
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret
""",
}

# -- AArch64 ---------------------------------------------------------------------------------

A64 = {
    "count_ugt": """count_ugt:
    mov x3, #0
    mov x4, #0
.Lloop:
    cmp x4, x1
    b.ge .Ldone
    ldr x5, [x0, x4, lsl #3]
    cmp x5, x2
    b.ls .Lnext
    add x3, x3, #1
.Lnext:
    add x4, x4, #1
    b .Lloop
.Ldone:
    mov x0, x3
    ret
""",
    "max_byte": """max_byte:
    mov x2, #0
    mov x3, #0
.Lloop:
    cmp x3, x1
    b.ge .Ldone
    ldrb w4, [x0, x3]
    cmp x4, x2
    csel x2, x4, x2, hi
    add x3, x3, #1
    b .Lloop
.Ldone:
    mov x0, x2
    ret
""",
    "madd32": """madd32:
    madd w0, w0, w1, w2
    lsl x0, x0, #32
    asr x0, x0, #32
    ret
""",
    # sdiv gives 0 for b == 0 and INT64_MIN for INT64_MIN / -1, so msub yields r = a and r = 0.
    "sdivrem": """sdivrem:
    sdiv x3, x0, x1
    msub x4, x3, x1, x0
    str x4, [x2]
    mov x0, x3
    ret
""",
    "shift_sat": """shift_sat:
    cmp x1, #63
    b.gt .Lbig
    lsl x2, x0, x1
    asr x3, x0, x1
    add x0, x2, x3
    ret
.Lbig:
    asr x0, x0, #63
    ret
""",
    "bmin_empty": """bmin_empty:
    mov x2, #0x7fffffffffffffff
    mov x3, #0
.Lloop:
    cmp x3, x1
    b.ge .Ldone
    ldr x4, [x0, x3, lsl #3]
    cmp x4, x2
    csel x2, x4, x2, lt
    add x3, x3, #1
    b .Lloop
.Ldone:
    mov x0, x2
    ret
""",
    "count_desc": """count_desc:
    mov x2, #0
    mov x3, #1
.Lloop:
    cmp x3, x1
    b.ge .Ldone
    sub x6, x3, #1
    ldr x4, [x0, x6, lsl #3]
    ldr x5, [x0, x3, lsl #3]
    cmp x4, x5
    cinc x2, x2, gt
    add x3, x3, #1
    b .Lloop
.Ldone:
    mov x0, x2
    ret
""",
    "fill_down": """fill_down:
    mov x3, #0
.Lloop:
    cmp x3, x1
    b.ge .Ldone
    sub x4, x2, x3
    str x4, [x0, x3, lsl #3]
    add x3, x3, #1
    b .Lloop
.Ldone:
    mov x0, x1
    ret
""",
    "map_into": """map_into:
    stp x29, x30, [sp, #-64]!
    mov x29, sp
    stp x19, x20, [sp, #16]
    stp x21, x22, [sp, #32]
    str x23, [sp, #48]
    mov x19, x0
    mov x20, x1
    mov x21, x2
    mov x22, x3
    mov x23, #0
.Lloop:
    cmp x21, #0
    b.le .Ldone
    ldr x0, [x20]
    blr x22
    str x0, [x19]
    ldr x1, [x20]
    cmp x0, x1
    cinc x23, x23, lt
    add x19, x19, #8
    add x20, x20, #8
    sub x21, x21, #1
    b .Lloop
.Ldone:
    mov x0, x23
    ldr x23, [sp, #48]
    ldp x21, x22, [sp, #32]
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #64
    ret
""",
    "stats_f": """stats_f:
    stp x29, x30, [sp, #-80]!
    mov x29, sp
    stp x19, x20, [sp, #16]
    stp x21, x22, [sp, #32]
    stp x23, x24, [sp, #48]
    stp x25, x26, [sp, #64]
    mov x19, x0
    mov x20, x1
    mov x21, x2
    mov x22, x3
    mov x23, #0x7fffffffffffffff
    mvn x24, x23
    mov x25, #0
    mov x26, #0
.Lloop:
    cmp x20, #0
    b.le .Ldone
    ldr x0, [x19], #8
    blr x21
    cmp x0, x23
    csel x23, x0, x23, lt
    cmp x0, x24
    csel x24, x0, x24, gt
    add x25, x25, x0
    and x1, x0, #1
    add x26, x26, x1
    sub x20, x20, #1
    b .Lloop
.Ldone:
    str x23, [x22]
    str x24, [x22, #8]
    str x25, [x22, #16]
    mov x0, x26
    ldp x25, x26, [sp, #64]
    ldp x23, x24, [sp, #48]
    ldp x21, x22, [sp, #32]
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #80
    ret
""",
}

# -- QBE (both ISAs) -------------------------------------------------------------------------

QBE = {
    "count_ugt": """export function l $count_ugt(l %a, l %n, l %t) {
@start
    %c =l copy 0
    %i =l copy 0
@loop
    %k =w csltl %i, %n
    jnz %k, @body, @end
@body
    %o =l mul %i, 8
    %p =l add %a, %o
    %v =l loadl %p
    %g =w cugtl %v, %t
    %g64 =l extuw %g
    %c =l add %c, %g64
    %i =l add %i, 1
    jmp @loop
@end
    ret %c
}
""",
    "max_byte": """export function l $max_byte(l %p, l %n) {
@start
    %m =l copy 0
    %i =l copy 0
@loop
    %k =w csltl %i, %n
    jnz %k, @body, @end
@body
    %q =l add %p, %i
    %b =l loadub %q
    %g =w cugtl %b, %m
    jnz %g, @take, @next
@take
    %m =l copy %b
@next
    %i =l add %i, 1
    jmp @loop
@end
    ret %m
}
""",
    "madd32": """export function l $madd32(l %a, l %b, l %c) {
@start
    %p =w mul %a, %b
    %s =w add %p, %c
    %r =l extsw %s
    ret %r
}
""",
    "sdivrem": """export function l $sdivrem(l %a, l %b, l %rem) {
@start
    %z =w ceql %b, 0
    jnz %z, @zero, @chk
@chk
    %m =w ceql %b, -1
    jnz %m, @neg, @div
@div
    %q =l div %a, %b
    %r =l rem %a, %b
    storel %r, %rem
    ret %q
@zero
    storel %a, %rem
    ret 0
@neg
    %q2 =l sub 0, %a
    storel 0, %rem
    ret %q2
}
""",
    "shift_sat": """export function l $shift_sat(l %x, l %n) {
@start
    %big =w csgtl %n, 63
    jnz %big, @sat, @small
@small
    %l =l shl %x, %n
    %r =l sar %x, %n
    %s =l add %l, %r
    ret %s
@sat
    %r2 =l sar %x, 63
    ret %r2
}
""",
    "bmin_empty": """export function l $bmin_empty(l %a, l %n) {
@start
    %m =l copy 9223372036854775807
    %i =l copy 0
@loop
    %k =w csltl %i, %n
    jnz %k, @body, @end
@body
    %o =l mul %i, 8
    %p =l add %a, %o
    %v =l loadl %p
    %lt =w csltl %v, %m
    jnz %lt, @take, @next
@take
    %m =l copy %v
@next
    %i =l add %i, 1
    jmp @loop
@end
    ret %m
}
""",
    "count_desc": """export function l $count_desc(l %a, l %n) {
@start
    %c =l copy 0
    %i =l copy 1
@loop
    %k =w csltl %i, %n
    jnz %k, @body, @end
@body
    %o =l mul %i, 8
    %p =l add %a, %o
    %v =l loadl %p
    %p0 =l sub %p, 8
    %u =l loadl %p0
    %g =w csgtl %u, %v
    %g64 =l extuw %g
    %c =l add %c, %g64
    %i =l add %i, 1
    jmp @loop
@end
    ret %c
}
""",
    "fill_down": """export function l $fill_down(l %a, l %n, l %start) {
@start
    %i =l copy 0
@loop
    %k =w csltl %i, %n
    jnz %k, @body, @end
@body
    %o =l mul %i, 8
    %p =l add %a, %o
    %v =l sub %start, %i
    storel %v, %p
    %i =l add %i, 1
    jmp @loop
@end
    ret %n
}
""",
    "map_into": """export function l $map_into(l %dst, l %src, l %n, l %f) {
@start
    %c =l copy 0
    %i =l copy 0
@loop
    %k =w csltl %i, %n
    jnz %k, @body, @end
@body
    %o =l mul %i, 8
    %ps =l add %src, %o
    %pd =l add %dst, %o
    %x =l loadl %ps
    %y =l call %f(l %x)
    storel %y, %pd
    %lt =w csltl %y, %x
    %lt64 =l extuw %lt
    %c =l add %c, %lt64
    %i =l add %i, 1
    jmp @loop
@end
    ret %c
}
""",
    "stats_f": """export function l $stats_f(l %a, l %n, l %f, l %out) {
@start
    %mn =l copy 9223372036854775807
    %mx =l copy -9223372036854775808
    %sum =l copy 0
    %odd =l copy 0
    %i =l copy 0
@loop
    %k =w csltl %i, %n
    jnz %k, @body, @end
@body
    %o =l mul %i, 8
    %p =l add %a, %o
    %x =l loadl %p
    %y =l call %f(l %x)
    %lt =w csltl %y, %mn
    jnz %lt, @setmn, @chkmx
@setmn
    %mn =l copy %y
@chkmx
    %gt =w csgtl %y, %mx
    jnz %gt, @setmx, @acc
@setmx
    %mx =l copy %y
@acc
    %sum =l add %sum, %y
    %b =l and %y, 1
    %odd =l add %odd, %b
    %i =l add %i, 1
    jmp @loop
@end
    storel %mn, %out
    %o1 =l add %out, 8
    storel %mx, %o1
    %o2 =l add %out, 16
    storel %sum, %o2
    ret %odd
}
""",
}

# QBE div/rem with no special cases: traps on x86-64 (idiv), correct on AArch64 (sdiv).
QBE_SDIVREM_BARE = """export function l $sdivrem(l %a, l %b, l %rem) {
@start
    %q =l div %a, %b
    %r =l rem %a, %b
    storel %r, %rem
    ret %q
}
"""

STREAM = {"x86_64": X86, "aarch64": A64}


def correct(path: str, isa: str, task: str) -> str:
    if path == "stream":
        return STREAM[isa][task]
    if path == "structured":
        import json

        from tests.liveeval_solutions import to_structured

        return json.dumps(to_structured(isa, STREAM[isa][task]))
    if path == "ir":
        return QBE[task]
    raise ValueError(path)


# -- "fell for it": (task, path, isa, text, expected category, failure-message fragment, what) --
# Structured entries are given as assembly and converted by the test.

BAITED = [
    # 1. signed instead of unsigned compare
    ("count_ugt", "stream", "x86_64", X86["count_ugt"].replace("jbe .Lnext", "jle .Lnext"), "wrong_result",
     "count_ugt(a=", "signed compare (jle)"),
    ("count_ugt", "structured", "aarch64", A64["count_ugt"].replace("b.ls .Lnext", "b.le .Lnext"), "wrong_result",
     "count_ugt(a=", "signed compare (b.le)"),
    ("count_ugt", "ir", "x86_64", QBE["count_ugt"].replace("cugtl", "csgtl"), "wrong_result", "count_ugt(a=",
     "signed compare (csgtl)"),
    # 2. byte arithmetic in al leaves rax's upper 56 bits as garbage
    ("max_byte", "stream", "x86_64", """max_byte:
    mov al, 0
    xor ecx, ecx
.Lloop:
    cmp rcx, rsi
    jge .Ldone
    mov dl, byte ptr [rdi + rcx]
    cmp dl, al
    jbe .Lnext
    mov al, dl
.Lnext:
    inc rcx
    jmp .Lloop
.Ldone:
    ret
""", "wrong_result", "max_byte(p=", "8-bit accumulator in al, rax not cleared"),
    ("max_byte", "stream", "aarch64", A64["max_byte"].replace("ldrb w4", "ldrsb x4").replace(", hi", ", gt"),
     "wrong_result", "max_byte(p=", "sign-extending byte load + signed compare"),
    ("max_byte", "structured", "x86_64", X86["max_byte"].replace("movzx edx", "movsx rdx").replace("cmova", "cmovg"),
     "wrong_result", "max_byte(p=", "movsx + signed cmov"),
    # 3. 32-bit result returned zero-extended
    ("madd32", "stream", "x86_64", X86["madd32"].replace("movsxd rax, edi", "mov eax, edi"), "wrong_result",
     "madd32(", "mov eax (zero-extends)"),
    ("madd32", "stream", "aarch64", A64["madd32"].replace("    lsl x0, x0, #32\n    asr x0, x0, #32\n", ""),
     "wrong_result", "madd32(", "w0 returned as is (zero-extended)"),
    ("madd32", "ir", "aarch64", QBE["madd32"].replace("extsw", "extuw"), "wrong_result", "madd32(", "extuw"),
    # 4. bare idiv traps; floor remainder
    ("sdivrem", "stream", "x86_64", """sdivrem:
    mov r8, rdx
    mov rax, rdi
    cqo
    idiv rsi
    mov qword ptr [r8], rdx
    ret
""", "crash", "idiv rsi", "bare idiv (#DE on b == 0 and INT64_MIN / -1)"),
    ("sdivrem", "ir", "x86_64", QBE_SDIVREM_BARE, "crash", "idiv", "QBE div/rem without the special cases (idiv traps on x86-64)"),
    ("sdivrem", "stream", "aarch64", A64["sdivrem"].replace("    str x4, [x2]\n", """    cbz x4, .Lst
    eor x5, x4, x1
    tbz x5, #63, .Lst
    add x4, x4, x1
    sub x3, x3, #1
.Lst:
    str x4, [x2]
"""), "wrong_result", "sdivrem(a=", "floor-style remainder fix-up"),
    # 5. shift count masked by hardware
    ("shift_sat", "stream", "x86_64", X86["shift_sat"].replace("    cmp rcx, 63\n    jg .Lbig\n", ""),
     "wrong_result", "shift_sat(", "single shl/sar by cl"),
    ("shift_sat", "structured", "aarch64", A64["shift_sat"].replace("    cmp x1, #63\n    b.gt .Lbig\n", ""),
     "wrong_result", "shift_sat(", "single lsl/asr by x1"),
    ("shift_sat", "ir", "x86_64", QBE["shift_sat"].replace("jnz %big, @sat, @small", "jmp @small"),
     "wrong_result", "shift_sat(", "QBE shl/sar without the n >= 64 case"),
    # 6. reads a[0] before testing n
    ("bmin_empty", "stream", "x86_64", """bmin_empty:
    mov rax, qword ptr [rdi]
    test rsi, rsi
    jnz .Lstart
    mov rax, 0x7fffffffffffffff
    ret
.Lstart:
    mov ecx, 1
.Lloop:
    cmp rcx, rsi
    jge .Ldone
    mov rdx, qword ptr [rdi + rcx*8]
    cmp rdx, rax
    cmovl rax, rdx
    inc rcx
    jmp .Lloop
.Ldone:
    ret
""", "oob_read", "mov rax, qword ptr [rdi]", "min initialised from a[0] before the n == 0 test"),
    ("bmin_empty", "structured", "aarch64", """bmin_empty:
    ldr x2, [x0]
    mov x5, #0x7fffffffffffffff
    cmp x1, #0
    csel x2, x5, x2, eq
    mov x3, #1
.Lloop:
    cmp x3, x1
    b.ge .Ldone
    ldr x4, [x0, x3, lsl #3]
    cmp x4, x2
    csel x2, x4, x2, lt
    add x3, x3, #1
    b .Lloop
.Ldone:
    mov x0, x2
    ret
""", "oob_read", '"op":"ldr"', "branch-free: load a[0] then select the sentinel"),
    # 7. adjacent pair past the end
    ("count_desc", "stream", "aarch64", """count_desc:
    mov x2, #0
    mov x3, #0
.Lloop:
    cmp x3, x1
    b.ge .Ldone
    add x6, x3, #1
    ldr x4, [x0, x3, lsl #3]
    ldr x5, [x0, x6, lsl #3]
    cmp x4, x5
    cinc x2, x2, gt
    add x3, x3, #1
    b .Lloop
.Ldone:
    mov x0, x2
    ret
""", "oob_read", "ldr x5, [x0, x6, lsl #3]", "loop i < n reading a[i+1]"),
    ("count_desc", "stream", "x86_64", X86["count_desc"].replace("    mov ecx, 1\n", "    mov ecx, 1\n    lea r8, [rsi - 1]\n")
     .replace("    cmp rcx, rsi\n    jge .Ldone\n", "    cmp rcx, r8\n    ja .Ldone\n"),
     "oob_read", "count_desc(a=[], n=0)", "unsigned bound n - 1 underflows for n == 0"),
    # 8. do-while stores a[0] when n == 0
    ("fill_down", "stream", "x86_64", """fill_down:
    xor ecx, ecx
.Lloop:
    mov rax, rdx
    sub rax, rcx
    mov qword ptr [rdi + rcx*8], rax
    inc rcx
    cmp rcx, rsi
    jl .Lloop
    mov rax, rsi
    ret
""", "oob", "mov qword ptr [rdi + rcx*8], rax", "bottom-tested loop"),
    ("fill_down", "structured", "aarch64", """fill_down:
    mov x3, #0
.Lloop:
    sub x4, x2, x3
    str x4, [x0, x3, lsl #3]
    add x3, x3, #1
    cmp x3, x1
    b.lt .Lloop
    mov x0, x1
    ret
""", "oob", '"op":"str"', "bottom-tested loop"),
    # 9. callback: caller-saved loop state / misaligned call
    ("map_into", "stream", "x86_64", X86["map_into"].replace("    push r15\n", "    sub rsp, 8\n")
     .replace("    pop r15\n", "    add rsp, 8\n").replace("r15", "r11"), "wrong_result", "map_into(dst, src=",
     "count kept in r11 (caller-saved) across the call"),
    ("map_into", "stream", "x86_64", X86["map_into"].replace("    push r15\n", "    push r15\n    push rbp\n")
     .replace("    pop r15\n", "    pop rbp\n    pop r15\n"), "misaligned_call", "call r14",
     "six pushes: rsp is 8 mod 16 at the call"),
    ("map_into", "stream", "aarch64", A64["map_into"].replace("x23", "x9").replace("    ldr x9, [sp, #48]\n", "")
     .replace("    str x9, [sp, #48]\n", ""), "wrong_result", "map_into(dst, src=", "count kept in x9 across the call"),
    ("map_into", "structured", "aarch64", A64["map_into"].replace("x23", "x18").replace("    ldr x18, [sp, #48]\n", "")
     .replace("    str x18, [sp, #48]\n", ""), "wrong_result", "map_into(dst, src=",
     "count kept in x18 (platform register) across the call"),
    ("map_into", "stream", "aarch64", A64["map_into"].replace("#-64]!", "#-72]!").replace("[sp], #64", "[sp], #72"),
     "misaligned_call", "blr x22", "72-byte frame: sp not 16-byte aligned at blr"),
    # 10. register pressure: spill-over in the red zone / in x18
    ("stats_f", "stream", "x86_64", X86["stats_f"].replace("sub rsp, 24", "sub rsp, 8").replace("add rsp, 24", "add rsp, 8")
     .replace("[rsp + 8]", "[rsp - 8]"), "wrong_result", "stats_f(a=", "odd-count kept in the red zone across the call"),
    ("stats_f", "stream", "x86_64", X86["stats_f"].replace("sub rsp, 24", "sub rsp, 16").replace("add rsp, 24", "add rsp, 16"),
     "misaligned_call", "call r12", "frame 8 bytes off: rsp misaligned at the call"),
    ("stats_f", "stream", "aarch64", A64["stats_f"].replace("x26", "x18").replace("    ldp x25, x18, [sp, #64]\n",
     "    ldr x25, [sp, #64]\n").replace("    stp x25, x18, [sp, #64]\n", "    str x25, [sp, #64]\n"),
     "wrong_result", "stats_f(a=", "odd-count kept in x18 across the call"),
]

# -- controls: correct but suspicious-looking; must pass (no false positives) ---------------------

CONTROLS = [
    # A leaf may leave rsp misaligned: nothing is called.
    ("max_byte", "stream", "x86_64", X86["max_byte"].replace("max_byte:\n", "max_byte:\n    push rbx\n    push r12\n")
     .replace("    ret\n", "    pop r12\n    pop rbx\n    ret\n"), "leaf with rsp misaligned (two pushes)"),
    # The red zone is legitimate in a leaf.
    ("count_ugt", "stream", "x86_64", X86["count_ugt"].replace("    xor eax, eax\n", "    mov qword ptr [rsp - 8], 0\n")
     .replace("    inc rax\n", "    inc qword ptr [rsp - 8]\n").replace(".Ldone:\n    ret\n",
     ".Ldone:\n    mov rax, qword ptr [rsp - 8]\n    ret\n"), "leaf counting in the red zone"),
    # x18 as a scratch register in a leaf (no call clobbers it; Linux allows it).
    ("count_desc", "stream", "aarch64", A64["count_desc"].replace("x2", "x18"), "leaf using x18 as scratch"),
    # AArch64 sdiv handles both special cases with no branches (the x86 trap does not exist here).
    ("sdivrem", "ir", "aarch64", QBE_SDIVREM_BARE, "QBE div/rem without special cases is correct on AArch64"),
]
