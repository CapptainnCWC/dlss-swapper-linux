"""Valve KeyValues: text (.vdf/.acf) and binary (shortcuts.vdf) formats."""
from __future__ import annotations

import struct


# ---------------------------------------------------------------- text format
def loads(text: str) -> dict:
    tokens = _tokenize(text)
    root: dict = {}
    stack = [root]
    key = None
    for kind, val in tokens:
        if kind == "{":
            if key is None:
                raise ValueError("unexpected {")
            new: dict = {}
            stack[-1][key] = new
            stack.append(new)
            key = None
        elif kind == "}":
            if len(stack) == 1:
                raise ValueError("unexpected }")
            stack.pop()
        else:
            if key is None:
                key = val
            else:
                stack[-1][key] = val
                key = None
    return root


def _tokenize(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j == -1 else j
        elif c in "{}":
            yield c, c
            i += 1
        elif c == '"':
            i += 1
            buf = []
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(nxt, "\\" + nxt))
                    i += 2
                else:
                    buf.append(text[i])
                    i += 1
            i += 1
            yield "s", "".join(buf)
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n{}"':
                j += 1
            yield "s", text[i:j]
            i = j


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def dumps(d: dict, indent: int = 0) -> str:
    out = []
    tab = "\t" * indent
    for k, v in d.items():
        if isinstance(v, dict):
            out.append(f'{tab}"{_esc(k)}"\n{tab}{{\n{dumps(v, indent + 1)}{tab}}}\n')
        else:
            out.append(f'{tab}"{_esc(k)}"\t\t"{_esc(str(v))}"\n')
    return "".join(out)


def get_ci(d: dict, key: str):
    """Case-insensitive dict lookup returning (real_key, value)."""
    if key in d:
        return key, d[key]
    lk = key.lower()
    for k, v in d.items():
        if k.lower() == lk:
            return k, v
    return None, None


# -------------------------------------------------------------- binary format
def binary_loads(data: bytes) -> dict:
    pos = 0

    def read_cstr() -> str:
        nonlocal pos
        end = data.index(b"\x00", pos)
        s = data[pos:end].decode("utf-8", "replace")
        pos = end + 1
        return s

    def read_map() -> dict:
        nonlocal pos
        res: dict = {}
        while True:
            t = data[pos]
            pos += 1
            if t == 0x08:
                return res
            name = read_cstr()
            if t == 0x00:
                res[name] = read_map()
            elif t == 0x01:
                res[name] = read_cstr()
            elif t == 0x02:
                res[name] = ("int32", struct.unpack_from("<i", data, pos)[0])
                pos += 4
            elif t == 0x07:
                res[name] = ("uint64", struct.unpack_from("<Q", data, pos)[0])
                pos += 8
            elif t == 0x03:
                res[name] = ("float", struct.unpack_from("<f", data, pos)[0])
                pos += 4
            else:
                raise ValueError(f"unknown binary vdf type {t:#x}")

    return read_map()


def binary_dumps(d: dict) -> bytes:
    out = bytearray()

    def write_map(m: dict) -> None:
        for k, v in m.items():
            kb = k.encode() + b"\x00"
            if isinstance(v, dict):
                out.append(0x00)
                out.extend(kb)
                write_map(v)
            elif isinstance(v, tuple):
                kind, num = v
                code, fmt = {"int32": (0x02, "<i"), "uint64": (0x07, "<Q"), "float": (0x03, "<f")}[kind]
                out.append(code)
                out.extend(kb)
                out.extend(struct.pack(fmt, num))
            else:
                out.append(0x01)
                out.extend(kb)
                out.extend(str(v).encode() + b"\x00")
        out.append(0x08)

    write_map(d)
    return bytes(out)
