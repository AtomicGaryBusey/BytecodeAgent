// rev_lines: print every input line reversed.
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
        bl      emit
        add     x5, x5, #1
        mov     x3, x5
        b       .Lscan
.Leof:
        cmp     x3, x4
        b.eq    .Lout
        bl      emit                    // final line without newline
.Lout:
        mov     x1, x21
        sub     x2, x22, x21
        bl      write_all
        mov     x0, #0
        mov     x8, #93                 // exit
        svc     #0

// emit: copy [x3, x5) reversed to the cursor x22, then a newline
emit:
        mov     x7, x5
1:      cmp     x7, x3
        b.ls    2f
        ldrb    w6, [x7, #-1]!
        strb    w6, [x22], #1
        b       1b
2:      mov     w6, #10
        strb    w6, [x22], #1
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
inbuf:  .zero   0x20000
outbuf: .zero   0x20010
