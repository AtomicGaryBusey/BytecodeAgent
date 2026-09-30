# recursive functions across sections: prints fib(N) for N read as one decimal line from stdin;
# with input "deep" it recurses forever (stack overflow)
    .globl _start
    .text
_start:
    xor eax, eax
    xor edi, edi
    lea rsi, [rip + inbuf]
    mov edx, 64
    syscall
    cmp byte ptr [rip + inbuf], 'd'
    je .Ldeep
    lea rdi, [rip + inbuf]
    call parse
    mov rdi, rax
    call fib
    mov rdi, rax
    call print_u64
    mov eax, 60
    xor edi, edi
    syscall
.Ldeep:
    call forever
    ud2

forever:
    sub rsp, 64
    call forever
    add rsp, 64
    ret

# parse decimal digits at rdi -> rax
parse:
    xor eax, eax
1:  movzx ecx, byte ptr [rdi]
    sub ecx, '0'
    cmp ecx, 9
    ja 2f
    imul rax, rax, 10
    add rax, rcx
    inc rdi
    jmp 1b
2:  ret

fib:
    cmp rdi, 2
    jb 1f
    push rbx
    push r12
    sub rsp, 8
    mov rbx, rdi
    lea rdi, [rbx - 1]
    call fib
    mov r12, rax
    lea rdi, [rbx - 2]
    call fib
    add rax, r12
    add rsp, 8
    pop r12
    pop rbx
    ret
1:  mov rax, rdi
    ret

    .section .text.util, "ax", @progbits
# print rdi as decimal + newline, using a digit table in .rodata and a jump through .data
print_u64:
    lea rsi, [rip + outbuf + 31]
    mov byte ptr [rsi], 10
    mov rax, rdi
    mov ecx, 10
    lea r8, [rip + digits]
1:  xor edx, edx
    div rcx
    dec rsi
    movzx edx, byte ptr [r8 + rdx]
    mov byte ptr [rsi], dl
    test rax, rax
    jnz 1b
    lea rdx, [rip + outbuf + 32]
    sub rdx, rsi
    mov eax, 1
    mov edi, 1
    jmp qword ptr [rip + do_syscall_ptr]

do_syscall:
    syscall
    ret

    .section .rodata
digits: .ascii "0123456789"
    .data
    .p2align 3
do_syscall_ptr: .quad do_syscall
    .bss
inbuf: .zero 64
outbuf: .zero 32
