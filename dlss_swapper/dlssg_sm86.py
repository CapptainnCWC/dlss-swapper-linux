"""Native DLSS Frame Generation on RTX 20/30 via sdli1995/dlssg_for_sm86.

The project ships a proxy DLL (version.dll by default), dlssg_sm86.ini and a
dlssg_sm86\\ folder with its backend, kernel packs and the stock DLSS-G runtime.
The proxy spoofs the GPU architecture for Streamline/the game only (not for
NVIDIA's own components) and runs NVIDIA's DLSS-G with its own kernels.

Under Proton we only need Wine to load the proxy (WINEDLLOVERRIDES=<proxy>=n,b)
and DXVK-NVAPI enabled. A global DXVK_NVAPI_GPU_ARCH spoof must NOT be used:
the project documents GPU hangs when NVIDIA's own components see a fake arch.

Files are downloaded from the author's GitHub releases (MIT) and verified
against the SHA-256 list in the release's manifest.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import launchopts
from .library import DATA_DIR, http_get
from .optiscaler import ini_set
from .swapper import load_state, save_state

REPO = "sdli1995/dlssg_for_sm86"
PROJECT_URL = f"https://github.com/{REPO}"
API = f"https://api.github.com/repos/{REPO}/releases"
BUNDLE_DIR = DATA_DIR / "dlssg_sm86"
STATE_KEY = "dlssg_sm86"
PROXIES = ["version.dll", "winmm.dll", "dbghelp.dll", "dinput8.dll", "dxgi.dll", "d3d12.dll"]
FRAMES = {1: "2X", 2: "3X", 3: "4X", 5: "6X"}
ENGINES = {"DLSS": "DLSS-G (NVIDIA)", "XeSS": "XeSS-FG (до 4X)"}
PRESETS = {"A": "A — стандарт", "B": "B — чище интерфейс", "C": "C — Lite, быстрее"}
SR_MODES = {"Off": "Выключено", "M-INT8": "Пресет M, INT8", "L-INT8": "Пресет L, INT8"}
DLSSG_MARKERS = ("sl.dlss_g.dll", "nvngx_dlssg.dll")


# ---------------------------------------------------------------- bundles
@dataclass
class Bundle:
    version: str
    path: Path


def local_bundles() -> list[Bundle]:
    if not BUNDLE_DIR.is_dir():
        return []
    out = [Bundle(p.name, p) for p in BUNDLE_DIR.iterdir()
           if (p / "version.dll").is_file() and (p / "dlssg_sm86").is_dir()]
    return sorted(out, key=lambda b: [int(x) if x.isdigit() else 0 for x in re.split(r"\D+", b.version)],
                  reverse=True)


def latest_release_info() -> tuple[str, str, str]:
    for rel in json.loads(http_get(API + "?per_page=5")):
        if rel.get("draft") or rel.get("prerelease"):
            continue
        for a in rel.get("assets", []):
            if a["name"].lower().endswith(".zip"):
                return rel["tag_name"].lstrip("v"), a["name"], a["browser_download_url"]
    raise RuntimeError("в релизах dlssg_for_sm86 не найден zip-архив")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(root: Path) -> list[str]:
    """Check files against manifest.json; returns a list of problems."""
    mf = root / "manifest.json"
    if not mf.is_file():
        return ["нет manifest.json — проверка целостности невозможна"]
    problems = []
    for e in json.loads(mf.read_text()).get("files", []):
        p = root / e["path"]
        if not p.is_file():
            problems.append(f"нет файла {e['path']}")
        elif e.get("sha256") and _sha256(p) != e["sha256"].lower():
            problems.append(f"повреждён {e['path']}")
    return problems


def import_bundle(src: Path, version: str | None = None) -> Bundle:
    src = Path(src)
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=BUNDLE_DIR) as td:
        tmp = Path(td) / "x"
        if src.is_dir():
            shutil.copytree(src, tmp)
        else:
            with zipfile.ZipFile(src) as z:
                dest = tmp.resolve()
                for m in z.namelist():
                    if not str((tmp / m).resolve()).startswith(str(dest)):
                        raise ValueError(f"unsafe path {m}")
                z.extractall(tmp)
        root = next((Path(r) for r, _d, f in os.walk(tmp) if "version.dll" in f and "dlssg_sm86.ini" in f), None)
        if root is None:
            raise FileNotFoundError("в архиве нет version.dll + dlssg_sm86.ini")
        problems = verify(root)
        if problems and (root / "manifest.json").is_file():
            raise IOError("проверка SHA-256 не прошла: " + "; ".join(problems[:5]))
        if not version:
            try:
                version = json.loads((root / "manifest.json").read_text())["bundle"]["version"]
            except (OSError, KeyError, ValueError):
                m = re.search(r"(\d+\.\d+\.\d+)", src.name)
                version = m.group(1) if m else src.stem
        version = re.sub(r"[^\w.\-]", "_", version)
        final = BUNDLE_DIR / version
        if final.exists():
            shutil.rmtree(final)
        shutil.move(str(root), final)
    return Bundle(version, final)


def download_latest(progress=None) -> Bundle:
    ver, name, url = latest_release_info()
    for b in local_bundles():
        if b.version == ver:
            return b
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    arch = BUNDLE_DIR / name
    arch.write_bytes(http_get(url, progress, timeout=900))
    try:
        return import_bundle(arch, ver)
    finally:
        arch.unlink(missing_ok=True)


# --------------------------------------------------------------- install
@dataclass
class Options:
    proxy: str = "version.dll"
    engine: str = "DLSS"
    max_frames: int = 1          # 1 = 2X, 3 = 4X, 5 = 6X
    preset: str = "A"
    super_resolution: str = "Off"
    verbose_log: bool = False


@dataclass
class Result:
    target: Path
    placed: list[str]
    backups: dict[str, str]
    warnings: list[str]


def game_has_dlssg(game_dir: Path) -> bool:
    for _r, _d, files in os.walk(game_dir):
        if any(f.lower() in DLSSG_MARKERS for f in files):
            return True
    return False


def installed(game_dir: Path) -> dict:
    return load_state(game_dir).get(STATE_KEY, {})


def launch_env(opts: Options) -> tuple[dict[str, str], dict[str, str]]:
    return {"PROTON_ENABLE_NVAPI": "1"}, {opts.proxy[:-4]: "n,b"}


def install(game_dir: Path, target_dir: Path, bundle: Bundle, opts: Options) -> Result:
    game_dir, target_dir = Path(game_dir), Path(target_dir)
    rel_target = str(target_dir.relative_to(game_dir)) if target_dir != game_dir else "."
    if rel_target in installed(game_dir):
        uninstall(game_dir, rel_target)
    state = load_state(game_dir)
    warnings: list[str] = []
    if not game_has_dlssg(game_dir):
        warnings.append("В игре нет DLSS Frame Generation (sl.dlss_g.dll / nvngx_dlssg.dll). "
                        "Мод включает только встроенный в игру DLSS-G — здесь он не сработает.")
    for k, v in state.get("optiscaler", {}).items():
        if v.get("proxy", "").lower() == opts.proxy.lower():
            raise RuntimeError(f"OptiScaler уже установлен как {opts.proxy} — выберите другое имя прокси")
        if v.get("fg_mode", "none") != "none":
            warnings.append("Установлен OptiScaler с Frame Generation — переключите его FG на "
                            "«Выключено», иначе они будут конфликтовать.")
    if opts.engine == "XeSS" and opts.max_frames > 3:
        opts.max_frames = 3
        warnings.append("XeSS-FG поддерживает максимум 4X — выставлено 4X.")

    placed: list[str] = []
    backups: dict[str, str] = {}

    def place(src: Path, dst: Path) -> None:
        rel = str(dst.relative_to(game_dir))
        if dst.exists():
            bak = dst.with_name(dst.name + ".dlssg-bak")
            if not bak.exists():
                os.replace(dst, bak)
                backups[rel] = str(bak.relative_to(game_dir))
            else:
                dst.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        placed.append(rel)

    proxy_src = bundle.path / ("version.dll" if opts.proxy == "version.dll" else f"alternatives/{opts.proxy}")
    if not proxy_src.is_file():
        raise FileNotFoundError(f"в сборке нет {proxy_src.name}")
    place(proxy_src, target_dir / opts.proxy)
    place(bundle.path / "dlssg_sm86.ini", target_dir / "dlssg_sm86.ini")
    skip = set()
    if opts.super_resolution == "Off":
        skip.add("sr")       # 180 MB, only needed for INT8 super resolution
    if opts.engine != "XeSS":
        skip.add("xefg")
    src_root = bundle.path / "dlssg_sm86"
    for root, dirs, files in os.walk(src_root):
        rel_root = Path(root).relative_to(src_root)
        if rel_root.parts and rel_root.parts[0] in skip:
            continue
        for fn in files:
            s = Path(root) / fn
            place(s, target_dir / "dlssg_sm86" / s.relative_to(src_root))

    ini = target_dir / "dlssg_sm86.ini"
    text = ini.read_text(errors="replace")
    for sec, key, val in [("General", "Enabled", "1"), ("FrameGeneration", "Engine", opts.engine),
                          ("FrameGeneration", "MaxGeneratedFrames", str(opts.max_frames)),
                          ("DLSSG", "Preset", opts.preset),
                          ("SuperResolution", "Mode", opts.super_resolution),
                          ("Logging", "Level", "2" if opts.verbose_log else "1")]:
        text, ok = ini_set(text, sec, key, val)
        if not ok:
            warnings.append(f"нет ключа [{sec}] {key} в dlssg_sm86.ini")
    ini.write_text(text)

    state.setdefault(STATE_KEY, {})[rel_target] = {
        "version": bundle.version, "proxy": opts.proxy, "engine": opts.engine,
        "max_frames": opts.max_frames, "placed": placed, "backups": backups,
    }
    save_state(game_dir, state)
    return Result(target_dir, placed, backups, warnings)


def set_launch_undo(game_dir: Path, rel_target: str, undo: dict | None) -> None:
    state = load_state(game_dir)
    inst = state.get(STATE_KEY, {}).get(rel_target)
    if inst is not None:
        inst["launch_undo"] = undo
        save_state(game_dir, state)


def uninstall(game_dir: Path, rel_target: str | None = None) -> list[dict]:
    game_dir = Path(game_dir)
    state = load_state(game_dir)
    insts = state.get(STATE_KEY, {})
    removed = []
    for k in ([rel_target] if rel_target else list(insts)):
        inst = insts.pop(k, None)
        if not inst:
            continue
        for rel in inst.get("placed", []):
            (game_dir / rel).unlink(missing_ok=True)
        for rel, bak in inst.get("backups", {}).items():
            if (game_dir / bak).exists():
                os.replace(game_dir / bak, game_dir / rel)
        mod_dir = game_dir / k / "dlssg_sm86"
        logs = mod_dir / "logs"
        if logs.is_dir():
            shutil.rmtree(logs, ignore_errors=True)
        for root, dirs, files in os.walk(mod_dir, topdown=False):
            if not os.listdir(root):
                os.rmdir(root)
        removed.append(inst)
    save_state(game_dir, state)
    return removed


def log_path(game_dir: Path, rel_target: str) -> Path:
    return Path(game_dir) / rel_target / "dlssg_sm86" / "logs" / "dlssg_sm86.log"


def revert_launch_options(current: str, inst: dict) -> str:
    undo = inst.get("launch_undo")
    return launchopts.revert(current, undo) if undo else current
