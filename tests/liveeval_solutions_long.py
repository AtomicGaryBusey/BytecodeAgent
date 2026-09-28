"""Canned solutions for battery "long" (bca.liveeval.tasks_long; fake-subject tests, no model).

X86 / A64 are hand-written GNU assembly answers for the stream path; the structured answers are
converted from them by `tests.liveeval_solutions.to_structured`, so every assembly answer
sticks to instructions both structured encoders support. QBE answers are ISA-independent.

WRONG lists deliberately broken answers, each of which must trigger one safety-net category.
"""

import json

from tests.liveeval_solutions import to_structured


def _s(v: int) -> int:
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >> 63 else v


P1, P2, P3 = 0x9E3779B185EBCA87, 0xC2B2AE3D27D4EB4F, 0x165667B19E3779F9
P4, P5 = 0x85EBCA77C2B2AE63, 0x27D4EB2F165667C5


def _movk(reg: str, v: int) -> str:
    """AArch64 movz/movk sequence for a 64-bit constant."""
    parts = [(v >> s) & 0xFFFF for s in (0, 16, 32, 48)]
    lines = [f"    movz {reg}, #{parts[0]:#x}"]
    lines += [f"    movk {reg}, #{p:#x}, lsl #{16 * i}" for i, p in enumerate(parts) if i and p]
    return "\n".join(lines) + "\n"


# -- x86-64 (GNU as, Intel syntax, noprefix) ---------------------------------------------------

X86 = {
    "vm_run": """vm_run:
    xor r8d, r8d                        # pc
    xor r9d, r9d                        # sp = number of entries
.Lfetch:
    cmp r8, rsi
    je .Lstop
    mov rax, qword ptr [rdi + r8*8]
    test rax, rax
    je .Lstop
    cmp rax, 1
    je .Lpush
    cmp rax, 10
    je .Ljz
    cmp rax, 11
    je .Ljmp
    cmp rax, 5
    je .Ldup
    cmp rax, 7
    je .Ldrop
    cmp rax, 8
    je .Lover
    mov r10, qword ptr [rdx + r9*8 - 8]     # b = top
    mov r11, qword ptr [rdx + r9*8 - 16]    # a = below top
    cmp rax, 6
    je .Lswap
    cmp rax, 2
    je .Ladd
    cmp rax, 3
    je .Lsub
    cmp rax, 4
    je .Lmul
    cmp r11, r10                        # 9: LT
    setl al
    movzx r11d, al
    jmp .Lbin
.Ladd:
    add r11, r10
    jmp .Lbin
.Lsub:
    sub r11, r10
    jmp .Lbin
.Lmul:
    imul r11, r10
.Lbin:
    mov qword ptr [rdx + r9*8 - 16], r11
    dec r9
    inc r8
    jmp .Lfetch
.Lswap:
    mov qword ptr [rdx + r9*8 - 8], r11
    mov qword ptr [rdx + r9*8 - 16], r10
    inc r8
    jmp .Lfetch
.Lpush:
    mov rax, qword ptr [rdi + r8*8 + 8]
    mov qword ptr [rdx + r9*8], rax
    inc r9
    add r8, 2
    jmp .Lfetch
.Ldup:
    mov rax, qword ptr [rdx + r9*8 - 8]
    mov qword ptr [rdx + r9*8], rax
    inc r9
    inc r8
    jmp .Lfetch
.Lover:
    mov rax, qword ptr [rdx + r9*8 - 16]
    mov qword ptr [rdx + r9*8], rax
    inc r9
    inc r8
    jmp .Lfetch
.Ldrop:
    dec r9
    inc r8
    jmp .Lfetch
.Ljz:
    dec r9
    mov rax, qword ptr [rdx + r9*8]
    test rax, rax
    jne .Ljznot
    mov r8, qword ptr [rdi + r8*8 + 8]
    jmp .Lfetch
.Ljznot:
    add r8, 2
    jmp .Lfetch
.Ljmp:
    mov r8, qword ptr [rdi + r8*8 + 8]
    jmp .Lfetch
.Lstop:
    xor eax, eax
    test r9, r9
    je .Lzero
    mov rax, qword ptr [rdx + r9*8 - 8]
.Lzero:
    cmp r9, rcx
    jge .Ldone
    mov qword ptr [rdx + r9*8], 0
    inc r9
    jmp .Lzero
.Ldone:
    ret
""",
    "qsort_pairs": """qsort_pairs:
    push rbx
    push r12
    push r13
    mov rbx, rdi                        # a
    mov r12, rsi                        # n (records)
.Ltop:
    cmp r12, 1
    jle .Lret
    lea rax, [r12 - 1]
    shl rax, 4                          # byte offset of the last record (pivot)
    mov r8, qword ptr [rbx + rax]       # pivot key
    mov r9, qword ptr [rbx + rax + 8]   # pivot value
    xor ecx, ecx                        # i (byte offset)
    xor edx, edx                        # j (byte offset)
.Lpart:
    cmp rdx, rax
    jge .Lpdone
    mov r10, qword ptr [rbx + rdx]
    mov r11, qword ptr [rbx + rdx + 8]
    cmp r10, r8
    jl .Lless
    jg .Lnext
    cmp r11, r9
    jge .Lnext
.Lless:
    mov rsi, qword ptr [rbx + rcx]
    mov rdi, qword ptr [rbx + rcx + 8]
    mov qword ptr [rbx + rcx], r10
    mov qword ptr [rbx + rcx + 8], r11
    mov qword ptr [rbx + rdx], rsi
    mov qword ptr [rbx + rdx + 8], rdi
    add rcx, 16
.Lnext:
    add rdx, 16
    jmp .Lpart
.Lpdone:
    mov rsi, qword ptr [rbx + rcx]
    mov rdi, qword ptr [rbx + rcx + 8]
    mov qword ptr [rbx + rcx], r8
    mov qword ptr [rbx + rcx + 8], r9
    mov qword ptr [rbx + rax], rsi
    mov qword ptr [rbx + rax + 8], rdi
    mov r13, rcx
    shr r13, 4                          # i (records before the pivot)
    mov rdi, rbx
    mov rsi, r13
    call qsort_pairs                    # left part
    mov rax, r13
    shl rax, 4
    lea rbx, [rbx + rax + 16]           # loop on the right part
    sub r12, r13
    dec r12
    jmp .Ltop
.Lret:
    xor eax, eax
    pop r13
    pop r12
    pop rbx
    ret
""",
    "base64_encode": """base64_encode:
    push rbx
    xor r8d, r8d                        # i
    xor r9d, r9d                        # o
.Lgroup:
    cmp r8, rsi
    jge .Ldone
    mov r10, rsi
    sub r10, r8                         # k = min(n - i, 3)
    cmp r10, 3
    jle .Lk
    mov r10d, 3
.Lk:
    movzx eax, byte ptr [rdi + r8]
    shl eax, 16
    cmp r10, 1
    je .Lbytes
    movzx r11d, byte ptr [rdi + r8 + 1]
    shl r11d, 8
    or eax, r11d
    cmp r10, 2
    je .Lbytes
    movzx r11d, byte ptr [rdi + r8 + 2]
    or eax, r11d
.Lbytes:
    xor r11d, r11d                      # j
    mov ecx, 18
.Lchar:
    cmp r11, r10
    jg .Lpad
    mov ebx, eax
    shr ebx, cl
    and ebx, 63
    cmp ebx, 26
    jb .Lupper
    cmp ebx, 52
    jb .Llower
    cmp ebx, 62
    jb .Ldigit
    je .Lplus
    mov ebx, 47
    jmp .Lstore
.Lplus:
    mov ebx, 43
    jmp .Lstore
.Lupper:
    add ebx, 65
    jmp .Lstore
.Llower:
    add ebx, 71
    jmp .Lstore
.Ldigit:
    sub ebx, 4
    jmp .Lstore
.Lpad:
    mov ebx, 61
.Lstore:
    mov byte ptr [rdx + r9], bl
    inc r9
    inc r11
    sub ecx, 6
    cmp r11, 4
    jl .Lchar
    add r8, 3
    jmp .Lgroup
.Ldone:
    mov rax, r9
    pop rbx
    ret
""",
    "xxh64": f"""xxh64:
    push rbx
    push r12
    push r13
    mov r12, {P1:#x}
    mov r13, {P2:#x}
    lea rbx, [rdi + rsi]                # end
    mov rcx, rdi                        # q
    cmp rsi, 32
    jb .Lsmall
    lea r8, [rdx + r12]
    add r8, r13                         # v1
    lea r9, [rdx + r13]                 # v2
    mov r10, rdx                        # v3
    mov r11, rdx
    sub r11, r12                        # v4
.Lstripe:
    mov rax, rbx
    sub rax, rcx
    cmp rax, 32
    jb .Lmerge
    mov rax, qword ptr [rcx]
    imul rax, r13
    add r8, rax
    rol r8, 31
    imul r8, r12
    mov rax, qword ptr [rcx + 8]
    imul rax, r13
    add r9, rax
    rol r9, 31
    imul r9, r12
    mov rax, qword ptr [rcx + 16]
    imul rax, r13
    add r10, rax
    rol r10, 31
    imul r10, r12
    mov rax, qword ptr [rcx + 24]
    imul rax, r13
    add r11, rax
    rol r11, 31
    imul r11, r12
    add rcx, 32
    jmp .Lstripe
.Lmerge:
    mov rax, r8
    rol rax, 1
    mov rdx, r9
    rol rdx, 7
    add rax, rdx
    mov rdx, r10
    rol rdx, 12
    add rax, rdx
    mov rdx, r11
    rol rdx, 18
    add rax, rdx
    mov rdx, {P4:#x}
    imul r8, r13
    rol r8, 31
    imul r8, r12
    xor rax, r8
    imul rax, r12
    add rax, rdx
    imul r9, r13
    rol r9, 31
    imul r9, r12
    xor rax, r9
    imul rax, r12
    add rax, rdx
    imul r10, r13
    rol r10, 31
    imul r10, r12
    xor rax, r10
    imul rax, r12
    add rax, rdx
    imul r11, r13
    rol r11, 31
    imul r11, r12
    xor rax, r11
    imul rax, r12
    add rax, rdx
    jmp .Laddn
.Lsmall:
    mov rax, {P5:#x}
    add rax, rdx
.Laddn:
    add rax, rsi
.L8:
    mov rdx, rbx
    sub rdx, rcx
    cmp rdx, 8
    jb .L4
    mov rdx, qword ptr [rcx]
    imul rdx, r13
    rol rdx, 31
    imul rdx, r12
    xor rax, rdx
    rol rax, 27
    imul rax, r12
    mov rdx, {P4:#x}
    add rax, rdx
    add rcx, 8
    jmp .L8
.L4:
    cmp rdx, 4
    jb .L1
    mov edx, dword ptr [rcx]
    imul rdx, r12
    xor rax, rdx
    rol rax, 23
    imul rax, r13
    mov rdx, {P3:#x}
    add rax, rdx
    add rcx, 4
.L1:
    cmp rcx, rbx
    jae .Lfinal
    movzx edx, byte ptr [rcx]
    mov r8, {P5:#x}
    imul rdx, r8
    xor rax, rdx
    rol rax, 11
    imul rax, r12
    inc rcx
    jmp .L1
.Lfinal:
    mov rdx, rax
    shr rdx, 33
    xor rax, rdx
    imul rax, r13
    mov rdx, rax
    shr rdx, 29
    xor rax, rdx
    mov rdx, {P3:#x}
    imul rax, rdx
    mov rdx, rax
    shr rdx, 32
    xor rax, rdx
    pop r13
    pop r12
    pop rbx
    ret
""",
    "packbits_encode": """packbits_encode:
    push rbx
    mov r11, rcx                        # cap (cl is needed for header bytes)
    xor r8d, r8d                        # i
    xor r9d, r9d                        # o
.Lpacket:
    cmp r8, rsi
    jge .Ldone
    movzx eax, byte ptr [rdi + r8]      # b = src[i]
    mov r10d, 1                         # r = run(i)
.Lrun:
    lea rcx, [r8 + r10]
    cmp rcx, rsi
    jge .Lrundone
    cmp r10, 128
    jge .Lrundone
    movzx ecx, byte ptr [rdi + rcx]
    cmp ecx, eax
    jne .Lrundone
    inc r10
    jmp .Lrun
.Lrundone:
    cmp r10, 3
    jl .Llit
    mov rcx, r11
    sub rcx, r9
    cmp rcx, 2
    jl .Lfull
    mov ecx, 1
    sub ecx, r10d                       # header 1 - r
    mov byte ptr [rdx + r9], cl
    mov byte ptr [rdx + r9 + 1], al
    add r9, 2
    add r8, r10
    jmp .Lpacket
.Llit:
    mov r10, r8                         # j
.Llitext:
    cmp r10, rsi
    jge .Llitend
    mov rcx, r10
    sub rcx, r8
    cmp rcx, 128
    jge .Llitend
    lea rcx, [r10 + 2]
    cmp rcx, rsi
    jge .Llitinc                        # fewer than 3 bytes left: no run of 3 here
    movzx eax, byte ptr [rdi + r10]
    movzx ecx, byte ptr [rdi + r10 + 1]
    cmp ecx, eax
    jne .Llitinc
    movzx ecx, byte ptr [rdi + r10 + 2]
    cmp ecx, eax
    je .Llitend
.Llitinc:
    inc r10
    jmp .Llitext
.Llitend:
    mov rbx, r10
    sub rbx, r8                         # L
    mov rcx, r11
    sub rcx, r9                         # room
    cmp rcx, rbx
    jle .Lfull                          # needs L + 1 bytes
    lea rcx, [rbx - 1]
    mov byte ptr [rdx + r9], cl
    inc r9
.Lcopy:
    movzx eax, byte ptr [rdi + r8]
    mov byte ptr [rdx + r9], al
    inc r8
    inc r9
    cmp r8, r10
    jl .Lcopy
    jmp .Lpacket
.Lfull:
    mov rax, -1
    pop rbx
    ret
.Ldone:
    mov rax, r9
    pop rbx
    ret
""",
    "packbits_decode": """packbits_decode:
    xor r8d, r8d                        # k
    xor r9d, r9d                        # o
.Lpacket:
    cmp r8, rsi
    jge .Ldone
    movsx r10, byte ptr [rdi + r8]      # h (signed)
    cmp r10, -128
    je .Lnop
    test r10, r10
    js .Lrepeat
    lea rax, [r8 + r10 + 2]             # end of this literal packet
    cmp rax, rsi
    jg .Lbad
    lea r11, [r8 + 1]
    mov r8, rax
.Llit:
    cmp r9, rcx
    je .Lfull
    movzx eax, byte ptr [rdi + r11]
    mov byte ptr [rdx + r9], al
    inc r9
    inc r11
    cmp r11, r8
    jl .Llit
    jmp .Lpacket
.Lrepeat:
    lea rax, [r8 + 2]
    cmp rax, rsi
    jg .Lbad
    movzx eax, byte ptr [rdi + r8 + 1]
    add r8, 2
    mov r11, 1
    sub r11, r10                        # count = 1 - h
.Lrep:
    cmp r9, rcx
    je .Lfull
    mov byte ptr [rdx + r9], al
    inc r9
    dec r11
    jnz .Lrep
    jmp .Lpacket
.Lnop:
    inc r8
    jmp .Lpacket
.Lbad:
    mov rax, -2
    ret
.Lfull:
    mov rax, -1
    ret
.Ldone:
    mov rax, r9
    ret
""",
    "mat_pow": """mat_pow:
    push rbx
    push r12
    push r13
    push r14
    push r15
    mov r15, rsi
    imul r15, rsi                       # nn = n*n
    xor r9d, r9d                        # out = identity
    xor r10d, r10d
    xor r11d, r11d
.Lid:
    cmp r9, r15
    jge .Lpow
    xor eax, eax
    cmp r10, r11
    sete al
    mov qword ptr [rcx + r9*8], rax
    inc r9
    inc r11
    cmp r11, rsi
    jl .Lid
    xor r11d, r11d
    inc r10
    jmp .Lid
.Lpow:
    test rdx, rdx
    jz .Lclear
    xor r10d, r10d                      # i
.Lrow:
    xor r11d, r11d                      # j
.Lcol:
    xor ebx, ebx                        # s
    xor r12d, r12d                      # t
    mov r13, r10
    imul r13, rsi
    lea r13, [rcx + r13*8]              # &out[i*n]
    lea r14, [rdi + r11*8]              # &a[j]
.Ldot:
    mov rax, qword ptr [r13 + r12*8]
    imul rax, qword ptr [r14]
    add rbx, rax
    lea r14, [r14 + rsi*8]
    inc r12
    cmp r12, rsi
    jl .Ldot
    mov rax, r10
    imul rax, rsi
    add rax, r11
    mov qword ptr [r8 + rax*8], rbx     # tmp[i*n+j] = s
    inc r11
    cmp r11, rsi
    jl .Lcol
    inc r10
    cmp r10, rsi
    jl .Lrow
    xor r9d, r9d
.Lcopy:
    mov rax, qword ptr [r8 + r9*8]
    mov qword ptr [rcx + r9*8], rax
    inc r9
    cmp r9, r15
    jl .Lcopy
    dec rdx
    jmp .Lpow
.Lclear:
    lea r9, [r15 + r15]
.Lz:
    dec r9
    js .Ldone
    mov qword ptr [r8 + r9*8], 0
    jmp .Lz
.Ldone:
    xor eax, eax
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbx
    ret
""",
    "lcs_len": """lcs_len:
    push rbx
    push r12
    xor eax, eax
.Lz:
    mov qword ptr [r8 + rax*8], 0
    inc rax
    cmp rax, rcx
    jle .Lz
    xor r9d, r9d                        # i
.Li:
    cmp r9, rsi
    jge .Ldone
    movzx r10d, byte ptr [rdi + r9]     # x[i]
    xor r11d, r11d                      # diag
    mov rax, 1                          # j
.Lj:
    cmp rax, rcx
    jg .Linext
    mov rbx, qword ptr [r8 + rax*8]     # up
    movzx r12d, byte ptr [rdx + rax - 1]
    cmp r12d, r10d
    jne .Lmax
    lea r12, [r11 + 1]
    jmp .Lstore
.Lmax:
    mov r12, qword ptr [r8 + rax*8 - 8]
    cmp r12, rbx
    cmovl r12, rbx
.Lstore:
    mov qword ptr [r8 + rax*8], r12
    mov r11, rbx
    inc rax
    jmp .Lj
.Linext:
    inc r9
    jmp .Li
.Ldone:
    mov rax, qword ptr [r8 + rcx*8]
    pop r12
    pop rbx
    ret
""",
    "mp_mul": """mp_mul:
    push rbx
    push r12
    push r13
    push r14
    mov r9, rdx                         # b (rdx is needed by mul)
    lea r10, [rsi + rcx]                # na + nb
    xor eax, eax
.Lz:
    cmp rax, r10
    jge .Lzdone
    mov qword ptr [r8 + rax*8], 0
    inc rax
    jmp .Lz
.Lzdone:
    xor r11d, r11d                      # i
.Li:
    cmp r11, rsi
    jge .Lcount
    mov rbx, qword ptr [rdi + r11*8]    # a[i]
    xor r12d, r12d                      # carry
    xor r13d, r13d                      # j
    lea r14, [r8 + r11*8]               # &r[i]
.Lj:
    cmp r13, rcx
    jge .Ljdone
    mov rax, qword ptr [r9 + r13*8]
    mul rbx
    add rax, r12
    adc rdx, 0
    add rax, qword ptr [r14 + r13*8]
    adc rdx, 0
    mov qword ptr [r14 + r13*8], rax
    mov r12, rdx
    inc r13
    jmp .Lj
.Ljdone:
    mov qword ptr [r14 + rcx*8], r12
    inc r11
    jmp .Li
.Lcount:
    mov rax, r10
.Lc:
    test rax, rax
    jz .Lret
    cmp qword ptr [r8 + rax*8 - 8], 0
    jne .Lret
    dec rax
    jmp .Lc
.Lret:
    pop r14
    pop r13
    pop r12
    pop rbx
    ret
""",
}


# -- AArch64 (GNU as) ------------------------------------------------------------------------------

A64 = {
    "vm_run": """vm_run:
    mov x4, #0                          // pc
    mov x5, #0                          // sp = number of entries
.Lfetch:
    cmp x4, x1
    b.eq .Lstop
    ldr x9, [x0, x4, lsl #3]
    cbz x9, .Lstop
    cmp x9, #1
    b.eq .Lpush
    cmp x9, #10
    b.eq .Ljz
    cmp x9, #11
    b.eq .Ljmp
    cmp x9, #5
    b.eq .Ldup
    cmp x9, #7
    b.eq .Ldrop
    cmp x9, #8
    b.eq .Lover
    sub x12, x5, #2
    add x12, x2, x12, lsl #3            // &stack[sp-2]
    ldp x11, x10, [x12]                 // a, b
    cmp x9, #6
    b.eq .Lswap
    cmp x9, #2
    b.eq .Ladd
    cmp x9, #3
    b.eq .Lsub
    cmp x9, #4
    b.eq .Lmul
    cmp x11, x10                        // 9: LT
    cset x11, lt
    b .Lbin
.Ladd:
    add x11, x11, x10
    b .Lbin
.Lsub:
    sub x11, x11, x10
    b .Lbin
.Lmul:
    mul x11, x11, x10
.Lbin:
    str x11, [x12]
    sub x5, x5, #1
    add x4, x4, #1
    b .Lfetch
.Lswap:
    stp x10, x11, [x12]
    add x4, x4, #1
    b .Lfetch
.Lpush:
    add x10, x4, #1
    ldr x10, [x0, x10, lsl #3]
    str x10, [x2, x5, lsl #3]
    add x5, x5, #1
    add x4, x4, #2
    b .Lfetch
.Ldup:
    sub x10, x5, #1
    ldr x10, [x2, x10, lsl #3]
    str x10, [x2, x5, lsl #3]
    add x5, x5, #1
    add x4, x4, #1
    b .Lfetch
.Lover:
    sub x10, x5, #2
    ldr x10, [x2, x10, lsl #3]
    str x10, [x2, x5, lsl #3]
    add x5, x5, #1
    add x4, x4, #1
    b .Lfetch
.Ldrop:
    sub x5, x5, #1
    add x4, x4, #1
    b .Lfetch
.Ljz:
    sub x5, x5, #1
    ldr x10, [x2, x5, lsl #3]
    cbnz x10, .Ljznot
.Ljmp:
    add x10, x4, #1
    ldr x4, [x0, x10, lsl #3]
    b .Lfetch
.Ljznot:
    add x4, x4, #2
    b .Lfetch
.Lstop:
    mov x9, #0
    cbz x5, .Lzero
    sub x10, x5, #1
    ldr x9, [x2, x10, lsl #3]
.Lzero:
    cmp x5, x3
    b.ge .Ldone
    str xzr, [x2, x5, lsl #3]
    add x5, x5, #1
    b .Lzero
.Ldone:
    mov x0, x9
    ret
""",
    "qsort_pairs": """qsort_pairs:
    stp x29, x30, [sp, #-48]!
    stp x19, x20, [sp, #16]
    str x21, [sp, #32]
    mov x19, x0                         // a
    mov x20, x1                         // n (records)
.Ltop:
    cmp x20, #1
    b.le .Lret
    sub x21, x20, #1
    add x15, x19, x21, lsl #4           // &pivot record (the last one)
    ldp x8, x9, [x15]                   // pivot key, value
    mov x10, x19                        // &record i
    mov x11, x19                        // &record j
.Lpart:
    cmp x11, x15
    b.hs .Lpdone
    ldp x12, x13, [x11]
    cmp x12, x8
    b.lt .Lless
    b.gt .Lnext
    cmp x13, x9
    b.ge .Lnext
.Lless:
    ldp x6, x7, [x10]
    stp x12, x13, [x10]
    stp x6, x7, [x11]
    add x10, x10, #16
.Lnext:
    add x11, x11, #16
    b .Lpart
.Lpdone:
    ldp x6, x7, [x10]
    stp x8, x9, [x10]
    stp x6, x7, [x15]
    sub x21, x10, x19
    lsr x21, x21, #4                    // i (records before the pivot)
    mov x0, x19
    mov x1, x21
    bl qsort_pairs                      // left part
    add x19, x19, x21, lsl #4           // loop on the right part
    add x19, x19, #16
    sub x20, x20, x21
    sub x20, x20, #1
    b .Ltop
.Lret:
    mov x0, #0
    ldr x21, [sp, #32]
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #48
    ret
""",
    "base64_encode": """base64_encode:
    mov x3, #0                          // i
    mov x4, #0                          // o
.Lgroup:
    cmp x3, x1
    b.ge .Ldone
    sub x5, x1, x3                      // k = min(n - i, 3)
    cmp x5, #3
    b.le .Lk
    mov x5, #3
.Lk:
    ldrb w6, [x0, x3]
    lsl w6, w6, #16
    cmp x5, #1
    b.eq .Lbytes
    add x10, x0, x3
    ldrb w9, [x10, #1]
    lsl w9, w9, #8
    orr w6, w6, w9
    cmp x5, #2
    b.eq .Lbytes
    ldrb w9, [x10, #2]
    orr w6, w6, w9
.Lbytes:
    mov x7, #0                          // j
    mov x8, #18                         // shift
.Lchar:
    cmp x7, x5
    b.gt .Lpad
    lsr w9, w6, w8
    and w9, w9, #63
    cmp w9, #26
    b.lo .Lupper
    cmp w9, #52
    b.lo .Llower
    cmp w9, #62
    b.lo .Ldigit
    b.eq .Lplus
    mov w9, #47
    b .Lstore
.Lplus:
    mov w9, #43
    b .Lstore
.Lupper:
    add w9, w9, #65
    b .Lstore
.Llower:
    add w9, w9, #71
    b .Lstore
.Ldigit:
    sub w9, w9, #4
    b .Lstore
.Lpad:
    mov w9, #61
.Lstore:
    strb w9, [x2, x4]
    add x4, x4, #1
    add x7, x7, #1
    sub x8, x8, #6
    cmp x7, #4
    b.lt .Lchar
    add x3, x3, #3
    b .Lgroup
.Ldone:
    mov x0, x4
    ret
""",
    "xxh64": "xxh64:\n" + _movk("x12", P1) + _movk("x13", P2) + _movk("x14", P4) + _movk("x15", P3)
    + _movk("x16", P5) + """    add x3, x0, x1                      // end
    cmp x1, #32
    b.lo .Lsmall
    add x4, x2, x12
    add x4, x4, x13                     // v1
    add x5, x2, x13                     // v2
    mov x6, x2                          // v3
    sub x7, x2, x12                     // v4
.Lstripe:
    sub x9, x3, x0
    cmp x9, #32
    b.lo .Lmerge
    ldr x9, [x0]
    madd x4, x9, x13, x4
    ror x4, x4, #33
    mul x4, x4, x12
    ldr x9, [x0, #8]
    madd x5, x9, x13, x5
    ror x5, x5, #33
    mul x5, x5, x12
    ldr x9, [x0, #16]
    madd x6, x9, x13, x6
    ror x6, x6, #33
    mul x6, x6, x12
    ldr x9, [x0, #24]
    madd x7, x9, x13, x7
    ror x7, x7, #33
    mul x7, x7, x12
    add x0, x0, #32
    b .Lstripe
.Lmerge:
    ror x8, x4, #63
    ror x9, x5, #57
    add x8, x8, x9
    ror x9, x6, #52
    add x8, x8, x9
    ror x9, x7, #46
    add x8, x8, x9
    mul x9, x4, x13
    ror x9, x9, #33
    mul x9, x9, x12
    eor x8, x8, x9
    madd x8, x8, x12, x14
    mul x9, x5, x13
    ror x9, x9, #33
    mul x9, x9, x12
    eor x8, x8, x9
    madd x8, x8, x12, x14
    mul x9, x6, x13
    ror x9, x9, #33
    mul x9, x9, x12
    eor x8, x8, x9
    madd x8, x8, x12, x14
    mul x9, x7, x13
    ror x9, x9, #33
    mul x9, x9, x12
    eor x8, x8, x9
    madd x8, x8, x12, x14
    b .Laddn
.Lsmall:
    add x8, x2, x16
.Laddn:
    add x8, x8, x1
.L8:
    sub x9, x3, x0
    cmp x9, #8
    b.lo .L4
    ldr x10, [x0], #8
    mul x10, x10, x13
    ror x10, x10, #33
    mul x10, x10, x12
    eor x8, x8, x10
    ror x8, x8, #37
    madd x8, x8, x12, x14
    b .L8
.L4:
    cmp x9, #4
    b.lo .L1
    ldr w10, [x0], #4
    mul x10, x10, x12
    eor x8, x8, x10
    ror x8, x8, #41
    madd x8, x8, x13, x15
.L1:
    cmp x0, x3
    b.hs .Lfinal
    ldrb w10, [x0], #1
    mul x10, x10, x16
    eor x8, x8, x10
    ror x8, x8, #53
    mul x8, x8, x12
    b .L1
.Lfinal:
    lsr x9, x8, #33
    eor x8, x8, x9
    mul x8, x8, x13
    lsr x9, x8, #29
    eor x8, x8, x9
    mul x8, x8, x15
    lsr x9, x8, #32
    eor x8, x8, x9
    mov x0, x8
    ret
""",
    "packbits_encode": """packbits_encode:
    mov x4, #0                          // i
    mov x5, #0                          // o
.Lpacket:
    cmp x4, x1
    b.ge .Ldone
    ldrb w6, [x0, x4]                   // b = src[i]
    mov x7, #1                          // r = run(i)
.Lrun:
    add x9, x4, x7
    cmp x9, x1
    b.ge .Lrundone
    cmp x7, #128
    b.ge .Lrundone
    ldrb w10, [x0, x9]
    cmp w10, w6
    b.ne .Lrundone
    add x7, x7, #1
    b .Lrun
.Lrundone:
    cmp x7, #3
    b.lt .Llit
    sub x9, x3, x5
    cmp x9, #2
    b.lt .Lfull
    mov x9, #1
    sub x9, x9, x7                      // header 1 - r
    strb w9, [x2, x5]
    add x5, x5, #1
    strb w6, [x2, x5]
    add x5, x5, #1
    add x4, x4, x7
    b .Lpacket
.Llit:
    mov x7, x4                          // j
.Llitext:
    cmp x7, x1
    b.ge .Llitend
    sub x9, x7, x4
    cmp x9, #128
    b.ge .Llitend
    add x9, x7, #2
    cmp x9, x1
    b.ge .Llitinc                       // fewer than 3 bytes left: no run of 3 here
    add x11, x0, x7
    ldrb w10, [x11]
    ldrb w12, [x11, #1]
    cmp w12, w10
    b.ne .Llitinc
    ldrb w12, [x11, #2]
    cmp w12, w10
    b.eq .Llitend
.Llitinc:
    add x7, x7, #1
    b .Llitext
.Llitend:
    sub x9, x7, x4                      // L
    sub x10, x3, x5                     // room
    cmp x10, x9
    b.le .Lfull                         // needs L + 1 bytes
    sub x10, x9, #1
    strb w10, [x2, x5]
    add x5, x5, #1
.Lcopy:
    ldrb w10, [x0, x4]
    strb w10, [x2, x5]
    add x4, x4, #1
    add x5, x5, #1
    cmp x4, x7
    b.lt .Lcopy
    b .Lpacket
.Lfull:
    mov x0, #-1
    ret
.Ldone:
    mov x0, x5
    ret
""",
    "packbits_decode": """packbits_decode:
    mov x4, #0                          // k
    mov x5, #0                          // o
.Lpacket:
    cmp x4, x1
    b.ge .Ldone
    ldrsb x6, [x0, x4]                  // h (signed)
    cmn x6, #128
    b.eq .Lnop
    tbnz x6, #63, .Lrepeat
    add x9, x4, x6
    add x9, x9, #2                      // end of this literal packet
    cmp x9, x1
    b.gt .Lbad
    add x10, x4, #1
    mov x4, x9
.Llit:
    cmp x5, x3
    b.eq .Lfull
    ldrb w11, [x0, x10]
    strb w11, [x2, x5]
    add x5, x5, #1
    add x10, x10, #1
    cmp x10, x4
    b.lt .Llit
    b .Lpacket
.Lrepeat:
    add x9, x4, #2
    cmp x9, x1
    b.gt .Lbad
    add x10, x4, #1
    ldrb w11, [x0, x10]
    mov x4, x9
    mov x10, #1
    sub x10, x10, x6                    // count = 1 - h
.Lrep:
    cmp x5, x3
    b.eq .Lfull
    strb w11, [x2, x5]
    add x5, x5, #1
    subs x10, x10, #1
    b.ne .Lrep
    b .Lpacket
.Lnop:
    add x4, x4, #1
    b .Lpacket
.Lbad:
    mov x0, #-2
    ret
.Lfull:
    mov x0, #-1
    ret
.Ldone:
    mov x0, x5
    ret
""",
    "mat_pow": """mat_pow:
    mul x15, x1, x1                     // nn = n*n
    mov x9, #0                          // out = identity
    mov x10, #0
    mov x11, #0
.Lid:
    cmp x9, x15
    b.ge .Lpow
    cmp x10, x11
    cset x12, eq
    str x12, [x3, x9, lsl #3]
    add x9, x9, #1
    add x11, x11, #1
    cmp x11, x1
    b.lt .Lid
    mov x11, #0
    add x10, x10, #1
    b .Lid
.Lpow:
    cbz x2, .Lclear
    mov x10, #0                         // i
.Lrow:
    mov x11, #0                         // j
.Lcol:
    mov x12, #0                         // s
    mov x13, #0                         // t
    mul x14, x10, x1
    add x14, x3, x14, lsl #3            // &out[i*n]
    add x16, x0, x11, lsl #3            // &a[j]
.Ldot:
    ldr x6, [x14, x13, lsl #3]
    ldr x7, [x16]
    madd x12, x6, x7, x12
    add x16, x16, x1, lsl #3
    add x13, x13, #1
    cmp x13, x1
    b.lt .Ldot
    madd x6, x10, x1, x11
    str x12, [x4, x6, lsl #3]           // tmp[i*n+j] = s
    add x11, x11, #1
    cmp x11, x1
    b.lt .Lcol
    add x10, x10, #1
    cmp x10, x1
    b.lt .Lrow
    mov x9, #0
.Lcopy:
    ldr x6, [x4, x9, lsl #3]
    str x6, [x3, x9, lsl #3]
    add x9, x9, #1
    cmp x9, x15
    b.lt .Lcopy
    sub x2, x2, #1
    b .Lpow
.Lclear:
    add x9, x15, x15
.Lz:
    cbz x9, .Ldone
    sub x9, x9, #1
    str xzr, [x4, x9, lsl #3]
    b .Lz
.Ldone:
    mov x0, #0
    ret
""",
    "lcs_len": """lcs_len:
    mov x9, #0
.Lz:
    str xzr, [x4, x9, lsl #3]
    add x9, x9, #1
    cmp x9, x3
    b.le .Lz
    mov x5, #0                          // i
.Li:
    cmp x5, x1
    b.ge .Ldone
    ldrb w6, [x0, x5]                   // x[i]
    mov x7, #0                          // diag
    mov x8, #1                          // j
.Lj:
    cmp x8, x3
    b.gt .Linext
    ldr x10, [x4, x8, lsl #3]           // up
    sub x11, x8, #1
    ldrb w12, [x2, x11]
    cmp w12, w6
    b.ne .Lmax
    add x12, x7, #1
    b .Lstore
.Lmax:
    ldr x12, [x4, x11, lsl #3]
    cmp x12, x10
    csel x12, x12, x10, ge
.Lstore:
    str x12, [x4, x8, lsl #3]
    mov x7, x10
    add x8, x8, #1
    b .Lj
.Linext:
    add x5, x5, #1
    b .Li
.Ldone:
    ldr x0, [x4, x3, lsl #3]
    ret
""",
    "mp_mul": """mp_mul:
    add x9, x1, x3                      // na + nb
    mov x10, #0
.Lz:
    cmp x10, x9
    b.ge .Lzdone
    str xzr, [x4, x10, lsl #3]
    add x10, x10, #1
    b .Lz
.Lzdone:
    mov x10, #0                         // i
.Li:
    cmp x10, x1
    b.ge .Lcount
    ldr x11, [x0, x10, lsl #3]          // a[i]
    mov x12, #0                         // carry
    mov x13, #0                         // j
    add x14, x4, x10, lsl #3            // &r[i]
.Lj:
    cmp x13, x3
    b.ge .Ljdone
    ldr x15, [x2, x13, lsl #3]
    mul x16, x11, x15
    umulh x17, x11, x15
    adds x16, x16, x12
    cinc x17, x17, hs
    ldr x15, [x14, x13, lsl #3]
    adds x16, x16, x15
    cinc x17, x17, hs
    str x16, [x14, x13, lsl #3]
    mov x12, x17
    add x13, x13, #1
    b .Lj
.Ljdone:
    str x12, [x14, x3, lsl #3]
    add x10, x10, #1
    b .Li
.Lcount:
    cbz x9, .Lret
    sub x10, x9, #1
    ldr x11, [x4, x10, lsl #3]
    cbnz x11, .Lret
    mov x9, x10
    b .Lcount
.Lret:
    mov x0, x9
    ret
""",
}


# -- QBE (both ISAs) -------------------------------------------------------------------------------


def _qrotl(dst: str, src: str, r: int, tmp: str) -> str:
    return (f"    {tmp}a =l shl {src}, {r}\n    {tmp}b =l shr {src}, {64 - r}\n"
            f"    {dst} =l or {tmp}a, {tmp}b\n")


def _qround(dst: str, acc: str | None, lane: str, tag: str) -> str:
    """dst = rotl(acc + lane * P2, 31) * P1 (acc None means 0)."""
    s = f"    %{tag}m =l mul {lane}, {_s(P2)}\n"
    if acc is not None:
        s += f"    %{tag}m =l add {acc}, %{tag}m\n"
    return s + _qrotl(f"%{tag}r", f"%{tag}m", 31, f"%{tag}t") + f"    {dst} =l mul %{tag}r, {_s(P1)}\n"


def _qxxh() -> str:
    s = """export function l $xxh64(l %p, l %n, l %seed) {
@start
    %end =l add %p, %n
    %q =l copy %p
    %big =w csgel %n, 32
    jnz %big, @init, @small
@init
"""
    s += f"    %v1 =l add %seed, {_s(P1 + P2)}\n    %v2 =l add %seed, {_s(P2)}\n    %v3 =l copy %seed\n"
    s += f"    %v4 =l sub %seed, {_s(P1)}\n"
    s += """@stripe
    %rem =l sub %end, %q
    %more =w csgel %rem, 32
    jnz %more, @sbody, @merge
@sbody
"""
    for i, v in enumerate(("%v1", "%v2", "%v3", "%v4")):
        s += f"    %a{i} =l add %q, {8 * i}\n    %l{i} =l loadl %a{i}\n" + _qround(v, v, f"%l{i}", f"s{i}")
    s += "    %q =l add %q, 32\n    jmp @stripe\n@merge\n"
    s += _qrotl("%h1", "%v1", 1, "%m1") + _qrotl("%h2", "%v2", 7, "%m2")
    s += _qrotl("%h3", "%v3", 12, "%m3") + _qrotl("%h4", "%v4", 18, "%m4")
    s += "    %h =l add %h1, %h2\n    %h =l add %h, %h3\n    %h =l add %h, %h4\n"
    for i, v in enumerate(("%v1", "%v2", "%v3", "%v4")):
        s += _qround(f"%k{i}", None, v, f"g{i}")
        s += f"    %h =l xor %h, %k{i}\n    %h =l mul %h, {_s(P1)}\n    %h =l add %h, {_s(P4)}\n"
    s += f"""    jmp @addn
@small
    %h =l add %seed, {_s(P5)}
@addn
    %h =l add %h, %n
@t8
    %r8 =l sub %end, %q
    %c8 =w csgel %r8, 8
    jnz %c8, @b8, @t4
@b8
    %w8 =l loadl %q
"""
    s += _qround("%k8", None, "%w8", "e8")
    s += "    %h =l xor %h, %k8\n" + _qrotl("%h", "%h", 27, "%z8")
    s += f"""    %h =l mul %h, {_s(P1)}
    %h =l add %h, {_s(P4)}
    %q =l add %q, 8
    jmp @t8
@t4
    %c4 =w csgel %r8, 4
    jnz %c4, @b4, @t1
@b4
    %w4 =l loaduw %q
    %w4 =l mul %w4, {_s(P1)}
    %h =l xor %h, %w4
"""
    s += _qrotl("%h", "%h", 23, "%z4")
    s += f"""    %h =l mul %h, {_s(P2)}
    %h =l add %h, {_s(P3)}
    %q =l add %q, 4
@t1
    %c1 =w cultl %q, %end
    jnz %c1, @b1, @fin
@b1
    %w1 =l loadub %q
    %w1 =l mul %w1, {_s(P5)}
    %h =l xor %h, %w1
"""
    s += _qrotl("%h", "%h", 11, "%z1")
    s += f"""    %h =l mul %h, {_s(P1)}
    %q =l add %q, 1
    jmp @t1
@fin
    %f =l shr %h, 33
    %h =l xor %h, %f
    %h =l mul %h, {_s(P2)}
    %f =l shr %h, 29
    %h =l xor %h, %f
    %h =l mul %h, {_s(P3)}
    %f =l shr %h, 32
    %h =l xor %h, %f
    ret %h
}}
"""
    return s


QBE = {
    "vm_run": """export function l $vm_run(l %code, l %len, l %stack, l %cap) {
@start
    %pc =l copy 0
    %sp =l copy 0
@fetch
    %end =w ceql %pc, %len
    jnz %end, @stop, @load
@load
    %o =l mul %pc, 8
    %pa =l add %code, %o
    %op =l loadl %pa
    %t =w ceql %op, 0
    jnz %t, @stop, @c1
@c1
    %t =w ceql %op, 1
    jnz %t, @push, @c10
@c10
    %t =w ceql %op, 10
    jnz %t, @jz, @c11
@c11
    %t =w ceql %op, 11
    jnz %t, @jmp, @c5
@c5
    %top =l mul %sp, 8
    %top =l add %stack, %top
    %t =w ceql %op, 5
    jnz %t, @dup, @c7
@c7
    %t =w ceql %op, 7
    jnz %t, @drop, @c8
@c8
    %pb =l sub %top, 8
    %pa2 =l sub %top, 16
    %b =l loadl %pb
    %a =l loadl %pa2
    %t =w ceql %op, 8
    jnz %t, @over, @c6
@c6
    %t =w ceql %op, 6
    jnz %t, @swap, @c2
@c2
    %t =w ceql %op, 2
    jnz %t, @add, @c3
@c3
    %t =w ceql %op, 3
    jnz %t, @sub, @c4
@c4
    %t =w ceql %op, 4
    jnz %t, @mul, @lt
@add
    %r =l add %a, %b
    jmp @bin
@sub
    %r =l sub %a, %b
    jmp @bin
@mul
    %r =l mul %a, %b
    jmp @bin
@lt
    %lw =w csltl %a, %b
    %r =l extuw %lw
@bin
    storel %r, %pa2
    %sp =l sub %sp, 1
    jmp @next
@swap
    storel %a, %pb
    storel %b, %pa2
    jmp @next
@over
    storel %a, %top
    %sp =l add %sp, 1
    jmp @next
@dup
    %pb2 =l sub %top, 8
    %d =l loadl %pb2
    storel %d, %top
    %sp =l add %sp, 1
    jmp @next
@drop
    %sp =l sub %sp, 1
@next
    %pc =l add %pc, 1
    jmp @fetch
@push
    %pv =l add %pa, 8
    %v =l loadl %pv
    %so =l mul %sp, 8
    %sa =l add %stack, %so
    storel %v, %sa
    %sp =l add %sp, 1
    %pc =l add %pc, 2
    jmp @fetch
@jz
    %sp =l sub %sp, 1
    %so2 =l mul %sp, 8
    %sa2 =l add %stack, %so2
    %v2 =l loadl %sa2
    %z =w ceql %v2, 0
    jnz %z, @jmp, @jznot
@jznot
    %pc =l add %pc, 2
    jmp @fetch
@jmp
    %pt =l add %pa, 8
    %pc =l loadl %pt
    jmp @fetch
@stop
    %res =l copy 0
    %nz =w cnel %sp, 0
    jnz %nz, @gettop, @zero
@gettop
    %so3 =l mul %sp, 8
    %sa3 =l add %stack, %so3
    %sa3 =l sub %sa3, 8
    %res =l loadl %sa3
@zero
    %c =w csltl %sp, %cap
    jnz %c, @zbody, @done
@zbody
    %so4 =l mul %sp, 8
    %sa4 =l add %stack, %so4
    storel 0, %sa4
    %sp =l add %sp, 1
    jmp @zero
@done
    ret %res
}
""",
    "qsort_pairs": """export function l $qsort_pairs(l %a0, l %n0) {
@start
    %a =l copy %a0
    %n =l copy %n0
@top
    %c =w csgtl %n, 1
    jnz %c, @init, @ret
@init
    %last =l sub %n, 1
    %lo =l mul %last, 16
    %pl =l add %a, %lo
    %pk =l loadl %pl
    %plv =l add %pl, 8
    %pv =l loadl %plv
    %pi =l copy %a
    %pj =l copy %a
@part
    %c2 =w cultl %pj, %pl
    jnz %c2, @body, @pdone
@body
    %k =l loadl %pj
    %pjv =l add %pj, 8
    %v =l loadl %pjv
    %lt =w csltl %k, %pk
    jnz %lt, @swap, @eqk
@eqk
    %eq =w ceql %k, %pk
    jnz %eq, @cmpv, @next
@cmpv
    %ltv =w csltl %v, %pv
    jnz %ltv, @swap, @next
@swap
    %piv =l add %pi, 8
    %ik =l loadl %pi
    %iv =l loadl %piv
    storel %k, %pi
    storel %v, %piv
    storel %ik, %pj
    storel %iv, %pjv
    %pi =l add %pi, 16
@next
    %pj =l add %pj, 16
    jmp @part
@pdone
    %piv2 =l add %pi, 8
    %ik2 =l loadl %pi
    %iv2 =l loadl %piv2
    storel %pk, %pi
    storel %pv, %piv2
    storel %ik2, %pl
    storel %iv2, %plv
    %d =l sub %pi, %a
    %i =l shr %d, 4
    %r =l call $qsort_pairs(l %a, l %i)
    %a =l add %pi, 16
    %n =l sub %last, %i
    jmp @top
@ret
    ret 0
}
""",
    "base64_encode": """export function l $base64_encode(l %src, l %n, l %out) {
@start
    %i =l copy 0
    %o =l copy 0
@group
    %c =w csltl %i, %n
    jnz %c, @g1, @done
@g1
    %k =l sub %n, %i
    %big =w csgtl %k, 3
    jnz %big, @k3, @load
@k3
    %k =l copy 3
@load
    %p =l add %src, %i
    %b0 =l loadub %p
    %v =l shl %b0, 16
    %one =w ceql %k, 1
    jnz %one, @chars, @l1
@l1
    %p1 =l add %p, 1
    %b1 =l loadub %p1
    %b1 =l shl %b1, 8
    %v =l or %v, %b1
    %two =w ceql %k, 2
    jnz %two, @chars, @l2
@l2
    %p2 =l add %p, 2
    %b2 =l loadub %p2
    %v =l or %v, %b2
@chars
    %j =l copy 0
    %sh =l copy 18
@char
    %pad =w csgtl %j, %k
    jnz %pad, @pad, @map
@map
    %x =l shr %v, %sh
    %x =l and %x, 63
    %u =w cultl %x, 26
    jnz %u, @upper, @m2
@m2
    %lw =w cultl %x, 52
    jnz %lw, @lower, @m3
@m3
    %d =w cultl %x, 62
    jnz %d, @digit, @m4
@m4
    %pl =w ceql %x, 62
    jnz %pl, @plus, @slash
@upper
    %ch =l add %x, 65
    jmp @store
@lower
    %ch =l add %x, 71
    jmp @store
@digit
    %ch =l sub %x, 4
    jmp @store
@plus
    %ch =l copy 43
    jmp @store
@slash
    %ch =l copy 47
    jmp @store
@pad
    %ch =l copy 61
@store
    %q =l add %out, %o
    storeb %ch, %q
    %o =l add %o, 1
    %j =l add %j, 1
    %sh =l sub %sh, 6
    %more =w csltl %j, 4
    jnz %more, @char, @next
@next
    %i =l add %i, 3
    jmp @group
@done
    ret %o
}
""",
    "xxh64": _qxxh(),
    "packbits_encode": """export function l $packbits_encode(l %src, l %n, l %out, l %cap) {
@start
    %i =l copy 0
    %o =l copy 0
@packet
    %c =w csltl %i, %n
    jnz %c, @begin, @done
@begin
    %pi =l add %src, %i
    %b =w loadub %pi
    %r =l copy 1
@run
    %q =l add %i, %r
    %c1 =w csltl %q, %n
    jnz %c1, @run2, @rundone
@run2
    %c2 =w csltl %r, 128
    jnz %c2, @run3, @rundone
@run3
    %pq =l add %src, %q
    %x =w loadub %pq
    %same =w ceqw %x, %b
    jnz %same, @runinc, @rundone
@runinc
    %r =l add %r, 1
    jmp @run
@rundone
    %rep =w csgel %r, 3
    jnz %rep, @repeat, @lit
@repeat
    %room =l sub %cap, %o
    %fits =w csgel %room, 2
    jnz %fits, @putrep, @full
@putrep
    %h =l sub 1, %r
    %po =l add %out, %o
    storeb %h, %po
    %po1 =l add %po, 1
    storeb %b, %po1
    %o =l add %o, 2
    %i =l add %i, %r
    jmp @packet
@lit
    %j =l copy %i
@litext
    %e1 =w csltl %j, %n
    jnz %e1, @lit2, @litend
@lit2
    %len =l sub %j, %i
    %e2 =w csltl %len, 128
    jnz %e2, @lit3, @litend
@lit3
    %j2 =l add %j, 2
    %e3 =w csltl %j2, %n
    jnz %e3, @lit4, @litinc
@lit4
    %pj =l add %src, %j
    %y0 =w loadub %pj
    %pj1 =l add %pj, 1
    %y1 =w loadub %pj1
    %s1 =w ceqw %y0, %y1
    jnz %s1, @lit5, @litinc
@lit5
    %pj2 =l add %pj, 2
    %y2 =w loadub %pj2
    %s2 =w ceqw %y0, %y2
    jnz %s2, @litend, @litinc
@litinc
    %j =l add %j, 1
    jmp @litext
@litend
    %L =l sub %j, %i
    %room2 =l sub %cap, %o
    %fit2 =w csgtl %room2, %L
    jnz %fit2, @putlit, @full
@putlit
    %hl =l sub %L, 1
    %ph =l add %out, %o
    storeb %hl, %ph
    %o =l add %o, 1
@copy
    %ps =l add %src, %i
    %cb =w loadub %ps
    %pd =l add %out, %o
    storeb %cb, %pd
    %i =l add %i, 1
    %o =l add %o, 1
    %more =w csltl %i, %j
    jnz %more, @copy, @packet
@full
    ret -1
@done
    ret %o
}
""",
    "packbits_decode": """export function l $packbits_decode(l %src, l %n, l %out, l %cap) {
@start
    %k =l copy 0
    %o =l copy 0
@packet
    %c =w csltl %k, %n
    jnz %c, @head, @done
@head
    %pk =l add %src, %k
    %hw =w loadsb %pk
    %h =l extsw %hw
    %nop =w ceql %h, -128
    jnz %nop, @skip, @kind
@kind
    %neg =w csltl %h, 0
    jnz %neg, @repeat, @literal
@literal
    %end =l add %k, %h
    %end =l add %end, 2
    %bad =w csgtl %end, %n
    jnz %bad, @malformed, @linit
@linit
    %s =l add %k, 1
    %k =l copy %end
@lloop
    %f1 =w ceql %o, %cap
    jnz %f1, @full, @lput
@lput
    %ps =l add %src, %s
    %v =w loadub %ps
    %pd =l add %out, %o
    storeb %v, %pd
    %o =l add %o, 1
    %s =l add %s, 1
    %lm =w csltl %s, %k
    jnz %lm, @lloop, @packet
@repeat
    %end2 =l add %k, 2
    %bad2 =w csgtl %end2, %n
    jnz %bad2, @malformed, @rinit
@rinit
    %pv =l add %pk, 1
    %rv =w loadub %pv
    %k =l copy %end2
    %cnt =l sub 1, %h
@rloop
    %f2 =w ceql %o, %cap
    jnz %f2, @full, @rput
@rput
    %pd2 =l add %out, %o
    storeb %rv, %pd2
    %o =l add %o, 1
    %cnt =l sub %cnt, 1
    %rm =w cnel %cnt, 0
    jnz %rm, @rloop, @packet
@skip
    %k =l add %k, 1
    jmp @packet
@malformed
    ret -2
@full
    ret -1
@done
    ret %o
}
""",
    "mat_pow": """export function l $mat_pow(l %a, l %n, l %k, l %out, l %tmp) {
@start
    %nn =l mul %n, %n
    %i =l copy 0
@id
    %c =w csltl %i, %nn
    jnz %c, @idb, @pow
@idb
    %r =l div %i, %n
    %cc =l rem %i, %n
    %e =w ceql %r, %cc
    %el =l extuw %e
    %io =l mul %i, 8
    %po =l add %out, %io
    storel %el, %po
    %i =l add %i, 1
    jmp @id
@pow
    %kk =w csgtl %k, 0
    jnz %kk, @mul, @clear
@mul
    %ri =l copy 0
@row
    %cj =l copy 0
@col
    %s =l copy 0
    %t =l copy 0
@dot
    %x =l mul %ri, %n
    %x =l add %x, %t
    %x =l mul %x, 8
    %x =l add %out, %x
    %xv =l loadl %x
    %y =l mul %t, %n
    %y =l add %y, %cj
    %y =l mul %y, 8
    %y =l add %a, %y
    %yv =l loadl %y
    %pr =l mul %xv, %yv
    %s =l add %s, %pr
    %t =l add %t, 1
    %ct =w csltl %t, %n
    jnz %ct, @dot, @put
@put
    %d =l mul %ri, %n
    %d =l add %d, %cj
    %d =l mul %d, 8
    %d =l add %tmp, %d
    storel %s, %d
    %cj =l add %cj, 1
    %cc2 =w csltl %cj, %n
    jnz %cc2, @col, @nextrow
@nextrow
    %ri =l add %ri, 1
    %cr =w csltl %ri, %n
    jnz %cr, @row, @copy0
@copy0
    %m =l copy 0
@copy
    %mo =l mul %m, 8
    %ps =l add %tmp, %mo
    %pd =l add %out, %mo
    %mv =l loadl %ps
    storel %mv, %pd
    %m =l add %m, 1
    %cm =w csltl %m, %nn
    jnz %cm, @copy, @dec
@dec
    %k =l sub %k, 1
    jmp @pow
@clear
    %z =l mul %nn, 2
@zl
    %cz =w csgtl %z, 0
    jnz %cz, @zb, @done
@zb
    %z =l sub %z, 1
    %zo =l mul %z, 8
    %pz =l add %tmp, %zo
    storel 0, %pz
    jmp @zl
@done
    ret 0
}
""",
    "lcs_len": """export function l $lcs_len(l %x, l %m, l %y, l %n, l %row) {
@start
    %z =l copy 0
@zl
    %zo =l mul %z, 8
    %pz =l add %row, %zo
    storel 0, %pz
    %z =l add %z, 1
    %cz =w cslel %z, %n
    jnz %cz, @zl, @init
@init
    %i =l copy 0
@il
    %ci =w csltl %i, %m
    jnz %ci, @ib, @done
@ib
    %px =l add %x, %i
    %xi =w loadub %px
    %diag =l copy 0
    %j =l copy 1
@jl
    %cj =w cslel %j, %n
    jnz %cj, @jb, @inext
@jb
    %jo =l mul %j, 8
    %pr =l add %row, %jo
    %up =l loadl %pr
    %j1 =l sub %j, 1
    %py =l add %y, %j1
    %yj =w loadub %py
    %eq =w ceqw %xi, %yj
    jnz %eq, @match, @nomatch
@match
    %val =l add %diag, 1
    jmp @store
@nomatch
    %pl =l sub %pr, 8
    %left =l loadl %pl
    %val =l copy %up
    %gt =w csgtl %left, %up
    jnz %gt, @useleft, @store
@useleft
    %val =l copy %left
@store
    storel %val, %pr
    %diag =l copy %up
    %j =l add %j, 1
    jmp @jl
@inext
    %i =l add %i, 1
    jmp @il
@done
    %no =l mul %n, 8
    %pn =l add %row, %no
    %res =l loadl %pn
    ret %res
}
""",
    "mp_mul": """export function l $mp_mul(l %a, l %na, l %b, l %nb, l %r) {
@start
    %tot =l add %na, %nb
    %z =l copy 0
@zl
    %c =w csltl %z, %tot
    jnz %c, @zb, @zd
@zb
    %zo =l mul %z, 8
    %zp =l add %r, %zo
    storel 0, %zp
    %z =l add %z, 1
    jmp @zl
@zd
    %i =l copy 0
@il
    %ci =w csltl %i, %na
    jnz %ci, @ib, @count
@ib
    %io =l mul %i, 8
    %ap =l add %a, %io
    %x =l loadl %ap
    %ri =l add %r, %io
    %carry =l copy 0
    %j =l copy 0
@jl
    %cj =w csltl %j, %nb
    jnz %cj, @jb, @jd
@jb
    %jo =l mul %j, 8
    %bp =l add %b, %jo
    %y =l loadl %bp
    %xl =l and %x, 4294967295
    %xh =l shr %x, 32
    %yl =l and %y, 4294967295
    %yh =l shr %y, 32
    %ll =l mul %xl, %yl
    %lh =l mul %xl, %yh
    %hl =l mul %xh, %yl
    %hh =l mul %xh, %yh
    %t =l shr %ll, 32
    %t =l add %t, %lh
    %tl =l and %t, 4294967295
    %th =l shr %t, 32
    %u =l add %tl, %hl
    %uh =l shr %u, 32
    %hi =l add %hh, %th
    %hi =l add %hi, %uh
    %lo =l mul %x, %y
    %lo =l add %lo, %carry
    %cy =w cultl %lo, %carry
    %cyl =l extuw %cy
    %hi =l add %hi, %cyl
    %rp =l add %ri, %jo
    %rv =l loadl %rp
    %lo =l add %lo, %rv
    %cy2 =w cultl %lo, %rv
    %cyl2 =l extuw %cy2
    %hi =l add %hi, %cyl2
    storel %lo, %rp
    %carry =l copy %hi
    %j =l add %j, 1
    jmp @jl
@jd
    %no =l mul %nb, 8
    %rp2 =l add %ri, %no
    storel %carry, %rp2
    %i =l add %i, 1
    jmp @il
@count
    %k =l copy %tot
@kl
    %ck =w cnel %k, 0
    jnz %ck, @kb, @ret
@kb
    %km =l sub %k, 1
    %ko =l mul %km, 8
    %kp =l add %r, %ko
    %kv =l loadl %kp
    %nz =w cnel %kv, 0
    jnz %nz, @ret, @kdec
@kdec
    %k =l copy %km
    jmp @kl
@ret
    ret %k
}
""",
}


STREAM = {"x86_64": X86, "aarch64": A64}


def correct(path: str, isa: str, task: str) -> str:
    """The canned correct response text for a cell."""
    if path == "stream":
        return STREAM[isa][task]
    if path == "structured":
        return json.dumps(to_structured(isa, STREAM[isa][task]))
    if path == "ir":
        return QBE[task]
    raise ValueError(path)


# -- deliberately wrong answers: (task, path, isa, text, expected category, message fragment) --
# Structured entries hold assembly text; the test converts it with to_structured.

XXH64_X86_IMM64 = X86["xxh64"].replace(
    f"    mov rdx, {P4:#x}\n    add rax, rdx\n    add rcx, 8\n", f"    add rax, {P4:#x}\n    add rcx, 8\n")
XXH64_A64_ROL = A64["xxh64"].replace("ror x8, x8, #41", "rol x8, x8, #23")

WRONG = [
    # Stream early abort deep into a long answer: x86 has no 64-bit immediate for add (line 99).
    ("xxh64", "stream", "x86_64", XXH64_X86_IMM64, "syntax", "line 99"),
    # AArch64 has no rol (rotate left is ror by 64 - r): rejected at line 103.
    ("xxh64", "stream", "aarch64", XXH64_A64_ROL, "syntax", "line 103"),
    # Off-by-one capacity check (jg instead of je): writes out[cap].
    ("packbits_decode", "stream", "x86_64", X86["packbits_decode"].replace(
        "    cmp r9, rcx\n    je .Lfull\n    mov byte ptr [rdx + r9], al\n    inc r9\n    dec r11\n",
        "    cmp r9, rcx\n    jg .Lfull\n    mov byte ptr [rdx + r9], al\n    inc r9\n    dec r11\n"),
     "oob", "mov byte ptr [rdx + r9], al"),
    # 32-bit sign extension (ldrsb w6) tested as a 64-bit value: negative headers look like literals.
    ("packbits_decode", "stream", "aarch64", A64["packbits_decode"].replace("ldrsb x6, [x0, x4]", "ldrsb w6, [x0, x4]"),
     "wrong_result", "packbits_decode(src="),
    # x21 used across the recursive call without being saved.
    ("qsort_pairs", "stream", "aarch64", A64["qsort_pairs"].replace("    str x21, [sp, #32]\n", "")
     .replace("    ldr x21, [sp, #32]\n", ""), "abi", "callee-saved register x21"),
    # Two pushes instead of three: rsp is 8 mod 16 at the recursive call.
    ("qsort_pairs", "stream", "x86_64", X86["qsort_pairs"].replace("    push r13\n", "").replace("    pop r13\n", ""),
     "misaligned_call", "call qsort_pairs"),
    # Recursion through a label after the entry (a "helper"), not the entry point itself.
    ("qsort_pairs", "structured", "x86_64",
     X86["qsort_pairs"].replace("qsort_pairs:\n", "qsort_pairs:\n    nop\n.Lqs:\n")
     .replace("call qsort_pairs", "call .Lqs"),
     "requirement", "recursive"),
    # Loads all three bytes of the final group even when fewer remain.
    ("base64_encode", "stream", "aarch64", A64["base64_encode"].replace("    cmp x5, #1\n    b.eq .Lbytes\n", "")
     .replace("    cmp x5, #2\n    b.eq .Lbytes\n", ""), "oob_read", "ldrb w9, [x10, #1]"),
    # Drops the carry out of the second 128-bit addition.
    ("mp_mul", "stream", "x86_64", X86["mp_mul"].replace(
        "    add rax, qword ptr [r14 + r13*8]\n    adc rdx, 0\n", "    add rax, qword ptr [r14 + r13*8]\n"),
     "wrong_result", "mp_mul(a="),
    # Leaves the scratch buffer dirty (the spec requires it to be all zero on return).
    ("mat_pow", "ir", "aarch64", QBE["mat_pow"].replace("    %z =l mul %nn, 2\n", "    %z =l copy 0\n"),
     "wrong_result", "memory ["),
    # Never decrements k: loops forever for k > 0.
    ("mat_pow", "stream", "aarch64", A64["mat_pow"].replace("    sub x2, x2, #1\n", ""), "hang", "did not return"),
    # Leaves stale entries above the final stack depth.
    ("vm_run", "stream", "aarch64", A64["vm_run"].replace("    str xzr, [x2, x5, lsl #3]\n", ""),
     "wrong_result", "vm_run(code="),
    # rbx and r12 used without saving them.
    ("lcs_len", "structured", "x86_64", X86["lcs_len"].replace("    push rbx\n    push r12\n", "")
     .replace("    pop r12\n    pop rbx\n", ""), "abi", "callee-saved register"),
]
