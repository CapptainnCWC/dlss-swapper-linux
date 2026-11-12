"""Small edits to a Wine prefix's registry files (system.reg / user.reg).

Used to report "Hardware-accelerated GPU scheduling" as enabled: some games
(e.g. Black Myth: Wukong) do not ask the driver but read the Windows setting
HKLM\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers\\HwSchMode (2 = on)
and grey out DLSS Frame Generation when it is missing. Wine has no such value.

The file must not be edited while the prefix's wineserver is running (it would
overwrite our change on exit), so callers check that the game is not running.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

# Wine stores CurrentControlSet as a link to ControlSet001 and saves values under the latter.
HAGS_KEY = r"System\\ControlSet001\\Control\\GraphicsDrivers"
HAGS_KEY_ALIASES = (HAGS_KEY, r"System\\CurrentControlSet\\Control\\GraphicsDrivers")
HAGS_VALUE = "HwSchMode"


class PrefixBusyError(RuntimeError):
    pass


def prefix_busy(pfx: Path | None = None) -> bool:
    """True if any wineserver is running (a running prefix would overwrite system.reg on exit)."""
    try:
        return subprocess.run(["pgrep", "-x", "wineserver"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def _section_re(key: str) -> re.Pattern:
    return re.compile(r"^\[" + re.escape(key) + r"\](?: \d+)?\n", re.M | re.I)


def get_dword(reg: Path, key: str, name: str) -> int | None:
    text = Path(reg).read_text(errors="replace")
    m = _section_re(key).search(text)
    if not m:
        return None
    body = text[m.end():].split("\n\n", 1)[0]
    v = re.search(r'^"' + re.escape(name) + r'"=dword:([0-9a-fA-F]{8})$', body, re.M)
    return int(v.group(1), 16) if v else None


def set_dword(reg: Path, key: str, name: str, value: int) -> bool:
    """Set a REG_DWORD; returns True if the file changed. Keeps a one-time backup."""
    reg = Path(reg)
    if get_dword(reg, key, name) == value:
        return False
    text = reg.read_text(errors="replace")
    line = f'"{name}"=dword:{value:08x}'
    m = _section_re(key).search(text)
    if m:
        end = text.find("\n\n", m.end())
        end = len(text) if end == -1 else end
        body = text[m.end():end]
        pat = re.compile(r'^"' + re.escape(name) + r'"=.*$', re.M)
        body = pat.sub(line, body) if pat.search(body) else body.rstrip("\n") + "\n" + line
        text = text[:m.end()] + body.lstrip("\n") + (text[end:] if end < len(text) else "\n")
    else:
        text = text.rstrip("\n") + f"\n\n[{key}] {int(time.time())}\n#time=0\n{line}\n"
    bak = reg.with_name(reg.name + ".dlss-swapper-bak")
    if not bak.exists():
        shutil.copy2(reg, bak)
    tmp = reg.with_name(reg.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(reg)
    return True


def delete_value(reg: Path, key: str, name: str) -> bool:
    reg = Path(reg)
    text = reg.read_text(errors="replace")
    m = _section_re(key).search(text)
    if not m:
        return False
    end = text.find("\n\n", m.end())
    end = len(text) if end == -1 else end
    body = text[m.end():end]
    new = re.sub(r'^"' + re.escape(name) + r'"=.*\n?', "", body, flags=re.M)
    if new == body:
        return False
    if not body.endswith("\n"):
        new = new.rstrip("\n")  # removed the last line of the section
    child = re.compile(r"^\[" + re.escape(key) + r"\\\\", re.M | re.I)
    if not re.search(r"^[^#\s]", new, re.M) and not child.search(text):
        # key is now empty and has no subkeys: drop its section
        before, after = text[:m.start()].rstrip("\n"), text[end:].lstrip("\n")
        reg.write_text(before + ("\n\n" + after if after else "\n"))
    else:
        reg.write_text(text[:m.end()] + new + text[end:])
    return True


# DXVK-NVAPI turns DXVK_NVAPI_SET_NGX_DEBUG_OPTIONS into these NGX registry values and they stay
# after the variable is gone, so one launch with the indicator keeps it on for good.
NGX_KEY = r"Software\\NVIDIA Corporation\\Global\\NGXCore"
NGX_INDICATOR_VALUES = ("DLSSG_IndicatorText", "ShowDlssIndicator", "DLSSIndicator")


def clear_ngx_indicators(pfx_dir: Path) -> bool:
    """Remove NVIDIA's on-screen DLSS / DLSS-G indicator switches from pfx/system.reg."""
    reg = Path(pfx_dir) / "pfx/system.reg"
    if not reg.is_file():
        return False
    return any([delete_value(reg, NGX_KEY, v) for v in NGX_INDICATOR_VALUES])


def hags_value(pfx_dir: Path) -> int | None:
    reg = Path(pfx_dir) / "pfx/system.reg"
    if not reg.is_file():
        return None
    for key in HAGS_KEY_ALIASES:
        v = get_dword(reg, key, HAGS_VALUE)
        if v is not None:
            return v
    return None


def enable_hags(pfx_dir: Path) -> bool:
    """pfx_dir = compatdata/<appid>; sets HwSchMode=2 in pfx/system.reg."""
    reg = Path(pfx_dir) / "pfx/system.reg"
    if not reg.is_file() or hags_value(pfx_dir) == 2:
        return False
    return set_dword(reg, HAGS_KEY, HAGS_VALUE, 2)


def disable_hags(pfx_dir: Path) -> bool:
    reg = Path(pfx_dir) / "pfx/system.reg"
    return reg.is_file() and any([delete_value(reg, k, HAGS_VALUE) for k in HAGS_KEY_ALIASES])
