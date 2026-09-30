# BROKEN (wc broken2): the byte count keeps only the last read's size
# wc: count lines, words, bytes of stdin; print "L W B\n".
        .text
        .globl _start
_start:
        lea     rbx, [rip+inbuf]
        xor     r12d, r12d              # lines
        xor     r13d, r13d              # words
        xor     r14d, r14d              # bytes
        xor     r15d, r15d              # 1 while inside a word
.Lread:
        xor     eax, eax                # read
        xor     edi, edi
        mov     rsi, rbx
        mov     edx, 4096
        syscall
        test    rax, rax
        jle     .Ldone
        mov     r14, rax
        mov     rsi, rbx
        lea     rdi, [rbx+rax]
.Lbyte:
        cmp     rsi, rdi
        jae     .Lread
        movzx   eax, byte ptr [rsi]
        inc     rsi
        cmp     eax, 10
        jne     1f
        inc     r12
1:      cmp     eax, 32
        je      .Lspace
        lea     ecx, [rax-9]
        cmp     ecx, 4                  # 9..13
        jbe     .Lspace
        test    r15, r15
        jnz     .Lbyte
        mov     r15d, 1
        inc     r13
        jmp     .Lbyte
.Lspace:
        xor     r15d, r15d
        jmp     .Lbyte
.Ldone:
        lea     rbp, [rip+outbuf]
        mov     rdi, rbp
        mov     rax, r12
        call    putnum
        mov     byte ptr [rdi], 32
        inc     rdi
        mov     rax, r13
        call    putnum
        mov     byte ptr [rdi], 32
        inc     rdi
        mov     rax, r14
        call    putnum
        mov     byte ptr [rdi], 10
        inc     rdi
        mov     rdx, rdi
        sub     rdx, rbp
        mov     rsi, rbp
        call    write_all
        mov     eax, 60                 # exit
        xor     edi, edi
        syscall

# putnum: rax = unsigned value, rdi = output cursor (advanced); clobbers rcx, rdx, r8, r9
putnum:
        sub     rsp, 32
        lea     r8, [rsp+32]
        mov     ecx, 10
1:      xor     edx, edx
        div     rcx
        add     edx, 48
        dec     r8
        mov     byte ptr [r8], dl
        test    rax, rax
        jnz     1b
        lea     r9, [rsp+32]
2:      mov     dl, byte ptr [r8]
        mov     byte ptr [rdi], dl
        inc     r8
        inc     rdi
        cmp     r8, r9
        jb      2b
        add     rsp, 32
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

        .bss
inbuf:  .zero   4096
outbuf: .zero   64
