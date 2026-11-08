"""Self-update via GitHub releases."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .library import DATA_DIR, http_get

REPO = "CapptainnCWC/dlss-swapper-linux"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PROJECT_URL = f"https://github.com/{REPO}"
APP_ROOT = Path(__file__).resolve().parent.parent


def _vkey(v: str) -> tuple[int, ...]:
    out = []
    for p in v.lstrip("vV").split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out)


@dataclass
class Release:
    version: str
    notes: str
    url: str
    deb_url: str | None
    deb_name: str | None

    @property
    def newer(self) -> bool:
        return _vkey(self.version) > _vkey(__version__)


def check() -> Release:
    r = json.loads(http_get(API, timeout=15))
    deb = next((a for a in r.get("assets", []) if a["name"].endswith(".deb")), None)
    return Release(r["tag_name"].lstrip("v"), r.get("body") or "", r["html_url"],
                   deb["browser_download_url"] if deb else None, deb["name"] if deb else None)


def install_mode() -> str:
    if (APP_ROOT / ".git").exists():
        return "git"
    if str(APP_ROOT).startswith("/usr/"):
        return "deb"
    return "other"


def apply(rel: Release, progress=None) -> str:
    """Install the update. Returns a human-readable result message."""
    mode = install_mode()
    if mode == "git":
        r = subprocess.run(["git", "-C", str(APP_ROOT), "pull", "--ff-only"],
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr.strip() or "git pull failed")
        return "Обновлено через git pull. Перезапустите приложение."
    if not rel.deb_url:
        raise RuntimeError("В релизе нет .deb пакета")
    dl = DATA_DIR / "updates"
    dl.mkdir(parents=True, exist_ok=True)
    deb = dl / rel.deb_name
    deb.write_bytes(http_get(rel.deb_url, progress, timeout=300))
    if shutil.which("pkexec"):
        cmd = ["pkexec", "apt-get", "install", "-y", "--allow-downgrades", str(deb)]
        if not shutil.which("apt-get"):
            cmd = ["pkexec", "dpkg", "-i", str(deb)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return f"Установлена версия {rel.version}. Перезапустите приложение."
        if r.returncode in (126, 127):
            raise RuntimeError("Установка отменена")
        raise RuntimeError(r.stderr.strip()[-500:] or "ошибка установки")
    subprocess.Popen(["xdg-open", str(deb)])
    return f"Пакет скачан: {deb}. Откройте его для установки."


def restart() -> None:
    import sys
    os.execv(sys.executable, [sys.executable] + sys.argv)
