# BROKEN (hexdump broken2): prints 0x7f as itself instead of '.'
# hexdump: canonical hex+ASCII dump (hexdump -C layout, no squeezing).
        .text
        .globl _start
_start:
        lea     rbx, [rip+inbuf]
        xor     r12d, r12d              # bytes read so far
.Lread:
        xor     eax, eax                # read
        xor     edi, edi
        lea     rsi, [rbx+r12]
        mov     edx, 0x20000
        sub     rdx, r12
        syscall
        test    rax, rax
        jle     .Lgot
        add     r12, rax
        jmp     .Lread
.Lgot:
        lea     rbp, [rip+outbuf]
        mov     rdi, rbp                # output cursor
        lea     r13, [rip+hexdigits]
        xor     r14d, r14d              # row offset
.Lrow:
        cmp     r14, r12
        jae     .Lfinal
        mov     r15, r12
        sub     r15, r14
        cmp     r15, 16
        jbe     1f
        mov     r15d, 16                # n = min(16, remaining)
1:      lea     r10, [rbx+r14]          # row pointer
        mov     rax, r14
        call    hex8
        mov     word ptr [rdi], 0x2020
        add     rdi, 2
        xor     ecx, ecx                # i
.Lhex:
        cmp     rcx, r15
        jae     .Lpad
        movzx   eax, byte ptr [r10+rcx]
        mov     edx, eax
        shr     edx, 4
        mov     dl, byte ptr [r13+rdx]
        mov     byte ptr [rdi], dl
        and     eax, 15
        mov     al, byte ptr [r13+rax]
        mov     byte ptr [rdi+1], al
        mov     byte ptr [rdi+2], 32
        add     rdi, 3
        jmp     .Lsep
.Lpad:
        mov     word ptr [rdi], 0x2020
        mov     byte ptr [rdi+2], 32
        add     rdi, 3
.Lsep:
        cmp     rcx, 7
        jne     1f
        mov     byte ptr [rdi], 32
        inc     rdi
1:      inc     rcx
        cmp     rcx, 16
        jb      .Lhex
        mov     word ptr [rdi], 0x7c20  # " |"
        add     rdi, 2
        xor     ecx, ecx
.Lchr:
        movzx   eax, byte ptr [r10+rcx]
        lea     edx, [rax-32]
        cmp     edx, 95                 # 0x20..0x7e
        jbe     1f
        mov     eax, 46                 # '.'
1:      mov     byte ptr [rdi], al
        inc     rdi
        inc     rcx
        cmp     rcx, r15
        jb      .Lchr
        mov     word ptr [rdi], 0x0a7c  # "|\n"
        add     rdi, 2
        add     r14, 16
        jmp     .Lrow
.Lfinal:
        mov     rax, r12
        call    hex8
        mov     byte ptr [rdi], 10
        inc     rdi
        mov     rdx, rdi
        sub     rdx, rbp
        mov     rsi, rbp
        call    write_all
        mov     eax, 60                 # exit
        xor     edi, edi
        syscall

# hex8: rax as 8 lowercase hex digits at the cursor rdi; clobbers rcx, rdx, r8
hex8:
        mov     ecx, 28
1:      mov     rdx, rax
        shr     rdx, cl
        and     edx, 15
        mov     r8b, byte ptr [r13+rdx]
        mov     byte ptr [rdi], r8b
        inc     rdi
        sub     ecx, 4
        jge     1b
        ret

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
hexdigits:
        .ascii  "0123456789abcdef"

        .bss
inbuf:  .zero   0x20000
outbuf: .zero   0x60000
