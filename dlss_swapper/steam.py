"""Steam discovery on Linux: native, Flatpak and Snap installs, libraries,
installed games, non-Steam shortcuts and per-game launch options."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from . import vdf

HOME = Path.home()
STEAM_ROOT_CANDIDATES = [
    HOME / ".steam/steam",
    HOME / ".steam/root",
    HOME / ".local/share/Steam",
    HOME / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    HOME / ".var/app/com.valvesoftware.Steam/data/Steam",
    HOME / "snap/steam/common/.local/share/Steam",
    HOME / "snap/steam/common/.steam/steam",
]

# Snap/Flatpak Steam may record host paths with a sandbox prefix.
SANDBOX_PREFIXES = ["/var/lib/snapd/hostfs", "/run/host"]

# Not games: runtimes, Proton builds, redistributables.
SKIP_APPIDS = {"228980", "1070560", "1391110", "1628350", "1493710", "2180100"}
SKIP_NAME_PREFIXES = ("Proton", "Steam Linux Runtime", "Steamworks Common", "SteamVR")


@dataclass
class Game:
    name: str
    path: Path
    appid: str
    source: str  # "steam" | "shortcut" | "folder"
    steam_root: Path | None = None
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.appid}:{self.path}"


def host_path(p: str) -> Path:
    for pre in SANDBOX_PREFIXES:
        if p.startswith(pre + "/"):
            real = p[len(pre):]
            if os.path.exists(real):
                return Path(real)
    return Path(p)


def find_steam_roots() -> list[Path]:
    roots: list[Path] = []
    seen = set()
    for c in STEAM_ROOT_CANDIDATES:
        try:
            real = c.resolve()
        except OSError:
            continue
        if (real / "steamapps").is_dir() and real not in seen:
            seen.add(real)
            roots.append(real)
    return roots


def library_folders(root: Path) -> list[Path]:
    libs = [root]
    for cand in (root / "steamapps/libraryfolders.vdf", root / "config/libraryfolders.vdf"):
        if not cand.is_file():
            continue
        try:
            data = vdf.loads(cand.read_text(errors="replace"))
        except ValueError:
            continue
        _, lf = vdf.get_ci(data, "libraryfolders")
        for v in (lf or {}).values():
            p = v.get("path") if isinstance(v, dict) else v
            if isinstance(p, str):
                hp = host_path(p)
                if (hp / "steamapps").is_dir():
                    libs.append(hp)
    out, seen = [], set()
    for lib in libs:
        r = lib.resolve()
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def steam_games(roots: list[Path] | None = None) -> list[Game]:
    games: dict[str, Game] = {}
    for root in roots if roots is not None else find_steam_roots():
        for lib in library_folders(root):
            for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
                try:
                    st = vdf.get_ci(vdf.loads(acf.read_text(errors="replace")), "AppState")[1] or {}
                except (ValueError, OSError):
                    continue
                appid = str(st.get("appid", ""))
                name = st.get("name", appid)
                inst = st.get("installdir")
                if not inst or appid in SKIP_APPIDS or name.startswith(SKIP_NAME_PREFIXES):
                    continue
                path = lib / "steamapps/common" / inst
                if path.is_dir() and appid not in games:
                    games[appid] = Game(name, path, appid, "steam", root)
    return sorted(games.values(), key=lambda g: g.name.lower())


# ------------------------------------------------------------------ users
def steam_users(root: Path) -> list[str]:
    """Return userdata ids (SteamID3 account ids), most recently used first."""
    ud = root / "userdata"
    if not ud.is_dir():
        return []
    ids = [p.name for p in ud.iterdir() if p.name.isdigit() and p.name != "0"]
    recent = None
    lu = root / "config/loginusers.vdf"
    if lu.is_file():
        try:
            users = vdf.get_ci(vdf.loads(lu.read_text(errors="replace")), "users")[1] or {}
            for sid64, info in users.items():
                if isinstance(info, dict) and str(vdf.get_ci(info, "MostRecent")[1]) == "1":
                    recent = str(int(sid64) - 76561197960265728)
        except (ValueError, OSError):
            pass
    ids.sort(key=lambda i: (i != recent, -(ud / i).stat().st_mtime))
    return ids


# --------------------------------------------------------------- shortcuts
def shortcut_appid(exe: str, name: str) -> str:
    crc = zlib.crc32((exe + name).encode()) | 0x80000000
    return str(crc)


def shortcut_games(roots: list[Path] | None = None) -> list[Game]:
    out = []
    for root in roots if roots is not None else find_steam_roots():
        for uid in steam_users(root):
            f = root / "userdata" / uid / "config/shortcuts.vdf"
            if not f.is_file():
                continue
            try:
                data = vdf.binary_loads(f.read_bytes())
            except (ValueError, IndexError):
                continue
            sc = vdf.get_ci(data, "shortcuts")[1] or {}
            for idx, e in sc.items():
                if not isinstance(e, dict):
                    continue
                name = vdf.get_ci(e, "AppName")[1] or vdf.get_ci(e, "appname")[1] or "?"
                exe = (vdf.get_ci(e, "Exe")[1] or "").strip('"')
                start = (vdf.get_ci(e, "StartDir")[1] or "").strip('"')
                aid = vdf.get_ci(e, "appid")[1]
                appid = str(aid[1] & 0xFFFFFFFF) if isinstance(aid, tuple) else shortcut_appid(exe, name)
                if not exe.lower().endswith(".exe"):
                    continue  # native Linux shortcut, Proton not involved
                d = Path(start) if start and os.path.isdir(start) else Path(exe).parent
                if name.lower().endswith(".exe"):
                    name = f"{d.name} ({name})"
                if d.is_dir():
                    out.append(Game(name, d, appid, "shortcut", root,
                                    {"uid": uid, "index": idx, "exe": exe}))
    return out


# ---------------------------------------------------------- launch options
def steam_running() -> bool:
    try:
        r = subprocess.run(["pgrep", "-x", "steam"], capture_output=True)
        if r.returncode == 0:
            return True
        r = subprocess.run(["pgrep", "-f", "steamwebhelper"], capture_output=True)
        return r.returncode == 0
    except FileNotFoundError:
        return False


def steam_command(root: Path | None = None) -> list[str] | None:
    """Command that starts the user's Steam client (snap, flatpak or native)."""
    root_s = str(root or "")
    if "/snap/steam/" in root_s and shutil.which("snap"):
        return ["snap", "run", "steam"]
    if "com.valvesoftware.Steam" in root_s and shutil.which("flatpak"):
        return ["flatpak", "run", "com.valvesoftware.Steam"]
    for exe in ("steam", "/usr/games/steam", "/snap/bin/steam"):
        if shutil.which(exe):
            return [shutil.which(exe)]
    return None


def shutdown_steam(root: Path | None = None, timeout: float = 45.0) -> bool:
    """Ask Steam to exit cleanly (it saves its config on exit) and wait for it."""
    cmd = steam_command(root)
    if not steam_running():
        return True
    if not cmd:
        return False
    subprocess.Popen(cmd + ["-shutdown"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    end = time.time() + timeout
    while time.time() < end:
        if not steam_running():
            time.sleep(1.5)  # let it finish writing files
            return True
        time.sleep(0.5)
    return False


def start_steam(root: Path | None = None) -> bool:
    cmd = steam_command(root)
    if not cmd:
        return False
    subprocess.Popen(cmd + ["-silent"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return True


def _localconfig(root: Path, uid: str) -> Path:
    return root / "userdata" / uid / "config/localconfig.vdf"


def _apps_node(data: dict, create: bool) -> dict | None:
    node = data
    for k in ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps"):
        real, val = vdf.get_ci(node, k)
        if val is None:
            if not create:
                return None
            val = {}
            node[k] = val
        node = val
    return node


def get_launch_options(game: Game) -> str:
    if game.source == "shortcut":
        f = game.steam_root / "userdata" / game.extra["uid"] / "config/shortcuts.vdf"
        data = vdf.binary_loads(f.read_bytes())
        e = vdf.get_ci(data, "shortcuts")[1][game.extra["index"]]
        return vdf.get_ci(e, "LaunchOptions")[1] or ""
    if game.source != "steam" or not game.steam_root:
        return ""
    for uid in steam_users(game.steam_root):
        f = _localconfig(game.steam_root, uid)
        if not f.is_file():
            continue
        apps = _apps_node(vdf.loads(f.read_text(errors="replace")), False) or {}
        _, app = vdf.get_ci(apps, game.appid)
        if isinstance(app, dict):
            return vdf.get_ci(app, "LaunchOptions")[1] or ""
        return ""
    return ""


class SteamRunningError(RuntimeError):
    pass


def set_launch_options(game: Game, value: str, force: bool = False) -> Path:
    """Write launch options. Steam rewrites its config on exit, so it must be closed."""
    if not force and steam_running():
        raise SteamRunningError(
            "Steam запущен: он перезапишет конфиг при выходе. Закройте Steam "
            "или вставьте строку вручную: Свойства игры → Параметры запуска.")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if game.source == "shortcut":
        f = game.steam_root / "userdata" / game.extra["uid"] / "config/shortcuts.vdf"
        data = vdf.binary_loads(f.read_bytes())
        e = vdf.get_ci(data, "shortcuts")[1][game.extra["index"]]
        k = vdf.get_ci(e, "LaunchOptions")[0] or "LaunchOptions"
        e[k] = value
        shutil.copy2(f, f.with_name(f.name + f".bak-{stamp}"))
        _atomic_write(f, vdf.binary_dumps(data))
        return f
    if game.source != "steam" or not game.steam_root:
        raise ValueError("launch options are only supported for Steam games and shortcuts")
    users = steam_users(game.steam_root)
    if not users:
        raise FileNotFoundError("no Steam userdata found")
    f = _localconfig(game.steam_root, users[0])
    data = vdf.loads(f.read_text(errors="replace"))
    apps = _apps_node(data, True)
    real, app = vdf.get_ci(apps, game.appid)
    if not isinstance(app, dict):
        app = {}
        apps[game.appid] = app
    k = vdf.get_ci(app, "LaunchOptions")[0] or "LaunchOptions"
    app[k] = value
    shutil.copy2(f, f.with_name(f.name + f".bak-{stamp}"))
    _prune_backups(f)
    _atomic_write(f, vdf.dumps(data).encode())
    return f


def _prune_backups(f: Path, keep: int = 5) -> None:
    baks = sorted(f.parent.glob(f.name + ".bak-*"))
    for b in baks[:-keep]:
        b.unlink(missing_ok=True)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
