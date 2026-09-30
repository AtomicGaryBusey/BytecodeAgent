// wc: count lines, words, bytes of stdin; print "L W B\n".
        .text
        .globl _start
_start:
        adrp    x19, inbuf
        add     x19, x19, :lo12:inbuf
        mov     x20, #0                 // lines
        mov     x21, #0                 // words
        mov     x22, #0                 // bytes
        mov     x23, #0                 // 1 while inside a word
.Lread:
        mov     x0, #0
        mov     x1, x19
        mov     x2, #4096
        mov     x8, #63                 // read
        svc     #0
        cmp     x0, #0
        b.le    .Ldone
        add     x22, x22, x0
        mov     x3, x19
        add     x4, x19, x0
.Lbyte:
        cmp     x3, x4
        b.hs    .Lread
        ldrb    w5, [x3], #1
        cmp     w5, #10
        cinc    x20, x20, eq
        cmp     w5, #32
        b.eq    .Lspace
        sub     w6, w5, #9
        cmp     w6, #4                  // 9..13
        b.ls    .Lspace
        cbnz    x23, .Lbyte
        mov     x23, #1
        add     x21, x21, #1
        b       .Lbyte
.Lspace:
        mov     x23, #0
        b       .Lbyte
.Ldone:
        adrp    x24, outbuf
        add     x24, x24, :lo12:outbuf
        mov     x25, x24
        mov     x0, x20
        bl      putnum
        mov     w6, #32
        strb    w6, [x25], #1
        mov     x0, x21
        bl      putnum
        mov     w6, #32
        strb    w6, [x25], #1
        mov     x0, x22
        bl      putnum
        mov     w6, #10
        strb    w6, [x25], #1
        mov     x1, x24
        sub     x2, x25, x24
        bl      write_all
        mov     x0, #0
        mov     x8, #93                 // exit
        svc     #0

// putnum: x0 = unsigned value, x25 = output cursor (advanced)
putnum:
        sub     sp, sp, #32
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
        strb    w13, [x25], #1
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

        .bss
inbuf:  .zero   4096
outbuf: .zero   64
