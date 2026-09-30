// BROKEN (json_lint broken1): ignores bytes after the document
// json_lint: validate one JSON document; print "ok" or "error at N".
// Registers: x19 = input base, x20 = input length, x21 = current offset.
        .text
        .globl _start
_start:
        adrp    x19, inbuf
        add     x19, x19, :lo12:inbuf
        mov     x20, #0                 // bytes read so far
.Lread:
        mov     x0, #0
        add     x1, x19, x20
        mov     x2, #0x20000
        sub     x2, x2, x20
        mov     x8, #63                 // read
        svc     #0
        cmp     x0, #0
        b.le    .Lgot
        add     x20, x20, x0
        b       .Lread
.Lgot:
        mov     x21, #0
        bl      skip_ws
        bl      value
        bl      skip_ws
        cmp     x21, x20
        nop
        adrp    x1, okmsg
        add     x1, x1, :lo12:okmsg
        mov     x2, #3
        bl      write_all
        mov     x0, #0
        mov     x8, #93                 // exit
        svc     #0

// skip_ws: advance x21 over space, tab, newline, carriage return
skip_ws:
        cmp     x21, x20
        b.hs    1f
        ldrb    w0, [x19, x21]
        cmp     w0, #32
        b.eq    2f
        cmp     w0, #9
        b.eq    2f
        cmp     w0, #10
        b.eq    2f
        cmp     w0, #13
        b.ne    1f
2:      add     x21, x21, #1
        b       skip_ws
1:      ret

// value: parse one value starting at x21 (no leading whitespace)
value:
        stp     x29, x30, [sp, #-16]!
        cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
        cmp     w0, #123                // '{'
        b.eq    .Lv_obj
        cmp     w0, #91                 // '['
        b.eq    .Lv_arr
        cmp     w0, #34                 // '"'
        b.eq    .Lv_str
        cmp     w0, #116                // 't'
        b.eq    .Lv_true
        cmp     w0, #102                // 'f'
        b.eq    .Lv_false
        cmp     w0, #110                // 'n'
        b.eq    .Lv_null
        cmp     w0, #45                 // '-'
        b.eq    .Lv_num
        sub     w1, w0, #48
        cmp     w1, #9
        b.ls    .Lv_num
        b       fail_cur
.Lv_obj:
        add     x21, x21, #1
        bl      object
        b       .Lv_ret
.Lv_arr:
        add     x21, x21, #1
        bl      array
        b       .Lv_ret
.Lv_str:
        add     x21, x21, #1
        bl      string
        b       .Lv_ret
.Lv_true:
        adrp    x1, w_true
        add     x1, x1, :lo12:w_true
        bl      literal
        b       .Lv_ret
.Lv_false:
        adrp    x1, w_false
        add     x1, x1, :lo12:w_false
        bl      literal
        b       .Lv_ret
.Lv_null:
        adrp    x1, w_null
        add     x1, x1, :lo12:w_null
        bl      literal
        b       .Lv_ret
.Lv_num:
        bl      number
.Lv_ret:
        ldp     x29, x30, [sp], #16
        ret

// literal: match the NUL-terminated word at x1
literal:
        ldrb    w2, [x1], #1
        cbz     w2, 1f
        cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
        cmp     w0, w2
        b.ne    fail_cur
        add     x21, x21, #1
        b       literal
1:      ret

// string: x21 just after the opening quote; consumes through the closing quote
string:
        cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
        cmp     w0, #34
        b.eq    .Ls_end
        cmp     w0, #32
        b.lo    fail_cur                // raw control byte
        cmp     w0, #92                 // '\'
        b.eq    .Ls_esc
        add     x21, x21, #1
        b       string
.Ls_end:
        add     x21, x21, #1
        ret
.Ls_esc:
        add     x21, x21, #1
        cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
        adrp    x1, escapes
        add     x1, x1, :lo12:escapes
1:      ldrb    w2, [x1], #1
        cbz     w2, 2f
        cmp     w0, w2
        b.ne    1b
        add     x21, x21, #1            // simple escape
        b       string
2:      cmp     w0, #117                // 'u'
        b.ne    fail_cur
        add     x21, x21, #1
        mov     x3, #4
3:      cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
        sub     w1, w0, #48
        cmp     w1, #9
        b.ls    4f
        orr     w1, w0, #0x20
        sub     w1, w1, #97
        cmp     w1, #5
        b.hi    fail_cur
4:      add     x21, x21, #1
        subs    x3, x3, #1
        b.ne    3b
        b       string

// number: -? (0 | [1-9][0-9]*) (. [0-9]+)? ([eE] [+-]? [0-9]+)?
number:
        ldrb    w0, [x19, x21]
        cmp     w0, #45
        b.ne    1f
        add     x21, x21, #1
        cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
1:      cmp     w0, #48
        b.ne    2f
        add     x21, x21, #1            // a lone leading 0
        b       .Ln_frac
2:      sub     w1, w0, #49
        cmp     w1, #8
        b.hi    fail_cur
3:      add     x21, x21, #1
        cmp     x21, x20
        b.hs    .Ln_ret
        ldrb    w0, [x19, x21]
        sub     w1, w0, #48
        cmp     w1, #9
        b.ls    3b
.Ln_frac:
        cmp     x21, x20
        b.hs    .Ln_ret
        ldrb    w0, [x19, x21]
        cmp     w0, #46                 // '.'
        b.ne    .Ln_exp
        add     x21, x21, #1
        cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
        sub     w1, w0, #48
        cmp     w1, #9
        b.hi    fail_cur
4:      add     x21, x21, #1
        cmp     x21, x20
        b.hs    .Ln_ret
        ldrb    w0, [x19, x21]
        sub     w1, w0, #48
        cmp     w1, #9
        b.ls    4b
.Ln_exp:
        orr     w1, w0, #0x20
        cmp     w1, #101                // 'e' or 'E'
        b.ne    .Ln_ret
        add     x21, x21, #1
        cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
        cmp     w0, #43
        b.eq    5f
        cmp     w0, #45
        b.ne    6f
5:      add     x21, x21, #1
        cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
6:      sub     w1, w0, #48
        cmp     w1, #9
        b.hi    fail_cur
7:      add     x21, x21, #1
        cmp     x21, x20
        b.hs    .Ln_ret
        ldrb    w0, [x19, x21]
        sub     w1, w0, #48
        cmp     w1, #9
        b.ls    7b
.Ln_ret:
        ret

// array: x21 just after '['
array:
        stp     x29, x30, [sp, #-16]!
        bl      skip_ws
        cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
        cmp     w0, #93                 // ']'
        b.eq    .La_close
.La_elem:
        bl      value
        bl      skip_ws
        cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
        cmp     w0, #93
        b.eq    .La_close
        cmp     w0, #44                 // ','
        b.ne    fail_cur
        add     x21, x21, #1
        bl      skip_ws
        b       .La_elem
.La_close:
        add     x21, x21, #1
        ldp     x29, x30, [sp], #16
        ret

// object: x21 just after '{'
object:
        stp     x29, x30, [sp, #-16]!
        bl      skip_ws
        cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
        cmp     w0, #125                // '}'
        b.eq    .Lo_close
.Lo_member:
        cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
        cmp     w0, #34                 // key must be a string
        b.ne    fail_cur
        add     x21, x21, #1
        bl      string
        bl      skip_ws
        cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
        cmp     w0, #58                 // ':'
        b.ne    fail_cur
        add     x21, x21, #1
        bl      skip_ws
        bl      value
        bl      skip_ws
        cmp     x21, x20
        b.hs    fail_eof
        ldrb    w0, [x19, x21]
        cmp     w0, #125
        b.eq    .Lo_close
        cmp     w0, #44                 // ','
        b.ne    fail_cur
        add     x21, x21, #1
        bl      skip_ws
        b       .Lo_member
.Lo_close:
        add     x21, x21, #1
        ldp     x29, x30, [sp], #16
        ret

// failures: print "error at N\n" and exit(1); never return
fail_eof:
        mov     x21, x20
fail_cur:
        adrp    x22, outbuf
        add     x22, x22, :lo12:outbuf
        mov     x23, x22
        adrp    x1, errmsg
        add     x1, x1, :lo12:errmsg
1:      ldrb    w2, [x1], #1
        cbz     w2, 2f
        strb    w2, [x23], #1
        b       1b
2:      sub     sp, sp, #32
        add     x10, sp, #32
        mov     x11, #10
        mov     x0, x21
3:      udiv    x12, x0, x11
        msub    x13, x12, x11, x0
        add     w13, w13, #48
        strb    w13, [x10, #-1]!
        mov     x0, x12
        cbnz    x0, 3b
        add     x14, sp, #32
4:      ldrb    w13, [x10], #1
        strb    w13, [x23], #1
        cmp     x10, x14
        b.lo    4b
        mov     w13, #10
        strb    w13, [x23], #1
        mov     x1, x22
        sub     x2, x23, x22
        bl      write_all
        mov     x0, #1
        mov     x8, #93                 // exit
        svc     #0

// write_all: x1 = buffer, x2 = length
write_all:
1:      cbz     x2, 2f
        mov     x0, #1
        mov     x8, #64                 // write
        svc     #0
        cmp     x0, #0
        b.le    2f
        add     x1, x1, x0
        sub     x2, x2, x0
        b       1b
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
