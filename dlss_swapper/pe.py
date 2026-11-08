"""Read file version from Windows PE files (VS_FIXEDFILEINFO) without Windows APIs."""
from __future__ import annotations

import hashlib
import mmap
import os
import struct

_SIG = struct.pack("<I", 0xFEEF04BD)


def file_version(path: str | os.PathLike) -> str | None:
    try:
        with open(path, "rb") as f:
            if os.fstat(f.fileno()).st_size < 64:
                return None
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                if mm[:2] != b"MZ":
                    return None
                # resources are usually near the end -> search backwards
                pos = mm.rfind(_SIG)
                while pos != -1:
                    ms, ls = struct.unpack_from("<II", mm, pos + 8)
                    struc_ver = struct.unpack_from("<I", mm, pos + 4)[0]
                    if struc_ver in (0x00010000, 0x00000000):
                        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
                    pos = mm.rfind(_SIG, 0, pos)
    except (OSError, ValueError):
        return None
    return None


def version_key(v: str | None) -> tuple[int, ...]:
    if not v:
        return (0,)
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def md5(path: str | os.PathLike) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()
