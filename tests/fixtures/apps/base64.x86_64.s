# base64: encode stdin, lines of 76 characters, each ending in a newline.
        .text
        .globl _start
_start:
        lea     rbx, [rip+inbuf]
        xor     r12d, r12d              # bytes read so far
.Lread:
        xor     eax, eax                # read
        xor     edi, edi
        lea     rsi, [rbx+r12]
        mov     edx, 0x10000
        sub     rdx, r12
        syscall
        test    rax, rax
        jle     .Lgot
        add     r12, rax
        jmp     .Lread
.Lgot:
        lea     rbp, [rip+outbuf]
        mov     rdi, rbp                # output cursor
        lea     r13, [rip+alphabet]
        xor     r14d, r14d              # column
        mov     rsi, rbx                # input pointer
        lea     r15, [rbx+r12]          # input end
.Lgroup:
        mov     r9, r15
        sub     r9, rsi
        cmp     r9, 3
        jb      .Ltail
        movzx   r10d, byte ptr [rsi]
        shl     r10d, 16
        movzx   eax, byte ptr [rsi+1]
        shl     eax, 8
        or      r10d, eax
        movzx   eax, byte ptr [rsi+2]
        or      r10d, eax
        add     rsi, 3
        mov     ecx, 18
        call    emit6
        mov     ecx, 12
        call    emit6
        mov     ecx, 6
        call    emit6
        xor     ecx, ecx
        call    emit6
        jmp     .Lgroup
.Ltail:
        test    r9, r9
        jz      .Lend
        movzx   r10d, byte ptr [rsi]
        shl     r10d, 16
        cmp     r9, 2
        jne     1f
        movzx   eax, byte ptr [rsi+1]
        shl     eax, 8
        or      r10d, eax
1:      mov     ecx, 18
        call    emit6
        mov     ecx, 12
        call    emit6
        cmp     r9, 2
        jne     2f
        mov     ecx, 6
        call    emit6
        jmp     3f
2:      mov     al, 61                  # '='
        call    putc
3:      mov     al, 61
        call    putc
.Lend:
        test    r14, r14
        jz      .Lwrite
        mov     byte ptr [rdi], 10
        inc     rdi
.Lwrite:
        mov     rdx, rdi
        sub     rdx, rbp
        mov     rsi, rbp
        call    write_all
        mov     eax, 60                 # exit
        xor     edi, edi
        syscall

# emit6: output alphabet[(r10d >> cl) & 63]
emit6:
        mov     eax, r10d
        shr     eax, cl
        and     eax, 63
        mov     al, byte ptr [r13+rax]
# putc: output al, wrapping after 76 characters
putc:
        mov     byte ptr [rdi], al
        inc     rdi
        inc     r14
        cmp     r14, 76
        jne     1f
        mov     byte ptr [rdi], 10
        inc     rdi
        xor     r14d, r14d
1:      ret

# write_all: rsi = buffer, rdx = length
write_all:
1:      test    rdx, rdx
        jz      2f
        mov     eax, 1                  # write
        mov     edi, 1
        syscall
        test    rax, rax
        jle     2f
        add     rsi, rax
        sub     rdx, rax
        jmp     1b
2:      ret

        .section .rodata
alphabet:
        .ascii  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

        .bss
inbuf:  .zero   0x10000
outbuf: .zero   0x18000
