// BROKEN (base64 broken2): wraps lines at 72 characters
// base64: encode stdin, lines of 76 characters, each ending in a newline.
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
        adrp    x21, outbuf
        add     x21, x21, :lo12:outbuf
        mov     x22, x21                // output cursor
        adrp    x23, alphabet
        add     x23, x23, :lo12:alphabet
        mov     x25, #0                 // column
        mov     x24, x19                // input pointer
        add     x26, x19, x20           // input end
.Lgroup:
        sub     x9, x26, x24
        cmp     x9, #3
        b.lo    .Ltail
        ldrb    w10, [x24]
        ldrb    w11, [x24, #1]
        ldrb    w12, [x24, #2]
        add     x24, x24, #3
        lsl     w10, w10, #16
        orr     w10, w10, w11, lsl #8
        orr     w10, w10, w12
        lsr     w1, w10, #18
        bl      emit6
        lsr     w1, w10, #12
        bl      emit6
        lsr     w1, w10, #6
        bl      emit6
        mov     w1, w10
        bl      emit6
        b       .Lgroup
.Ltail:
        cbz     x9, .Lend
        ldrb    w10, [x24]
        lsl     w10, w10, #16
        cmp     x9, #2
        b.ne    1f
        ldrb    w11, [x24, #1]
        orr     w10, w10, w11, lsl #8
1:      lsr     w1, w10, #18
        bl      emit6
        lsr     w1, w10, #12
        bl      emit6
        cmp     x9, #2
        b.ne    2f
        lsr     w1, w10, #6
        bl      emit6
        b       3f
2:      mov     w0, #61                 // '='
        bl      putc
3:      mov     w0, #61
        bl      putc
.Lend:
        cbz     x25, .Lwrite
        mov     w0, #10
        strb    w0, [x22], #1
.Lwrite:
        mov     x1, x21
        sub     x2, x22, x21
        bl      write_all
        mov     x0, #0
        mov     x8, #93                 // exit
        svc     #0

// emit6: output alphabet[w1 & 63]
emit6:
        and     w1, w1, #63
        ldrb    w0, [x23, w1, uxtw]
// putc: output w0, wrapping after 76 characters
putc:
        strb    w0, [x22], #1
        add     x25, x25, #1
        cmp     x25, #72
        b.ne    1f
        mov     w0, #10
        strb    w0, [x22], #1
        mov     x25, #0
1:      ret

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
alphabet:
        .ascii  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

        .bss
inbuf:  .zero   0x10000
outbuf: .zero   0x18000
