# rpn: evaluate one RPN expression (int64, + - * / %) per line.
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
        lea     r15, [rbx+r12]          # input end
        lea     rbp, [rip+outbuf]
        mov     rdi, rbp                # output cursor
        lea     r13, [rip+stack]
        mov     r8, rbx                 # current position
.Lline:
        cmp     r8, r15
        jae     .Lfinish
        mov     r9, r8                  # find the end of this line
1:      cmp     r9, r15
        jae     2f
        cmp     byte ptr [r9], 10
        je      2f
        inc     r9
        jmp     1b
2:      xor     r14d, r14d              # stack depth
.Ltok:
        cmp     r8, r9
        jae     .Lend
        movzx   eax, byte ptr [r8]
        cmp     eax, 32
        je      .Lskip
        cmp     eax, 9
        je      .Lskip
        mov     r10, r8                 # token is [r8, r10)
3:      inc     r10
        cmp     r10, r9
        jae     4f
        movzx   ecx, byte ptr [r10]
        cmp     ecx, 32
        je      4f
        cmp     ecx, 9
        je      4f
        jmp     3b
4:      mov     rcx, r10
        sub     rcx, r8
        cmp     rcx, 1
        jne     .Lnum
        cmp     eax, 43
        je      .Lop
        cmp     eax, 45
        je      .Lop
        cmp     eax, 42
        je      .Lop
        cmp     eax, 47
        je      .Lop
        cmp     eax, 37
        je      .Lop
.Lnum:
        mov     rsi, r8
        xor     r11d, r11d              # negative?
        cmp     eax, 45
        jne     5f
        mov     r11d, 1
        inc     rsi
5:      mov     rcx, r10
        sub     rcx, rsi                # digit count
        jz      .Lerr
        cmp     rcx, 18
        ja      .Lerr
        xor     edx, edx                # value
6:      movzx   eax, byte ptr [rsi]
        inc     rsi
        sub     eax, 48
        cmp     eax, 9
        ja      .Lerr
        imul    rdx, rdx, 10
        add     rdx, rax
        cmp     rsi, r10
        jb      6b
        test    r11, r11
        jz      7f
        neg     rdx
7:      mov     qword ptr [r13+r14*8], rdx
        inc     r14
        mov     r8, r10
        jmp     .Ltok
.Lskip:
        inc     r8
        jmp     .Ltok
.Lop:
        cmp     r14, 2
        jb      .Lerr
        dec     r14
        mov     rcx, qword ptr [r13+r14*8]      # b
        lea     r11, [r14-1]
        mov     rsi, qword ptr [r13+r11*8]      # a
        cmp     eax, 43
        jne     1f
        add     rsi, rcx
        jmp     .Lpush
1:      cmp     eax, 45
        jne     1f
        sub     rsi, rcx
        jmp     .Lpush
1:      cmp     eax, 42
        jne     1f
        imul    rsi, rcx
        jmp     .Lpush
1:      test    rcx, rcx
        jz      .Lerr                   # / and %: division by zero
        cmp     rcx, -1
        jne     2f
        mov     rdx, 0x8000000000000000
        cmp     rsi, rdx
        je      .Lerr                   # INT64_MIN / -1
2:      mov     r12d, eax               # keep the operator
        mov     rax, rsi
        cqo
        idiv    rcx
        mov     rsi, rax
        cmp     r12d, 47
        je      .Lpush
        mov     rsi, rdx                # remainder
.Lpush:
        mov     qword ptr [r13+r11*8], rsi
        mov     r8, r10
        jmp     .Ltok
.Lend:
        cmp     r14, 1
        jne     .Lerr
        mov     rax, qword ptr [r13]
        call    putint
        mov     byte ptr [rdi], 10
        inc     rdi
        jmp     .Lnext
.Lerr:
        mov     dword ptr [rdi], 0x6f727265     # "erro"
        mov     word ptr [rdi+4], 0x0a72        # "r\n"
        add     rdi, 6
.Lnext:
        lea     r8, [r9+1]              # skip past the newline
        jmp     .Lline
.Lfinish:
        mov     rdx, rdi
        sub     rdx, rbp
        mov     rsi, rbp
        call    write_all
        mov     eax, 60                 # exit
        xor     edi, edi
        syscall

# putint: signed rax in decimal at the cursor rdi; clobbers rcx, rdx, rsi, r11
putint:
        test    rax, rax
        jns     0f
        mov     byte ptr [rdi], 45
        inc     rdi
        neg     rax                     # INT64_MIN stays 2^63 as unsigned
0:      sub     rsp, 32
        lea     rsi, [rsp+32]
        mov     ecx, 10
1:      xor     edx, edx
        div     rcx
        add     edx, 48
        dec     rsi
        mov     byte ptr [rsi], dl
        test    rax, rax
        jnz     1b
        lea     r11, [rsp+32]
2:      mov     dl, byte ptr [rsi]
        mov     byte ptr [rdi], dl
        inc     rsi
        inc     rdi
        cmp     rsi, r11
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
        .balign 16
stack:  .zero   0x2000
inbuf:  .zero   0x20000
outbuf: .zero   0x80000
