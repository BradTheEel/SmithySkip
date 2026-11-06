.text
.globl SmithySkip_AnimationThunk
.def SmithySkip_AnimationThunk; .scl 2; .type 32; .endef
.seh_proc SmithySkip_AnimationThunk
SmithySkip_AnimationThunk:
 subq $0xc8,%rsp
 .seh_stackalloc 0xc8
 .seh_endprologue
 movq %rax,0x20(%rsp)
 movq %rcx,0x28(%rsp)
 movq %rdx,0x30(%rsp)
 movq %r8,0x38(%rsp)
 movq %r9,0x40(%rsp)
 movq %r10,0x48(%rsp)
 movq %r11,0x50(%rsp)
 movups %xmm0,0x60(%rsp)
 movups %xmm1,0x70(%rsp)
 movups %xmm2,0x80(%rsp)
 movups %xmm3,0x90(%rsp)
 movups %xmm4,0xa0(%rsp)
 movups %xmm5,0xb0(%rsp)
 movq 0xc8(%rsp),%r8
 call SmithySkip_AnimationAdjust
 movss %xmm0,0x70(%rsp)
 movups 0x60(%rsp),%xmm0
 movups 0x70(%rsp),%xmm1
 movups 0x80(%rsp),%xmm2
 movups 0x90(%rsp),%xmm3
 movups 0xa0(%rsp),%xmm4
 movups 0xb0(%rsp),%xmm5
 movq 0x28(%rsp),%rcx
 movq 0x30(%rsp),%rdx
 movq 0x38(%rsp),%r8
 movq 0x40(%rsp),%r9
 movq 0x48(%rsp),%r10
 movq 0x50(%rsp),%r11
 movq 0x20(%rsp),%rax
 addq $0xc8,%rsp
 jmp *animation_trampoline(%rip)
.seh_endproc

