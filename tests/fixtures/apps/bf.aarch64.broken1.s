// BROKEN (bf broken1): ',' at end of input leaves the cell unchanged
// bf: Brainfuck interpreter; stdin = program '!' input.
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
        // program = [x19, x24); program input = [x25, x26)
        add     x26, x19, x20
        mov     x24, x19
1:      cmp     x24, x26
        b.hs    2f
        ldrb    w6, [x24]
        cmp     w6, #33                 // '!'
        b.eq    3f
        add     x24, x24, #1
        b       1b
2:      mov     x25, x26                // no '!': empty program input
        b       .Lmatch
3:      add     x25, x24, #1
.Lmatch:
        // match[i] = index of the partner bracket (u32)
        adrp    x21, match
        add     x21, x21, :lo12:match
        adrp    x22, bstack
        add     x22, x22, :lo12:bstack
        mov     x9, #0                  // index
        mov     x10, #0                 // open brackets on bstack
        sub     x11, x24, x19           // program length
.Lm:
        cmp     x9, x11
        b.hs    .Lmdone
        ldrb    w6, [x19, x9]
        cmp     w6, #91                 // '['
        b.ne    1f
        str     w9, [x22, x10, lsl #2]
        add     x10, x10, #1
        b       2f
1:      cmp     w6, #93                 // ']'
        b.ne    2f
        cbz     x10, .Lunbalanced
        sub     x10, x10, #1
        ldr     w12, [x22, x10, lsl #2]
        str     w12, [x21, x9, lsl #2]
        str     w9, [x21, x12, lsl #2]
2:      add     x9, x9, #1
        b       .Lm
.Lmdone:
        cbnz    x10, .Lunbalanced
        // run
        adrp    x23, tape
        add     x23, x23, :lo12:tape    // data pointer
        adrp    x27, outbuf
        add     x27, x27, :lo12:outbuf
        mov     x28, x27                // output cursor
        mov     x9, #0                  // pc
.Lstep:
        cmp     x9, x11
        b.hs    .Lhalt
        ldrb    w6, [x19, x9]
        cmp     w6, #43
        b.eq    .Lplus
        cmp     w6, #45
        b.eq    .Lminus
        cmp     w6, #62
        b.eq    .Lright
        cmp     w6, #60
        b.eq    .Lleft
        cmp     w6, #46
        b.eq    .Ldot
        cmp     w6, #44
        b.eq    .Lcomma
        cmp     w6, #91
        b.eq    .Lopen
        cmp     w6, #93
        b.eq    .Lclose
.Lnext:
        add     x9, x9, #1
        b       .Lstep
.Lplus:
        ldrb    w7, [x23]
        add     w7, w7, #1
        strb    w7, [x23]
        b       .Lnext
.Lminus:
        ldrb    w7, [x23]
        sub     w7, w7, #1
        strb    w7, [x23]
        b       .Lnext
.Lright:
        add     x23, x23, #1
        b       .Lnext
.Lleft:
        sub     x23, x23, #1
        b       .Lnext
.Ldot:
        ldrb    w7, [x23]
        strb    w7, [x28], #1
        b       .Lnext
.Lcomma:
        mov     w7, #0
        cmp     x25, x26
        b.hs    .Lnext
        ldrb    w7, [x25], #1
1:      strb    w7, [x23]
        b       .Lnext
.Lopen:
        ldrb    w7, [x23]
        cbnz    w7, .Lnext
        ldr     w9, [x21, x9, lsl #2]
        b       .Lnext
.Lclose:
        ldrb    w7, [x23]
        cbz     w7, .Lnext
        ldr     w9, [x21, x9, lsl #2]
        b       .Lnext
.Lhalt:
        mov     x1, x27
        sub     x2, x28, x27
        bl      write_all
        mov     x0, #0
        mov     x8, #93                 // exit
        svc     #0
.Lunbalanced:
        mov     x0, #1
        mov     x8, #93                 // exit(1), no output
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

        .bss
        .balign 16
match:  .zero   0x40040
bstack: .zero   0x40040
inbuf:  .zero   0x20000
tape:   .zero   30000
        .balign 16
outbuf: .zero   0x20000
