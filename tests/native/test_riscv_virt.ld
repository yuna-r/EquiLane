OUTPUT_ARCH(riscv)
ENTRY(_start)
SECTIONS
{
    . = 0x80000000;
    .text : { KEEP(*(.text.init)) *(.text .text.*) }
    .rodata : { *(.rodata .rodata.*) }
    .data : { *(.data .data.*) *(.sdata .sdata.*) }
    .bss : {
        __bss_start = .;
        *(.bss .bss.*) *(.sbss .sbss.*) *(COMMON)
        __bss_end = .;
    }
    . = ALIGN(16);
    . += 0x10000;
    __stack_top = .;
}
