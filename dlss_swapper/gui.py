"""GTK4 + libadwaita interface."""
from __future__ import annotations

import subprocess
import threading
import traceback
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__, core, gpu, optiscaler, steam, swapper, updater  # noqa: E402
from .library import DLL_TYPES, Library  # noqa: E402

APP_ID = "io.github.capptainncwc.DlssSwapperLinux"
PRESETS = [("Не менять", None), ("Как в игре", 0), ("J", 10), ("K", 11), ("L", 12), ("M", 13)]
UPSCALER_NAMES = {"dlss": "DLSS", "fsr31": "FSR 3.1 / FSR 4", "xess": "XeSS", "fsr22": "FSR 2.2",
                  "auto": "Как в OptiScaler.ini"}
SOURCE_NAMES = {"steam": "Steam", "shortcut": "Ярлык Steam", "folder": "Папка"}

CSS = b"""
.game-title { font-weight: 600; }
.chip { font-size: 0.8em; padding: 1px 7px; border-radius: 99px;
        background: alpha(currentColor, 0.08); }
.chip.accent { background: alpha(@accent_bg_color, 0.18); color: @accent_color; }
.chip.success { background: alpha(@success_bg_color, 0.18); color: @success_color; }
.ver-swapped { color: @success_color; }
"""


def _strings(items: list[str]) -> Gtk.StringList:
    return Gtk.StringList.new(items)


def _combo(title: str, items: list[str], selected: int = 0, subtitle: str = "") -> Adw.ComboRow:
    row = Adw.ComboRow(title=title, model=_strings(items))
    if subtitle:
        row.set_subtitle(subtitle)
    if items:
        row.set_selected(min(selected, len(items) - 1))
    return row


def _switch(title: str, active: bool, subtitle: str = "", sensitive: bool = True) -> Adw.SwitchRow:
    row = Adw.SwitchRow(title=title, subtitle=subtitle, active=active)
    row.set_sensitive(sensitive)
    return row


def _chip(text: str, css: str = "") -> Gtk.Label:
    lb = Gtk.Label(label=text)
    lb.add_css_class("chip")
    if css:
        lb.add_css_class(css)
    return lb


class Window(Adw.ApplicationWindow):
    def __init__(self, app, extra_dirs):
        super().__init__(application=app, title="DLSS Swapper")
        self.set_default_size(1100, 780)
        self.set_size_request(360, 400)
        self.lib = Library()
        self.gpu = gpu.detect()
        self.extra_dirs = list(extra_dirs)
        self.games: list[steam.Game] = []
        self.scan_cache: dict = {}
        self.current: steam.Game | None = None
        self.dlls: list[swapper.FoundDll] = []
        self._busy = False
        self._release: updater.Release | None = None

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)
        self.split = Adw.NavigationSplitView()
        self.split.set_min_sidebar_width(280)
        self.split.set_max_sidebar_width(380)
        self.toasts.set_child(self.split)

        # adaptive: collapse on narrow windows
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 700sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        self._build_sidebar()
        self._build_content()
        self._actions()
        self.show_placeholder()
        self.load_games()
        self.check_updates(silent=True)

    # ============================================================ layout
    def _build_sidebar(self) -> None:
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        b = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Пересканировать игры")
        b.connect("clicked", lambda *_: self.load_games())
        hb.pack_start(b)
        b = Gtk.Button(icon_name="folder-new-symbolic", tooltip_text="Добавить папку с игрой")
        b.connect("clicked", self.on_add_folder)
        hb.pack_start(b)
        menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append("Импортировать DLL…", "win.import-dll")
        sec.append("Импортировать OptiScaler…", "win.import-opti")
        sec.append("Скачать последний OptiScaler", "win.fetch-opti")
        sec.append("Обновить список версий DLL", "win.refresh-manifest")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append("Папка библиотеки", "win.open-lib")
        sec.append("Проверить обновления", "win.check-update")
        sec.append("О программе", "win.about")
        menu.append_section(None, sec)
        hb.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                   tooltip_text="Меню"))
        tv.add_top_bar(hb)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=10,
                       margin_end=10, margin_bottom=6)
        srow = Gtk.Box(spacing=6)
        self.search = Gtk.SearchEntry(placeholder_text="Поиск", hexpand=True)
        self.search.connect("search-changed", lambda *_: self.game_list.invalidate_filter())
        srow.append(self.search)
        self.only_libs = Gtk.ToggleButton(icon_name="funnel-symbolic", active=True,
                                          tooltip_text="Только игры с DLSS / FSR / XeSS")
        self.only_libs.add_css_class("flat")
        self.only_libs.connect("toggled", lambda *_: self.game_list.invalidate_filter())
        srow.append(self.only_libs)
        sbox.append(srow)
        box.append(sbox)
        self.game_list = Gtk.ListBox()
        self.game_list.add_css_class("navigation-sidebar")
        self.game_list.set_filter_func(self._filter)
        self.game_list.connect("row-selected", self.on_game_activated)
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_child(self.game_list)
        box.append(sw)
        self.gpu_label = Gtk.Label(label=self.gpu.name, margin_top=6, margin_bottom=8)
        self.gpu_label.add_css_class("dim-label")
        self.gpu_label.add_css_class("caption")
        box.append(self.gpu_label)
        tv.set_content(box)
        page = Adw.NavigationPage(title="Игры", child=tv)
        self.split.set_sidebar(page)

    def _build_content(self) -> None:
        tv = Adw.ToolbarView()
        self.content_header = Adw.HeaderBar()
        if hasattr(Adw, "Spinner"):  # libadwaita >= 1.6
            self.spinner = Adw.Spinner()
        else:
            self.spinner = Gtk.Spinner(spinning=True)
        self.spinner.set_visible(False)
        self.content_header.pack_end(self.spinner)
        tv.add_top_bar(self.content_header)
        self.update_banner = Adw.Banner(button_label="Обновить")
        self.update_banner.connect("button-clicked", self.on_update)
        tv.add_top_bar(self.update_banner)
        self.progress = Gtk.ProgressBar()
        self.progress.add_css_class("osd")
        self.progress.set_visible(False)
        tv.add_top_bar(self.progress)
        self.content_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        tv.set_content(self.content_stack)
        self.content_page = Adw.NavigationPage(title="DLSS Swapper", child=tv)
        self.split.set_content(self.content_page)

    def _actions(self) -> None:
        for name, cb in (("import-dll", self.on_import_dll), ("import-opti", self.on_import_opti),
                         ("fetch-opti", self.on_fetch_opti), ("refresh-manifest", self.on_refresh_manifest),
                         ("open-lib", lambda *_: self._open(self.lib.dir)),
                         ("check-update", lambda *_: self.check_updates(silent=False)),
                         ("about", self.on_about)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

    # ============================================================ helpers
    def toast(self, text: str, timeout: int = 3) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout))

    def run_bg(self, fn, done=None, label: str | None = None, quiet: bool = False) -> None:
        if self._busy and not quiet:
            self.toast("Подождите завершения текущей операции")
            return
        if not quiet:
            self._busy = True
            self.spinner.set_visible(True)
            if label:
                self.content_page.set_title(label)

        def work():
            try:
                res, err = fn(), None
            except Exception as e:  # noqa: BLE001
                if not quiet:
                    traceback.print_exc()
                res, err = None, e
            GLib.idle_add(finish, res, err)

        def finish(res, err):
            if not quiet:
                self._busy = False
                self.spinner.set_visible(False)
                self.progress.set_visible(False)
                self.content_page.set_title(self.current.name if self.current else "DLSS Swapper")
            if err and not quiet:
                self.alert("Ошибка", str(err))
            elif not err and done:
                done(res)
            return False

        threading.Thread(target=work, daemon=True).start()

    def progress_cb(self, done: int, total: int) -> None:
        if total:
            def upd():
                self.progress.set_visible(True)
                self.progress.set_fraction(done / total)
            GLib.idle_add(upd)

    def alert(self, title: str, body: str) -> None:
        d = Adw.AlertDialog(heading=title, body=body)
        d.add_response("ok", "OK")
        d.present(self)

    def _open(self, path: Path) -> None:
        try:
            subprocess.Popen(["xdg-open", str(path)])
        except OSError as e:
            self.alert("Не удалось открыть", str(e))

    def _copy(self, text: str) -> None:
        Gdk.Display.get_default().get_clipboard().set(text)

    def _set_page(self, widget: Gtk.Widget) -> None:
        old = self.content_stack.get_visible_child()
        self.content_stack.add_child(widget)
        self.content_stack.set_visible_child(widget)
        if old is not None:
            GLib.timeout_add(400, lambda: (self.content_stack.remove(old), False)[1])

    # ============================================================ games list
    def load_games(self) -> None:
        def fn():
            games = core.all_games(self.extra_dirs)
            return games, {g.key: swapper.scan(g.path) for g in games}

        def done(res):
            self.games, self.scan_cache = res
            self.game_list.remove_all()
            for g in self.games:
                self.game_list.append(self._game_row(g))
            n = sum(1 for g in self.games if self._has_libs(g))
            self.gpu_label.set_label(f"{self.gpu.name} · игр: {n}/{len(self.games)}")

        self.run_bg(fn, done, "Поиск игр…")

    def _has_libs(self, g) -> bool:
        return bool(self.scan_cache.get(g.key)) or bool(optiscaler.installed(g.path))

    def _game_row(self, g: steam.Game) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.game = g
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6, margin_bottom=6,
                      margin_start=4, margin_end=4)
        title = Gtk.Label(label=g.name, xalign=0, ellipsize=3)
        title.add_css_class("game-title")
        box.append(title)
        chips = Gtk.Box(spacing=4)
        dl = [d for d in self.scan_cache.get(g.key, []) if not d.owned_by_optiscaler]
        dlss = next((d for d in dl if d.kind == "dlss"), None)
        if dlss:
            chips.append(_chip(f"DLSS {dlss.version}", "success" if dlss.swapped else ""))
        if any(d.kind == "dlss_g" for d in dl):
            chips.append(_chip("FG"))
        if any(d.kind.startswith("fsr") for d in dl):
            chips.append(_chip("FSR"))
        if any(d.kind.startswith("xess") for d in dl):
            chips.append(_chip("XeSS"))
        if optiscaler.installed(g.path):
            chips.append(_chip("Opti", "accent"))
        if not chips.get_first_child():
            chips.append(_chip(SOURCE_NAMES[g.source]))
        box.append(chips)
        row.set_child(box)
        return row

    def _filter(self, row) -> bool:
        g = row.game
        q = self.search.get_text().lower()
        if q and q not in g.name.lower():
            return False
        return not (self.only_libs.get_active() and not self._has_libs(g))

    def on_game_activated(self, _lb, row) -> None:
        if row is None or getattr(self, "_refreshing", False) or row.game is self.current:
            if row is not None and not getattr(self, "_refreshing", False):
                self.split.set_show_content(True)
            return
        self.current = row.game
        self.show_game()
        self.split.set_show_content(True)

    def _refresh_row(self, g) -> None:
        row = self.game_list.get_first_child()
        while row:
            if getattr(row, "game", None) is g:
                idx = row.get_index()
                self.game_list.remove(row)
                new = self._game_row(g)
                self._refreshing = True
                self.game_list.insert(new, idx)
                self.game_list.select_row(new)
                self._refreshing = False
                break
            row = row.get_next_sibling()

    # ============================================================ pages
    def show_placeholder(self) -> None:
        sp = Adw.StatusPage(icon_name="applications-games-symbolic", title="Выберите игру",
                            description=("Замена версий DLSS, DLSS FG, Ray Reconstruction, FSR и XeSS.\n"
                                         "OptiScaler с разблокировкой Frame Generation для карт без RTX 40.\n"
                                         "Игры запускаются через Steam + Proton как обычно."))
        self._set_page(sp)

    def show_game(self) -> None:
        g = self.current
        self.content_page.set_title(g.name)

        def fn():
            return (swapper.scan(g.path), swapper.anti_cheat(g.path), swapper.exe_candidates(g.path),
                    {k: self.lib.records(k) for k in DLL_TYPES}, optiscaler.local_bundles(),
                    steam.get_launch_options(g) if g.source != "folder" else "")

        def done(res):
            if self.current is not g:
                return
            self.dlls, ac, cands, recs, bundles, lopts = res
            self.scan_cache[g.key] = self.dlls
            self._set_page(self._game_page(g, ac, cands, recs, bundles, lopts))

        self.run_bg(fn, done, "Сканирование…")

    def _game_page(self, g, ac, cands, recs, bundles, lopts) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        # ---- info
        grp = Adw.PreferencesGroup()
        row = Adw.ActionRow(title=SOURCE_NAMES[g.source] + (f" · {g.appid}" if g.appid else ""),
                            subtitle=str(g.path), subtitle_selectable=True)
        row.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
        b = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Открыть")
        b.add_css_class("flat")
        b.connect("clicked", lambda *_: self._open(g.path))
        row.add_suffix(b)
        grp.add(row)
        if ac:
            w = Adw.ActionRow(title="Обнаружен античит",
                              subtitle="Моды в онлайн-играх могут привести к бану: " + ", ".join(ac[:3]))
            w.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
            w.add_css_class("warning")
            grp.add(w)
        page.add(grp)

        # ---- upscaler DLLs
        grp = Adw.PreferencesGroup(title="Апскейлеры",
                                   description="Оригиналы сохраняются рядом как *.dlsss")
        game_dlls = [d for d in self.dlls if not d.owned_by_optiscaler]
        if not game_dlls:
            grp.add(Adw.ActionRow(title="DLL не найдены",
                                  subtitle="В игре нет nvngx_dlss*.dll, FSR 3.1 или XeSS"))
        for d in game_dlls:
            rlist = recs.get(d.kind, [])
            sub = f"Сейчас {d.version or '?'}"
            if d.swapped:
                sub += f" · оригинал {d.original_version}"
            row = _combo(d.label, [r.display for r in rlist] or ["нет данных"], subtitle=sub)
            row.set_tooltip_text(str(d.path.relative_to(g.path)))
            if d.swapped:
                b = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER,
                               tooltip_text="Вернуть оригинал")
                b.add_css_class("flat")
                b.connect("clicked", self.on_restore, d)
                row.add_suffix(b)
            b = Gtk.Button(label="Заменить", valign=Gtk.Align.CENTER, sensitive=bool(rlist))
            b.add_css_class("suggested-action")
            b.connect("clicked", self.on_swap, d, row, rlist)
            row.add_suffix(b)
            grp.add(row)
        page.add(grp)

        # ---- OptiScaler
        inst = optiscaler.installed(g.path)
        if self.gpu.native_dlss_fg:
            desc = "У вас RTX 40/50 — DLSS FG работает нативно"
        elif self.gpu.is_nvidia:
            desc = "Разблокирует DLSS FG: игра видит RTX 40, кадры генерирует FSR / XeSS FG"
        else:
            desc = "Подмена DLSS на FSR/XeSS и разблокировка FG через fakenvapi"
        grp = Adw.PreferencesGroup(title="OptiScaler · Frame Generation", description=desc)
        if inst:
            k, v = next(iter(inst.items()))
            st = Adw.ActionRow(title=f"Установлен {v['version']}",
                               subtitle=f"{k} · {v['proxy']} · {optiscaler.FG_MODES.get(v['fg_mode'], '')}")
            st.add_prefix(Gtk.Image(icon_name="emblem-ok-symbolic"))
            b = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER,
                           tooltip_text="Открыть OptiScaler.ini")
            b.add_css_class("flat")
            b.connect("clicked", lambda *_: self._open(g.path / k / "OptiScaler.ini"))
            st.add_suffix(b)
            grp.add(st)
        self._bundles = bundles
        self._cands = cands or [g.path]
        has_fg = any(d.kind == "dlss_g" for d in self.dlls)
        has_dlss = any(d.kind == "dlss" for d in self.dlls)
        self._modes = list(optiscaler.FG_MODES)
        mode = "optifg" if has_fg else ("upscaler" if has_dlss else "none")
        if self.gpu.native_dlss_fg:
            mode = "none"
        self.w_fg = _combo("Frame Generation", [optiscaler.FG_MODES[m] for m in self._modes],
                           self._modes.index(mode),
                           "" if has_fg else "В игре нет DLSS FG — подойдёт «FG из апскейлера»")
        grp.add(self.w_fg)
        self.w_up = _combo("Апскейлер", [UPSCALER_NAMES[u] for u in optiscaler.UPSCALERS],
                           0 if self.gpu.has_dlss else 1)
        grp.add(self.w_up)
        self.w_preset = _combo("Пресет DLSS", [p[0] for p in PRESETS])
        grp.add(self.w_preset)

        adv = Adw.ExpanderRow(title="Дополнительно")
        self.w_bundle = _combo("Версия OptiScaler", [b.tag for b in bundles] + ["Последняя с GitHub"])
        adv.add_row(self.w_bundle)
        self.w_target = _combo("Папка с exe", [str(p.relative_to(g.path)) if p != g.path else "."
                                               for p in self._cands])
        adv.add_row(self.w_target)
        self.w_proxy = _combo("Имя прокси-DLL", optiscaler.PROXY_NAMES,
                              subtitle="Если игра не стартует с dxgi.dll — попробуйте winmm.dll")
        adv.add_row(self.w_proxy)
        self.w_spoof = _switch("Показывать игре RTX 40", self.gpu.is_nvidia and not self.gpu.native_dlss_fg,
                               "DXVK_NVAPI_GPU_ARCH=AD100", self.gpu.is_nvidia)
        adv.add_row(self.w_spoof)
        self.w_overwrite = _switch("Заменять FSR/XeSS DLL игры", False, "файлами из комплекта OptiScaler")
        adv.add_row(self.w_overwrite)
        self.w_writelaunch = _switch("Прописать параметры запуска", g.source != "folder",
                                     "Steam должен быть закрыт", g.source != "folder")
        adv.add_row(self.w_writelaunch)
        grp.add(adv)

        bb = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=12)
        if inst:
            b = Gtk.Button(label="Удалить")
            b.add_css_class("destructive-action")
            b.add_css_class("pill")
            b.connect("clicked", self.on_remove_opti)
            bb.append(b)
        b = Gtk.Button(label="Переустановить" if inst else "Установить")
        b.add_css_class("suggested-action")
        b.add_css_class("pill")
        b.connect("clicked", self.on_install_opti)
        bb.append(b)
        grp.add(bb)
        page.add(grp)

        # ---- launch options
        grp = Adw.PreferencesGroup(
            title="Параметры запуска Steam",
            description=("Применяются при запуске через Proton. Steam должен быть закрыт во время записи."
                         if g.source != "folder" else
                         "Добавьте игру в Steam как стороннюю, включите Proton и обновите список"))
        self.w_launch = Adw.EntryRow(title="Параметры", text=lopts, show_apply_button=True)
        self.w_launch.set_sensitive(g.source != "folder")
        self.w_launch.connect("apply", self.on_save_launch)
        b = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Копировать")
        b.add_css_class("flat")
        b.connect("clicked", lambda *_: (self._copy(self.w_launch.get_text()), self.toast("Скопировано")))
        self.w_launch.add_suffix(b)
        grp.add(self.w_launch)
        page.add(grp)
        return page

    # ============================================================ actions
    def on_swap(self, _b, d, row, rlist) -> None:
        rec = rlist[row.get_selected()]
        g = self.current

        def fn():
            src = self.lib.ensure(rec, self.progress_cb)
            swapper.swap(g.path, d, rec, src)

        def done(_):
            self.toast(f"{d.label}: {d.version} → {rec.version}")
            self.show_game()
            self._refresh_row(g)

        self.run_bg(fn, done, f"Замена → {rec.version}")

    def on_restore(self, _b, d) -> None:
        ok = swapper.restore(self.current.path, d)
        self.toast("Оригинал восстановлен" if ok else "Бэкап не найден")
        self.show_game()
        self._refresh_row(self.current)

    def _opts(self) -> optiscaler.InstallOptions:
        return optiscaler.InstallOptions(
            proxy=optiscaler.PROXY_NAMES[self.w_proxy.get_selected()],
            fg_mode=self._modes[self.w_fg.get_selected()],
            upscaler=optiscaler.UPSCALERS[self.w_up.get_selected()],
            nvidia=self.gpu.is_nvidia,
            spoof_ada=self.w_spoof.get_active(),
            overwrite_game_files=self.w_overwrite.get_active(),
            dlss_preset=PRESETS[self.w_preset.get_selected()][1])

    def on_install_opti(self, _b) -> None:
        g = self.current
        opts = self._opts()
        target = self._cands[self.w_target.get_selected()]
        bi = self.w_bundle.get_selected()
        write = self.w_writelaunch.get_active()

        def fn():
            bundle = self._bundles[bi] if bi < len(self._bundles) else optiscaler.download_latest(self.progress_cb)
            res = optiscaler.install(g.path, target, bundle, opts)
            rel = str(target.relative_to(g.path)) if target != g.path else "."
            return bundle, res, core.apply_launch_options(g, rel, opts, write=write)

        def done(r):
            bundle, res, lu = r
            msg = [f"Файлов скопировано: {len(res.placed)}"]
            if res.skipped:
                msg.append("Оставлены файлы игры: " + ", ".join(Path(s).name for s in res.skipped))
            msg += [f"⚠ {w}" for w in res.warnings]
            if res.ini_missing:
                msg.append("Нет ключей в ini: " + ", ".join(res.ini_missing))
            msg.append(f"\nПараметры запуска:\n{lu.new}")
            if lu.written:
                msg.append("✓ Записаны в Steam")
            else:
                self._copy(lu.new)
                msg.append(("⚠ " + lu.error + "\n" if lu.error else "") + "Строка скопирована в буфер обмена")
            msg.append("\nВ игре включите DLSS и DLSS Frame Generation. Меню OptiScaler — Insert.")
            self.alert(f"OptiScaler {bundle.tag} установлен", "\n".join(msg))
            self.show_game()
            self._refresh_row(g)

        self.run_bg(fn, done, "Установка OptiScaler…")

    def on_remove_opti(self, _b) -> None:
        g = self.current
        lu = core.remove_optiscaler(g, None, write=self.w_writelaunch.get_active())
        if lu and not lu.written and lu.new != lu.old:
            self._copy(lu.new)
            self.alert("OptiScaler удалён", (lu.error or "") +
                       f"\n\nВерните параметры запуска вручную (скопировано):\n{lu.new or '(пусто)'}")
        else:
            self.toast("OptiScaler удалён")
        self.show_game()
        self._refresh_row(g)

    def on_save_launch(self, entry) -> None:
        try:
            steam.set_launch_options(self.current, entry.get_text())
            self.toast("Параметры запуска сохранены")
        except steam.SteamRunningError as e:
            self._copy(entry.get_text())
            self.alert("Steam запущен", str(e) + "\n\nСтрока скопирована в буфер обмена.")
        except Exception as e:  # noqa: BLE001
            self.alert("Ошибка", str(e))

    # ============================================================ menu
    def on_add_folder(self, *_):
        def cb(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            self.extra_dirs.append(Path(f.get_path()))
            self.load_games()

        Gtk.FileDialog(title="Папка с игрой").select_folder(self, None, cb)

    def on_import_dll(self, *_):
        def cb(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            out = []
            for i in range(files.get_n_items()):
                f = files.get_item(i)
                try:
                    r = self.lib.import_dll(Path(f.get_path()))
                    out.append(f"✓ {DLL_TYPES[r.kind][1]} {r.version}")
                except Exception as e:  # noqa: BLE001
                    out.append(f"✗ {f.get_basename()}: {e}")
            self.alert("Импорт DLL", "\n".join(out))
            if self.current:
                self.show_game()

        flt = Gtk.FileFilter(name="DLL")
        flt.add_pattern("*.dll")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        Gtk.FileDialog(title="DLL для библиотеки", filters=filters).open_multiple(self, None, cb)

    def on_import_opti(self, *_):
        def cb(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            self.run_bg(lambda: optiscaler.import_bundle(Path(f.get_path())),
                        lambda b: (self.toast(f"OptiScaler {b.tag} импортирован"),
                                   self.current and self.show_game()), "Распаковка…")

        Gtk.FileDialog(title="Архив OptiScaler (.7z / .zip)").open(self, None, cb)

    def on_fetch_opti(self, *_):
        self.run_bg(lambda: optiscaler.download_latest(self.progress_cb),
                    lambda b: (self.toast(f"OptiScaler {b.tag} готов"), self.current and self.show_game()),
                    "Скачивание OptiScaler…")

    def on_refresh_manifest(self, *_):
        self.run_bg(lambda: self.lib.manifest(refresh=True),
                    lambda m: (self.toast(f"Версий DLSS: {len(m.get('dlss', []))}"),
                               self.current and self.show_game()), "Обновление…")

    def on_about(self, *_):
        about = Adw.AboutDialog(
            application_name="DLSS Swapper", application_icon=APP_ID, version=__version__,
            developer_name="CapptainnCWC", website=updater.PROJECT_URL,
            issue_url=updater.PROJECT_URL + "/issues", license_type=Gtk.License.MIT_X11,
            comments="Замена DLSS/FSR/XeSS и OptiScaler с разблокировкой Frame Generation "
                     "для игр в Steam Proton.")
        about.add_credit_section("Использует", ["DLSS Swapper manifest (beeradmoore)",
                                                "OptiScaler", "dlssg-to-fsr3 (Nukem9)", "fakenvapi"])
        about.present(self)

    # ============================================================ updates
    def check_updates(self, silent: bool) -> None:
        def done(rel):
            self._release = rel
            if rel.newer:
                self.update_banner.set_title(f"Доступна версия {rel.version}")
                self.update_banner.set_revealed(True)
            elif not silent:
                self.toast(f"Установлена последняя версия ({__version__})")

        def fail_safe():
            try:
                return updater.check()
            except Exception as e:  # noqa: BLE001
                if silent:
                    raise
                raise RuntimeError(f"Не удалось проверить обновления: {e}") from e

        self.run_bg(fail_safe, done, quiet=silent)

    def on_update(self, *_):
        rel = self._release
        if not rel:
            return
        d = Adw.AlertDialog(heading=f"Обновить до {rel.version}?",
                            body=(rel.notes[:800] or "Новая версия доступна на GitHub."))
        d.add_response("cancel", "Отмена")
        d.add_response("web", "Открыть страницу")
        d.add_response("update", "Обновить")
        d.set_response_appearance("update", Adw.ResponseAppearance.SUGGESTED)

        def resp(_d, r):
            if r == "web":
                self._open(rel.url)
            elif r == "update":
                self.run_bg(lambda: updater.apply(rel, self.progress_cb), self._updated, "Обновление…")

        d.connect("response", resp)
        d.present(self)

    def _updated(self, msg: str) -> None:
        self.update_banner.set_revealed(False)
        d = Adw.AlertDialog(heading="Готово", body=msg)
        d.add_response("later", "Позже")
        d.add_response("restart", "Перезапустить")
        d.set_response_appearance("restart", Adw.ResponseAppearance.SUGGESTED)
        d.connect("response", lambda _d, r: r == "restart" and updater.restart())
        d.present(self)


class App(Adw.Application):
    def __init__(self, extra_dirs):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.extra_dirs = extra_dirs

    def do_activate(self):
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS, -1)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Window(self, self.extra_dirs).present()


def run(extra_dirs=None) -> None:
    App(extra_dirs or []).run([])
