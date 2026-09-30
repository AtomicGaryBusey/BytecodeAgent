# BROKEN (json_lint broken3): reports an unexpected end of input one byte early
# json_lint: validate one JSON document; print "ok" or "error at N".
# Registers: rbx = input base, r12 = input length, r13 = current offset.
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
        xor     r13d, r13d
        call    skip_ws
        call    value
        call    skip_ws
        cmp     r13, r12
        jb      fail_cur                # trailing bytes
        lea     rsi, [rip+okmsg]
        mov     edx, 3
        call    write_all
        mov     eax, 60                 # exit
        xor     edi, edi
        syscall

# skip_ws: advance r13 over space, tab, newline, carriage return
skip_ws:
        cmp     r13, r12
        jae     1f
        movzx   eax, byte ptr [rbx+r13]
        cmp     eax, 32
        je      2f
        cmp     eax, 9
        je      2f
        cmp     eax, 10
        je      2f
        cmp     eax, 13
        jne     1f
2:      inc     r13
        jmp     skip_ws
1:      ret

# value: parse one value starting at r13 (no leading whitespace)
value:
        cmp     r13, r12
        jae     fail_eof
        movzx   eax, byte ptr [rbx+r13]
        cmp     eax, 123                # '{'
        je      .Lv_obj
        cmp     eax, 91                 # '['
        je      .Lv_arr
        cmp     eax, 34                 # '"'
        je      .Lv_str
        lea     rsi, [rip+w_true]
        cmp     eax, 116                # 't'
        je      literal
        lea     rsi, [rip+w_false]
        cmp     eax, 102                # 'f'
        je      literal
        lea     rsi, [rip+w_null]
        cmp     eax, 110                # 'n'
        je      literal
        cmp     eax, 45                 # '-'
        je      number
        lea     ecx, [rax-48]
        cmp     ecx, 9
        jbe     number
        jmp     fail_cur
.Lv_obj:
        inc     r13
        jmp     object
.Lv_arr:
        inc     r13
        jmp     array
.Lv_str:
        inc     r13
        jmp     string

# literal: match the NUL-terminated word at rsi
literal:
        movzx   ecx, byte ptr [rsi]
        test    ecx, ecx
        jz      1f
        cmp     r13, r12
        jae     fail_eof
        cmp     byte ptr [rbx+r13], cl
        jne     fail_cur
        inc     r13
        inc     rsi
        jmp     literal
1:      ret

# string: r13 just after the opening quote; consumes through the closing quote
string:
        cmp     r13, r12
        jae     fail_eof
        movzx   eax, byte ptr [rbx+r13]
        cmp     eax, 34
        je      .Ls_end
        cmp     eax, 32
        jb      fail_cur                # raw control byte
        cmp     eax, 92                 # '\'
        je      .Ls_esc
        inc     r13
        jmp     string
.Ls_end:
        inc     r13
        ret
.Ls_esc:
        inc     r13
        cmp     r13, r12
        jae     fail_eof
        movzx   eax, byte ptr [rbx+r13]
        lea     rsi, [rip+escapes]
1:      movzx   ecx, byte ptr [rsi]
        test    ecx, ecx
        jz      2f
        inc     rsi
        cmp     eax, ecx
        jne     1b
        inc     r13                     # simple escape
        jmp     string
2:      cmp     eax, 117                # 'u'
        jne     fail_cur
        inc     r13
        mov     edx, 4
3:      cmp     r13, r12
        jae     fail_eof
        movzx   eax, byte ptr [rbx+r13]
        lea     ecx, [rax-48]
        cmp     ecx, 9
        jbe     4f
        or      eax, 0x20
        sub     eax, 97
        cmp     eax, 5
        ja      fail_cur
4:      inc     r13
        dec     edx
        jnz     3b
        jmp     string

# number: -? (0 | [1-9][0-9]*) (. [0-9]+)? ([eE] [+-]? [0-9]+)?
number:
        movzx   eax, byte ptr [rbx+r13]
        cmp     eax, 45
        jne     1f
        inc     r13
        cmp     r13, r12
        jae     fail_eof
        movzx   eax, byte ptr [rbx+r13]
1:      cmp     eax, 48
        jne     2f
        inc     r13                     # a lone leading 0
        jmp     .Ln_frac
2:      lea     ecx, [rax-49]
        cmp     ecx, 8
        ja      fail_cur
3:      inc     r13
        cmp     r13, r12
        jae     .Ln_ret
        movzx   eax, byte ptr [rbx+r13]
        lea     ecx, [rax-48]
        cmp     ecx, 9
        jbe     3b
.Ln_frac:
        cmp     r13, r12
        jae     .Ln_ret
        movzx   eax, byte ptr [rbx+r13]
        cmp     eax, 46                 # '.'
        jne     .Ln_exp
        inc     r13
        cmp     r13, r12
        jae     fail_eof
        movzx   eax, byte ptr [rbx+r13]
        lea     ecx, [rax-48]
        cmp     ecx, 9
        ja      fail_cur
4:      inc     r13
        cmp     r13, r12
        jae     .Ln_ret
        movzx   eax, byte ptr [rbx+r13]
        lea     ecx, [rax-48]
        cmp     ecx, 9
        jbe     4b
.Ln_exp:
        or      eax, 0x20
        cmp     eax, 101                # 'e' or 'E'
        jne     .Ln_ret
        inc     r13
        cmp     r13, r12
        jae     fail_eof
        movzx   eax, byte ptr [rbx+r13]
        cmp     eax, 43
        je      5f
        cmp     eax, 45
        jne     6f
5:      inc     r13
        cmp     r13, r12
        jae     fail_eof
        movzx   eax, byte ptr [rbx+r13]
6:      lea     ecx, [rax-48]
        cmp     ecx, 9
        ja      fail_cur
7:      inc     r13
        cmp     r13, r12
        jae     .Ln_ret
        movzx   eax, byte ptr [rbx+r13]
        lea     ecx, [rax-48]
        cmp     ecx, 9
        jbe     7b
.Ln_ret:
        ret

# array: r13 just after '['
array:
        call    skip_ws
        cmp     r13, r12
        jae     fail_eof
        cmp     byte ptr [rbx+r13], 93  # ']'
        je      .La_close
.La_elem:
        call    value
        call    skip_ws
        cmp     r13, r12
        jae     fail_eof
        movzx   eax, byte ptr [rbx+r13]
        cmp     eax, 93
        je      .La_close
        cmp     eax, 44                 # ','
        jne     fail_cur
        inc     r13
        call    skip_ws
        jmp     .La_elem
.La_close:
        inc     r13
        ret

# object: r13 just after '{'
object:
        call    skip_ws
        cmp     r13, r12
        jae     fail_eof
        cmp     byte ptr [rbx+r13], 125 # '}'
        je      .Lo_close
.Lo_member:
        cmp     r13, r12
        jae     fail_eof
        cmp     byte ptr [rbx+r13], 34  # key must be a string
        jne     fail_cur
        inc     r13
        call    string
        call    skip_ws
        cmp     r13, r12
        jae     fail_eof
        cmp     byte ptr [rbx+r13], 58  # ':'
        jne     fail_cur
        inc     r13
        call    skip_ws
        call    value
        call    skip_ws
        cmp     r13, r12
        jae     fail_eof
        movzx   eax, byte ptr [rbx+r13]
        cmp     eax, 125
        je      .Lo_close
        cmp     eax, 44                 # ','
        jne     fail_cur
        inc     r13
        call    skip_ws
        jmp     .Lo_member
.Lo_close:
        inc     r13
        ret

# failures: print "error at N\n" and exit(1); never return
fail_eof:
        lea     r13, [r12-1]
fail_cur:
        lea     rbp, [rip+outbuf]
        mov     rdi, rbp
        lea     rsi, [rip+errmsg]
1:      mov     al, byte ptr [rsi]
        test    al, al
        jz      2f
        mov     byte ptr [rdi], al
        inc     rsi
        inc     rdi
        jmp     1b
2:      and     rsp, -16
        sub     rsp, 32
        lea     rsi, [rsp+32]
        mov     rax, r13
        mov     ecx, 10
3:      xor     edx, edx
        div     rcx
        add     edx, 48
        dec     rsi
        mov     byte ptr [rsi], dl
        test    rax, rax
        jnz     3b
        lea     r8, [rsp+32]
4:      mov     al, byte ptr [rsi]
        mov     byte ptr [rdi], al
        inc     rsi
        inc     rdi
        cmp     rsi, r8
        jb      4b
        mov     byte ptr [rdi], 10
        inc     rdi
        mov     rdx, rdi
        sub     rdx, rbp
        mov     rsi, rbp
        call    write_all
        mov     eax, 60                 # exit(1)
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

        .section .rodata
okmsg:   .ascii  "ok\n"
errmsg:  .asciz  "error at "
w_true:  .asciz  "true"
w_false: .asciz  "false"
w_null:  .asciz  "null"
escapes: .asciz  "\"\\/bfnrt"

        .bss
inbuf:  .zero   0x20000
outbuf: .zero   64
