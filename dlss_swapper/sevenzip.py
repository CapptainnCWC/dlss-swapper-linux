"""Minimal pure-Python 7z extractor (stdlib only).

Supports what OptiScaler / mod archives typically use: LZMA, LZMA2, BCJ (x86),
ARM64, Delta and Copy coders in a simple chain, with compressed ("encoded")
headers. BCJ2, PPMd and encrypted archives are not supported -- for those the
caller falls back to an external 7z binary.
"""
from __future__ import annotations

import io
import lzma
import os
import re
import struct
import zlib
from dataclasses import dataclass, field

SIGNATURE = b"7z\xbc\xaf\x27\x1c"


class SevenZipError(Exception):
    pass


class Unsupported(SevenZipError):
    pass


# property ids
K_END, K_HEADER, K_ARCHIVE_PROPS, K_ADD_STREAMS, K_MAIN_STREAMS, K_FILES = range(6)
K_PACK_INFO, K_UNPACK_INFO, K_SUBSTREAMS, K_SIZE, K_CRC, K_FOLDER = range(6, 12)
K_CODERS_UNPACK_SIZE, K_NUM_UNPACK_STREAM, K_EMPTY_STREAM, K_EMPTY_FILE = range(12, 16)
K_NAME = 0x11
K_ATTRIBUTES = 0x15
K_ENCODED_HEADER = 0x17
K_DUMMY = 0x19


@dataclass
class Coder:
    method: bytes
    num_in: int
    num_out: int
    props: bytes


@dataclass
class Folder:
    coders: list[Coder]
    bind_pairs: list[tuple[int, int]]
    packed: list[int]
    unpack_sizes: list[int] = field(default_factory=list)
    crc: int | None = None

    def main_unpack_size(self) -> int:
        bound_out = {o for _, o in self.bind_pairs}
        for i, size in enumerate(self.unpack_sizes):
            if i not in bound_out:
                return size
        raise SevenZipError("folder without main output stream")


class _Reader:
    def __init__(self, data: bytes):
        self.b = io.BytesIO(data)

    def byte(self) -> int:
        c = self.b.read(1)
        if not c:
            raise SevenZipError("unexpected end of header")
        return c[0]

    def read(self, n: int) -> bytes:
        d = self.b.read(n)
        if len(d) != n:
            raise SevenZipError("unexpected end of header")
        return d

    def num(self) -> int:
        first = self.byte()
        mask = 0x80
        value = 0
        for i in range(8):
            if first & mask == 0:
                high = first & (mask - 1)
                value |= high << (8 * i)
                return value
            value |= self.byte() << (8 * i)
            mask >>= 1
        return value

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def bits(self, n: int) -> list[bool]:
        out, mask, cur = [], 0, 0
        for _ in range(n):
            if mask == 0:
                cur, mask = self.byte(), 0x80
            out.append(bool(cur & mask))
            mask >>= 1
        return out

    def bits_all_defined(self, n: int) -> list[bool]:
        if self.byte():
            return [True] * n
        return self.bits(n)


@dataclass
class _Streams:
    pack_pos: int = 0
    pack_sizes: list[int] = field(default_factory=list)
    folders: list[Folder] = field(default_factory=list)
    substream_counts: list[int] = field(default_factory=list)
    substream_sizes: list[int] = field(default_factory=list)


def _read_digests(r: _Reader, n: int) -> list[int | None]:
    defined = r.bits_all_defined(n)
    return [r.u32() if d else None for d in defined]


def _read_pack_info(r: _Reader, s: _Streams) -> None:
    s.pack_pos = r.num()
    n = r.num()
    while True:
        t = r.num()
        if t == K_END:
            break
        if t == K_SIZE:
            s.pack_sizes = [r.num() for _ in range(n)]
        elif t == K_CRC:
            _read_digests(r, n)
        else:
            raise SevenZipError(f"bad pack info id {t}")


def _read_folder(r: _Reader) -> Folder:
    coders = []
    for _ in range(r.num()):
        flags = r.byte()
        method = r.read(flags & 0x0F)
        if flags & 0x10:
            nin, nout = r.num(), r.num()
        else:
            nin, nout = 1, 1
        props = r.read(r.num()) if flags & 0x20 else b""
        if flags & 0x80:
            raise Unsupported("alternative coder methods")
        coders.append(Coder(method, nin, nout, props))
    total_in = sum(c.num_in for c in coders)
    total_out = sum(c.num_out for c in coders)
    binds = [(r.num(), r.num()) for _ in range(total_out - 1)]
    npacked = total_in - len(binds)
    if npacked == 1:
        bound_in = {i for i, _ in binds}
        packed = [next(i for i in range(total_in) if i not in bound_in)]
    else:
        packed = [r.num() for _ in range(npacked)]
    return Folder(coders, binds, packed)


def _read_unpack_info(r: _Reader, s: _Streams) -> None:
    if r.num() != K_FOLDER:
        raise SevenZipError("expected folder")
    n = r.num()
    if r.byte() != 0:
        raise Unsupported("external folders")
    s.folders = [_read_folder(r) for _ in range(n)]
    if r.num() != K_CODERS_UNPACK_SIZE:
        raise SevenZipError("expected unpack sizes")
    for f in s.folders:
        f.unpack_sizes = [r.num() for _ in range(sum(c.num_out for c in f.coders))]
    while True:
        t = r.num()
        if t == K_END:
            break
        if t == K_CRC:
            for f, crc in zip(s.folders, _read_digests(r, n)):
                f.crc = crc
        else:
            raise SevenZipError(f"bad unpack info id {t}")


def _read_substreams(r: _Reader, s: _Streams) -> None:
    s.substream_counts = [1] * len(s.folders)
    t = r.num()
    if t == K_NUM_UNPACK_STREAM:
        s.substream_counts = [r.num() for _ in s.folders]
        t = r.num()
    sizes = []
    has_sizes = t == K_SIZE
    for f, cnt in zip(s.folders, s.substream_counts):
        if cnt == 0:
            continue
        total = 0
        if has_sizes:
            for _ in range(cnt - 1):
                v = r.num()
                sizes.append(v)
                total += v
        sizes.append(f.main_unpack_size() - total)
    if has_sizes:
        t = r.num()
    while t != K_END:
        if t == K_CRC:
            unknown = sum(
                cnt for f, cnt in zip(s.folders, s.substream_counts)
                if not (cnt == 1 and f.crc is not None)
            )
            _read_digests(r, unknown)
        else:
            raise SevenZipError(f"bad substreams id {t}")
        t = r.num()
    s.substream_sizes = sizes


def _read_streams_info(r: _Reader) -> _Streams:
    s = _Streams()
    while True:
        t = r.num()
        if t == K_END:
            break
        if t == K_PACK_INFO:
            _read_pack_info(r, s)
        elif t == K_UNPACK_INFO:
            _read_unpack_info(r, s)
        elif t == K_SUBSTREAMS:
            _read_substreams(r, s)
        else:
            raise SevenZipError(f"bad streams info id {t}")
    if not s.substream_counts:
        s.substream_counts = [1] * len(s.folders)
        s.substream_sizes = [f.main_unpack_size() for f in s.folders]
    return s


def _lzma_filter(c: Coder) -> dict:
    m = c.method
    if m == b"\x21":  # LZMA2
        p = c.props[0]
        dict_size = 0xFFFFFFFF if p == 40 else (2 | (p & 1)) << (p // 2 + 11)
        return {"id": lzma.FILTER_LZMA2, "dict_size": dict_size}
    if m == b"\x03\x01\x01":  # LZMA
        d = c.props[0]
        lc, rem = d % 9, d // 9
        lp, pb = rem % 5, rem // 5
        dict_size = struct.unpack("<I", c.props[1:5])[0]
        return {"id": lzma.FILTER_LZMA1, "lc": lc, "lp": lp, "pb": pb, "dict_size": dict_size}
    if m == b"\x03\x03\x01\x03":
        return {"id": lzma.FILTER_X86}
    if m == b"\x0a":
        return {"id": lzma.FILTER_ARM64} if hasattr(lzma, "FILTER_ARM64") else _unsup(m)
    if m == b"\x03":
        return {"id": lzma.FILTER_DELTA, "dist": c.props[0] + 1}
    return _unsup(m)


def _unsup(m: bytes):
    names = {b"\x03\x03\x01\x1b": "BCJ2", b"\x03\x04\x01": "PPMd", b"\x06\xf1\x07\x01": "AES"}
    raise Unsupported(f"7z coder {names.get(m, m.hex())} is not supported by the built-in extractor")


BCJ2_ID = b"\x03\x03\x01\x1b"
COPY_ID = b"\x00"
_LZ_IDS = (b"\x21", b"\x03\x01\x01")
_J_RE = re.compile(rb"[\xe8\xe9]|(?<=\x0f)[\x80-\x8f]")


def _bcj2_decode(main: bytes, call: bytes, jump: bytes, rc: bytes, out_size: int) -> bytes:
    """Classic 7-Zip BCJ2 (x86 branch converter, 4 input streams)."""
    out = bytearray()
    probs = [1024] * 258
    rng, code = 0xFFFFFFFF, 0
    rc_pos = 5
    for i in range(5):
        code = (code << 8) | rc[i]
    mpos = cpos = jpos = 0
    prev = 0
    mlen = len(main)
    while len(out) < out_size:
        # find next candidate byte in main stream
        if mpos < mlen and prev == 0x0F and 0x80 <= main[mpos] <= 0x8F:
            i = mpos
        elif mpos < mlen and main[mpos] in (0xE8, 0xE9):
            i = mpos
        else:
            m = _J_RE.search(main, mpos + 1) if mpos < mlen else None
            if m is None:
                out += main[mpos:]
                break
            i = m.start()
        need = out_size - len(out)
        if i - mpos + 1 > need:
            out += main[mpos:mpos + need]
            break
        out += main[mpos:i + 1]
        b = main[i]
        if i > mpos:
            prev = main[i - 1]
        mpos = i + 1
        if len(out) >= out_size:
            break
        idx = prev if b == 0xE8 else (256 if b == 0xE9 else 257)
        p = probs[idx]
        bound = (rng >> 11) * p
        if code < bound:
            rng = bound
            probs[idx] = p + ((2048 - p) >> 5)
            bit = 0
        else:
            rng -= bound
            code -= bound
            probs[idx] = p - (p >> 5)
            bit = 1
        if rng < 0x1000000:
            rng = (rng << 8) & 0xFFFFFFFF
            code = ((code << 8) | rc[rc_pos]) & 0xFFFFFFFF
            rc_pos += 1
        if bit:
            if b == 0xE8:
                src = struct.unpack_from(">I", call, cpos)[0]
                cpos += 4
            else:
                src = struct.unpack_from(">I", jump, jpos)[0]
                jpos += 4
            dest = (src - (len(out) + 4)) & 0xFFFFFFFF
            out += struct.pack("<I", dest)
            prev = dest >> 24
        else:
            prev = b
    return bytes(out[:out_size])


def _decode_folder(fp, s: _Streams, folder_index: int) -> bytes:
    f = s.folders[folder_index]
    pack_index = sum(len(x.packed) for x in s.folders[:folder_index])
    pack_base = 32 + s.pack_pos + sum(s.pack_sizes[:pack_index])

    # stream index tables
    in_start, out_start, i_acc, o_acc = [], [], 0, 0
    for c in f.coders:
        in_start.append(i_acc)
        out_start.append(o_acc)
        i_acc += c.num_in
        o_acc += c.num_out
    bind_in_to_out = dict(f.bind_pairs)

    def coder_of_out(o: int) -> int:
        for ci, c in enumerate(f.coders):
            if out_start[ci] <= o < out_start[ci] + c.num_out:
                return ci
        raise SevenZipError("bad out stream index")

    def read_packed(in_idx: int) -> bytes:
        k = f.packed.index(in_idx)
        off = pack_base + sum(s.pack_sizes[pack_index:pack_index + k])
        fp.seek(off)
        return fp.read(s.pack_sizes[pack_index + k])

    def decode_in(in_idx: int) -> bytes:
        if in_idx in bind_in_to_out:
            return decode_out(bind_in_to_out[in_idx])
        return read_packed(in_idx)

    def decode_out(o: int) -> bytes:
        ci = coder_of_out(o)
        c = f.coders[ci]
        size = f.unpack_sizes[o]
        if c.method == BCJ2_ID:
            ins = [decode_in(in_start[ci] + k) for k in range(4)]
            return _bcj2_decode(*ins, size)
        if c.num_in != 1 or c.num_out != 1:
            raise Unsupported(f"coder {c.method.hex()} with multiple streams")
        # gather a chain of simple coders that lzma can run as one filter list
        chain = [c]
        cur = ci
        while chain[-1].method not in _LZ_IDS:
            nxt = in_start[cur]
            if nxt not in bind_in_to_out:
                break
            cur = coder_of_out(bind_in_to_out[nxt])
            nc = f.coders[cur]
            if nc.num_in != 1 or nc.num_out != 1:
                break
            chain.append(nc)
        src = decode_in(in_start[cur])
        filters = [_lzma_filter(x) for x in chain if x.method != COPY_ID]
        if not filters:
            data = src
        else:
            if filters[-1]["id"] not in (lzma.FILTER_LZMA1, lzma.FILTER_LZMA2):
                raise Unsupported("filter chain without LZMA compressor")
            dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
            data = dec.decompress(src, max_length=size)
        if len(data) < size:
            raise SevenZipError("truncated 7z stream")
        return data[:size]

    bound_out = {o for _, o in f.bind_pairs}
    main_out = next(i for i in range(len(f.unpack_sizes)) if i not in bound_out)
    data = decode_out(main_out)
    if f.crc is not None and zlib.crc32(data) != f.crc:
        raise SevenZipError("CRC mismatch")
    return data


@dataclass
class Entry:
    name: str
    is_dir: bool
    size: int


class SevenZipFile:
    def __init__(self, path: str | os.PathLike):
        self.path = os.fspath(path)
        self._fp = open(self.path, "rb")
        sig = self._fp.read(32)
        if sig[:6] != SIGNATURE:
            raise SevenZipError("not a 7z archive")
        next_off, next_size, _crc = struct.unpack("<QQI", sig[12:32])
        self._fp.seek(32 + next_off)
        header = self._fp.read(next_size)
        r = _Reader(header)
        t = r.num()
        while t == K_ENCODED_HEADER:
            s = _read_streams_info(r)
            header = _decode_folder(self._fp, s, 0)
            r = _Reader(header)
            t = r.num()
        if t != K_HEADER:
            raise SevenZipError("bad 7z header")
        self._parse_header(r)

    def _parse_header(self, r: _Reader) -> None:
        self.streams = _Streams()
        names: list[str] = []
        empty_stream: list[bool] = []
        empty_file: list[bool] = []
        attrs: list[int | None] = []
        while True:
            t = r.num()
            if t == K_END:
                break
            if t == K_ARCHIVE_PROPS:
                while r.num() != K_END:
                    r.read(r.num())
            elif t == K_ADD_STREAMS:
                _read_streams_info(r)
            elif t == K_MAIN_STREAMS:
                self.streams = _read_streams_info(r)
            elif t == K_FILES:
                nfiles = r.num()
                empty_stream = [False] * nfiles
                attrs = [None] * nfiles
                while True:
                    pt = r.num()
                    if pt == K_END:
                        break
                    size = r.num()
                    blob = r.read(size)
                    pr = _Reader(blob)
                    if pt == K_EMPTY_STREAM:
                        empty_stream = pr.bits(nfiles)
                    elif pt == K_EMPTY_FILE:
                        empty_file = pr.bits(sum(empty_stream))
                    elif pt == K_NAME:
                        if pr.byte() != 0:
                            raise Unsupported("external names")
                        names = blob[1:].decode("utf-16-le").split("\x00")[:nfiles]
                    elif pt == K_ATTRIBUTES:
                        defined = pr.bits_all_defined(nfiles)
                        if pr.byte() != 0:
                            raise Unsupported("external attributes")
                        attrs = [pr.u32() if d else None for d in defined]
            else:
                raise SevenZipError(f"bad header id {t}")
        self.entries: list[Entry] = []
        self._map: list[tuple[int, int, int] | None] = []  # folder, offset, size
        empty_i = 0
        folder_i, sub_i, global_sub, offset = 0, 0, 0, 0
        counts = self.streams.substream_counts
        for i, name in enumerate(names):
            name = name.replace("\\", "/")
            if empty_stream and empty_stream[i]:
                is_file = empty_file[empty_i] if empty_i < len(empty_file) else False
                empty_i += 1
                is_dir = not is_file
                self.entries.append(Entry(name, is_dir, 0))
                self._map.append(None)
                continue
            while folder_i < len(counts) and sub_i >= counts[folder_i]:
                folder_i, sub_i, offset = folder_i + 1, 0, 0
            size = self.streams.substream_sizes[global_sub]
            self.entries.append(Entry(name, False, size))
            self._map.append((folder_i, offset, size))
            offset += size
            sub_i += 1
            global_sub += 1

    def getnames(self) -> list[str]:
        return [e.name for e in self.entries]

    def extractall(self, dest: str | os.PathLike) -> list[str]:
        dest = os.path.realpath(os.fspath(dest))
        cache: dict[int, bytes] = {}
        written = []
        for e, m in zip(self.entries, self._map):
            target = os.path.realpath(os.path.join(dest, e.name))
            if not (target == dest or target.startswith(dest + os.sep)):
                raise SevenZipError(f"unsafe path in archive: {e.name}")
            if e.is_dir:
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if m is None:
                open(target, "wb").close()
            else:
                folder, off, size = m
                if folder not in cache:
                    cache.clear()
                    cache[folder] = _decode_folder(self._fp, self.streams, folder)
                with open(target, "wb") as out:
                    out.write(cache[folder][off:off + size])
            written.append(e.name)
        return written

    def close(self) -> None:
        self._fp.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
