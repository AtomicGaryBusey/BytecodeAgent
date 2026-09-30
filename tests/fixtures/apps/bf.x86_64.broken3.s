# BROKEN (bf broken3): does not reject an unclosed '['
# bf: Brainfuck interpreter; stdin = program '!' input.
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
        # program = [rbx, rbx+r11); program input = [r14, r15)
        lea     r15, [rbx+r12]
        mov     rsi, rbx
1:      cmp     rsi, r15
        jae     2f
        cmp     byte ptr [rsi], 33      # '!'
        je      3f
        inc     rsi
        jmp     1b
2:      mov     r14, r15                # no '!': empty program input
        jmp     .Lmatch
3:      lea     r14, [rsi+1]
.Lmatch:
        mov     r11, rsi
        sub     r11, rbx                # program length
        # match[i] = index of the partner bracket (u32)
        lea     r13, [rip+match]
        lea     rbp, [rip+bstack]
        xor     ecx, ecx                # index
        xor     edx, edx                # open brackets on bstack
.Lm:
        cmp     rcx, r11
        jae     .Lmdone
        movzx   eax, byte ptr [rbx+rcx]
        cmp     eax, 91                 # '['
        jne     1f
        mov     dword ptr [rbp+rdx*4], ecx
        inc     rdx
        jmp     2f
1:      cmp     eax, 93                 # ']'
        jne     2f
        test    rdx, rdx
        jz      .Lunbalanced
        dec     rdx
        mov     eax, dword ptr [rbp+rdx*4]
        mov     dword ptr [r13+rcx*4], eax
        mov     dword ptr [r13+rax*4], ecx
2:      inc     rcx
        jmp     .Lm
.Lmdone:
        test    rdx, rdx
        nop
        # run
        lea     r8, [rip+tape]          # data pointer
        lea     rbp, [rip+outbuf]
        mov     rdi, rbp                # output cursor
        xor     ecx, ecx                # pc
.Lstep:
        cmp     rcx, r11
        jae     .Lhalt
        movzx   eax, byte ptr [rbx+rcx]
        cmp     eax, 43
        je      .Lplus
        cmp     eax, 45
        je      .Lminus
        cmp     eax, 62
        je      .Lright
        cmp     eax, 60
        je      .Lleft
        cmp     eax, 46
        je      .Ldot
        cmp     eax, 44
        je      .Lcomma
        cmp     eax, 91
        je      .Lopen
        cmp     eax, 93
        je      .Lclose
.Lnext:
        inc     rcx
        jmp     .Lstep
.Lplus:
        inc     byte ptr [r8]
        jmp     .Lnext
.Lminus:
        dec     byte ptr [r8]
        jmp     .Lnext
.Lright:
        inc     r8
        jmp     .Lnext
.Lleft:
        dec     r8
        jmp     .Lnext
.Ldot:
        mov     al, byte ptr [r8]
        mov     byte ptr [rdi], al
        inc     rdi
        jmp     .Lnext
.Lcomma:
        xor     eax, eax
        cmp     r14, r15
        jae     1f
        mov     al, byte ptr [r14]
        inc     r14
1:      mov     byte ptr [r8], al
        jmp     .Lnext
.Lopen:
        cmp     byte ptr [r8], 0
        jne     .Lnext
        mov     ecx, dword ptr [r13+rcx*4]
        jmp     .Lnext
.Lclose:
        cmp     byte ptr [r8], 0
        je      .Lnext
        mov     ecx, dword ptr [r13+rcx*4]
        jmp     .Lnext
.Lhalt:
        mov     rdx, rdi
        sub     rdx, rbp
        mov     rsi, rbp
        call    write_all
        mov     eax, 60                 # exit
        xor     edi, edi
        syscall
.Lunbalanced:
        mov     eax, 60                 # exit(1), no output
        mov     edi, 1
        syscall

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
        .balign 16
match:  .zero   0x40040
bstack: .zero   0x40040
inbuf:  .zero   0x20000
tape:   .zero   30000
        .balign 16
outbuf: .zero   0x20000
