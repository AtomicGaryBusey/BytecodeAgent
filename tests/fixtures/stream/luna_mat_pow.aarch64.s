mat_pow:
stp x29, x30, [sp, #-64]!
stp x19, x20, [sp, #16]
stp x21, x22, [sp, #32]
stp x23, x24, [sp, #48]
mov x19, x0
mov x20, x1
mov x21, x2
mov x22, x3
mov x23, x4
mul x24, x20, x20
mov x9, x22
mov x10, x24
.Lzero_out:
cbz x10, .Linit_diag
str xzr, [x9], #8
sub x10, x10, #1
b .Lzero_out
.Linit_diag:
mov x9, x22
mov x10, x20
add x11, x20, #1
lsl x11, x11, #3
.Ldiag_loop:
cbz x10, .Lpower_loop
mov x12, #1
str x12, [x9]
add x9, x9, x11
sub x10, x10, #1
b .Ldiag_loop
.Lpower_loop:
cbz x21, .Lclear_tmp
mov x17, x20, lsl #3
mov x9, #0
mov x10, #0
.Lrow_loop:
cmp x9, x20
b.ge .Lcopy_product
mov x11, #0
add x12, x23, x10
.Lcol_loop:
cmp x11, x20
b.ge .Lnext_row
mov x13, #0
mov x14, #0
add x15, x22, x10
add x16, x19, x11, lsl #3
.Ldot_loop:
cmp x14, x20
b.ge .Lstore_product
ldr x0, [x15]
ldr x1, [x16]
mul x0, x0, x1
add x13, x13, x0
add x15, x15, #8
add x16, x16, x17
add x14, x14, #1
b .Ldot_loop
.Lstore_product:
str x13, [x12], #8
add x11, x11, #1
b .Lcol_loop
.Lnext_row:
add x10, x10, x17
add x9, x9, #1
b .Lrow_loop
.Lcopy_product:
mov x9, x23
mov x10, x22
mov x11, x24
.Lcopy_loop:
cbz x11, .Lfinish_product
ldr x12, [x9], #8
str x12, [x10], #8
sub x11, x11, #1
b .Lcopy_loop
.Lfinish_product:
sub x21, x21, #1
b .Lpower_loop
.Lclear_tmp:
mov x9, x23
lsl x10, x24, #1
.Lclear_loop:
cbz x10, .Lreturn
str xzr, [x9], #8
sub x10, x10, #1
b .Lclear_loop
.Lreturn:
mov x0, #0
ldp x19, x20, [sp, #16]
ldp x21, x22, [sp, #32]
ldp x23, x24, [sp, #48]
ldp x29, x30, [sp], #64
ret