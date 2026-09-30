# rev_lines: print every input line reversed.
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
        mov     r8, rbx                 # line start
        lea     r9, [rbx+r12]           # input end
        mov     r10, rbx                # scan
.Lscan:
        cmp     r10, r9
        jae     .Leof
        cmp     byte ptr [r10], 10
        je      .Lnl
        inc     r10
        jmp     .Lscan
.Lnl:
        call    emit
        inc     r10
        mov     r8, r10
        jmp     .Lscan
.Leof:
        cmp     r8, r9
        je      .Lout
        call    emit                    # final line without newline
.Lout:
        mov     rdx, rdi
        sub     rdx, rbp
        mov     rsi, rbp
        call    write_all
        mov     eax, 60                 # exit
        xor     edi, edi
        syscall

# emit: copy [r8, r10) reversed to the cursor rdi, then a newline
emit:
        mov     r11, r10
1:      cmp     r11, r8
        jbe     2f
        dec     r11
        mov     al, byte ptr [r11]
        mov     byte ptr [rdi], al
        inc     rdi
        jmp     1b
2:      mov     byte ptr [rdi], 10
        inc     rdi
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
inbuf:  .zero   0x20000
outbuf: .zero   0x20010
