"""High level operations shared by CLI and GUI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import compat, dlssg_sm86, dlssgopen, launchopts, optiscaler, steam, wineprefix
from .steam import Game
from .swapper import load_state, save_state


def all_games(extra_dirs: list[Path] | None = None) -> list[Game]:
    roots = steam.find_steam_roots()
    games = steam.steam_games(roots) + steam.shortcut_games(roots)
    for d in extra_dirs or []:
        d = Path(d)
        if d.is_dir():
            games.append(Game(d.name, d, "", "folder"))
    return games


@dataclass
class LaunchUpdate:
    old: str
    new: str
    written: bool
    error: str | None = None


# ------------------------------------------------------------ unified FG setup
# One "Frame Generation" choice per game. Not every way works in every game, so the
# user picks; detect.py recommends one.
FG_METHODS = {
    "none": "Только апскейлер",
    "dlssg_open": "DLSSG (NVIDIA)",
    "optifg": "FSR FG",
    "xefg": "XeSS FG",
    "nukems": "DLSSG → FSR3 (Nukem)",
}
FG_METHOD_HINTS = {
    "none": "OptiScaler без генерации кадров",
    "dlssg_open": "Настоящий DLSS-G игры на RTX 20/30 через dlssg-open (Proton-обёртка, nvcuda, DXVK-NVAPI)",
    "optifg": "OptiScaler генерирует кадры FSR FG из данных апскейлера (DLSS, FSR, XeSS). Включите апскейлер в игре",
    "xefg": "OptiScaler генерирует кадры XeSS FG из данных апскейлера. Только для игр без DLSS FG",
    "nukems": "DLSS FG игры → FSR3 FG через dlssg-to-fsr3 (OptiScaler, DX12 и Vulkan)",
}
# Under Proton OptiScaler draws its menu on vkd3d's Vulkan swapchain. With FG running, opening it
# crashed the GPU in WUCHANG (Xid 109/32 with FSR FG, Xid 13 with XeSS FG) and The Last of Us.
OPTI_FG_METHODS = {"optifg", "xefg", "nukems", "upscaler"}
OPTI_MENU_WARNING = ("Не открывайте меню OptiScaler (Insert) в игре, пока работает генерация кадров: "
                     "под Proton это может уронить видеокарту. Все настройки меняйте здесь")
# dlssg_for_sm86 needs NvAPI_D3D12_CreateCuModule, which DXVK-NVAPI does not implement,
# so under Proton it crashes on FG start. Kept for the CLI, hidden in the GUI.
HIDDEN_METHODS = {"native": "DLSS FG (dlssg_for_sm86)"}
STREAMLINE_FG = {"dlssg_open", "nukems", "native"}
# "upscaler" was a separate preset; with FSR FG now always fed from the upscaler it is the same thing
METHOD_ALIASES = {"upscaler": "optifg"}
XEFG_NEEDS_NO_DLSSG = "В игре есть DLSSG — XeSS FG здесь не используется. Выберите DLSSG (NVIDIA) или FSR FG"


@dataclass
class FgSetupResult:
    method: str
    version: str
    target: Path
    placed: int
    warnings: list
    launch: LaunchUpdate
    notes: list = None


def method_label(m: str) -> str:
    return FG_METHODS.get(m) or HIDDEN_METHODS.get(m, m)


def current_fg(game: Game) -> tuple[str | None, str | None, dict]:
    """(method, rel_target, saved options) of what is installed now."""
    st = load_state(game.path)
    for k, v in (st.get(dlssgopen.STATE_KEY) or {}).items():
        return "dlssg_open", k, v.get("options", {})
    dg = dlssg_sm86.installed(game.path)
    if dg:
        k, v = next(iter(dg.items()))
        return "native", k, v.get("options", {})
    op = optiscaler.installed(game.path)
    if op:
        k, v = next(iter(op.items()))
        m = v.get("fg_mode", "none")
        return METHOD_ALIASES.get(m, m), k, v.get("options", {})
    return None, None, {}


def _read_opts(game: Game) -> str:
    if game.source in ("steam", "shortcut"):
        try:
            return steam.get_launch_options(game)
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _write_opts(game: Game, old: str, new: str, write: bool) -> LaunchUpdate:
    if game.source == "folder":
        return LaunchUpdate(old, new, False, "Игра не из Steam: добавьте её как стороннюю игру в Steam "
                                             "и вставьте строку в Параметры запуска.")
    if not write or new == old:
        return LaunchUpdate(old, new, new == old)
    try:
        steam.set_launch_options(game, new)
        return LaunchUpdate(old, new, True)
    except steam.SteamRunningError as e:
        return LaunchUpdate(old, new, False, str(e))
    except Exception as e:  # noqa: BLE001
        return LaunchUpdate(old, new, False, f"Не удалось записать параметры запуска: {e}")


# ------------------------------------------------------------ Proton wrapper per game
def _undo_compat(game: Game, notes: list) -> None:
    """Drop the runtime overrides; restore the previous compatibility tool and prefix nvcuda.dll."""
    st = load_state(game.path)
    c = st.pop("compat", None)
    st.pop("proton_runtime", None)
    save_state(game.path, st)
    if not c:
        return
    if game.steam_root and c.get("tool"):
        compat.set_game_runtime(game.steam_root, c["tool"], game.appid, None)
    compat.restore_prefix(game)
    if game.steam_root and compat.get_tool(game.steam_root, game.appid) == c.get("tool"):
        try:
            compat.set_tool(game.steam_root, game.appid, c.get("previous"))
            notes.append("Возвращён прежний инструмент совместимости")
        except steam.SteamRunningError:
            notes.append("Steam открыт: в Свойствах игры → Совместимость верните прежний Proton")
    if game.steam_root and c.get("tool") and compat.remove_tool_if_unused(game.steam_root, c["tool"]):
        notes.append("Proton-обёртка больше не используется и удалена")


def _setup_compat(game: Game, runtime: dict, notes: list) -> None:
    """Select our Proton wrapper for this game and record which runtime files it should use."""
    if game.source not in ("steam", "shortcut") or not game.steam_root:
        notes.append("Игра не из Steam: Proton-обёртку назначить нельзя, этот способ не сработает")
        return
    current = compat.get_tool(game.steam_root, game.appid)
    base = compat.resolve_base(game.steam_root, current)
    if base is None:
        notes.append("Не найден установленный Proton для обёртки")
        return
    tool = compat.install_tool(game.steam_root, base)
    st = load_state(game.path)
    old = st.get("compat") or {}
    previous = old.get("previous", current if not (current or "").startswith(compat.TOOL_PREFIX) else None)
    st["compat"] = {"tool": tool, "previous": previous, "base": str(base)}
    st["proton_runtime"] = runtime
    save_state(game.path, st)
    compat.set_game_runtime(game.steam_root, tool, game.appid, runtime)
    placed = True
    for kind in ("nvcuda", "nvapi64"):
        if runtime.get(kind):
            placed = bool(compat.place_in_prefix(game, runtime[kind], kind)) and placed
    if current != tool:
        try:
            compat.set_tool(game.steam_root, game.appid, tool)
            notes.append(f"Игре назначен инструмент совместимости «{base.name} + DLSS FG (dlss-swapper)»")
        except steam.SteamRunningError:
            notes.append(f"⚠ ОБЁРТКА НЕ НАЗНАЧЕНА (Steam открыт). Без неё игра упадёт при включении FG. "
                         f"Закройте Steam и нажмите «Назначить» в приложении или выберите в Свойствах игры → "
                         f"Совместимость «{base.name} + DLSS FG (dlss-swapper)»")
    if not placed:
        notes.append("Префикс игры ещё не создан: файлы положатся при первом запуске")


def _enable_hags(game: Game, notes: list) -> None:
    """Some games read the Windows HAGS setting from the registry (Wine has none)."""
    pfx = compat.prefix_dir(game)
    if not pfx:
        return
    if wineprefix.hags_value(pfx) == 2:
        return
    if wineprefix.prefix_busy():
        notes.append("Wine запущен: HwSchMode (HAGS) в реестр не записан — повторите, когда игра закрыта")
        return
    if wineprefix.enable_hags(pfx):
        st = load_state(game.path)
        st["hags_added"] = True   # we wrote it, so Remove takes it back out
        save_state(game.path, st)
        notes.append("В реестр префикса записан HwSchMode=2 (HAGS включён)")


def _undo_hags(game: Game, notes: list) -> None:
    st = load_state(game.path)
    if not st.pop("hags_added", None):
        return
    pfx = compat.prefix_dir(game)
    if pfx and wineprefix.prefix_busy():
        st["hags_added"] = True
        notes.append("Wine запущен: HwSchMode из реестра не удалён — нажмите «Удалить» ещё раз, когда игра закрыта")
    elif pfx and wineprefix.disable_hags(pfx):
        notes.append("HwSchMode удалён из реестра префикса")
    save_state(game.path, st)


def _uninstall_all(game: Game, cur: str, notes: list, keep_compat: bool = False) -> tuple[str, bool]:
    removed = False
    for inst in optiscaler.uninstall(game.path):
        cur, removed = optiscaler.revert_launch_options(cur, inst), True
    for inst in dlssg_sm86.uninstall(game.path):
        cur, removed = dlssg_sm86.revert_launch_options(cur, inst), True
    st = load_state(game.path)
    for inst in (st.pop(dlssgopen.STATE_KEY, None) or {}).values():
        if inst.get("launch_undo"):
            cur = launchopts.revert(cur, inst["launch_undo"])
        removed = True
    save_state(game.path, st)
    if not keep_compat:
        _undo_compat(game, notes)
    return cur, removed


def wrapper_status(game: Game) -> tuple[str | None, bool]:
    """(tool name the game needs, whether it is selected in Steam) for wrapper-based methods."""
    c = load_state(game.path).get("compat") or {}
    tool = c.get("tool")
    return tool, compat.wrapper_assigned(game, tool)


def assign_wrapper(game: Game) -> str:
    tool, ok = wrapper_status(game)
    if not tool:
        raise RuntimeError("Для этой игры обёртка не нужна")
    if not ok:
        compat.set_tool(game.steam_root, game.appid, tool)
    return tool


def remove_all_fg(game: Game, write: bool = True) -> LaunchUpdate | None:
    old = _read_opts(game)
    notes: list = []
    cur, removed = _uninstall_all(game, old, notes)
    _undo_hags(game, notes)
    if not removed:
        return None
    lu = _write_opts(game, old, cur, write)
    if notes:
        lu.error = "\n".join(([lu.error] if lu.error else []) + notes)
    return lu


def setup_fg(game: Game, target: Path, method: str, opti_opts: optiscaler.InstallOptions,
             opti_bundle, dlssg_opts: dlssg_sm86.Options, dlssg_bundle, write: bool = True,
             dlssgopen_bundle=None, dlssg_indicator: bool = False, allow_any: bool = False) -> FgSetupResult:
    """Remove whatever is installed, install the chosen method, write launch options once.

    *_bundle are callables returning a bundle (they may download)."""
    method = METHOD_ALIASES.get(method, method)
    if method == "xefg" and not allow_any and dlssg_sm86.game_has_dlssg(game.path):
        raise ValueError(XEFG_NEEDS_NO_DLSSG)
    old = _read_opts(game)
    notes: list[str] = []
    needs_wrapper = method == "dlssg_open" or (method == "native" and dlssg_opts.fake_cuda)
    cur, _ = _uninstall_all(game, old, notes, keep_compat=needs_wrapper)
    rel = str(target.relative_to(game.path)) if target != game.path else "."
    warnings: list[str] = []
    placed = 0
    if method == "dlssg_open":
        bundle = (dlssgopen_bundle or dlssgopen.download_latest)()
        ok, msg = dlssgopen.runtime_ok(game.path)
        if not ok:
            warnings.append(msg)
        sm = dlssgopen.gpu_sm()
        cur, undo = launchopts.apply(cur, dlssgopen.launch_env(sm, dlssg_indicator), {})
        st = load_state(game.path)
        st[dlssgopen.STATE_KEY] = {rel: {"version": bundle.version,
                                         "options": {"sm": sm, "indicator": dlssg_indicator},
                                         "launch_undo": undo}}
        save_state(game.path, st)
        _setup_compat(game, {"nvcuda": dlssgopen.VARIANT, "nvapi64": dlssgopen.VARIANT}, notes)
        version = bundle.version
    elif method == "native":
        bundle = dlssg_bundle()
        res = dlssg_sm86.install(game.path, target, bundle, dlssg_opts)
        env, ov = dlssg_sm86.launch_env(dlssg_opts, target)
        cur, undo = launchopts.apply(cur, env, ov)
        dlssg_sm86.set_launch_undo(game.path, rel, undo)
        version, placed, warnings = bundle.version, len(res.placed), res.warnings
        if dlssg_opts.fake_cuda:
            _setup_compat(game, {"nvcuda": "fake"}, notes)
    else:
        opti_opts.fg_mode = method
        bundle = opti_bundle()
        res = optiscaler.install(game.path, target, bundle, opti_opts)
        env, ov = optiscaler.launch_env(opti_opts)
        cur, undo = launchopts.apply(cur, env, ov)
        optiscaler.set_launch_undo(game.path, rel, undo)
        version, placed = bundle.tag, len(res.placed)
        warnings = list(res.warnings) + [f"нет ключа в ini: {m}" for m in res.ini_missing]
        if res.skipped:
            warnings.append("Оставлены файлы игры: " + ", ".join(Path(s).name for s in res.skipped))
    if method in STREAMLINE_FG:
        _enable_hags(game, notes)
    else:
        _undo_hags(game, notes)
    lu = _write_opts(game, old, cur, write)
    spoof_ok = method == "dlssg_open" or (method in optiscaler.FG_MODES and opti_opts.spoof_ada)
    if "DXVK_NVAPI_GPU_ARCH" in lu.new and not spoof_ok:
        lu.error = ((lu.error + "\n") if lu.error else "") + (
            "В параметрах запуска есть DXVK_NVAPI_GPU_ARCH — уберите его: на RTX 30 он вешает видеокарту.")
    return FgSetupResult(method, version, target, placed, warnings, lu, notes)
