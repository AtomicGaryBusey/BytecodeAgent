# copy stdin to stdout through a 4 KiB .bss buffer, handling short reads and writes
    .globl _start
    .text
_start:
.Lread:
    xor eax, eax            # read(0, buf, 4096)
    xor edi, edi
    lea rsi, [rip + buf]
    mov edx, 4096
    syscall
    test rax, rax
    jle .Ldone
    mov rbx, rax            # bytes left to write
    lea r12, [rip + buf]
.Lwrite:
    mov eax, 1              # write(1, r12, rbx)
    mov edi, 1
    mov rsi, r12
    mov rdx, rbx
    syscall
    add r12, rax
    sub rbx, rax
    jnz .Lwrite
    jmp .Lread
.Ldone:
    mov eax, 231            # exit_group(0)
    xor edi, edi
    syscall

    .bss
buf:
    .zero 4096
