"""Generate a tiny Windows nvcuda.dll for dlssg_for_sm86 under Proton.

Proton ships nvcuda.dll as an empty Wine stub, so dlssg_for_sm86 refuses to
start ("Missing cuInit"). On RTX 20/30 the mod uses the CUDA driver API only to
check the driver and describe the GPU; its kernels run through NVAPI
(NvAPI_D3D12_*Cu*), which DXVK-NVAPI and vkd3d-proton implement.

This module writes a minimal x64 PE DLL (one import from gdi32, ~2 KB) whose exports answer
those queries from a data block filled in for the local GPU:

    cuInit, cuDevicePrimaryCtxRelease(_v2), cuCtxPushCurrent(_v2)  -> CUDA_SUCCESS
    cuDriverGetVersion       -> CUDA 13.0 (driver R580+, the mod's floor)
    cuDeviceGetCount / Get   -> one device, ordinal 0
    cuDeviceComputeCapability, cuDeviceGetAttribute, cuDeviceGetName -> local GPU data
    cuDevicePrimaryCtxRetain, cuCtxPopCurrent(_v2) -> a dummy context handle
    cuDeviceGetLuid          -> LUID of the first adapter from gdi32!D3DKMTEnumAdapters2,
                                the same adapter list D3D12/DXGI use under Wine
    cuLink*                  -> CUDA_ERROR_NOT_SUPPORTED (PTX JIT is RTX 40 only)

The machine code below was assembled once with GNU as (source kept in
packaging/nvcuda.s); it is position independent and has no relocations.
"""
from __future__ import annotations

import re
import struct
import subprocess
from pathlib import Path

CUDA_VERSION = 13000  # R580 floor required by dlssg_for_sm86 0.4.x
DLL_NAME = "nvcuda.dll"
MARKER = b"dlss-swapper-linux fake nvcuda v3"
_MARKER_PREFIX = b"dlss-swapper-linux fake nvcuda"

CODE = bytes.fromhex(
    "b801000000c331c0c3b821030000c38b05fb000000eb0bb801000000eb0431c0eb004885c97402890131"
    "c0c34885c974088b05dd00000089014885d274088b05d4000000890231c0c34c8d0df0000000418b0185"
    "c0740a39d0740a4983c108ebef31c0ebbd418b4104ebb74885c9742385d27e1f4c8d0dc5010000ffca74"
    "11418a01880184c0740b49ffc148ffc1ebebc6010031c0c35356574881ec700100004889cb4889d6488d"
    "7c2430c74424201000000048897c2428488d4c2420ff156d00000085c07522837c242000741b4885db74"
    "07488b47044889034885f67406c7060100000031c0eb05b8e70300004881c4700100005f5e5bc34885c9"
    "740a488d051b00000048890131c0c30f1f440000")
OFF = {"DllMain": 0x00, "ret_ok": 0x06, "ret_unsupported": 0x09, "cuDriverGetVersion": 0x0F, "cuDeviceGetCount": 0x17, "cuDeviceGet": 0x1E, "cuDeviceComputeCapability": 0x2C, "cuDeviceGetAttribute": 0x49, "cuDeviceGetName": 0x6B, "cuDeviceGetLuid": 0x96, "ctx_out": 0xF9}
D_VERSION, D_MAJOR, D_MINOR, D_CTX, D_IAT, D_ATTRS, D_NAME, D_END = 0x110, 0x114, 0x118, 0x120, 0x130, 0x140, 0x240, 0x340
MAX_ATTRS = 31  # 32 slots, last one stays 0 as terminator

EXPORTS = {
    "cuInit": "ret_ok",
    "cuDriverGetVersion": "cuDriverGetVersion",
    "cuDeviceGetCount": "cuDeviceGetCount",
    "cuDeviceGet": "cuDeviceGet",
    "cuDeviceComputeCapability": "cuDeviceComputeCapability",
    "cuDeviceGetAttribute": "cuDeviceGetAttribute",
    "cuDeviceGetName": "cuDeviceGetName",
    "cuDeviceGetLuid": "cuDeviceGetLuid",
    "cuDevicePrimaryCtxRetain": "ctx_out",
    "cuDevicePrimaryCtxRelease": "ret_ok",
    "cuDevicePrimaryCtxRelease_v2": "ret_ok",
    "cuCtxPushCurrent": "ret_ok",
    "cuCtxPushCurrent_v2": "ret_ok",
    "cuCtxPopCurrent": "ctx_out",
    "cuCtxPopCurrent_v2": "ctx_out",
    "cuLinkCreate": "ret_unsupported",
    "cuLinkCreate_v2": "ret_unsupported",
    "cuLinkAddData": "ret_unsupported",
    "cuLinkAddData_v2": "ret_unsupported",
    "cuLinkComplete": "ret_unsupported",
    "cuLinkDestroy": "ret_unsupported",
}

# SM counts of common RTX 20/30 cards (CU_DEVICE_ATTRIBUTE_MULTIPROCESSOR_COUNT)
SM_COUNTS = {"2060": 30, "2060 super": 34, "2070": 36, "2070 super": 40, "2080": 46, "2080 super": 48,
             "2080 ti": 68, "3050": 20, "3060": 28, "3060 ti": 38, "3070": 46, "3070 ti": 48,
             "3080": 68, "3080 ti": 80, "3090": 82, "3090 ti": 84}


def gpu_params() -> dict:
    """Name, compute capability and PCI location of the first NVIDIA GPU."""
    p = {"name": "NVIDIA GeForce RTX 3060", "major": 8, "minor": 6, "bus": 0, "device": 0, "domain": 0}
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,compute_cap,pci.bus,pci.device,pci.domain",
                              "--format=csv,noheader"], capture_output=True, text=True, timeout=5).stdout
        line = out.strip().splitlines()[0]
        name, cc, bus, dev, dom = [x.strip() for x in line.split(",")]
        major, minor = cc.split(".")
        p.update(name=name, major=int(major), minor=int(minor), bus=int(bus, 16),
                 device=int(dev, 16), domain=int(dom, 16))
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        pass
    return p


def _attrs(p: dict) -> list[tuple[int, int]]:
    m = re.search(r"RTX\s*(\d{4}(?:\s*(?:ti|super))?)", p["name"], re.I)
    sms = SM_COUNTS.get(m.group(1).lower().replace("  ", " ") if m else "", 30)
    return [
        (75, p["major"]), (76, p["minor"]),       # COMPUTE_CAPABILITY_MAJOR / MINOR
        (16, sms),                                 # MULTIPROCESSOR_COUNT
        (1, 1024), (2, 1024), (3, 1024), (4, 2147483647), (5, 65535), (6, 65535),  # block / grid dims
        (8, 49152), (12, 65536), (10, 32),         # shared mem / regs per block, warp size
        (39, 1536), (81, 102400), (82, 65536), (97, 101376),  # threads, smem, regs per SM, smem optin
        (33, p["bus"]), (34, p["device"]), (50, p["domain"]),  # PCI location
        (88, 0), (89, 1), (95, 1),                 # integrated=0, unified addressing, compute preemption
    ]


def build(params: dict | None = None, version: int = CUDA_VERSION) -> bytes:
    p = params or gpu_params()
    data = bytearray(D_END - len(CODE))
    base = len(CODE)
    struct.pack_into("<III", data, D_VERSION - base, version, p["major"], p["minor"])
    for i, (k, v) in enumerate(_attrs(p)[:MAX_ATTRS]):
        struct.pack_into("<Ii", data, D_ATTRS - base + 8 * i, k, v)
    name = p["name"].encode()[:255]
    data[D_NAME - base:D_NAME - base + len(name)] = name
    return _pe(CODE + bytes(data))


def _pe(code: bytes) -> bytes:
    sect_rva, file_align = 0x1000, 0x200
    body = bytearray(code)
    body += MARKER + b"\0"
    while len(body) % 16:
        body.append(0)
    names = sorted(EXPORTS)
    n = len(names)
    exp_rva = sect_rva + len(body)
    eat, npt = exp_rva + 40, exp_rva + 40 + 4 * n
    ot, strings = npt + 4 * n, npt + 6 * n
    blob = bytearray(DLL_NAME.encode() + b"\0")
    name_rvas = []
    for e in names:
        name_rvas.append(strings + len(blob))
        blob += e.encode() + b"\0"
    exp = struct.pack("<IIHHIIIIIII", 0, 0, 0, 0, strings, 1, n, n, eat, npt, ot)
    exp += b"".join(struct.pack("<I", sect_rva + OFF[EXPORTS[e]]) for e in names)
    exp += b"".join(struct.pack("<I", r) for r in name_rvas)
    exp += b"".join(struct.pack("<H", i) for i in range(n)) + blob
    body += exp
    # import gdi32!D3DKMTEnumAdapters2 into the d_iat slot the code calls through
    while len(body) % 8:
        body.append(0)
    imp_rva = sect_rva + len(body)
    int_rva = imp_rva + 40                     # descriptor + null descriptor
    hint_rva = int_rva + 16
    dll_rva = hint_rva + 2 + len(b"D3DKMTEnumAdapters2\0") + 1
    imp = struct.pack("<IIIII", int_rva, 0, 0, dll_rva, sect_rva + D_IAT) + b"\0" * 20
    imp += struct.pack("<QQ", hint_rva, 0)
    imp += struct.pack("<H", 0) + b"D3DKMTEnumAdapters2\0"
    imp += b"\0" * (dll_rva - (imp_rva + len(imp))) + b"gdi32.dll\0"
    body += imp
    struct.pack_into("<QQ", body, D_IAT, hint_rva, 0)   # unbound IAT = same as INT
    vsize = len(body)
    raw = (vsize + file_align - 1) // file_align * file_align
    body += b"\0" * (raw - vsize)

    dos = bytearray(0x80)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x80)
    coff = struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, 0xF0, 0x2022)
    image = sect_rva + (vsize + 0xFFF) // 0x1000 * 0x1000
    opt = struct.pack("<HBBIIIII", 0x20B, 14, 0, raw, 0, 0, sect_rva + OFF["DllMain"], sect_rva)
    opt += struct.pack("<QIIHHHHHHIIIIHH", 0x180000000, 0x1000, file_align, 6, 0, 0, 0, 6, 0, 0,
                       image, 0x200, 0, 3, 0x0160)
    opt += struct.pack("<QQQQII", 0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    dirs = [(exp_rva, len(exp)), (imp_rva, 40)] + [(0, 0)] * 10 + [(sect_rva + D_IAT, 16)] + [(0, 0)] * 3
    opt += b"".join(struct.pack("<II", a, sz) for a, sz in dirs)
    sect = struct.pack("<8sIIIIIIHHI", b".text", vsize, sect_rva, raw, 0x200, 0, 0, 0, 0, 0xE0000020)  # RWX: loader writes the IAT slot
    hdr = bytes(dos) + b"PE\0\0" + coff + opt + sect
    return hdr + b"\0" * (0x200 - len(hdr)) + bytes(body)


def is_ours(path: Path) -> bool:
    try:
        return _MARKER_PREFIX in Path(path).read_bytes()[:8192]
    except OSError:
        return False


def is_current(path: Path, params: dict | None = None) -> bool:
    try:
        return Path(path).read_bytes() == build(params)
    except OSError:
        return False


def write(path: Path, params: dict | None = None) -> Path:
    path = Path(path)
    path.write_bytes(build(params))
    return path
