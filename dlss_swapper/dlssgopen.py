"""Real NVIDIA DLSS Frame Generation on RTX 20/30 under Proton via dlssg-open.

https://github.com/ProfessorTee/dlssg-open (MIT) fills the gaps in Proton's open
layers so the game's own DLSS-G 310.x runtime runs on Ampere/Turing:

* nvcuda-lite: a small nvcuda.dll answering the CUDA calls NGX makes (Proton's
  nvcuda.dll is an empty stub);
* a patched DXVK-NVAPI nvapi64.dll that, at kernel upload time, retargets the
  runtime's sm_89 PTX to the local SM and swaps in the sm_86 cubins the runtime
  already carries (vkd3d-proton cannot load sm_89 cubins on Ampere);
* environment: DXVK_NVAPI_GPU_ARCH=AD100, NVCUDA_LITE_CC=8.9,
  DXVK_NVAPI_CUBIN_RETARGET_SM=<sm>.

Upstream installs these by overwriting files inside Proton. We instead put them
in our per-game compatibility tool (compat.py), so other games and Proton
updates are unaffected. nvcuda-lite carries the Wine "builtin" signature; we
clear it so Wine loads our copy from the prefix with "nvcuda=n,b".
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .library import DATA_DIR, http_get

REPO = "ProfessorTee/dlssg-open"
PROJECT_URL = f"https://github.com/{REPO}"
API = f"https://api.github.com/repos/{REPO}/releases"
BUNDLE_DIR = DATA_DIR / "dlssg_open"
VARIANT = "dlssg_open"
STATE_KEY = "dlssg_open"
BUILTIN_SIG = b"Wine builtin DLL"


@dataclass
class Bundle:
    version: str
    path: Path

    @property
    def nvapi64(self) -> Path:
        return self.path / "nvapi64.dll"

    @property
    def nvcuda(self) -> Path:
        return self.path / "nvcuda-native.dll"


def local_bundles() -> list[Bundle]:
    if not BUNDLE_DIR.is_dir():
        return []
    out = [Bundle(p.name, p) for p in BUNDLE_DIR.iterdir()
           if (p / "nvapi64.dll").is_file() and (p / "nvcuda-native.dll").is_file()]
    return sorted(out, key=lambda b: [int(x) for x in re.findall(r"\d+", b.version)], reverse=True)


def payloads() -> dict[str, dict[str, Path]]:
    bs = local_bundles()
    return {VARIANT: {"nvcuda": bs[0].nvcuda, "nvapi64": bs[0].nvapi64}} if bs else {}


def _strip_builtin(data: bytes) -> bytes:
    """Clear Wine's builtin marker (in the DOS stub) so the DLL loads as native."""
    i = data.find(BUILTIN_SIG, 0, 0x200)
    if i == -1:
        return data
    return data[:i] + b"\0" * len(BUILTIN_SIG) + data[i + len(BUILTIN_SIG):]


def import_bundle(src: Path, version: str | None = None) -> Bundle:
    """Import a release tarball (or an extracted folder) and verify SHA256SUMS."""
    src = Path(src)
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=BUNDLE_DIR) as td:
        tmp = Path(td)
        if src.is_dir():
            root = src
        else:
            with tarfile.open(src) as t:
                for m in t.getmembers():
                    if m.name.startswith("/") or ".." in Path(m.name).parts or not (m.isfile() or m.isdir()):
                        raise ValueError(f"unsafe entry in archive: {m.name}")
                t.extractall(tmp)
            root = next((Path(r) for r, _d, f in os.walk(tmp) if "nvapi64.dll" in f and "nvcuda.dll" in f), None)
            if root is None:
                raise FileNotFoundError("в архиве нет nvapi64.dll и nvcuda.dll")
        sums = root / "SHA256SUMS"
        if sums.is_file():
            for line in sums.read_text().splitlines():
                parts = line.split()
                if len(parts) != 2:
                    continue
                f = root / Path(parts[1]).name
                if f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest() != parts[0].lower():
                    raise IOError(f"SHA-256 не совпал: {f.name}")
        if not version:
            m = re.search(r"(\d+\.\d+\.\d+)", src.name)
            version = m.group(1) if m else "local"
        final = BUNDLE_DIR / version
        final.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "nvapi64.dll", final / "nvapi64.dll")
        nv = (root / "nvcuda.dll").read_bytes()
        (final / "nvcuda.dll").write_bytes(nv)
        (final / "nvcuda-native.dll").write_bytes(_strip_builtin(nv))
        for extra in ("LICENSE", "README.md"):
            if (root / extra).is_file():
                shutil.copyfile(root / extra, final / extra)
    return Bundle(version, final)


def download_latest(progress=None) -> Bundle:
    rels = json.loads(http_get(API + "?per_page=5"))
    for rel in rels:
        if rel.get("draft"):
            continue
        asset = next((a for a in rel.get("assets", []) if a["name"].endswith((".tar.gz", ".tgz"))), None)
        if not asset:
            continue
        ver = rel["tag_name"].lstrip("v")
        for b in local_bundles():
            if b.version == ver:
                return b
        BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
        arch = BUNDLE_DIR / asset["name"]
        arch.write_bytes(http_get(asset["browser_download_url"], progress, timeout=600))
        try:
            return import_bundle(arch, ver)
        finally:
            arch.unlink(missing_ok=True)
    raise RuntimeError("в релизах dlssg-open не найден архив")


# ------------------------------------------------------------------ game checks
def runtimes(game_dir: Path) -> list[tuple[Path, str | None]]:
    """The game's DLSS-G runtimes (nvngx_dlssg.dll) and their versions."""
    from .pe import file_version
    out = []
    for root, _dirs, files in os.walk(game_dir):
        for fn in files:
            if fn.lower() == "nvngx_dlssg.dll" and "dlssg_sm86" not in root:
                p = Path(root) / fn
                out.append((p, file_version(p)))
    return out


def runtime_ok(game_dir: Path) -> tuple[bool, str]:
    rts = runtimes(game_dir)
    if not rts:
        return False, "В игре нет DLSS FG (nvngx_dlssg.dll)"
    bad = [v for _p, v in rts if not (v or "").startswith("310.")]
    if bad:
        return False, (f"DLSS-G в игре версии {bad[0]}, нужна 310.x — замените его на 310.x "
                       "в разделе «Апскейлеры» (DLSS Frame Generation)")
    return True, f"DLSS-G {rts[0][1]}"


def launch_env(sm: int = 86, indicator: bool = False) -> dict[str, str]:
    env = {"PROTON_ENABLE_NVAPI": "1", "DXVK_NVAPI_GPU_ARCH": "AD100", "NVCUDA_LITE_CC": "8.9",
           "DXVK_NVAPI_CUBIN_RETARGET_SM": str(sm)}
    if indicator:  # NVIDIA's own on-screen DLSS-G status overlay
        env["DXVK_NVAPI_SET_NGX_DEBUG_OPTIONS"] = "DLSSGIndicator=2"
    return env


def gpu_sm() -> int:
    from .fakecuda import gpu_params
    p = gpu_params()
    return p["major"] * 10 + p["minor"]
