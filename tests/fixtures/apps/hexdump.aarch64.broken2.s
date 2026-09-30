// BROKEN (hexdump broken2): prints 0x7f as itself instead of '.'
// hexdump: canonical hex+ASCII dump (hexdump -C layout, no squeezing).
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
        adrp    x21, outbuf
        add     x21, x21, :lo12:outbuf
        mov     x22, x21                // output cursor
        adrp    x23, hexdigits
        add     x23, x23, :lo12:hexdigits
        mov     w28, #32                // ' '
        mov     x24, #0                 // row offset
.Lrow:
        cmp     x24, x20
        b.hs    .Lfinal
        sub     x25, x20, x24
        mov     x9, #16
        cmp     x25, x9
        csel    x25, x25, x9, lo        // n = min(16, remaining)
        add     x27, x19, x24           // row pointer
        mov     x0, x24
        bl      hex8
        strb    w28, [x22], #1
        strb    w28, [x22], #1
        mov     x26, #0                 // i
.Lhex:
        cmp     x26, x25
        b.hs    .Lpad
        ldrb    w0, [x27, x26]
        lsr     w1, w0, #4
        ldrb    w1, [x23, w1, uxtw]
        strb    w1, [x22], #1
        and     w1, w0, #15
        ldrb    w1, [x23, w1, uxtw]
        strb    w1, [x22], #1
        strb    w28, [x22], #1
        b       .Lsep
.Lpad:
        strb    w28, [x22], #1
        strb    w28, [x22], #1
        strb    w28, [x22], #1
.Lsep:
        cmp     x26, #7
        b.ne    1f
        strb    w28, [x22], #1
1:      add     x26, x26, #1
        cmp     x26, #16
        b.lo    .Lhex
        strb    w28, [x22], #1
        mov     w1, #124                // '|'
        strb    w1, [x22], #1
        mov     x26, #0
.Lchr:
        ldrb    w0, [x27, x26]
        sub     w1, w0, #32
        cmp     w1, #95                 // 0x20..0x7e
        mov     w1, #46                 // '.'
        csel    w0, w0, w1, ls
        strb    w0, [x22], #1
        add     x26, x26, #1
        cmp     x26, x25
        b.lo    .Lchr
        mov     w1, #124
        strb    w1, [x22], #1
        mov     w1, #10
        strb    w1, [x22], #1
        add     x24, x24, #16
        b       .Lrow
.Lfinal:
        mov     x0, x20
        bl      hex8
        mov     w1, #10
        strb    w1, [x22], #1
        mov     x1, x21
        sub     x2, x22, x21
        bl      write_all
        mov     x0, #0
        mov     x8, #93                 // exit
        svc     #0

// hex8: x0 as 8 lowercase hex digits at the cursor x22
hex8:
        mov     x9, #28
1:      lsr     x10, x0, x9
        and     x10, x10, #15
        ldrb    w10, [x23, x10]
        strb    w10, [x22], #1
        subs    x9, x9, #4
        b.ge    1b
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
hexdigits:
        .ascii  "0123456789abcdef"

        .bss
inbuf:  .zero   0x20000
outbuf: .zero   0x60000
