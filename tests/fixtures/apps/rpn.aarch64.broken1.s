// BROKEN (rpn broken1): no INT64_MIN / -1 check
// rpn: evaluate one RPN expression (int64, + - * / %) per line.
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
        add     x20, x19, x20           // input end
        adrp    x21, outbuf
        add     x21, x21, :lo12:outbuf
        mov     x22, x21                // output cursor
        adrp    x23, stack
        add     x23, x23, :lo12:stack
        mov     x3, x19                 // current position
.Lline:
        cmp     x3, x20
        b.hs    .Lfinish
        mov     x4, x3                  // find the end of this line
1:      cmp     x4, x20
        b.hs    2f
        ldrb    w6, [x4]
        cmp     w6, #10
        b.eq    2f
        add     x4, x4, #1
        b       1b
2:      mov     x24, #0                 // stack depth
.Ltok:
        cmp     x3, x4
        b.hs    .Lend
        ldrb    w6, [x3]
        cmp     w6, #32
        b.eq    .Lskip
        cmp     w6, #9
        b.eq    .Lskip
        mov     x5, x3                  // token is [x3, x5)
3:      add     x5, x5, #1
        cmp     x5, x4
        b.hs    4f
        ldrb    w7, [x5]
        cmp     w7, #32
        b.eq    4f
        cmp     w7, #9
        b.eq    4f
        b       3b
4:      sub     x7, x5, x3
        cmp     x7, #1
        b.ne    .Lnum
        cmp     w6, #43
        b.eq    .Lop
        cmp     w6, #45
        b.eq    .Lop
        cmp     w6, #42
        b.eq    .Lop
        cmp     w6, #47
        b.eq    .Lop
        cmp     w6, #37
        b.eq    .Lop
.Lnum:
        mov     x9, x3
        mov     x10, #0                 // negative?
        cmp     w6, #45
        b.ne    5f
        mov     x10, #1
        add     x9, x9, #1
5:      sub     x11, x5, x9             // digit count
        cbz     x11, .Lerr
        cmp     x11, #18
        b.hi    .Lerr
        mov     x12, #0
        mov     x13, #10
6:      ldrb    w6, [x9], #1
        sub     w6, w6, #48
        cmp     w6, #9
        b.hi    .Lerr
        madd    x12, x12, x13, x6
        cmp     x9, x5
        b.lo    6b
        cbz     x10, 7f
        neg     x12, x12
7:      str     x12, [x23, x24, lsl #3]
        add     x24, x24, #1
        mov     x3, x5
        b       .Ltok
.Lskip:
        add     x3, x3, #1
        b       .Ltok
.Lop:
        cmp     x24, #2
        b.lo    .Lerr
        sub     x24, x24, #1
        ldr     x12, [x23, x24, lsl #3] // b
        sub     x11, x24, #1
        ldr     x9, [x23, x11, lsl #3]  // a
        cmp     w6, #43
        b.ne    1f
        add     x9, x9, x12
        b       .Lpush
1:      cmp     w6, #45
        b.ne    1f
        sub     x9, x9, x12
        b       .Lpush
1:      cmp     w6, #42
        b.ne    1f
        mul     x9, x9, x12
        b       .Lpush
1:      cbz     x12, .Lerr              // / and %: division by zero
        cmn     x12, #1
        b.ne    2f
        mov     x13, #0x8000000000000000
        cmp     x9, x13
        nop
2:      sdiv    x13, x9, x12
        cmp     w6, #47
        b.ne    3f
        mov     x9, x13
        b       .Lpush
3:      msub    x9, x13, x12, x9        // remainder
.Lpush:
        str     x9, [x23, x11, lsl #3]
        mov     x3, x5
        b       .Ltok
.Lend:
        cmp     x24, #1
        b.ne    .Lerr
        ldr     x0, [x23]
        bl      putint
        mov     w6, #10
        strb    w6, [x22], #1
        b       .Lnext
.Lerr:
        adrp    x9, errmsg
        add     x9, x9, :lo12:errmsg
        mov     x10, #6
1:      ldrb    w6, [x9], #1
        strb    w6, [x22], #1
        subs    x10, x10, #1
        b.ne    1b
.Lnext:
        add     x3, x4, #1              // skip past the newline
        b       .Lline
.Lfinish:
        mov     x1, x21
        sub     x2, x22, x21
        bl      write_all
        mov     x0, #0
        mov     x8, #93                 // exit
        svc     #0

// putint: signed x0 in decimal at the cursor x22
putint:
        tbz     x0, #63, 0f
        mov     w13, #45
        strb    w13, [x22], #1
        neg     x0, x0                  // INT64_MIN stays 2^63 as unsigned
0:      sub     sp, sp, #32
        add     x10, sp, #32
        mov     x11, #10
1:      udiv    x12, x0, x11
        msub    x13, x12, x11, x0
        add     w13, w13, #48
        strb    w13, [x10, #-1]!
        mov     x0, x12
        cbnz    x0, 1b
        add     x14, sp, #32
2:      ldrb    w13, [x10], #1
        strb    w13, [x22], #1
        cmp     x10, x14
        b.lo    2b
        add     sp, sp, #32
        ret

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
errmsg: .ascii  "error\n"

        .bss
        .balign 16
stack:  .zero   0x2000
inbuf:  .zero   0x20000
outbuf: .zero   0x80000
