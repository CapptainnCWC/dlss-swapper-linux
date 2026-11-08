"""Merge / un-merge environment variables and WINEDLLOVERRIDES in Steam launch options.

Steam launch options look like:  VAR=1 OTHER="a b" %command% -game-args
Everything before %command% is treated as env assignments (or wrapper commands
like gamemoderun / mangohud, which are kept untouched).
"""
from __future__ import annotations

import re
import shlex

_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)


def split(opts: str) -> tuple[list[str], str]:
    """Return (prefix tokens, suffix) where suffix includes %command% and args."""
    opts = opts.strip()
    if "%command%" not in opts:
        # plain arguments for the game
        return [], "%command%" + (" " + opts if opts else "")
    pre, post = opts.split("%command%", 1)
    try:
        tokens = shlex.split(pre)
    except ValueError:
        tokens = pre.split()
    return tokens, "%command%" + post


def _quote(tok: str) -> str:
    m = _ASSIGN.match(tok)
    if m:
        k, v = m.groups()
        return f"{k}={shlex.quote(v)}" if v and re.search(r"[^\w@%+=:,./-]", v) else tok
    return shlex.quote(tok)


def join(tokens: list[str], suffix: str) -> str:
    return " ".join([_quote(t) for t in tokens] + [suffix]).strip()


def _env_index(tokens: list[str]) -> dict[str, int]:
    out = {}
    for i, t in enumerate(tokens):
        m = _ASSIGN.match(t)
        if m:
            out[m.group(1)] = i
    return out


def _parse_overrides(v: str) -> dict[str, str]:
    res = {}
    for part in v.split(";"):
        if "=" in part:
            k, val = part.split("=", 1)
            for name in k.split(","):
                if name.strip():
                    res[name.strip()] = val
    return res


def apply(opts: str, env: dict[str, str], dll_overrides: dict[str, str]) -> tuple[str, dict]:
    """Add vars/overrides. Returns new string and an 'undo' record."""
    tokens, suffix = split(opts)
    idx = _env_index(tokens)
    undo = {"env": {}, "overrides": {}}
    for k, v in env.items():
        if k in idx:
            old = _ASSIGN.match(tokens[idx[k]]).group(2)
            undo["env"][k] = old
            tokens[idx[k]] = f"{k}={v}"
        else:
            undo["env"][k] = None
            tokens.insert(0, f"{k}={v}")
            idx = _env_index(tokens)
    if dll_overrides:
        idx = _env_index(tokens)
        cur = _parse_overrides(_ASSIGN.match(tokens[idx["WINEDLLOVERRIDES"]]).group(2)) \
            if "WINEDLLOVERRIDES" in idx else {}
        for k, v in dll_overrides.items():
            undo["overrides"][k] = cur.get(k)
            cur[k] = v
        val = ";".join(f"{k}={v}" for k, v in cur.items())
        if "WINEDLLOVERRIDES" in idx:
            tokens[idx["WINEDLLOVERRIDES"]] = f"WINEDLLOVERRIDES={val}"
        else:
            tokens.insert(0, f"WINEDLLOVERRIDES={val}")
    return join(tokens, suffix), undo


def revert(opts: str, undo: dict) -> str:
    tokens, suffix = split(opts)
    for k, old in undo.get("env", {}).items():
        idx = _env_index(tokens)
        if k not in idx:
            continue
        if old is None:
            tokens.pop(idx[k])
        else:
            tokens[idx[k]] = f"{k}={old}"
    ov = undo.get("overrides", {})
    if ov:
        idx = _env_index(tokens)
        if "WINEDLLOVERRIDES" in idx:
            cur = _parse_overrides(_ASSIGN.match(tokens[idx["WINEDLLOVERRIDES"]]).group(2))
            for k, old in ov.items():
                if old is None:
                    cur.pop(k, None)
                else:
                    cur[k] = old
            if cur:
                tokens[idx["WINEDLLOVERRIDES"]] = "WINEDLLOVERRIDES=" + ";".join(
                    f"{k}={v}" for k, v in cur.items())
            else:
                tokens.pop(idx["WINEDLLOVERRIDES"])
    result = join(tokens, suffix)
    return "" if result == "%command%" else result
