"""Minimalistischer Pomodoro-Timer für Windows.

Reines Python (Standardbibliothek, Tkinter). Start:
    pythonw pomodoro.py
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
import sys
import time
import traceback
from datetime import date
from pathlib import Path

APP_NAME = "Pomodoro"
IS_WINDOWS = sys.platform.startswith("win")

# ------------------------------------------------------------- Config ------

if IS_WINDOWS:
    CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "MinimalPomodoro"
else:
    CONFIG_DIR = Path.home() / ".config" / "minimal-pomodoro"
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = CONFIG_DIR / "fehler.log"


# ------------------------------------------------------- Fehlermeldungen ----


def report_error(message: str, detail: str = "") -> None:
    """Fehler sichtbar machen - auch wenn ohne Konsole gestartet wurde.

    pythonw.exe und die .exe haben kein Konsolenfenster; ein Traceback waere
    sonst unsichtbar und das Programm wuerde einfach kommentarlos verschwinden.
    """
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text(f"{message}\n\n{detail}\n", encoding="utf-8")
        message += f"\n\nDetails stehen in:\n{LOG_PATH}"
    except OSError:
        message += f"\n\n{detail}"

    if IS_WINDOWS:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
            return
        except Exception:
            pass
    print(f"{message}\n{detail}", file=sys.stderr)


try:
    import tkinter as tk
    import tkinter.font as tkfont
except ImportError as exc:  # Python ohne tcl/tk installiert
    report_error(
        "Zu dieser Python-Installation fehlt Tkinter.\n\n"
        "So geht es weiter: Systemsteuerung → Programme → Python → Ändern → "
        "Modify und dabei \"tcl/tk and IDLE\" anhaken.\n\n"
        "Alternativ läuft pomodoro.html ohne Installation im Browser.",
        f"{exc}\n\nPython: {sys.executable}\nVersion: {sys.version}",
    )
    raise SystemExit(1)

# ---------------------------------------------------------------- Farben ----

BG = "#0a0a0a"
BG_SOFT = "#141414"
BG_HOVER = "#1e1e1e"
FG = "#f2f2f2"
FG_DIM = "#6f6f6f"
FG_MID = "#9a9a9a"
ACCENT = "#e5533d"        # Fokus
ACCENT_BREAK = "#3d9a76"  # Pause
LINE = "#232323"

MODE_COLORS = {"focus": ACCENT, "short": ACCENT_BREAK, "long": ACCENT_BREAK}
MODE_LABELS = {"focus": "FOKUS", "short": "KURZE PAUSE", "long": "LANGE PAUSE"}

UI_FONT = "TkDefaultFont"  # wird beim Start auf die beste vorhandene Familie gesetzt

# ------------------------------------------------------------ Vorgaben -----

DEFAULT_CONFIG = {
    "focus": 25.0,
    "short": 5.0,
    "long": 15.0,
    "long_every": 4,
    "auto_start_after_break": True,
    "sound": True,
    "always_on_top": False,
    "plans": [
        {"name": "Klassisch", "spec": "4x25/5"},
        {"name": "Deep Work", "spec": "4x90/20"},
        {"name": "Sprint", "spec": "6x45/10"},
    ],
    "active_plan": None,
    "stats": {"date": "", "sessions": 0, "minutes": 0.0},
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # tiefe Kopie
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            cfg.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


# -------------------------------------------------------------- Helfer -----


def parse_minutes(text: str) -> float:
    """'90', '1.5', '1:30' -> Minuten. ValueError bei Unsinn."""
    text = str(text).strip().replace(",", ".")
    if not text:
        raise ValueError("leer")
    if ":" in text:
        mm, _, ss = text.partition(":")
        minutes = float(mm or 0) + float(ss or 0) / 60.0
    else:
        minutes = float(text)
    if minutes <= 0 or minutes > 24 * 60:
        raise ValueError("außerhalb des gültigen Bereichs")
    return round(minutes, 4)


def fmt_minutes(minutes: float) -> str:
    """Minuten kompakt: 90 -> '90', 5.5 -> '5:30'."""
    if abs(minutes - round(minutes)) < 1e-6:
        return str(int(round(minutes)))
    total = int(round(minutes * 60))
    return f"{total // 60}:{total % 60:02d}"


def fmt_duration(minutes: float) -> str:
    total = int(round(minutes))
    if total < 60:
        return f"{total} min"
    h, m = divmod(total, 60)
    return f"{h} h" if m == 0 else f"{h} h {m} min"


def fmt_clock(seconds: float) -> str:
    seconds = max(0, int(math.ceil(seconds - 1e-6)))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


SEGMENT_RE = re.compile(
    r"^\s*(?:(\d+)\s*[x*]\s*)?([\d.,:]+)\s*/\s*([\d.,:]+)\s*$", re.IGNORECASE
)


def parse_plan(spec: str) -> list[dict]:
    """'4x90/20' oder '4x25/5 + 1x25/15' -> Liste von Schritten.

    Jeder Schritt: {kind: 'focus'|'break', minutes, round, rounds}
    """
    spec = (spec or "").strip()
    if not spec:
        raise ValueError("Plan ist leer")
    parts = [p for p in re.split(r"[,+;]", spec) if p.strip()]
    if not parts:
        raise ValueError("Plan ist leer")

    blocks: list[tuple[int, float, float]] = []
    for part in parts:
        m = SEGMENT_RE.match(part)
        if not m:
            raise ValueError(f"'{part.strip()}' verstehe ich nicht (Format: 4x90/20)")
        count = int(m.group(1) or 1)
        if not 1 <= count <= 50:
            raise ValueError("Anzahl der Runden muss zwischen 1 und 50 liegen")
        blocks.append((count, parse_minutes(m.group(2)), parse_minutes(m.group(3))))

    total_rounds = sum(b[0] for b in blocks)
    steps: list[dict] = []
    index = 0
    for count, focus, brk in blocks:
        for _ in range(count):
            index += 1
            steps.append({"kind": "focus", "minutes": focus,
                          "round": index, "rounds": total_rounds})
            steps.append({"kind": "break", "minutes": brk,
                          "round": index, "rounds": total_rounds})
    return steps


def describe_plan(steps: list[dict]) -> str:
    focus = sum(s["minutes"] for s in steps if s["kind"] == "focus")
    pause = sum(s["minutes"] for s in steps if s["kind"] == "break")
    rounds = steps[0]["rounds"] if steps else 0
    return (f"{rounds} Runden · {fmt_duration(focus)} Fokus + "
            f"{fmt_duration(pause)} Pause · gesamt {fmt_duration(focus + pause)}")


# --------------------------------------------------------------- Sound -----


def build_chime(freqs=(880.0, 1174.7, 1567.98), seconds=1.6, rate=44100) -> bytes:
    """WAV-Bytes eines weichen Akkords mit Abklingen - ohne externe Datei."""
    frames = int(rate * seconds)
    samples = bytearray()
    for i in range(frames):
        t = i / rate
        value = 0.0
        for n, f in enumerate(freqs):
            start = n * 0.12
            if t < start:
                continue
            env = math.exp(-3.0 * (t - start))
            value += math.sin(2 * math.pi * f * (t - start)) * env
        value /= len(freqs)
        value *= min(1.0, t * 200)  # Klick am Anfang vermeiden
        samples += struct.pack("<h", int(max(-1.0, min(1.0, value)) * 26000))

    data = bytes(samples)
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


class Sound:
    def __init__(self) -> None:
        self._cache: dict[str, bytes] = {}

    def _wav(self, kind: str) -> bytes:
        if kind not in self._cache:
            if kind == "focus":  # Fokus vorbei -> heller Dreiklang
                self._cache[kind] = build_chime((880.0, 1174.7, 1567.98), 1.8)
            else:                # Pause vorbei -> ruhiger, tiefer
                self._cache[kind] = build_chime((523.25, 659.25, 783.99), 1.5)
        return self._cache[kind]

    def play(self, kind: str = "focus") -> None:
        if IS_WINDOWS:
            try:
                import winsound

                winsound.PlaySound(self._wav(kind),
                                   winsound.SND_MEMORY | winsound.SND_ASYNC)
                return
            except Exception:
                pass
        try:  # macOS / Linux: nur Systemton
            print("\a", end="", flush=True)
        except Exception:
            pass


# ------------------------------------------------------------- Widgets -----


class FlatButton(tk.Label):
    """Flacher Button ohne Rahmen - passend zum dunklen Layout."""

    def __init__(self, master, text, command, primary=False, small=False,
                 accent=FG, **kw):
        self.command = command
        self.primary = primary
        self.accent = accent
        self.enabled = True
        font = (UI_FONT, 9 if small else 10, "normal")
        super().__init__(
            master,
            text=text,
            font=font,
            bg=BG_SOFT if primary else BG,
            fg=accent if primary else FG_MID,
            padx=16 if small else 20,
            pady=7 if small else 9,
            cursor="hand2",
            **kw,
        )
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def _click(self, _event=None):
        if self.enabled and self.command:
            self.command()

    def _enter(self, _event=None):
        if self.enabled:
            self.configure(bg=BG_HOVER, fg=self.accent if self.primary else FG)

    def _leave(self, _event=None):
        self.configure(bg=BG_SOFT if self.primary else BG,
                       fg=self.accent if self.primary else FG_MID)

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self.configure(fg=(self.accent if self.primary else FG_MID)
                       if enabled else "#3a3a3a",
                       cursor="hand2" if enabled else "arrow")


def _hwnd(window: tk.Misc):
    """Fenster-Handle des echten Rahmenfensters (nicht des Tk-Kindfensters)."""
    import ctypes
    from ctypes import wintypes

    get_parent = ctypes.windll.user32.GetParent
    get_parent.argtypes = [wintypes.HWND]      # ohne argtypes wird das Handle
    get_parent.restype = wintypes.HWND         # auf 64 Bit abgeschnitten
    return get_parent(wintypes.HWND(window.winfo_id()))


def dark_titlebar(window: tk.Misc) -> None:
    """Dunkle Titelleiste unter Windows 10/11."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        window.update_idletasks()
        hwnd = _hwnd(window)
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (neu/alt)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, ctypes.c_int(attribute), ctypes.byref(value),
                ctypes.sizeof(value)
            )
    except Exception:
        pass


def flash_taskbar(window: tk.Misc) -> None:
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        ctypes.windll.user32.FlashWindow(_hwnd(window), True)
    except Exception:
        pass


# ----------------------------------------------------------------- App -----


class PomodoroApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_config()
        self.sound = Sound()

        self.state_name = "idle"        # idle | running | paused | choice
        self.mode = "focus"             # focus | short | long
        self.duration = self.cfg["focus"] * 60.0
        self.remaining = self.duration
        self.deadline = 0.0
        self.focus_done = 0             # abgeschlossene Fokus-Einheiten (Zyklus)

        self.plan_steps: list[dict] = []
        self.plan_index = 0
        self.plan_name: str | None = None

        self._build_fonts()
        self._build_window()
        self._build_ui()
        self._load_active_plan()
        self._roll_stats()
        self.apply_mode(self.mode, self._current_default_minutes())
        self.render()
        self._tick_id = self.after(100, self.tick)

    # ------------------------------------------------------------ Setup --

    def _build_fonts(self) -> None:
        families = set(tkfont.families())

        def pick(candidates, fallback):
            for name in candidates:
                if name in families:
                    return name
            return fallback

        ui = pick(["Segoe UI", "Inter", "Helvetica Neue", "DejaVu Sans"], "TkDefaultFont")
        digits = pick(["Cascadia Mono Light", "Cascadia Mono", "Consolas",
                       "DejaVu Sans Mono", "Courier New"], "TkFixedFont")
        self.font_ui = ui
        self.font_digits = digits
        self._digit_size = 64
        global UI_FONT
        UI_FONT = ui
        try:
            tkfont.nametofont("TkDefaultFont").configure(family=ui, size=10)
        except tk.TclError:
            pass

    def _build_window(self) -> None:
        self.title(APP_NAME)
        self.configure(bg=BG)
        self.geometry("440x360")
        self.minsize(390, 330)
        dark_titlebar(self)
        self.attributes("-topmost", bool(self.cfg["always_on_top"]))
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.bind("<space>", lambda e: self.toggle())
        self.bind("<Return>", lambda e: self.toggle())
        self.bind("<r>", lambda e: self.reset())
        self.bind("<s>", lambda e: self.skip())
        self.bind("<Key-1>", lambda e: self.choose_break("short"))
        self.bind("<Key-2>", lambda e: self.choose_break("long"))
        self.bind("<Key-3>", lambda e: self.choose_focus())
        self.bind("<Escape>", lambda e: self.iconify())
        self.bind("<Configure>", self._on_resize)

    def _build_ui(self) -> None:
        root = tk.Frame(self, bg=BG)
        root.pack(fill="both", expand=True, padx=26, pady=18)

        # zuerst packen, damit die Fusszeile ihren Platz behaelt
        bottom = tk.Frame(root, bg=BG)
        bottom.pack(side="bottom", fill="x")
        self.stats_label = tk.Label(bottom, text="", bg=BG, fg=FG_DIM,
                                    font=(self.font_ui, 8))
        self.stats_label.pack(side="left")
        FlatButton(bottom, "Einstellungen", self.open_settings, small=True).pack(side="right")

        top = tk.Frame(root, bg=BG)
        top.pack(fill="x")
        self.mode_label = tk.Label(top, text="FOKUS", bg=BG, fg=ACCENT,
                                   font=(self.font_ui, 9, "bold"))
        self.mode_label.pack(side="left")
        self.plan_label = tk.Label(top, text="", bg=BG, fg=FG_DIM,
                                   font=(self.font_ui, 9), cursor="hand2")
        self.plan_label.pack(side="right")
        self.plan_label.bind("<Button-1>", self.open_plan_menu)

        self.time_label = tk.Label(root, text="25:00", bg=BG, fg=FG,
                                   font=(self.font_digits, 64))
        self.time_label.pack(pady=(14, 6))

        self.bar = tk.Canvas(root, height=3, bg=LINE, highlightthickness=0, bd=0)
        self.bar.pack(fill="x")
        self.bar_fill = self.bar.create_rectangle(0, 0, 0, 3, fill=ACCENT, width=0)

        self.hint_label = tk.Label(root, text="", bg=BG, fg=FG_DIM,
                                   font=(self.font_ui, 9))
        self.hint_label.pack(pady=(10, 0))

        self.actions = tk.Frame(root, bg=BG)
        self.actions.pack(pady=(12, 0))

    # ------------------------------------------------------------- Plan --

    def _load_active_plan(self) -> None:
        name = self.cfg.get("active_plan")
        if not name:
            return
        for plan in self.cfg["plans"]:
            if plan["name"] == name:
                try:
                    self.plan_steps = parse_plan(plan["spec"])
                    self.plan_name = plan["name"]
                    self.plan_index = 0
                except ValueError:
                    self.cfg["active_plan"] = None
                return
        self.cfg["active_plan"] = None

    def set_plan(self, name: str | None) -> None:
        self.plan_steps = []
        self.plan_index = 0
        self.plan_name = None
        self.cfg["active_plan"] = None
        if name:
            for plan in self.cfg["plans"]:
                if plan["name"] == name:
                    try:
                        self.plan_steps = parse_plan(plan["spec"])
                    except ValueError:
                        return
                    self.plan_name = name
                    self.cfg["active_plan"] = name
                    break
        save_config(self.cfg)
        self.stop_timer()
        self.focus_done = 0
        step = self.current_step()
        if step:
            self.apply_mode("focus", step["minutes"])
        else:
            self.apply_mode("focus", self.cfg["focus"])
        self.render()

    def current_step(self) -> dict | None:
        if self.plan_steps and self.plan_index < len(self.plan_steps):
            return self.plan_steps[self.plan_index]
        return None

    def open_plan_menu(self, _event=None) -> None:
        menu = tk.Menu(self, tearoff=0, bg=BG_SOFT, fg=FG,
                       activebackground=BG_HOVER, activeforeground=FG,
                       bd=0, font=(self.font_ui, 9))
        menu.add_command(label="Kein Plan", command=lambda: self.set_plan(None))
        menu.add_separator()
        for plan in self.cfg["plans"]:
            label = f"{plan['name']}  ({plan['spec']})"
            menu.add_command(label=label,
                             command=lambda n=plan["name"]: self.set_plan(n))
        menu.add_separator()
        menu.add_command(label="Pläne bearbeiten…", command=self.open_settings)
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    # ------------------------------------------------------------ Timer --

    def _current_default_minutes(self) -> float:
        step = self.current_step()
        if step and step["kind"] == "focus":
            return step["minutes"]
        return self.cfg["focus"]

    def apply_mode(self, mode: str, minutes: float) -> None:
        self.mode = mode
        self.duration = max(1.0, minutes * 60.0)
        self.remaining = self.duration
        self.state_name = "idle"

    def start(self) -> None:
        if self.state_name == "running":
            return
        if self.remaining <= 0:
            self.remaining = self.duration
        self.deadline = time.monotonic() + self.remaining
        self.state_name = "running"
        self.render()

    def pause(self) -> None:
        if self.state_name != "running":
            return
        self.remaining = max(0.0, self.deadline - time.monotonic())
        self.state_name = "paused"
        self.render()

    def toggle(self) -> None:
        if self.state_name == "running":
            self.pause()
        elif self.state_name in ("idle", "paused"):
            self.start()

    def stop_timer(self) -> None:
        self.state_name = "idle"
        self.remaining = self.duration

    def reset(self) -> None:
        self.state_name = "idle"
        self.remaining = self.duration
        self.render()

    def skip(self) -> None:
        """Laufende Einheit überspringen - wie reguläres Ende, ohne Statistik."""
        if self.state_name == "choice":
            return
        self.state_name = "idle"
        self.remaining = 0
        self.finish(counted=False)

    def tick(self) -> None:
        if self.state_name == "running":
            self.remaining = self.deadline - time.monotonic()
            if self.remaining <= 0:
                self.remaining = 0
                self.finish()
            self.render()
        self._tick_id = self.after(120, self.tick)

    # ------------------------------------------------------------- Ende --

    def finish(self, counted: bool = True) -> None:
        was_focus = self.mode == "focus"
        if counted:
            if self.cfg["sound"]:
                self.sound.play("focus" if was_focus else "break")
            flash_taskbar(self)
            self.lift()
            if was_focus:
                self._count_session(self.duration / 60.0)

        if was_focus:
            self.focus_done += 1
            if self.plan_steps and self.plan_index < len(self.plan_steps):
                self.plan_index += 1  # weiter zum Pausen-Schritt
            self.state_name = "choice"
            self.render()
            return

        # Pause ist vorbei -> nächste Fokus-Einheit
        if self.plan_steps and self.plan_index < len(self.plan_steps):
            self.plan_index += 1
        step = self.current_step()
        if self.plan_steps and step is None:
            self.plan_finished()
            return
        minutes = step["minutes"] if step else self.cfg["focus"]
        self.apply_mode("focus", minutes)
        if self.cfg["auto_start_after_break"]:
            self.start()
        else:
            self.render()

    def plan_finished(self) -> None:
        self.plan_index = 0
        self.focus_done = 0
        self.apply_mode("focus", self._current_default_minutes())
        self.hint_label.configure(text="Plan abgeschlossen · gut gemacht")
        self.render(keep_hint=True)

    def _count_session(self, minutes: float) -> None:
        self._roll_stats()
        self.cfg["stats"]["sessions"] += 1
        self.cfg["stats"]["minutes"] = round(
            self.cfg["stats"]["minutes"] + minutes, 1)
        save_config(self.cfg)

    def _roll_stats(self) -> None:
        today = date.today().isoformat()
        stats = self.cfg.setdefault("stats", {"date": "", "sessions": 0, "minutes": 0.0})
        if stats.get("date") != today:
            stats.update({"date": today, "sessions": 0, "minutes": 0.0})

    # ---------------------------------------------------------- Auswahl --

    def choose_break(self, kind: str) -> None:
        """Pause nach eigener Vorgabe (kurz/lang) starten."""
        minutes = self.cfg["short"] if kind == "short" else self.cfg["long"]
        self.apply_mode(kind, minutes)
        self.start()

    def choose_plan_break(self) -> None:
        step = self.current_step()
        if not step or step["kind"] != "break":
            self.choose_break("short")
            return
        kind = "long" if step["minutes"] >= self.cfg["long"] else "short"
        self.apply_mode(kind, step["minutes"])
        self.start()

    def choose_focus(self) -> None:
        """Pause überspringen und direkt weiterarbeiten."""
        if self.plan_steps:
            if self.plan_index < len(self.plan_steps) and \
                    self.plan_steps[self.plan_index]["kind"] == "break":
                self.plan_index += 1
            step = self.current_step()
            if step is None:
                self.plan_finished()
                return
            self.apply_mode("focus", step["minutes"])
        else:
            self.apply_mode("focus", self.cfg["focus"])
        self.start()

    def choose_stop(self) -> None:
        if self.plan_steps and self.plan_index < len(self.plan_steps) and \
                self.plan_steps[self.plan_index]["kind"] == "break":
            self.plan_index += 1
        step = self.current_step()
        self.apply_mode("focus", step["minutes"] if step else self.cfg["focus"])
        self.render()

    # ----------------------------------------------------------- Render --

    def render(self, keep_hint: bool = False) -> None:
        color = MODE_COLORS[self.mode]

        if self.state_name == "choice":
            self.mode_label.configure(text="SESSION BEENDET", fg=FG)
            text = "00:00"
            self.time_label.configure(text=text, fg=FG_MID)
        else:
            self.mode_label.configure(text=MODE_LABELS[self.mode], fg=color)
            text = fmt_clock(self.remaining)
            self.time_label.configure(
                text=text,
                fg=FG if self.state_name == "running" else FG_MID,
            )
        self._fit_time_font(text)

        progress = 0.0 if self.duration <= 0 else 1 - max(0.0, self.remaining) / self.duration
        if self.state_name == "choice":
            progress = 1.0
        width = max(1, self.bar.winfo_width())
        self.bar.coords(self.bar_fill, 0, 0, width * min(1.0, progress), 3)
        self.bar.itemconfigure(self.bar_fill, fill=color)

        # Plan-Anzeige
        if self.plan_steps:
            step = self.current_step()
            rounds = self.plan_steps[0]["rounds"]
            current = step["round"] if step else rounds
            self.plan_label.configure(
                text=f"{self.plan_name} · Runde {current}/{rounds}  ▾", fg=FG_MID)
        else:
            self.plan_label.configure(text="Kein Plan  ▾", fg=FG_DIM)

        if not keep_hint:
            self.hint_label.configure(text=self._hint_text())

        stats = self.cfg["stats"]
        self.stats_label.configure(
            text=f"Heute: {stats['sessions']} Sessions · {fmt_duration(stats['minutes'])}")

        self._render_actions()

    def _hint_text(self) -> str:
        if self.state_name == "choice":
            return "Wie geht es weiter?"
        if self.state_name == "running":
            return "Leertaste: Pause · S: überspringen"
        if self.state_name == "paused":
            return "Pausiert · Leertaste: weiter"
        return f"{fmt_minutes(self.duration / 60)} min · Leertaste: Start"

    def _render_actions(self) -> None:
        step = self.current_step()
        key = (self.state_name, self.mode, self.plan_name,
               step["kind"] if step else None, step["minutes"] if step else None,
               self.cfg["short"], self.cfg["long"], self.focus_done)
        if key == getattr(self, "_actions_key", None):
            return
        self._actions_key = key

        for child in self.actions.winfo_children():
            child.destroy()

        if self.state_name == "choice":
            row_top = tk.Frame(self.actions, bg=BG)
            row_top.pack()
            row_bottom = tk.Frame(self.actions, bg=BG)
            row_bottom.pack(pady=(2, 0))

            short_txt = f"Kurz · {fmt_minutes(self.cfg['short'])}"
            long_txt = f"Lang · {fmt_minutes(self.cfg['long'])}"

            if step and step["kind"] == "break":
                # Plan schlägt die Pause vor - kurz/lang bleiben trotzdem wählbar
                FlatButton(row_top,
                           f"Plan-Pause · {fmt_minutes(step['minutes'])} min",
                           self.choose_plan_break, primary=True,
                           accent=ACCENT_BREAK).pack(side="left", padx=(0, 4))
                FlatButton(row_top, short_txt, lambda: self.choose_break("short"),
                           small=True).pack(side="left")
                FlatButton(row_top, long_txt, lambda: self.choose_break("long"),
                           small=True).pack(side="left")
            else:
                long_due = (self.cfg["long_every"] > 0 and
                            self.focus_done % self.cfg["long_every"] == 0)
                if long_due:
                    FlatButton(row_top,
                               f"Lange Pause · {fmt_minutes(self.cfg['long'])} min",
                               lambda: self.choose_break("long"), primary=True,
                               accent=ACCENT_BREAK).pack(side="left", padx=(0, 4))
                    FlatButton(row_top, short_txt, lambda: self.choose_break("short"),
                               small=True).pack(side="left")
                else:
                    FlatButton(row_top,
                               f"Kurze Pause · {fmt_minutes(self.cfg['short'])} min",
                               lambda: self.choose_break("short"), primary=True,
                               accent=ACCENT_BREAK).pack(side="left", padx=(0, 4))
                    FlatButton(row_top, long_txt, lambda: self.choose_break("long"),
                               small=True).pack(side="left")

            FlatButton(row_bottom, "Weiter arbeiten", self.choose_focus,
                       small=True).pack(side="left")
            FlatButton(row_bottom, "Stopp", self.choose_stop,
                       small=True).pack(side="left")
            return

        label = "Pause" if self.state_name == "running" else (
            "Weiter" if self.state_name == "paused" else "Start")
        FlatButton(self.actions, label, self.toggle, primary=True,
                   accent=MODE_COLORS[self.mode]).pack(side="left", padx=(0, 6))
        FlatButton(self.actions, "Reset", self.reset, small=True).pack(side="left", padx=(0, 6))
        FlatButton(self.actions, "Skip", self.skip, small=True).pack(side="left")

    def _fit_time_font(self, text: str) -> None:
        """Ziffern so groß wie möglich - aber immer vollständig sichtbar."""
        inner = max(140, self.winfo_width() - 52)
        size = int(inner * 0.78 / (0.8 * max(4, len(text))))
        size = min(size, int(max(120, self.winfo_height()) * 0.26))
        size = max(24, min(76, size))
        if size != self._digit_size:
            self._digit_size = size
            self.time_label.configure(font=(self.font_digits, size))

    def _on_resize(self, event) -> None:
        # kann feuern, bevor der Aufbau fertig ist
        if event.widget is not self or not hasattr(self, "time_label"):
            return
        self.render()

    # -------------------------------------------------------- Settings ---

    def open_settings(self) -> None:
        SettingsWindow(self)

    def on_close(self) -> None:
        save_config(self.cfg)
        self.destroy()

    def destroy(self) -> None:
        if getattr(self, "_tick_id", None):
            try:
                self.after_cancel(self._tick_id)
            except tk.TclError:
                pass
            self._tick_id = None
        super().destroy()


# ------------------------------------------------------- Einstellungen -----


class SettingsWindow(tk.Toplevel):
    def __init__(self, app: PomodoroApp) -> None:
        super().__init__(app, bg=BG)
        self.app = app
        self.cfg = app.cfg
        self.title("Einstellungen")
        self.configure(bg=BG)
        self.geometry("470x620")
        self.minsize(440, 560)
        self.transient(app)
        dark_titlebar(self)
        self.bind("<Escape>", lambda e: self.destroy())

        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=24, pady=20)

        self._section(wrap, "ZEITEN (MINUTEN)")
        row = tk.Frame(wrap, bg=BG)
        row.pack(fill="x", pady=(2, 12))
        self.e_focus = self._field(row, "Fokus", self.cfg["focus"])
        self.e_short = self._field(row, "Kurze Pause", self.cfg["short"])
        self.e_long = self._field(row, "Lange Pause", self.cfg["long"])

        self._section(wrap, "ABLAUF")
        row2 = tk.Frame(wrap, bg=BG)
        row2.pack(fill="x", pady=(2, 4))
        self.e_every = self._field(row2, "Lange Pause nach x Runden",
                                   self.cfg["long_every"], width=6)

        self.v_auto = self._check(wrap, "Nach der Pause automatisch weiterlaufen",
                                  self.cfg["auto_start_after_break"])
        self.v_sound = self._check(wrap, "Signalton am Ende", self.cfg["sound"])
        self.v_top = self._check(wrap, "Fenster immer im Vordergrund",
                                 self.cfg["always_on_top"])
        FlatButton(wrap, "Ton testen", lambda: self.app.sound.play("focus"),
                   small=True).pack(anchor="w", pady=(2, 10))

        self._section(wrap, "PLÄNE")
        tk.Label(wrap, text="Format:  4x90/20   ·   mehrere Blöcke mit +  ·  4x50/10 + 1x50/30",
                 bg=BG, fg=FG_DIM, font=(app.font_ui, 8), justify="left").pack(anchor="w")

        list_wrap = tk.Frame(wrap, bg=BG_SOFT)
        list_wrap.pack(fill="both", expand=True, pady=(6, 6))
        self.listbox = tk.Listbox(
            list_wrap, bg=BG_SOFT, fg=FG, bd=0, highlightthickness=0,
            selectbackground=BG_HOVER, selectforeground=FG, activestyle="none",
            font=(app.font_ui, 9), height=5,
        )
        self.listbox.pack(fill="both", expand=True, padx=8, pady=6)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        form = tk.Frame(wrap, bg=BG)
        form.pack(fill="x")
        name_box = tk.Frame(form, bg=BG)
        name_box.pack(side="left", padx=(0, 12))
        tk.Label(name_box, text="Name", bg=BG, fg=FG_MID,
                 font=(app.font_ui, 9)).pack(anchor="w")
        self.e_name = self._entry(name_box, width=14)
        self.e_name.pack(anchor="w", ipady=4, pady=(2, 0))

        spec_box = tk.Frame(form, bg=BG)
        spec_box.pack(side="left")
        tk.Label(spec_box, text="Ablauf", bg=BG, fg=FG_MID,
                 font=(app.font_ui, 9)).pack(anchor="w")
        self.e_spec = self._entry(spec_box, width=16)
        self.e_spec.pack(anchor="w", ipady=4, pady=(2, 0))

        form_buttons = tk.Frame(wrap, bg=BG)
        form_buttons.pack(fill="x", pady=(6, 0))
        FlatButton(form_buttons, "Plan speichern", self._save_plan,
                   small=True).pack(side="left")
        FlatButton(form_buttons, "Löschen", self._delete_plan,
                   small=True).pack(side="left")

        self.preview = tk.Label(wrap, text="", bg=BG, fg=FG_DIM,
                                font=(app.font_ui, 8), justify="left", anchor="w")
        self.preview.pack(fill="x", pady=(6, 0))
        self.e_spec.bind("<KeyRelease>", lambda e: self._update_preview())

        self.error = tk.Label(wrap, text="", bg=BG, fg=ACCENT,
                              font=(app.font_ui, 9), justify="left", anchor="w")
        self.error.pack(fill="x", pady=(6, 0))

        actions = tk.Frame(wrap, bg=BG)
        actions.pack(fill="x", side="bottom", pady=(12, 0))
        FlatButton(actions, "Übernehmen", self._apply, primary=True,
                   accent=ACCENT).pack(side="right")
        FlatButton(actions, "Abbrechen", self.destroy, small=True).pack(side="right", padx=(0, 6))

        self._refresh_plans()

    # -- kleine Bausteine

    def _section(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg=FG_DIM,
                 font=(self.app.font_ui, 8, "bold")).pack(anchor="w", pady=(8, 0))

    def _entry(self, parent, width=8, value=""):
        e = tk.Entry(parent, bg=BG_SOFT, fg=FG, insertbackground=FG, bd=0,
                     highlightthickness=1, highlightbackground=LINE,
                     highlightcolor=FG_DIM, width=width,
                     font=(self.app.font_ui, 10), relief="flat")
        e.insert(0, str(value))
        return e

    def _field(self, parent, label, value, width=8):
        box = tk.Frame(parent, bg=BG)
        box.pack(side="left", padx=(0, 14))
        tk.Label(box, text=label, bg=BG, fg=FG_MID,
                 font=(self.app.font_ui, 9)).pack(anchor="w")
        entry = self._entry(box, width=width,
                            value=fmt_minutes(value) if isinstance(value, float) else value)
        entry.pack(anchor="w", ipady=4, pady=(2, 0))
        return entry

    def _check(self, parent, text, value):
        var = tk.BooleanVar(value=bool(value))
        tk.Checkbutton(parent, text=text, variable=var, bg=BG, fg=FG_MID,
                       activebackground=BG, activeforeground=FG,
                       selectcolor=BG_SOFT, bd=0, highlightthickness=0,
                       font=(self.app.font_ui, 9),
                       anchor="w").pack(fill="x", pady=1)
        return var

    # -- Pläne

    def _refresh_plans(self):
        self.listbox.delete(0, "end")
        for plan in self.cfg["plans"]:
            self.listbox.insert("end", f"{plan['name']}   —   {plan['spec']}")

    def _on_select(self, _event=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        plan = self.cfg["plans"][sel[0]]
        self.e_name.delete(0, "end")
        self.e_name.insert(0, plan["name"])
        self.e_spec.delete(0, "end")
        self.e_spec.insert(0, plan["spec"])
        self._update_preview()

    def _update_preview(self):
        try:
            steps = parse_plan(self.e_spec.get())
        except ValueError as exc:
            self.preview.configure(text=str(exc), fg=ACCENT)
            return
        self.preview.configure(text=describe_plan(steps), fg=FG_DIM)

    def _save_plan(self):
        name = self.e_name.get().strip()
        spec = self.e_spec.get().strip()
        if not name:
            self.error.configure(text="Bitte einen Namen für den Plan angeben.")
            return
        try:
            parse_plan(spec)
        except ValueError as exc:
            self.error.configure(text=f"Plan: {exc}")
            return
        for plan in self.cfg["plans"]:
            if plan["name"] == name:
                plan["spec"] = spec
                break
        else:
            self.cfg["plans"].append({"name": name, "spec": spec})
        self.error.configure(text="")
        self._refresh_plans()
        save_config(self.cfg)
        self.app.render()

    def _delete_plan(self):
        name = self.e_name.get().strip()
        self.cfg["plans"] = [p for p in self.cfg["plans"] if p["name"] != name]
        if self.cfg.get("active_plan") == name:
            self.app.set_plan(None)
        self._refresh_plans()
        save_config(self.cfg)

    # -- übernehmen

    def _apply(self):
        try:
            focus = parse_minutes(self.e_focus.get())
            short = parse_minutes(self.e_short.get())
            long = parse_minutes(self.e_long.get())
        except ValueError:
            self.error.configure(text="Zeiten bitte als Minuten angeben (z. B. 90 oder 1:30).")
            return
        try:
            every = int(float(self.e_every.get().replace(",", ".")))
        except ValueError:
            every = 0

        self.cfg.update({
            "focus": focus, "short": short, "long": long,
            "long_every": max(0, every),
            "auto_start_after_break": bool(self.v_auto.get()),
            "sound": bool(self.v_sound.get()),
            "always_on_top": bool(self.v_top.get()),
        })
        save_config(self.cfg)

        self.app.attributes("-topmost", self.cfg["always_on_top"])
        if self.app.state_name == "idle" and self.app.mode == "focus" \
                and not self.app.plan_steps:
            self.app.apply_mode("focus", focus)
        self.app.render()
        self.destroy()


def main() -> None:
    try:
        app = PomodoroApp()
        app.mainloop()
    except SystemExit:
        raise
    except BaseException:
        report_error(
            "Der Timer konnte nicht gestartet werden.\n\n"
            "Bitte schick mir den Text aus der Logdatei, dann finde ich die "
            "Ursache.",
            traceback.format_exc() + f"\nPython: {sys.executable}\n"
                                     f"Version: {sys.version}\n"
                                     f"Plattform: {sys.platform}",
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
