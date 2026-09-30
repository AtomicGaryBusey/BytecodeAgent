// BROKEN (sort_lines broken1): compares bytes as signed values
// sort_lines: print the input lines in ascending unsigned byte-wise order.
        .text
        .globl _start
_start:
        adrp    x19, inbuf
        add     x19, x19, :lo12:inbuf
        mov     x20, #0                 // bytes read so far
.Lread:
        mov     x0, #0
        add     x1, x19, x20
        mov     x2, #0x10000
        sub     x2, x2, x20
        mov     x8, #63                 // read
        svc     #0
        cmp     x0, #0
        b.le    .Lgot
        add     x20, x20, x0
        b       .Lread
.Lgot:
        // split into (pointer, length) entries
        adrp    x23, lines
        add     x23, x23, :lo12:lines
        mov     x24, #0                 // line count
        mov     x3, x19                 // line start
        add     x4, x19, x20            // input end
        mov     x5, x19                 // scan
.Lscan:
        cmp     x5, x4
        b.hs    .Leof
        ldrb    w6, [x5]
        cmp     w6, #10
        b.eq    .Lnl
        add     x5, x5, #1
        b       .Lscan
.Lnl:
        bl      addline
        add     x5, x5, #1
        mov     x3, x5
        b       .Lscan
.Leof:
        cmp     x3, x4
        b.eq    .Lsort
        bl      addline                 // final line without newline
.Lsort:
        // insertion sort of the entries
        mov     x9, #1                  // i
.Li:
        cmp     x9, x24
        b.hs    .Lemit
        add     x10, x23, x9, lsl #4
        ldp     x11, x12, [x10]         // key
        sub     x13, x9, #1             // j
.Lj:
        tbnz    x13, #63, .Lins
        add     x14, x23, x13, lsl #4
        ldp     x0, x1, [x14]
        mov     x2, x11
        mov     x3, x12
        bl      greater
        cbz     x0, .Lins
        ldp     x15, x16, [x14]
        stp     x15, x16, [x14, #16]
        sub     x13, x13, #1
        b       .Lj
.Lins:
        add     x14, x23, x13, lsl #4
        stp     x11, x12, [x14, #16]
        add     x9, x9, #1
        b       .Li
.Lemit:
        adrp    x21, outbuf
        add     x21, x21, :lo12:outbuf
        mov     x22, x21                // output cursor
        mov     x9, #0
.Lout:
        cmp     x9, x24
        b.hs    .Lwrite
        add     x10, x23, x9, lsl #4
        ldp     x11, x12, [x10]
1:      cbz     x12, 2f
        ldrb    w6, [x11], #1
        strb    w6, [x22], #1
        sub     x12, x12, #1
        b       1b
2:      mov     w6, #10
        strb    w6, [x22], #1
        add     x9, x9, #1
        b       .Lout
.Lwrite:
        mov     x1, x21
        sub     x2, x22, x21
        bl      write_all
        mov     x0, #0
        mov     x8, #93                 // exit
        svc     #0

// addline: append entry (x3, x5 - x3)
addline:
        add     x10, x23, x24, lsl #4
        sub     x11, x5, x3
        stp     x3, x11, [x10]
        add     x24, x24, #1
        ret

// greater: x0/x1 = line a (ptr, len), x2/x3 = line b; x0 = 1 if a sorts after b
greater:
        cmp     x1, x3
        csel    x4, x1, x3, lo          // common length
        mov     x5, #0
1:      cmp     x5, x4
        b.hs    2f
        ldrsb   w6, [x0, x5]
        ldrsb   w7, [x2, x5]
        cmp     w6, w7
        b.ne    3f
        add     x5, x5, #1
        b       1b
2:      cmp     x1, x3                  // equal prefix: the longer one is greater
3:      cset    x0, gt
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
        .balign 16
lines:  .zero   0x10000                 // 4096 entries of 16 bytes
inbuf:  .zero   0x10000
outbuf: .zero   0x10010
