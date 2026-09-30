// write "hello, world\n" to stdout, exit 0
    .globl _start
    .text
_start:
    mov x0, #1
    adrp x1, msg
    add x1, x1, :lo12:msg
    mov x2, #msglen
    mov x8, #64             // write
    svc #0
    mov x0, #0
    mov x8, #93             // exit
    svc #0

    .section .rodata
msg:
    .ascii "hello, world\n"
    msglen = . - msg
