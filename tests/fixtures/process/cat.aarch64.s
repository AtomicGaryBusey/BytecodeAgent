// copy stdin to stdout through a 4 KiB .bss buffer, handling short reads and writes
    .globl _start
    .text
_start:
.Lread:
    mov x0, #0
    adrp x1, buf
    add x1, x1, :lo12:buf
    mov x2, #4096
    mov x8, #63             // read
    svc #0
    cmp x0, #0
    b.le .Ldone
    mov x19, x0
    adrp x20, buf
    add x20, x20, :lo12:buf
.Lwrite:
    mov x0, #1
    mov x1, x20
    mov x2, x19
    mov x8, #64             // write
    svc #0
    add x20, x20, x0
    subs x19, x19, x0
    b.ne .Lwrite
    b .Lread
.Ldone:
    mov x0, #0
    mov x8, #94             // exit_group
    svc #0

    .bss
buf:
    .zero 4096
