"""Inspect a game's files and recommend what to install and how.

Looks at the engine, graphics API (from the exe's import tables), shipped
upscaler / frame-generation libraries, anti-cheat and DLL names already in use
next to the exe, and turns that into a recommended Frame Generation method,
proxy DLL name, target folder and OptiScaler options.
"""
from __future__ import annotations

import mmap
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import swapper
from .gpu import GpuInfo
from .swapper import load_state

PROXY_ORDER = ["dxgi.dll", "winmm.dll", "version.dll", "dbghelp.dll", "wininet.dll", "winhttp.dll"]

# file name (lower case) -> feature key
FEATURE_FILES = {
    "nvngx_dlss.dll": "dlss",
    "nvngx_dlssd.dll": "dlss_rr",
    "nvngx_dlssg.dll": "dlss_fg",
    "sl.dlss_g.dll": "dlss_fg",
    "sl.interposer.dll": "streamline",
    "sl.reflex.dll": "reflex",
    "nvlowlatencyvk.dll": "reflex",
    "amd_fidelityfx_dx12.dll": "fsr_dx12",
    "amd_fidelityfx_loader_dx12.dll": "fsr_dx12",
    "amd_fidelityfx_framegeneration_dx12.dll": "fsr_fg",
    "amd_fidelityfx_vk.dll": "fsr_vk",
    "ffx_fsr3_x64.dll": "fsr3_legacy",
    "ffx_frameinterpolation_x64.dll": "fsr3_legacy",
    "libxess.dll": "xess",
    "libxess_dx11.dll": "xess",
    "libxess_fg.dll": "xess_fg",
    "libxell.dll": "xell",
}
FEATURE_NAMES = {
    "dlss": "DLSS", "dlss_rr": "DLSS RR", "dlss_fg": "DLSS FG", "streamline": "Streamline",
    "reflex": "Reflex", "fsr_dx12": "FSR 3.1", "fsr_fg": "FSR FG", "fsr_vk": "FSR (Vulkan)",
    "fsr3_legacy": "FSR 3.0", "xess": "XeSS", "xess_fg": "XeSS FG", "xell": "XeLL",
}


@dataclass
class Analysis:
    engine: str
    api: str                         # DX12 | DX11 | Vulkan | ?
    exe: Path | None
    target: Path
    features: set[str] = field(default_factory=set)
    foreign_proxies: list[str] = field(default_factory=list)   # DLL names taken by something else
    anti_cheat: list[str] = field(default_factory=list)
    game_fsr31: bool = False
    # recommendation
    method: str = "none"
    proxy: str = "dxgi.dll"
    use_game_fsr: bool = True
    supported: bool = True
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def feature_labels(self) -> list[str]:
        order = list(FEATURE_NAMES)
        return [FEATURE_NAMES[f] for f in sorted(self.features, key=order.index)]


# ------------------------------------------------------------------ PE imports
def _rva_to_off(sections, rva: int) -> int | None:
    for va, vsize, raw, rsize in sections:
        if va <= rva < va + max(vsize, rsize):
            return raw + (rva - va)
    return None


def pe_imports(path: Path) -> set[str]:
    """Lower-case DLL names from the import and delay-import tables."""
    out: set[str] = set()
    try:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            if mm[:2] != b"MZ":
                return out
            pe = struct.unpack_from("<I", mm, 0x3C)[0]
            if mm[pe:pe + 4] != b"PE\0\0":
                return out
            nsec = struct.unpack_from("<H", mm, pe + 6)[0]
            opt_size = struct.unpack_from("<H", mm, pe + 20)[0]
            opt = pe + 24
            magic = struct.unpack_from("<H", mm, opt)[0]
            dd = opt + (112 if magic == 0x20B else 96)
            sec_off = opt + opt_size
            sections = [struct.unpack_from("<IIII", mm, sec_off + i * 40 + 8)
                        for i in range(nsec)]
            sections = [(va, vs, raw, rs) for vs, va, rs, raw in sections]

            def name_at(rva: int) -> str | None:
                off = _rva_to_off(sections, rva)
                if off is None:
                    return None
                end = mm.find(b"\0", off, off + 260)
                return mm[off:end].decode("ascii", "replace").lower() if end > off else None

            for index, entry_size, name_field in ((1, 20, 12), (13, 32, 4)):
                rva = struct.unpack_from("<I", mm, dd + index * 8)[0]
                off = _rva_to_off(sections, rva) if rva else None
                while off is not None and off + entry_size <= len(mm):
                    if not any(mm[off:off + entry_size]):
                        break
                    n = name_at(struct.unpack_from("<I", mm, off + name_field)[0])
                    if n:
                        out.add(n)
                    off += entry_size
                    if len(out) > 400:
                        break
    except (OSError, ValueError, struct.error):
        pass
    return out


# --------------------------------------------------------------------- analysis
def _engine(game_dir: Path, exe: Path | None, names: set[str]) -> str:
    if exe and exe.name.lower().endswith(("-win64-shipping.exe", "-wingdk-shipping.exe")):
        return "Unreal Engine"
    if (game_dir / "Engine").is_dir() and any(game_dir.glob("*/Binaries/Win64")):
        return "Unreal Engine"
    if "unityplayer.dll" in names:
        return "Unity"
    if any(game_dir.glob("re_chunk_000.pak")):
        return "RE Engine"
    if "oo2core_9_win64.dll" in names and any(game_dir.glob("*.psarc")):
        return "Nixxes"
    return "неизвестен"


def _main_exe(target: Path) -> Path | None:
    exes = [p for p in target.glob("*.exe")
            if not any(b in p.name.lower() for b in swapper.EXE_BLACKLIST)]
    return max(exes, key=lambda p: p.stat().st_size, default=None)


def _api(exe: Path | None, target: Path, names: set[str]) -> str:
    imports = pe_imports(exe) if exe else set()
    has12 = "d3d12.dll" in imports or (target / "D3D12").is_dir() or "d3d12core.dll" in names
    if has12:
        return "DX12"
    if "vulkan-1.dll" in imports:
        return "Vulkan"
    if "d3d11.dll" in imports:
        return "DX11"
    # renderer loaded dynamically (e.g. Baldur's Gate 3): look for the DLL name inside the exe
    if exe:
        try:
            with open(exe, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                for needle, api in ((b"d3d12.dll", "DX12"), (b"vulkan-1.dll", "Vulkan"), (b"d3d11.dll", "DX11")):
                    if mm.find(needle) != -1 or mm.find(needle.upper()) != -1:
                        return api
        except (OSError, ValueError):
            pass
    return "?"


def _dlssg_310(game_dir: Path) -> bool:
    from .dlssgopen import runtime_ok
    return runtime_ok(game_dir)[0]


def analyze(game_dir: Path, gpu: GpuInfo, dlls: list | None = None) -> Analysis:
    game_dir = Path(game_dir)
    state = load_state(game_dir)
    ours_paths: set[str] = set()
    for key in ("optiscaler", "dlssg_sm86"):
        for inst in state.get(key, {}).values():
            ours_paths.update(inst.get("placed", []))
    ours = {p.lower() for p in ours_paths}

    features: set[str] = set()
    names: set[str] = set()
    for root, dirs, files in os.walk(game_dir):
        dirs[:] = [d for d in dirs if d.lower() != "dlssg_sm86"]
        for fn in files:
            low = fn.lower()
            rel = str((Path(root) / fn).relative_to(game_dir)).lower()
            if rel in ours:
                continue
            names.add(low)
            if low in FEATURE_FILES:
                features.add(FEATURE_FILES[low])

    cands = swapper.exe_candidates(game_dir)
    target = cands[0] if cands else game_dir
    exe = _main_exe(target)
    a = Analysis(engine=_engine(game_dir, exe, names), api=_api(exe, target, names), exe=exe,
                 target=target, features=features, anti_cheat=swapper.anti_cheat(game_dir))

    from .optiscaler import game_has_fsr31  # local import: avoid cycle
    a.game_fsr31 = game_has_fsr31(target)
    a.use_game_fsr = a.game_fsr31

    # DLL names next to the exe that belong to the game or another mod
    ours_in_target = {Path(p).name.lower() for p in ours_paths if (game_dir / p).parent == target}
    for name in PROXY_ORDER + ["d3d12.dll", "dinput8.dll"]:
        if (target / name).exists() and name not in ours_in_target:
            a.foreign_proxies.append(name)
    a.proxy = next((p for p in PROXY_ORDER if p not in a.foreign_proxies), "winmm.dll")
    if a.api == "Vulkan" and a.proxy == "dxgi.dll":
        a.proxy = "winmm.dll"
        a.reasons.append("Vulkan-игра не загружает dxgi.dll — OptiScaler ставится как winmm.dll")
    if a.proxy != "dxgi.dll" and "dxgi.dll" in a.foreign_proxies:
        a.reasons.append("dxgi.dll уже занят (ReShade или другой мод) — выбран " + a.proxy)

    # recommendation
    has = features.__contains__
    if gpu.native_dlss_fg and has("dlss_fg"):
        a.method = "none"
        a.reasons.insert(0, "У вас RTX 40/50 и в игре есть DLSS FG — он работает без модов")
    elif has("dlss_fg") and a.api == "DX12" and gpu.is_nvidia and _dlssg_310(game_dir):
        a.method = "dlssg_open"
        a.reasons.insert(0, "В игре есть DLSS FG 310.x — можно запустить настоящий DLSS-G NVIDIA на этой карте "
                            "(dlssg-open). Если не заработает — FSR FG через OptiScaler")
    elif has("dlss_fg") and a.api == "DX12":
        a.method = "optifg"
        a.reasons.insert(0, "В игре есть DLSS FG — OptiScaler разблокирует его, кадры генерирует FSR FG")
    elif has("dlss") and a.api == "DX12":
        a.method = "upscaler"
        a.reasons.insert(0, "DLSS есть, DLSS FG нет — OptiScaler генерирует кадры из данных DLSS")
    elif (has("fsr_fg") or has("fsr3_legacy")) and not has("dlss"):
        a.method = "none"
        a.reasons.insert(0, "В игре есть свой FSR FG — включите его в настройках игры, моды не нужны")
    elif has("dlss") or has("xess") or has("fsr_dx12") or has("fsr_vk"):
        a.method = "none"
        a.reasons.insert(0, f"Генерация кадров OptiScaler работает только в DX12 (здесь {a.api}). "
                            "Можно только заменить апскейлер")
    else:
        a.method = "none"
        a.supported = False
        a.reasons.insert(0, "В игре нет DLSS, FSR 3.1 или XeSS — OptiScaler здесь подцепиться не к чему")

    if a.game_fsr31 and a.method in ("optifg", "upscaler"):
        a.reasons.append("Свой FSR 3.1 игры будет использован для FG (стабильнее FSR 4 из комплекта под Proton)")
    if has("dlss_fg") and not gpu.native_dlss_fg and gpu.is_nvidia and not _dlssg_310(game_dir):
        a.warnings.append("DLSS-G в игре старее 310.x — для настоящего DLSS FG замените nvngx_dlssg.dll "
                          "на 310.x в разделе «Апскейлеры»")
    if a.anti_cheat:
        a.warnings.append("Античит: " + ", ".join(a.anti_cheat[:3]) + " — не используйте моды онлайн")
    others = [p.name for p in target.glob("*.exe") if exe and p != exe and "dx11" in p.name.lower()]
    if others and a.api != "DX11":
        a.reasons.append(f"Есть отдельный DX11-запуск ({others[0]}); моды ставятся для {a.api} ({exe.name})")
    if a.engine == "Unreal Engine" and target == game_dir:
        a.warnings.append("Не найдена папка */Binaries/Win64 — проверьте «Папку с exe»")
    return a
