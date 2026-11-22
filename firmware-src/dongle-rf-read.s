@ SPDX-License-Identifier: GPL-3.0-or-later
@ Firmware 1.03, optional 2.4 GHz RF receive-buffer diagnostic.
@ Replaces the single call at 0xD598 (CD94 flash read) with this wrapper.
@ Only address 0x843108 and a length <= 32 are read from RAM. Every other
@ request is delegated unchanged to 1.03's flash-read routine at 0xCD94.
@ This is a read-only diagnostic: does not change packets, link mode or USB.
@ The packet may contain transient inputs; its battery layout is UNKNOWN.
	.text
rf_read:
	tpush	{r4, lr}
	tloadr	r3, .Lrf_addr
	tcmp	r0, r3
	tjne	.Lflash
	tcmp	r2, #32
	tjhi	.Lflash
	tmovs	r4, #0
.Lcopy:
	tcmp	r4, r2
	tjeq	.Ldone
	tloadrb	r3, [r0, r4]
	tstorerb	r3, [r1, r4]
	tadd	r4, #1
	tj	.Lcopy
.Ldone:
	tmovs	r0, #1
	tj	.Lexit
.Lflash:
	tjl	flash_read
.Lexit:
	tpop	{r4, pc}
	.balign	4
.Lrf_addr:
	.word	0x843108
