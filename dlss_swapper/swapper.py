"""Scan game folders for upscaler DLLs, swap versions, restore originals."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import pe
from .library import DLL_TYPES, FILENAME_TO_TYPE, DllRecord

STATE_FILE = ".dlss-swapper-linux.json"
BACKUP_SUFFIX = ".dlsss"  # same convention as DLSS Swapper on Windows

ANTI_CHEAT_MARKERS = ("easyanticheat", "battleye", "beservice", "start_protected_game",
                      "vgc.exe", "vgk.sys", "nprotect", "xigncode", "anticheatexpert", "ace-base")
EXE_BLACKLIST = ("unins", "crash", "redist", "vc_redist", "vcredist", "dxsetup", "dotnet",
                 "setup", "installer", "ue4prereq", "ueprereq", "easyanticheat", "battleye",
                 "beservice", "cefprocess", "quicksfv", "report", "helper", "7z", "touchup",
                 "physx", "oalinst", "directx")


@dataclass
class FoundDll:
    kind: str
    path: Path
    version: str | None
    swapped: bool = False
    original_version: str | None = None
    owned_by_optiscaler: bool = False

    @property
    def label(self) -> str:
        return DLL_TYPES[self.kind][1]


# ------------------------------------------------------------------ state
def load_state(game_dir: Path) -> dict:
    f = Path(game_dir) / STATE_FILE
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return {}


def save_state(game_dir: Path, state: dict) -> None:
    f = Path(game_dir) / STATE_FILE
    if not any(state.values()):
        f.unlink(missing_ok=True)
        return
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    os.replace(tmp, f)


# ------------------------------------------------------------------- scan
def scan(game_dir: Path) -> list[FoundDll]:
    game_dir = Path(game_dir)
    state = load_state(game_dir)
    swaps = state.get("swaps", {})
    opti_files = set()
    for key in ("optiscaler", "dlssg_sm86"):
        for inst in state.get(key, {}).values():
            opti_files.update(inst.get("placed", []))
    found = []
    for root, dirs, files in os.walk(game_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in ("__pycache__",)]
        for fn in files:
            kind = FILENAME_TO_TYPE.get(fn.lower())
            if not kind:
                continue
            p = Path(root) / fn
            rel = str(p.relative_to(game_dir))
            sw = swaps.get(rel)
            found.append(FoundDll(kind, p, pe.file_version(p), bool(sw),
                                  sw.get("orig_version") if sw else None, rel in opti_files))
    found.sort(key=lambda f: (list(DLL_TYPES).index(f.kind), str(f.path)))
    return found


def anti_cheat(game_dir: Path) -> list[str]:
    """Detect kernel/user-mode anti-cheat that may ban for injected DLLs."""
    hits = set()
    base_depth = len(Path(game_dir).parts)
    for root, dirs, files in os.walk(game_dir):
        if len(Path(root).parts) - base_depth >= 4:
            dirs[:] = []
        for n in dirs + files:
            ln = n.lower()
            if any(ln == m or ln.startswith(m) for m in ANTI_CHEAT_MARKERS):
                hits.add(n)
    return sorted(hits)


def exe_candidates(game_dir: Path) -> list[Path]:
    """Directories with game executables, best guess first (where OptiScaler goes)."""
    game_dir = Path(game_dir)
    scored: dict[Path, float] = {}
    for root, dirs, files in os.walk(game_dir):
        rl = root.lower()
        if "/engine/" in rl + "/" and "binaries" in rl:
            continue  # UE Engine/Binaries is never the right folder
        names_l = {f.lower() for f in files}
        for fn in files:
            fl = fn.lower()
            if not fl.endswith(".exe") or any(b in fl for b in EXE_BLACKLIST):
                continue
            p = Path(root) / fn
            try:
                size = p.stat().st_size
            except OSError:
                continue
            score = min(size / (1 << 20), 200)  # bigger exe -> more likely the game
            if fl.endswith("-win64-shipping.exe") or fl.endswith("-wingdk-shipping.exe"):
                score += 500
            if "launcher" in fl:
                score -= 150
            if names_l & {"nvngx_dlss.dll", "sl.interposer.dll", "nvngx_dlssg.dll"}:
                score += 300
            if names_l & {"d3d12.dll", "steam_api64.dll"}:
                score += 30
            d = Path(root)
            scored[d] = max(scored.get(d, -1e9), score)
    return [d for d, _ in sorted(scored.items(), key=lambda kv: -kv[1])]


# ------------------------------------------------------------- swap/restore
def swap(game_dir: Path, dll: FoundDll, rec: DllRecord, src: Path) -> None:
    game_dir = Path(game_dir)
    state = load_state(game_dir)
    swaps = state.setdefault("swaps", {})
    rel = str(dll.path.relative_to(game_dir))
    backup = dll.path.with_name(dll.path.name + BACKUP_SUFFIX)
    if rel not in swaps:
        if not backup.exists():
            shutil.copy2(dll.path, backup)
        swaps[rel] = {"backup": str(backup.relative_to(game_dir)),
                      "orig_version": dll.version, "orig_md5": pe.md5(backup)}
    tmp = dll.path.with_name(dll.path.name + ".new")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dll.path)
    swaps[rel]["current"] = rec.version
    if pe.md5(dll.path) == swaps[rel]["orig_md5"]:
        # swapped back to the original: drop the backup bookkeeping
        backup.unlink(missing_ok=True)
        del swaps[rel]
    save_state(game_dir, state)


def restore(game_dir: Path, dll: FoundDll) -> bool:
    game_dir = Path(game_dir)
    state = load_state(game_dir)
    rel = str(dll.path.relative_to(game_dir))
    info = state.get("swaps", {}).get(rel)
    backup = dll.path.with_name(dll.path.name + BACKUP_SUFFIX)
    if info:
        backup = game_dir / info["backup"]
    if not backup.exists():
        return False
    os.replace(backup, dll.path)
    state.get("swaps", {}).pop(rel, None)
    save_state(game_dir, state)
    return True


def restore_all(game_dir: Path) -> int:
    n = 0
    for d in scan(game_dir):
        if d.swapped and restore(game_dir, d):
            n += 1
    return n
