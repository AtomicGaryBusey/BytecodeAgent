// recursive functions across sections: prints fib(N) for N read as one decimal line from stdin;
// with input "deep" it recurses forever (stack overflow)
    .globl _start
    .text
_start:
    mov x0, #0
    adrp x1, inbuf
    add x1, x1, :lo12:inbuf
    mov x2, #64
    mov x8, #63
    svc #0
    adrp x1, inbuf
    ldrb w1, [x1, :lo12:inbuf]
    cmp w1, #'d'
    b.eq .Ldeep
    adrp x0, inbuf
    add x0, x0, :lo12:inbuf
    bl parse
    bl fib
    bl print_u64
    mov x0, #0
    mov x8, #93
    svc #0
.Ldeep:
    bl forever
    udf #0

forever:
    stp x29, x30, [sp, #-64]!
    bl forever
    ldp x29, x30, [sp], #64
    ret

// parse decimal digits at x0 -> x0
parse:
    mov x1, x0
    mov x0, #0
    mov x3, #10
1:  ldrb w2, [x1], #1
    sub w2, w2, #'0'
    cmp w2, #9
    b.hi 2f
    madd x0, x0, x3, x2
    b 1b
2:  ret

fib:
    cmp x0, #2
    b.lo 1f
    stp x29, x30, [sp, #-32]!
    stp x19, x20, [sp, #16]
    mov x19, x0
    sub x0, x19, #1
    bl fib
    mov x20, x0
    sub x0, x19, #2
    bl fib
    add x0, x0, x20
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #32
1:  ret

    .section .text.util, "ax", %progbits
// print x0 as decimal + newline, using a digit table in .rodata and a jump through .data
print_u64:
    adrp x1, outbuf
    add x1, x1, :lo12:outbuf
    add x1, x1, #31
    mov w2, #10
    strb w2, [x1]
    mov x3, #10
    adrp x4, digits
    add x4, x4, :lo12:digits
1:  udiv x5, x0, x3
    msub x6, x5, x3, x0
    ldrb w6, [x4, x6]
    sub x1, x1, #1
    strb w6, [x1]
    mov x0, x5
    cbnz x0, 1b
    adrp x2, outbuf
    add x2, x2, :lo12:outbuf
    add x2, x2, #32
    sub x2, x2, x1
    mov x0, #1
    mov x8, #64
    ldr x9, do_syscall_lit
    br x9

do_syscall:
    svc #0
    ret
    .p2align 3
do_syscall_lit: .quad do_syscall

    .section .rodata
digits: .ascii "0123456789"
    .bss
inbuf: .zero 64
outbuf: .zero 32
