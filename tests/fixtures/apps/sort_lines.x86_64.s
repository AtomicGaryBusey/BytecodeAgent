# sort_lines: print the input lines in ascending unsigned byte-wise order.
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
        # split into (pointer, length) entries
        lea     r13, [rip+lines]
        xor     r14d, r14d              # line count
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
        call    addline
        inc     r10
        mov     r8, r10
        jmp     .Lscan
.Leof:
        cmp     r8, r9
        je      .Lsort
        call    addline                 # final line without newline
.Lsort:
        # insertion sort of the entries
        mov     r15d, 1                 # i
.Li:
        cmp     r15, r14
        jae     .Lemit
        mov     rax, r15
        shl     rax, 4
        mov     r8, qword ptr [r13+rax]         # key pointer
        mov     r9, qword ptr [r13+rax+8]       # key length
        lea     r12, [r15-1]            # j
.Lj:
        test    r12, r12
        js      .Lins
        mov     rbp, r12
        shl     rbp, 4
        add     rbp, r13                # &entry[j]
        mov     rsi, qword ptr [rbp]
        mov     rdx, qword ptr [rbp+8]
        call    greater
        test    eax, eax
        jz      .Lins
        mov     rsi, qword ptr [rbp]
        mov     rdx, qword ptr [rbp+8]
        mov     qword ptr [rbp+16], rsi
        mov     qword ptr [rbp+24], rdx
        dec     r12
        jmp     .Lj
.Lins:
        mov     rbp, r12
        shl     rbp, 4
        add     rbp, r13
        mov     qword ptr [rbp+16], r8
        mov     qword ptr [rbp+24], r9
        inc     r15
        jmp     .Li
.Lemit:
        lea     rbp, [rip+outbuf]
        mov     rdi, rbp                # output cursor
        xor     ecx, ecx
.Lout:
        cmp     rcx, r14
        jae     .Lwrite
        mov     rax, rcx
        shl     rax, 4
        mov     rsi, qword ptr [r13+rax]
        mov     rdx, qword ptr [r13+rax+8]
1:      test    rdx, rdx
        jz      2f
        mov     al, byte ptr [rsi]
        mov     byte ptr [rdi], al
        inc     rsi
        inc     rdi
        dec     rdx
        jmp     1b
2:      mov     byte ptr [rdi], 10
        inc     rdi
        inc     rcx
        jmp     .Lout
.Lwrite:
        mov     rdx, rdi
        sub     rdx, rbp
        mov     rsi, rbp
        call    write_all
        mov     eax, 60                 # exit
        xor     edi, edi
        syscall

# addline: append entry (r8, r10 - r8)
addline:
        mov     rax, r14
        shl     rax, 4
        mov     qword ptr [r13+rax], r8
        mov     rdx, r10
        sub     rdx, r8
        mov     qword ptr [r13+rax+8], rdx
        inc     r14
        ret

# greater: rsi/rdx = line a (ptr, len), r8/r9 = line b; eax = 1 if a sorts after b
greater:
        mov     rcx, rdx
        cmp     rcx, r9
        jbe     1f
        mov     rcx, r9                 # common length
1:      xor     r10d, r10d
2:      cmp     r10, rcx
        jae     3f
        movzx   eax, byte ptr [rsi+r10]
        cmp     al, byte ptr [r8+r10]
        jne     4f
        inc     r10
        jmp     2b
3:      cmp     rdx, r9                 # equal prefix: the longer one is greater
4:      seta    al
        movzx   eax, al
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
lines:  .zero   0x10000                 # 4096 entries of 16 bytes
inbuf:  .zero   0x10000
outbuf: .zero   0x10010
