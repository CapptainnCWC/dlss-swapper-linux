"""OptiScaler download / install / uninstall for Proton games.

Frame Generation unlock for GPUs without native DLSS FG (RTX 20/30, AMD, Intel):
the game's own DLSS-FG (Streamline) inputs are taken by OptiScaler and fed to
FSR 3/4 FG (OptiFG), Nukem's dlssg-to-fsr3 or XeFG. OptiScaler spoofs the GPU
for Streamline so the in-game "DLSS Frame Generation" toggle becomes available.
On Linux/Proton with NVIDIA we additionally ask DXVK-NVAPI to report an Ada GPU
(DXVK_NVAPI_GPU_ARCH=AD100) and tell Wine to load OptiScaler's proxy DLL
(WINEDLLOVERRIDES=<proxy>=n,b).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import launchopts, sevenzip
from .library import DATA_DIR, http_get
from .swapper import load_state, save_state

GITHUB_API = "https://api.github.com/repos/optiscaler/OptiScaler/releases"
BUNDLE_DIR = DATA_DIR / "optiscaler"
PROXY_NAMES = ["dxgi.dll", "winmm.dll", "version.dll", "dbghelp.dll", "d3d12.dll",
               "wininet.dll", "winhttp.dll"]
SKIP_FILES = {"setup_linux.sh", "setup_windows.bat", "optiscaler.dll"}
LOG_FILES = ["OptiScaler.log", "fakenvapi.log", "dlssg_to_fsr3.log"]

FG_MODES = {
    "none": "Без FG (только апскейлер)",
    "optifg": "DLSS FG → FSR FG (OptiFG, рекомендуется)",
    "nukems": "DLSS FG → FSR3 FG (Nukem dlssg-to-fsr3)",
    "xefg": "DLSS FG → XeSS FG",
    "upscaler": "FG из апскейлера (игры без DLSS FG)",
}
FG_SHORT = {
    "none": "Выключено",
    "optifg": "FSR FG (OptiFG)",
    "nukems": "FSR3 FG (Nukem)",
    "xefg": "XeSS FG",
    "upscaler": "Из апскейлера",
}
FG_HINTS = {
    "none": "Только апскейлер, без генерации кадров",
    "optifg": "DLSS FG игры → FSR FG. Рекомендуется",
    "nukems": "DLSS FG игры → dlssg-to-fsr3",
    "xefg": "DLSS FG игры → XeSS FG",
    "upscaler": "Для игр без DLSS FG. Включается в меню OptiScaler",
}
UPSCALERS =["dlss", "fsr31", "xess", "fsr22", "auto"]


# ---------------------------------------------------------------- bundles
@dataclass
class Bundle:
    tag: str
    path: Path

    @property
    def has_nukem(self) -> bool:
        return (self.path / "dlssg_to_fsr3_amd_is_better.dll").is_file()

    @property
    def has_fakenvapi(self) -> bool:
        return (self.path / "fakenvapi.dll").is_file()


def local_bundles() -> list[Bundle]:
    if not BUNDLE_DIR.is_dir():
        return []
    out = [Bundle(p.name, p) for p in BUNDLE_DIR.iterdir() if (p / "OptiScaler.dll").is_file()]
    return sorted(out, key=lambda b: b.path.stat().st_mtime, reverse=True)


def latest_release_info() -> tuple[str, str, str]:
    """(tag, asset_name, url) of the newest OptiScaler release."""
    rels = json.loads(http_get(GITHUB_API + "?per_page=5"))
    for rel in rels:
        if rel.get("draft"):
            continue
        for a in rel.get("assets", []):
            n = a["name"].lower()
            if n.endswith((".7z", ".zip")) and "optiscaler" in n:
                return rel["tag_name"], a["name"], a["browser_download_url"]
    raise RuntimeError("не найден архив OptiScaler в релизах GitHub")


def extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as z:
            for m in z.namelist():
                t = (dest / m).resolve()
                if not str(t).startswith(str(dest.resolve())):
                    raise ValueError(f"unsafe path {m}")
            z.extractall(dest)
        return
    for tool in ("7zz", "7z", "7za"):
        exe = shutil.which(tool)
        if exe:
            subprocess.run([exe, "x", "-y", f"-o{dest}", str(archive)], check=True,
                           stdout=subprocess.DEVNULL)
            return
    try:
        with sevenzip.SevenZipFile(archive) as z:
            z.extractall(dest)
    except sevenzip.Unsupported as e:
        if shutil.which("bsdtar"):
            subprocess.run(["bsdtar", "-xf", str(archive), "-C", str(dest)], check=True)
            return
        raise RuntimeError(f"{e}. Установите 7zip (sudo apt install 7zip) "
                           "или распакуйте архив сами и укажите папку.") from e


def _normalize_bundle(tmp: Path) -> Path:
    """Archives sometimes contain a top-level folder; find the one with OptiScaler.dll."""
    for root, _dirs, files in os.walk(tmp):
        if "OptiScaler.dll" in files:
            return Path(root)
    raise FileNotFoundError("в архиве нет OptiScaler.dll")


def import_bundle(src: Path, tag: str | None = None) -> Bundle:
    """Import an OptiScaler archive (.7z/.zip) or an already extracted folder."""
    src = Path(src)
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=BUNDLE_DIR) as td:
        tmp = Path(td)
        if src.is_dir():
            shutil.copytree(src, tmp / "x")
        else:
            extract(src, tmp / "x")
        root = _normalize_bundle(tmp / "x")
        if not tag:
            m = re.search(r"(\d+\.\d+\.\d+[a-z]?)", src.name)
            tag = ("v" + m.group(1)) if m else src.stem
        tag = re.sub(r"[^\w.\-]", "_", tag)
        final = BUNDLE_DIR / tag
        if final.exists():
            shutil.rmtree(final)
        shutil.move(str(root), final)
    return Bundle(tag, final)


def download_latest(progress=None) -> Bundle:
    tag, name, url = latest_release_info()
    for b in local_bundles():
        if b.tag == tag:
            return b
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    arch = BUNDLE_DIR / name
    arch.write_bytes(http_get(url, progress, timeout=600))
    try:
        return import_bundle(arch, tag)
    finally:
        arch.unlink(missing_ok=True)


# ------------------------------------------------------------------ ini
def ini_set(text: str, section: str, key: str, value: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    cur = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
            continue
        if cur == section and re.match(rf"^{re.escape(key)}\s*=", s):
            nl = "\r\n" if line.endswith("\r\n") else "\n"
            lines[i] = f"{key}={value}{nl}"
            return "".join(lines), True
    return text, False


def ini_get(text: str, section: str, key: str) -> str | None:
    cur = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
        elif cur == section and re.match(rf"^{re.escape(key)}\s*=", s):
            return s.split("=", 1)[1].strip()
    return None


@dataclass
class InstallOptions:
    proxy: str = "dxgi.dll"
    fg_mode: str = "optifg"
    upscaler: str = "dlss"           # DX12/Vulkan/DX11 upscaler OptiScaler should use
    nvidia: bool = True
    # DXVK_NVAPI_GPU_ARCH=AD100. Off by default: NVIDIA's own components (DLSS SR via NGX) also
    # see the fake arch and may launch Ada kernels on Ampere -> GPU hang (Xid 13/32).
    spoof_ada: bool = False
    hudfix: bool = False             # OptiFG HUDFix; OptiScaler warns it can crash
    overwrite_game_files: bool = False
    dlss_preset: int | None = None   # 0=default, 11=K, 13=M ...
    extra_ini: dict[str, str] = field(default_factory=dict)  # "Section.Key" -> value


def ini_settings(opts: InstallOptions) -> list[tuple[str, str, str]]:
    s: list[tuple[str, str, str]] = []
    if opts.upscaler != "auto":
        dx11 = "dlss" if opts.upscaler == "dlss" else {"fsr31": "fsr31_12", "xess": "xess_12"}.get(
            opts.upscaler, opts.upscaler)
        s += [("Upscalers", "Dx12Upscaler", opts.upscaler),
              ("Upscalers", "VulkanUpscaler", opts.upscaler if opts.upscaler != "xess" else "xess"),
              ("Upscalers", "Dx11Upscaler", dx11)]
    if opts.fg_mode == "none":
        s += [("FrameGen", "Enabled", "false"), ("FrameGen", "FGInput", "nofg"),
              ("FrameGen", "FGOutput", "nofg")]
    else:
        fg_in, fg_out = {"optifg": ("dlssg", "fsrfg"), "nukems": ("nukems", "nukems"),
                         "xefg": ("dlssg", "xefg"), "upscaler": ("upscaler", "fsrfg")}[opts.fg_mode]
        s += [("FrameGen", "Enabled", "true"), ("FrameGen", "FGInput", fg_in),
              ("FrameGen", "FGOutput", fg_out), ("Menu", "OverlayMenu", "true"),
              ("OptiFG", "HUDFix", "true" if opts.hudfix else "false")]
        if uses_dlssg_input(opts.fg_mode):
            # unlock the game's DLSS-G toggle: spoof only for Streamline, not globally
            s += [("Spoofing", "StreamlineSpoofing", "true"), ("Spoofing", "SpoofHAGS", "true")]
        else:
            s.append(("Spoofing", "StreamlineSpoofing", "false"))
    if opts.nvidia:
        # keep the real GPU visible to the game, only Streamline is spoofed
        s.append(("Spoofing", "Dxgi", "false"))
    if opts.dlss_preset is not None:
        s += [("DLSS", "RenderPresetOverride", "true" if opts.dlss_preset else "false"),
              ("DLSS", "RenderPresetForAll", str(opts.dlss_preset))]
    for k, v in opts.extra_ini.items():
        sec, key = k.split(".", 1)
        s.append((sec, key, v))
    return s


def uses_dlssg_input(fg_mode: str) -> bool:
    """Modes that take the game's own DLSS-G inputs (need the in-game DLSS FG toggle)."""
    return fg_mode in ("optifg", "nukems", "xefg")


def launch_env(opts: InstallOptions) -> tuple[dict[str, str], dict[str, str]]:
    env: dict[str, str] = {}
    if opts.nvidia:
        env["PROTON_ENABLE_NVAPI"] = "1"
        if opts.spoof_ada and uses_dlssg_input(opts.fg_mode):
            env["DXVK_NVAPI_GPU_ARCH"] = "AD100"
    overrides = {opts.proxy[:-4] if opts.proxy.endswith(".dll") else opts.proxy: "n,b"}
    return env, overrides


# --------------------------------------------------------------- install
@dataclass
class InstallResult:
    target: Path
    placed: list[str]
    skipped: list[str]
    backups: dict[str, str]
    ini_missing: list[str]
    warnings: list[str]


def installed(game_dir: Path) -> dict:
    return load_state(game_dir).get("optiscaler", {})


def install(game_dir: Path, target_dir: Path, bundle: Bundle, opts: InstallOptions) -> InstallResult:
    game_dir, target_dir = Path(game_dir), Path(target_dir)
    rel_target = str(target_dir.relative_to(game_dir)) if target_dir != game_dir else "."
    state = load_state(game_dir)
    if rel_target in state.get("optiscaler", {}):
        uninstall(game_dir, rel_target)
        state = load_state(game_dir)
    warnings: list[str] = []
    names_l = {p.name.lower() for p in target_dir.iterdir()}
    if opts.fg_mode in ("optifg", "nukems", "xefg") and not any(
            f.lower() == "nvngx_dlssg.dll" for _r, _d, fs in os.walk(game_dir) for f in fs):
        warnings.append("В игре не найден nvngx_dlssg.dll: режимы с входом DLSS FG могут не "
                        "заработать. Попробуйте режим 'upscaler'.")
    if opts.fg_mode == "nukems" and not bundle.has_nukem:
        warnings.append("В сборке OptiScaler нет dlssg_to_fsr3_amd_is_better.dll — "
                        "положите его в папку игры вручную.")

    placed, skipped, backups = [], [], {}

    def place(src: Path, dst: Path):
        rel = str(dst.relative_to(game_dir))
        if dst.exists():
            if dst.name.lower() == opts.proxy.lower() or opts.overwrite_game_files:
                bak = dst.with_name(dst.name + ".optiscaler-bak")
                if not bak.exists():
                    os.replace(dst, bak)
                    backups[rel] = str(bak.relative_to(game_dir))
                else:
                    dst.unlink()
            else:
                skipped.append(rel)
                return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        placed.append(rel)

    for root, _dirs, files in os.walk(bundle.path):
        for fn in files:
            if fn.lower() in SKIP_FILES or fn.startswith("!!"):
                continue
            src = Path(root) / fn
            place(src, target_dir / src.relative_to(bundle.path))
    place(bundle.path / "OptiScaler.dll", target_dir / opts.proxy)
    if opts.proxy.lower() in names_l and opts.proxy.lower() == "dxgi.dll":
        warnings.append("В папке уже был dxgi.dll (ReShade/другой мод?) — он сохранён как "
                        "dxgi.dll.optiscaler-bak. Если игра не запустится, выберите winmm.dll.")

    # configure ini
    ini_missing = []
    ini = target_dir / "OptiScaler.ini"
    if ini.is_file():
        text = ini.read_text(errors="replace")
        for sec, key, val in ini_settings(opts):
            text, ok = ini_set(text, sec, key, val)
            if not ok:
                ini_missing.append(f"[{sec}] {key}")
        ini.write_text(text)

    state.setdefault("optiscaler", {})[rel_target] = {
        "version": bundle.tag, "proxy": opts.proxy, "fg_mode": opts.fg_mode,
        "placed": placed, "backups": backups,
    }
    save_state(game_dir, state)
    return InstallResult(target_dir, placed, skipped, backups, ini_missing, warnings)


def set_launch_undo(game_dir: Path, rel_target: str, undo: dict | None) -> None:
    state = load_state(game_dir)
    inst = state.get("optiscaler", {}).get(rel_target)
    if inst is not None:
        inst["launch_undo"] = undo
        save_state(game_dir, state)


def uninstall(game_dir: Path, rel_target: str | None = None) -> list[dict]:
    """Remove OptiScaler files; returns removed install records (with launch_undo)."""
    game_dir = Path(game_dir)
    state = load_state(game_dir)
    insts = state.get("optiscaler", {})
    keys = [rel_target] if rel_target else list(insts)
    removed = []
    for k in keys:
        inst = insts.pop(k, None)
        if not inst:
            continue
        target = game_dir / k
        for rel in inst.get("placed", []):
            (game_dir / rel).unlink(missing_ok=True)
        for rel, bak in inst.get("backups", {}).items():
            b = game_dir / bak
            if b.exists():
                os.replace(b, game_dir / rel)
        for log in LOG_FILES:
            (target / log).unlink(missing_ok=True)
        for d in ("D3D12_Optiscaler", "Licenses", "DlssOverrides"):
            p = target / d
            if p.is_dir() and not any(p.iterdir()):
                p.rmdir()
        removed.append(inst)
    save_state(game_dir, state)
    return removed


def revert_launch_options(current: str, inst: dict) -> str:
    undo = inst.get("launch_undo")
    return launchopts.revert(current, undo) if undo else current
