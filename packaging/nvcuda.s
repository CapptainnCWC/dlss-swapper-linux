.intel_syntax noprefix
.text
DllMain:            mov eax, 1
                    ret
ret_ok:             xor eax, eax
                    ret
ret_unsupported:    mov eax, 801
                    ret
cuDriverGetVersion: mov eax, [rip + d_version]
                    jmp store_rcx
cuDeviceGetCount:   mov eax, 1
                    jmp store_rcx
cuDeviceGet:        xor eax, eax
                    jmp store_rcx
store_rcx:          test rcx, rcx
                    jz 1f
                    mov [rcx], eax
1:                  xor eax, eax
                    ret
cuDeviceComputeCapability:
                    test rcx, rcx
                    jz 1f
                    mov eax, [rip + d_major]
                    mov [rcx], eax
1:                  test rdx, rdx
                    jz 2f
                    mov eax, [rip + d_minor]
                    mov [rdx], eax
2:                  xor eax, eax
                    ret
cuDeviceGetAttribute:
                    lea r9, [rip + d_attrs]
3:                  mov eax, [r9]
                    test eax, eax
                    jz 4f
                    cmp eax, edx
                    je 5f
                    add r9, 8
                    jmp 3b
4:                  xor eax, eax
                    jmp store_rcx
5:                  mov eax, [r9 + 4]
                    jmp store_rcx
cuDeviceGetName:
                    test rcx, rcx
                    jz 9f
                    test edx, edx
                    jle 9f
                    lea r9, [rip + d_name]
6:                  dec edx
                    jz 8f
                    mov al, [r9]
                    mov [rcx], al
                    test al, al
                    jz 9f
                    inc r9
                    inc rcx
                    jmp 6b
8:                  mov byte ptr [rcx], 0
9:                  xor eax, eax
                    ret
# cuDeviceGetLuid(char *luid, unsigned *nodeMask, CUdevice dev):
# ask the kernel thunk D3DKMTEnumAdapters2 (gdi32) for adapter LUIDs - the same
# list D3D12/DXGI use under Wine - and return the first one.
cuDeviceGetLuid:
                    push rbx
                    push rsi
                    push rdi
                    sub rsp, 368
                    mov rbx, rcx
                    mov rsi, rdx
                    lea rdi, [rsp + 48]
                    mov dword ptr [rsp + 32], 16
                    mov [rsp + 40], rdi
                    lea rcx, [rsp + 32]
                    call [rip + d_iat]
                    test eax, eax
                    jnz 7f
                    cmp dword ptr [rsp + 32], 0
                    je 7f
                    test rbx, rbx
                    jz 1f
                    mov rax, [rdi + 4]
                    mov [rbx], rax
1:                  test rsi, rsi
                    jz 2f
                    mov dword ptr [rsi], 1
2:                  xor eax, eax
                    jmp 3f
7:                  mov eax, 999
3:                  add rsp, 368
                    pop rdi
                    pop rsi
                    pop rbx
                    ret
ctx_out:            test rcx, rcx
                    jz 1f
                    lea rax, [rip + d_ctx]
                    mov [rcx], rax
1:                  xor eax, eax
                    ret
.p2align 4
d_version:          .long 13000
d_major:            .long 8
d_minor:            .long 6
d_pad:              .long 0
d_ctx:              .quad 0, 0
d_iat:              .quad 0, 0
d_attrs:            .fill 64, 4, 0
d_name:             .fill 256, 1, 0
d_end:
