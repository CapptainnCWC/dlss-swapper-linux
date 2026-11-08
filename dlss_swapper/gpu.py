"""GPU detection: vendor and whether native DLSS Frame Generation is available."""
from __future__ import annotations

import glob
import re
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class GpuInfo:
    vendor: str  # nvidia | amd | intel | unknown
    name: str

    @property
    def is_nvidia(self) -> bool:
        return self.vendor == "nvidia"

    @property
    def native_dlss_fg(self) -> bool:
        """RTX 40xx (Ada) and newer support DLSS FG natively."""
        if not self.is_nvidia:
            return False
        m = re.search(r"RTX\s*(?:PRO\s*)?(\d{4})", self.name, re.I)
        if m:
            return int(m.group(1)[:2]) >= 40 and int(m.group(1)) >= 4000
        return bool(re.search(r"RTX\s*(\d+)\s*Ada|\bL40|\bH100|Blackwell", self.name, re.I))

    @property
    def has_dlss(self) -> bool:
        return self.is_nvidia and bool(re.search(r"RTX", self.name, re.I))


def detect() -> GpuInfo:
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return GpuInfo("nvidia", r.stdout.strip().splitlines()[0])
        except (OSError, subprocess.TimeoutExpired):
            pass
    for f in glob.glob("/proc/driver/nvidia/gpus/*/information"):
        try:
            for line in open(f):
                if line.startswith("Model:"):
                    return GpuInfo("nvidia", line.split(":", 1)[1].strip())
        except OSError:
            pass
    vendors = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}
    found = []
    for f in glob.glob("/sys/class/drm/card*/device/vendor"):
        try:
            v = open(f).read().strip()
        except OSError:
            continue
        if v in vendors:
            found.append(vendors[v])
    for pref in ("nvidia", "amd", "intel"):
        if pref in found:
            name = pref.upper()
            if shutil.which("lspci"):
                try:
                    out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5).stdout
                    for line in out.splitlines():
                        if re.search(r"VGA|3D|Display", line) and pref[:3] in line.lower():
                            name = line.split(":", 2)[-1].strip()
                            break
                except (OSError, subprocess.TimeoutExpired):
                    pass
            return GpuInfo(pref, name)
    return GpuInfo("unknown", "unknown")
