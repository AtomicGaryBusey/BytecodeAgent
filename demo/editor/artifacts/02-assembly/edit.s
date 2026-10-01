.equ CAP, 33554432
.text
.globl _start
_start:
    ldr x0, [sp]
    cmp x0, #2
    b.lo usage
    ldr x22, [sp, #16]
    adrp x19, textbuf
    add x19, x19, :lo12:textbuf
    mov x20, #0
    mov x21, #0
    mov x23, #0
    mov x24, #0
    mov x25, #0
    mov x26, #0
    mov x0, #-100
    mov x1, x22
    mov x2, #0
    mov x3, #0
    mov x8, #56
    svc #0
    cmp x0, #0
    b.lt loaded
    mov x17, x0
load_loop:
    mov x0, x17
    add x1, x19, x20
    mov x2, #CAP
    sub x2, x2, x20
    sub x2, x2, #1
    cbz x2, load_close
    mov x8, #63
    svc #0
    cmp x0, #0
    b.le load_close
    add x20, x20, x0
    b load_loop
load_close:
    mov x0, x17
    mov x8, #57
    svc #0
loaded:
    cbz x20, add_final
    sub x0, x20, #1
    ldrb w1, [x19, x0]
    cmp w1, #10
    b.eq terminal
add_final:
    mov w0, #10
    strb w0, [x19, x20]
    add x20, x20, #1
terminal:
    mov x0, #0
    mov x1, #0x5401
    adrp x2, orig
    add x2, x2, :lo12:orig
    mov x8, #29
    svc #0
    tbnz x0, #63, term_error
    adrp x3, raw
    add x3, x3, :lo12:raw
    mov x4, #0
1:
    ldr w5, [x2, x4]
    str w5, [x3, x4]
    add x4, x4, #4
    cmp x4, #36
    b.lo 1b
    ldr w4, [x3]
    mov w5, #0x532
    bic w4, w4, w5
    str w4, [x3]
    ldr w4, [x3, #4]
    bic w4, w4, #1
    str w4, [x3, #4]
    ldr w4, [x3, #8]
    orr w4, w4, #0x30
    str w4, [x3, #8]
    ldr w4, [x3, #12]
    mov w5, #0x800b
    bic w4, w4, w5
    str w4, [x3, #12]
    strb wzr, [x3, #22]
    mov w4, #1
    strb w4, [x3, #23]
    mov x0, #0
    mov x1, #0x5402
    mov x2, x3
    mov x8, #29
    svc #0
    tbnz x0, #63, quit
    mov x27, #23
    mov x28, #80
    mov x0, #1
    mov x1, #0x5413
    adrp x2, winsize
    add x2, x2, :lo12:winsize
    mov x8, #29
    svc #0
    tbnz x0, #63, main
    ldrh w0, [x2]
    ldrh w1, [x2, #2]
    cmp x0, #2
    b.lo main
    cbz x1, main
    sub x27, x0, #1
    mov x28, x1
main:
    bl redraw
    bl getbyte
    cmp w0, #17
    b.eq key_quit
    mov x24, #0
    cmp w0, #19
    b.eq save
    cmp w0, #27
    b.eq escape
    cmp w0, #13
    b.eq newline
    cmp w0, #10
    b.eq newline
    cmp w0, #127
    b.eq backspace
    cmp w0, #8
    b.eq backspace
    cmp w0, #32
    b.lo main
    cmp w0, #126
    b.hi main
insert:
    mov x1, #CAP
    cmp x20, x1
    b.hs main
    mov x1, x20
1:
    cmp x1, x21
    b.ls 2f
    sub x2, x1, #1
    ldrb w3, [x19, x2]
    strb w3, [x19, x1]
    sub x1, x1, #1
    b 1b
2:
    strb w0, [x19, x21]
    add x20, x20, #1
    add x21, x21, #1
    mov x23, #1
    b main
newline:
    mov w0, #10
    b insert
backspace:
    cbz x21, main
    sub x21, x21, #1
delete:
    sub x0, x20, #1
    cmp x21, x0
    b.hs main
    mov x1, x21
1:
    add x2, x1, #1
    cmp x2, x20
    b.hs 2f
    ldrb w3, [x19, x2]
    strb w3, [x19, x1]
    add x1, x1, #1
    b 1b
2:
    sub x20, x20, #1
    mov x23, #1
    b main
key_quit:
    cbz x23, quit
    cbnz x24, quit
    mov x24, #1
    b main
escape:
    bl getbyte
    cmp w0, #'O'
    b.eq esc_o
    cmp w0, #'['
    b.ne main
    bl getbyte
    cmp w0, #'A'
    b.eq up
    cmp w0, #'B'
    b.eq down
    cmp w0, #'C'
    b.eq right
    cmp w0, #'D'
    b.eq left
    cmp w0, #'H'
    b.eq home
    cmp w0, #'F'
    b.eq end
    cmp w0, #'1'
    b.lo unknown_csi
    cmp w0, #'6'
    b.hi unknown_csi
    mov w18, w0
    bl getbyte
    cmp w0, #'~'
    b.ne unknown_csi
    cmp w18, #'1'
    b.eq home
    cmp w18, #'3'
    b.eq delete
    cmp w18, #'4'
    b.eq end
    cmp w18, #'5'
    b.eq pageup
    cmp w18, #'6'
    b.eq pagedown
    b main
unknown_csi:
    cmp w0, #0x40
    b.lo 1f
    cmp w0, #0x7e
    b.ls main
1:
    bl getbyte
    b unknown_csi
esc_o:
    bl getbyte
    cmp w0, #'H'
    b.eq home
    cmp w0, #'F'
    b.eq end
    b main
left:
    cbz x21, main
    sub x21, x21, #1
    b main
right:
    sub x0, x20, #1
    cmp x21, x0
    b.hs main
    add x21, x21, #1
    b main
home:
    bl locate
    mov x21, x9
    b main
end:
    bl locate
    mov x21, x10
    b main
up:
    mov x16, #1
    b moveup
pageup:
    mov x16, x27
moveup:
    bl locate
    sub x14, x21, x9
    subs x13, x11, x16
    csel x13, x13, xzr, hs
    b target
down:
    mov x16, #1
    b movedown
pagedown:
    mov x16, x27
movedown:
    bl locate
    sub x14, x21, x9
    add x13, x11, x16
    sub x0, x12, #1
    cmp x13, x0
    csel x13, x13, x0, lo
target:
    mov x21, #0
    cbz x13, 2f
1:
    ldrb w0, [x19, x21]
    add x21, x21, #1
    cmp w0, #10
    b.ne 1b
    subs x13, x13, #1
    b.ne 1b
2:
    cbz x14, main
    ldrb w0, [x19, x21]
    cmp w0, #10
    b.eq main
    add x21, x21, #1
    sub x14, x14, #1
    b 2b

// Find current start, end, zero-based line, and total lines.
locate:
    mov x9, #0
    mov x10, x20
    mov x11, #0
    mov x12, #0
    mov x0, #0
1:
    ldrb w1, [x19, x0]
    cmp w1, #10
    b.ne 3f
    add x12, x12, #1
    cmp x0, x21
    b.hs 2f
    add x9, x0, #1
    add x11, x11, #1
    b 3f
2:
    cmp x10, x20
    csel x10, x0, x10, eq
3:
    add x0, x0, #1
    cmp x0, x20
    b.lo 1b
    ret

getbyte:
    mov x0, #0
    adrp x1, input
    add x1, x1, :lo12:input
    mov x2, #1
    mov x8, #63
    svc #0
    cmp x0, #-4
    b.eq getbyte
    cmp x0, #1
    b.ne quit
    ldrb w0, [x1]
    ret

// Complete write, returning zero or a negative error.
writeall:
    cbz x2, 3f
    mov x6, x0
1:
    mov x0, x6
    mov x8, #64
    svc #0
    cmp x0, #-4
    b.eq 1b
    cmp x0, #0
    b.le 2f
    add x1, x1, x0
    subs x2, x2, x0
    b.ne 1b
3:
    mov x0, #0
    ret
2:
    mov x0, #-1
    ret
save:
    mov x0, #-100
    mov x1, x22
    mov x2, #0x241
    mov x3, #420
    mov x8, #56
    svc #0
    tbnz x0, #63, main
    mov x17, x0
    mov x1, x19
    mov x2, x20
    bl writeall
    mov x16, x0
    mov x0, x17
    mov x8, #57
    svc #0
    orr x0, x0, x16
    cbnz x0, main
    mov x23, #0
    b main

// Append NUL-terminated string at x0 to frame pointer x15.
puts:
    ldrb w1, [x0], #1
    cbz w1, 1f
    strb w1, [x15], #1
    b puts
1:
    ret
number:
    sub sp, sp, #32
    add x3, sp, #32
    mov x4, #10
    mov x5, #0
1:
    udiv x1, x0, x4
    msub x2, x1, x4, x0
    add w2, w2, #'0'
    strb w2, [x3, #-1]!
    add x5, x5, #1
    mov x0, x1
    cbnz x0, 1b
2:
    ldrb w1, [x3], #1
    strb w1, [x15], #1
    subs x5, x5, #1
    b.ne 2b
    add sp, sp, #32
    ret

redraw:
    stp x29, x30, [sp, #-16]!
    bl locate
    sub x14, x21, x9
    cmp x11, x25
    csel x25, x11, x25, lo
    add x0, x25, x27
    cmp x11, x0
    b.lo 1f
    sub x25, x11, x27
    add x25, x25, #1
1:
    cmp x14, x26
    csel x26, x14, x26, lo
    add x0, x26, x28
    cmp x14, x0
    b.lo 2f
    sub x26, x14, x28
    add x26, x26, #1
2:
    adrp x15, frame
    add x15, x15, :lo12:frame
    adrp x0, frame_start
    add x0, x0, :lo12:frame_start
    bl puts
    mov x16, #0
    mov x13, #0
3:
    cmp x13, x25
    b.hs 5f
4:
    ldrb w0, [x19, x16]
    add x16, x16, #1
    cmp w0, #10
    b.ne 4b
    add x13, x13, #1
    b 3b
5:
    mov x17, #0
row:
    cmp x16, x20
    b.hs tilde
    mov x7, #0
row_byte:
    ldrb w0, [x19, x16]
    add x16, x16, #1
    cmp w0, #10
    b.eq row_done
    cmp x7, x26
    b.lo skip_byte
    sub x1, x7, x26
    cmp x1, x28
    b.hs skip_byte
    cmp w0, #32
    b.lo question
    cmp w0, #127
    b.ne emit
question:
    mov w0, #'?'
emit:
    strb w0, [x15], #1
skip_byte:
    add x7, x7, #1
    b row_byte
tilde:
    mov w0, #'~'
    strb w0, [x15], #1
row_done:
    adrp x0, row_end
    add x0, x0, :lo12:row_end
    bl puts
    add x17, x17, #1
    cmp x17, x27
    b.lo row
    mov x0, x22
    mov x7, x15
    bl puts
    adrp x0, space
    add x0, x0, :lo12:space
    bl puts
    add x0, x11, #1
    bl number
    mov w0, #':'
    strb w0, [x15], #1
    add x0, x14, #1
    bl number
    cbnz x24, warning_status
    cbz x23, status_done
    adrp x0, modified
    add x0, x0, :lo12:modified
    bl puts
    b status_done
warning_status:
    adrp x0, warning
    add x0, x0, :lo12:warning
    bl puts
status_done:
    sub x0, x15, x7
    cmp x0, x28
    b.ls 6f
    add x15, x7, x28
6:
    adrp x0, erase
    add x0, x0, :lo12:erase
    bl puts
    adrp x0, csi
    add x0, x0, :lo12:csi
    bl puts
    sub x0, x11, x25
    add x0, x0, #1
    bl number
    mov w0, #';'
    strb w0, [x15], #1
    sub x0, x14, x26
    add x0, x0, #1
    bl number
    adrp x0, frame_end
    add x0, x0, :lo12:frame_end
    bl puts
    adrp x1, frame
    add x1, x1, :lo12:frame
    sub x2, x15, x1
    mov x0, #1
    bl writeall
    ldp x29, x30, [sp], #16
    ret

quit:
    mov x0, #1
    adrp x1, clear
    add x1, x1, :lo12:clear
    mov x2, #13
    bl writeall
    mov x0, #0
    mov x1, #0x5402
    adrp x2, orig
    add x2, x2, :lo12:orig
    mov x8, #29
    svc #0
    mov x0, #0
    b exit
usage:
    adrp x1, usage_msg
    add x1, x1, :lo12:usage_msg
    mov x2, #17
    b error
term_error:
    adrp x1, error_msg
    add x1, x1, :lo12:error_msg
    mov x2, #22
error:
    mov x0, #2
    bl writeall
    mov x0, #1
exit:
    mov x8, #94
    svc #0
    b exit

.section .rodata
usage_msg:
    .ascii "usage: edit FILE\n"
error_msg:
    .ascii "edit: not a terminal\n"
frame_start:
    .asciz "\033[?25l\033[H"
row_end:
    .asciz "\033[K\r\n"
erase:
    .asciz "\033[K"
csi:
    .asciz "\033["
frame_end:
    .asciz "H\033[?25h"
clear:
    .ascii "\033[2J\033[H\033[?25h"
space:
    .asciz " "
modified:
    .asciz " [modified]"
warning:
    .asciz " unsaved changes: press Ctrl-Q again to quit"

.bss
.balign 16
orig:
    .zero 64
raw:
    .zero 64
winsize:
    .zero 8
input:
    .zero 8
textbuf:
    .zero CAP
frame:
    .zero 33554432
