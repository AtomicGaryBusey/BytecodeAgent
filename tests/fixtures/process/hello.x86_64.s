# write "hello, world\n" to stdout, exit 0
    .globl _start
    .text
_start:
    mov eax, 1              # write
    mov edi, 1
    lea rsi, [rip + msg]
    mov edx, OFFSET msglen
    syscall
    mov eax, 60             # exit
    xor edi, edi
    syscall

    .section .rodata
msg:
    .ascii "hello, world\n"
    msglen = . - msg
