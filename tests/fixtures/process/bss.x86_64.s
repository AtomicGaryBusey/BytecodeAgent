# exit 0 iff all 64 KiB of .bss (and the aligned block) are zero and .data holds its initial values
    .globl _start
    .text
_start:
    lea rsi, [rip + zeros]
    mov ecx, 65536
    xor eax, eax
1:  or al, byte ptr [rsi]
    inc rsi
    dec ecx
    jnz 1b
    lea rsi, [rip + cblock]
    mov ecx, 256
2:  or al, byte ptr [rsi]
    inc rsi
    dec ecx
    jnz 2b
    test al, al
    jnz .Lbad
    mov rax, qword ptr [rip + magic]
    movabs rdx, 0x1122334455667788
    cmp rax, rdx
    jne .Lbad
    mov eax, 60
    xor edi, edi
    syscall
.Lbad:
    mov eax, 60
    mov edi, 1
    syscall

    .data
magic:
    .quad 0x1122334455667788
    .bss
    .p2align 4
zeros:
    .zero 65536
    .balign 32
cblock: .zero 256
