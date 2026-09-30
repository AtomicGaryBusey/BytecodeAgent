// exit 0 iff all 64 KiB of .bss (and the aligned block) are zero and .data holds its initial values
    .globl _start
    .text
_start:
    adrp x1, zeros
    add x1, x1, :lo12:zeros
    mov x2, #65536
    mov x0, #0
1:  ldrb w3, [x1], #1
    orr x0, x0, x3
    subs x2, x2, #1
    b.ne 1b
    adrp x1, cblock
    add x1, x1, :lo12:cblock
    mov x2, #256
2:  ldrb w3, [x1], #1
    orr x0, x0, x3
    subs x2, x2, #1
    b.ne 2b
    cbnz x0, .Lbad
    adrp x1, magic
    ldr x1, [x1, :lo12:magic]
    ldr x2, =0x1122334455667788
    cmp x1, x2
    b.ne .Lbad
    mov x0, #0
    mov x8, #93
    svc #0
.Lbad:
    mov x0, #1
    mov x8, #93
    svc #0

    .data
    .p2align 3
magic:
    .quad 0x1122334455667788
    .bss
    .p2align 4
zeros:
    .zero 65536
    .balign 32
cblock: .zero 256
