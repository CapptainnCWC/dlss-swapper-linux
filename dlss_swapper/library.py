"""Local DLL library: DLSS Swapper manifest, downloads with MD5 checks, imports."""
from __future__ import annotations

import io
import json
import os
import shutil
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import pe

APP = "dlss-swapper-linux"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP
MANIFEST_URL = "https://raw.githubusercontent.com/beeradmoore/dlss-swapper-manifest-builder/main/manifest.json"
USER_AGENT = f"{APP}/1.0"

# manifest key -> (dll filename, human label)
DLL_TYPES: dict[str, tuple[str, str]] = {
    "dlss": ("nvngx_dlss.dll", "DLSS Super Resolution"),
    "dlss_g": ("nvngx_dlssg.dll", "DLSS Frame Generation"),
    "dlss_d": ("nvngx_dlssd.dll", "DLSS Ray Reconstruction"),
    "fsr_31_dx12": ("amd_fidelityfx_dx12.dll", "FSR 3.1 DX12"),
    "fsr_31_vk": ("amd_fidelityfx_vk.dll", "FSR 3.1 Vulkan"),
    "xess": ("libxess.dll", "XeSS"),
    "xess_dx11": ("libxess_dx11.dll", "XeSS DX11"),
    "xess_fg": ("libxess_fg.dll", "XeSS Frame Generation"),
    "xell": ("libxell.dll", "XeLL"),
}
FILENAME_TO_TYPE = {v[0].lower(): k for k, v in DLL_TYPES.items()}

ProgressCB = Callable[[int, int], None] | None


@dataclass
class DllRecord:
    kind: str
    version: str
    md5: str
    url: str | None = None
    zip_md5: str | None = None
    size: int = 0
    label: str = ""
    is_dev: bool = False
    local: Path | None = None  # cached path, if downloaded / imported

    @property
    def display(self) -> str:
        extra = f" ({self.label})" if self.label else ""
        dev = " [dev]" if self.is_dev else ""
        mark = " ✓" if self.local else ""
        return f"{self.version}{extra}{dev}{mark}"


def http_get(url: str, progress: ProgressCB = None, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            buf.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
        return buf.getvalue()


class Library:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.dir = data_dir
        self.dll_dir = data_dir / "dlls"
        self.manifest_path = data_dir / "manifest.json"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._manifest: dict | None = None

    # ---------------------------------------------------------- manifest
    def manifest(self, refresh: bool = False, max_age: int = 24 * 3600) -> dict:
        if self._manifest is not None and not refresh:
            return self._manifest
        stale = (not self.manifest_path.exists()
                 or time.time() - self.manifest_path.stat().st_mtime > max_age)
        if refresh or stale:
            try:
                data = http_get(MANIFEST_URL)
                json.loads(data)
                self.manifest_path.write_bytes(data)
            except Exception:
                if not self.manifest_path.exists():
                    raise
        self._manifest = json.loads(self.manifest_path.read_text())
        return self._manifest

    def records(self, kind: str, offline_ok: bool = True) -> list[DllRecord]:
        recs: dict[str, DllRecord] = {}
        try:
            for e in self.manifest().get(kind, []):
                r = DllRecord(kind, e["version"], e["md5_hash"].upper(), e.get("download_url"),
                              (e.get("zip_md5_hash") or "").upper() or None,
                              e.get("file_size", 0), e.get("additional_label", ""),
                              bool(e.get("is_dev_file")))
                recs[r.md5] = r
        except Exception:
            if not offline_ok:
                raise
        for local in self.local_dlls(kind):
            md5 = local.parent.name.rsplit("_", 1)[-1].upper()
            if md5 in recs:
                recs[md5].local = local
            else:
                recs[md5] = DllRecord(kind, pe.file_version(local) or "?", md5,
                                      label="импорт", local=local)
        return sorted(recs.values(), key=lambda r: (pe.version_key(r.version), r.label), reverse=True)

    def local_dlls(self, kind: str) -> list[Path]:
        d = self.dll_dir / kind
        fname = DLL_TYPES[kind][0]
        return [p / fname for p in d.iterdir() if (p / fname).is_file()] if d.is_dir() else []

    # --------------------------------------------------------- get files
    def _slot(self, kind: str, version: str, md5: str) -> Path:
        return self.dll_dir / kind / f"{version}_{md5.upper()}" / DLL_TYPES[kind][0]

    def ensure(self, rec: DllRecord, progress: ProgressCB = None) -> Path:
        """Return local path of the DLL, downloading and verifying if needed."""
        if rec.local and rec.local.is_file():
            return rec.local
        target = self._slot(rec.kind, rec.version, rec.md5)
        if target.is_file() and pe.md5(target) == rec.md5:
            rec.local = target
            return target
        if not rec.url:
            raise FileNotFoundError(f"нет ссылки на скачивание для {rec.kind} {rec.version}")
        blob = http_get(rec.url, progress, timeout=300)
        if rec.zip_md5:
            import hashlib
            got = hashlib.md5(blob).hexdigest().upper()
            if got != rec.zip_md5:
                raise IOError(f"MD5 архива не совпал: {got} != {rec.zip_md5}")
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            want = DLL_TYPES[rec.kind][0].lower()
            names = [n for n in z.namelist() if os.path.basename(n).lower() == want]
            if not names:
                names = [n for n in z.namelist() if n.lower().endswith(".dll")]
            if not names:
                raise IOError("в архиве нет DLL")
            data = z.read(names[0])
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".part")
        tmp.write_bytes(data)
        if pe.md5(tmp) != rec.md5:
            tmp.unlink()
            raise IOError("MD5 DLL не совпал с манифестом")
        os.replace(tmp, target)
        rec.local = target
        return target

    def import_dll(self, path: Path, kind: str | None = None) -> DllRecord:
        """Add a user-supplied DLL (e.g. from TechPowerUp or another game)."""
        path = Path(path)
        kind = kind or FILENAME_TO_TYPE.get(path.name.lower())
        if kind is None:
            raise ValueError(f"не знаю тип DLL '{path.name}', укажите --type ({', '.join(DLL_TYPES)})")
        version = pe.file_version(path)
        if not version:
            raise ValueError(f"{path} не похож на Windows DLL (нет версии)")
        md5 = pe.md5(path)
        target = self._slot(kind, version, md5)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return DllRecord(kind, version, md5, label="импорт", local=target)

    def latest(self, kind: str, allow_dev: bool = False) -> DllRecord | None:
        for r in self.records(kind):
            if allow_dev or not r.is_dev:
                return r
        return None

    def find(self, kind: str, version: str) -> DllRecord | None:
        if version in ("latest", "newest"):
            return self.latest(kind)
        cands = [r for r in self.records(kind) if r.version == version or r.md5 == version.upper()]
        return (sorted(cands, key=lambda r: r.is_dev) or [None])[0]
