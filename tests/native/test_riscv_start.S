.section .text.init
.globl _start
_start:
    csrr t0, mhartid
    bnez t0, .Lhalt
    la sp, __stack_top
    li t0, 0x6600
    csrs mstatus, t0
    csrw fcsr, zero
    csrw vcsr, zero
    la t0, .Ltrap
    csrw mtvec, t0
    la t0, __bss_start
    la t1, __bss_end
.Lbss:
    bgeu t0, t1, .Lrun
    sb zero, 0(t0)
    addi t0, t0, 1
    j .Lbss
.Lrun:
    call riscv_smoke_main
    beqz a0, .Lpass
    li t1, 0x13333
    j .Lexit
.Lpass:
    li t1, 0x5555
.Lexit:
    li t0, 0x100000
    sw t1, 0(t0)
.Lhalt:
    wfi
    j .Lhalt
.balign 4
.Ltrap:
    li t0, 0x10000000
    li t1, 69
    sb t1, 0(t0)
    li t1, 10
    sb t1, 0(t0)
    li t0, 0x100000
    li t1, 0x23333
    sw t1, 0(t0)
    j .Lhalt
