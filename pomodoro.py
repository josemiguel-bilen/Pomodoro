"""Minimaler Pomodoro-Timer für Windows (Python-Standardbibliothek + Tkinter).

Lernansicht: kleines schwarzes Widget ohne Rahmen, nur "Start".
Rechtsklick -> Bearbeiten (Zeiten, Aufgaben, Look, Statistik).

Start:           pythonw pomodoro.py
Einrichten:      pythonw pomodoro.py --install    (Verknüpfungen + Autostart)
Entfernen:       pythonw pomodoro.py --uninstall
"""

from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import sys
import time
import traceback
import zlib
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

APP_NAME = "Pomodoro"
VERSION = "2.0"
IS_WINDOWS = sys.platform.startswith("win")
SCRIPT_PATH = Path(__file__).resolve()

if os.environ.get("POMODORO_DATA"):  # zum Testen
    DATA_DIR = Path(os.environ["POMODORO_DATA"])
elif IS_WINDOWS:
    DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "MinimalPomodoro"
else:
    DATA_DIR = Path.home() / ".config" / "minimal-pomodoro"
CONFIG_PATH = DATA_DIR / "config.json"
SESSIONS_PATH = DATA_DIR / "sessions.jsonl"
CURRENT_PATH = DATA_DIR / "current.json"   # laufender Pomodoro (Absturzschutz)
ICON_PATH = DATA_DIR / "pomodoro.ico"
LOG_PATH = DATA_DIR / "fehler.log"
EVENT_LOG = DATA_DIR / "ereignisse.log"


def log_event(text: str) -> None:
    """Kurzes Protokoll für Fehler/Hänger/Wiederherstellung (max. ~200 KB)."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if EVENT_LOG.exists() and EVENT_LOG.stat().st_size > 200_000:
            EVENT_LOG.replace(EVENT_LOG.with_suffix(".old.log"))
        with EVENT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')}  {text}\n")
    except OSError:
        pass


def clock() -> float:
    """Sekunden-Uhr, die im Standby/Ruhezustand NICHT weiterläuft.

    So zählt ein zugeklappter Laptop nicht als Lernzeit, ein hängendes oder
    neu gestartetes Programm aber schon. Systemweit gültig bis zum Neustart.
    """
    if IS_WINDOWS:
        try:
            import ctypes

            v = ctypes.c_ulonglong()
            if ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(v)):
                return v.value / 1e7
        except Exception:
            pass
    return time.monotonic()


def report_error(message: str, detail: str = "") -> None:
    """Fehler sichtbar machen - pythonw hat keine Konsole."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text(f"{message}\n\n{detail}\n", encoding="utf-8")
        message += f"\n\nDetails: {LOG_PATH}"
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
except ImportError as exc:
    report_error(
        "Zu dieser Python-Installation fehlt Tkinter.\n\n"
        "Einstellungen → Apps → Python → Ändern → Modify → \"tcl/tk and IDLE\" anhaken.",
        f"{exc}\n{sys.executable}",
    )
    raise SystemExit(1)

# ---------------------------------------------------------------- Themes ----
# (Name, Hintergrund, Schrift, passende Button-Farben)

THEMES = [
    ("Schwarz", "#000000", "#f5f5f5",
     ["#f5f5f5", "#ff6b5e", "#f2d8a7", "#9ee6c4", "#9cc8ff"]),
    ("Graphit", "#1c1c1e", "#f2f2f7",
     ["#f2f2f7", "#ffb340", "#8ec5ff", "#ff8a7a", "#b5e48c"]),
    ("Mitternacht", "#0b1220", "#e9eef7",
     ["#e8d5a9", "#bfe3ff", "#ffb38a", "#f5f5f5"]),
    ("Wald", "#0f1a14", "#e9f0e6",
     ["#b7d9b0", "#efe6cf", "#e3b25a", "#f5f5f5"]),
    ("Aubergine", "#1b1020", "#f3eaf2",
     ["#f3c4d3", "#cdb7ff", "#f0c674", "#f5f5f5"]),
    ("Espresso", "#1a1410", "#f3ebe2",
     ["#e8b07a", "#f1e4cf", "#d9785a", "#f5f5f5"]),
    ("Petrol", "#061a1f", "#e6f3f2",
     ["#7fd6d0", "#ff9b85", "#eadbb8", "#f5f5f5"]),
    ("Papier", "#f2eee6", "#1a1a1a",
     ["#1a1a1a", "#c8553d", "#2f5d50", "#3b4a6b"]),
]
THEME_BY_NAME = {t[0]: t for t in THEMES}
SIZES = [("S", 0.85), ("M", 1.0), ("L", 1.25)]

DEFAULT_CONFIG = {
    "focus": 25.0,
    "short": 5.0,
    "long": 15.0,
    "long_every": 4,
    "sound": True,
    "topmost": False,
    "theme": "Schwarz",
    "button": "#f5f5f5",
    "scale": 1.0,
    "tasks": [
        {"id": "study", "name": "study", "goal": 0.0},
        {"id": "work", "name": "work", "goal": 0.0},
    ],
    "active_task": "study",
    "pos": None,
    "edit_tab": "stats",
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    if isinstance(stored, dict):
        if "always_on_top" in stored and "topmost" not in stored:  # v1
            stored["topmost"] = stored["always_on_top"]
        cfg.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG})
    if cfg["theme"] not in THEME_BY_NAME:
        cfg["theme"] = "Schwarz"
    if not cfg["tasks"]:
        cfg["tasks"] = json.loads(json.dumps(DEFAULT_CONFIG["tasks"]))
    if cfg["active_task"] not in {t["id"] for t in cfg["tasks"]}:
        cfg["active_task"] = cfg["tasks"][0]["id"]
    return cfg


def save_config(cfg: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_PATH)
    except OSError:
        pass


# ---------------------------------------------------------------- Helfer ----


def hex2rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def rgb2hex(r: float, g: float, b: float) -> str:
    return "#%02x%02x%02x" % (round(r), round(g), round(b))


def blend(a: str, b: str, t: float) -> str:
    """t=0 -> a, t=1 -> b"""
    ra, ga, ba = hex2rgb(a)
    rb, gb, bb = hex2rgb(b)
    return rgb2hex(ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t)


def parse_minutes(text: str) -> float:
    text = str(text).strip().replace(",", ".")
    if ":" in text:
        mm, _, ss = text.partition(":")
        minutes = float(mm or 0) + float(ss or 0) / 60.0
    else:
        minutes = float(text)
    if not 0 < minutes <= 24 * 60:
        raise ValueError
    return round(minutes, 4)


def fmt_min(minutes: float) -> str:
    """Minuten kurz: 5 -> '5', 2.5 -> '2:30'."""
    if abs(minutes - round(minutes)) < 1e-6:
        return str(int(round(minutes)))
    s = int(round(minutes * 60))
    return f"{s // 60}:{s % 60:02d}"


def fmt_hm(minutes: float) -> str:
    m = int(minutes + 1e-6)
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h {m % 60:02d}m"


def fmt_clock(seconds: float) -> str:
    s = max(0, int(math.ceil(seconds - 1e-6)))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Palette:
    def __init__(self, cfg: dict) -> None:
        _, bg, fg, buttons = THEME_BY_NAME[cfg["theme"]]
        self.bg = bg
        self.fg = fg
        self.accent = cfg["button"] if cfg["button"] else buttons[0]
        self.dim = blend(fg, bg, 0.45)
        self.faint = blend(fg, bg, 0.72)
        self.line = blend(bg, fg, 0.14)
        self.soft = blend(bg, fg, 0.06)
        self.tint = blend(bg, self.accent, 0.16)
        self.hover = blend(bg, fg, 0.10)


# ------------------------------------------------------------- Sessions -----


def append_session(entry: dict) -> bool:
    for attempt in range(3):  # OneDrive/Virenscanner sperren Dateien manchmal kurz
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with SESSIONS_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            return True
        except OSError as exc:
            if attempt == 2:
                log_event(f"Session konnte nicht gespeichert werden: {exc} {entry}")
            time.sleep(0.2)
    return False


def write_current(state: dict | None) -> None:
    """Zustand des laufenden Pomodoros atomar sichern (None = löschen)."""
    try:
        if state is None:
            CURRENT_PATH.unlink(missing_ok=True)
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CURRENT_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(state))
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(CURRENT_PATH)
    except OSError as exc:
        log_event(f"Zwischenstand nicht gespeichert: {exc}")


def read_current() -> dict | None:
    try:
        state = json.loads(CURRENT_PATH.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else None
    except (OSError, ValueError):
        return None


def load_sessions() -> list[dict]:
    out: list[dict] = []
    try:
        lines = SESSIONS_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for n, line in enumerate(lines):
        try:
            s = json.loads(line)
            s["_start"] = datetime.fromisoformat(s["start"])
            s["min"] = float(s.get("min", 0))
            s["_line"] = n
            out.append(s)
        except (ValueError, KeyError, TypeError):
            continue
    return out


def rewrite_sessions(edit) -> bool:
    """Datei sicher umschreiben: edit(list[str]) -> list[str]. Atomar, mit fsync."""
    try:
        try:
            lines = SESSIONS_PATH.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        new = edit(lines)
        tmp = SESSIONS_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write("".join(line + "\n" for line in new))
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(SESSIONS_PATH)
        return True
    except OSError as exc:
        log_event(f"Einträge konnten nicht geändert werden: {exc}")
        return False


def parse_day(text: str, today: date) -> date:
    """'heute', 'gestern', '24.11.', '24.11.2026', '2026-11-24' -> Datum (nicht in der Zukunft)."""
    t = text.strip().lower()
    if t in ("", "heute", "today"):
        d = today
    elif t in ("gestern", "yesterday"):
        d = today - timedelta(days=1)
    elif t in ("vorgestern",):
        d = today - timedelta(days=2)
    elif "-" in t:
        d = date.fromisoformat(t)
    else:
        parts = [p for p in t.split(".") if p]
        if len(parts) not in (2, 3):
            raise ValueError
        day, month = int(parts[0]), int(parts[1])
        year = int(parts[2]) if len(parts) == 3 else today.year
        if year < 100:
            year += 2000
        d = date(year, month, day)
        if len(parts) == 2 and d > today:   # '28.12.' im Januar = letztes Jahr
            d = date(year - 1, month, day)
    if d > today:
        raise ValueError
    return d


def parse_clock_time(text: str) -> tuple[int, int]:
    t = text.strip().replace(".", ":")
    if not t:
        return 12, 0
    hh, _, mm = t.partition(":")
    h, m = int(hh), int(mm or 0)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError
    return h, m


def parse_signed_duration(text: str) -> float:
    """'90', '1:30', '1,5h', '-20' -> Minuten (negativ = Zeit abziehen)."""
    t = text.strip().lower().replace(" ", "").replace(",", ".")
    sign = -1.0 if t.startswith("-") else 1.0
    t = t.lstrip("+-")
    if t.endswith("min"):
        t = t[:-3]
    if t.endswith("h"):
        minutes = float(t[:-1]) * 60
    else:
        minutes = parse_minutes(t)
    if not 0 < minutes <= 24 * 60:
        raise ValueError
    return round(sign * minutes, 2)


def daily_totals(sessions: list[dict], task: str | None = None) -> dict:
    days: dict = defaultdict(float)
    for s in sessions:
        if task is None or s.get("task") == task:
            days[s["_start"].date()] += s["min"]
    # abgezogene Zeit (negative Einträge) kann einen Tag nie unter 0 drücken
    return {d: m for d, m in days.items() if m > 1e-9}


def streaks(days: dict, today: date, threshold: float = 1.0) -> tuple[int, int]:
    active = {d for d, m in days.items() if m >= threshold}
    current = 0
    d = today if today in active else today - timedelta(days=1)
    while d in active:
        current += 1
        d -= timedelta(days=1)
    longest = run = 0
    prev = None
    for d in sorted(active):
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        longest = max(longest, run)
        prev = d
    return current, longest


def hour_histogram(sessions: list[dict], task: str | None, since: datetime) -> list[float]:
    hist = [0.0] * 24
    for s in sessions:
        if (task is not None and s.get("task") != task) or s["_start"] < since:
            continue
        t, left = s["_start"], s["min"]
        if left < 0:   # abgezogene Zeit
            hist[t.hour] = max(0.0, hist[t.hour] + left)
            continue
        while left > 1e-9:
            nxt = t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            chunk = min(left, (nxt - t).total_seconds() / 60.0)
            hist[t.hour] += chunk
            left -= chunk
            t = nxt
    return hist


def window_sum(days: dict, today: date, n: int) -> tuple[float, int]:
    """Summe der letzten n Tage und wie viele Tage davon schon 'existieren'."""
    first = min(days) if days else today
    span = max(1, min(n, (today - first).days + 1))
    total = sum(days.get(today - timedelta(days=i), 0.0) for i in range(span))
    return total, span


# ---------------------------------------------------------- Grafik (AA) -----


def _sdf_box(px, py, cx, cy, hw, hh, r):
    qx = abs(px - cx) - (hw - r)
    qy = abs(py - cy) - (hh - r)
    out = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return out + min(max(qx, qy), 0.0) - r


def shape_photo(w: int, h: int, bg: str, layers) -> "tk.PhotoImage":
    """Kantengeglättetes Bild. layers: [(farbe, sdf(px,py))] von unten nach oben."""
    img = tk.PhotoImage(width=w, height=h)
    rgb_layers = [(hex2rgb(c), f) for c, f in layers]
    bgr = hex2rgb(bg)
    rows = []
    for y in range(h):
        py = y + 0.5
        row = []
        for x in range(w):
            px = x + 0.5
            r, g, b = bgr
            for (cr, cg, cb), f in rgb_layers:
                a = 0.5 - f(px, py)
                if a <= 0:
                    continue
                if a >= 1:
                    r, g, b = cr, cg, cb
                else:
                    r, g, b = r + (cr - r) * a, g + (cg - g) * a, b + (cb - b) * a
            row.append(rgb2hex(r, g, b))
        rows.append("{" + " ".join(row) + "}")
    img.put(" ".join(rows))
    return img


def pill_photo(w: int, h: int, fill: str, bg: str) -> "tk.PhotoImage":
    return shape_photo(w, h, bg, [
        (fill, lambda px, py: _sdf_box(px, py, w / 2, h / 2, w / 2 - 0.5, h / 2 - 0.5, h / 2 - 0.5))])


def disc_photo(d: int, fill: str, bg: str, ring: str | None) -> "tk.PhotoImage":
    c = d / 2
    layers = []
    if ring:
        layers.append((ring, lambda px, py: abs(math.hypot(px - c, py - c) - (c - 1.5)) - 1.0))
    inner = c - (5 if ring else 2)
    layers.append((fill, lambda px, py: math.hypot(px - c, py - c) - inner))
    return shape_photo(d, d, bg, layers)


# ------------------------------------------------------------------ Icon ----


def _png(w: int, h: int, rows: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _icon_png(size: int) -> bytes:
    s = size
    rows = []
    for y in range(s):
        row = bytearray()
        for x in range(s):
            px, py = x + 0.5, y + 0.5
            a_bg = min(1.0, max(0.0, 0.5 - _sdf_box(px, py, s / 2, s / 2, s / 2, s / 2, s * 0.22)))
            a_p = min(1.0, max(0.0, 0.5 - _sdf_box(px, py, s / 2, s / 2, s * 0.31, s * 0.12, s * 0.12)))
            v = round(13 + (245 - 13) * a_p)
            row += bytes((v, v, v, round(255 * a_bg)))
        rows.append(bytes(row))
    return _png(s, s, rows)


def ensure_icon() -> Path | None:
    if ICON_PATH.exists():
        return ICON_PATH
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        sizes = [16, 24, 32, 48, 256]
        pngs = [_icon_png(s) for s in sizes]
        out = struct.pack("<HHH", 0, 1, len(sizes))
        offset = 6 + 16 * len(sizes)
        for s, p in zip(sizes, pngs):
            out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(p), offset)
            offset += len(p)
        ICON_PATH.write_bytes(out + b"".join(pngs))
        return ICON_PATH
    except OSError:
        return None


# ---------------------------------------------------------------- Sound -----


def build_chime(freqs, seconds, rate=22050) -> bytes:
    frames = int(rate * seconds)
    samples = bytearray()
    for i in range(frames):
        t = i / rate
        v = 0.0
        for n, f in enumerate(freqs):
            st = n * 0.12
            if t >= st:
                v += math.sin(2 * math.pi * f * (t - st)) * math.exp(-3.0 * (t - st))
        v = v / len(freqs) * min(1.0, t * 200)
        samples += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 24000))
    data = bytes(samples)
    head = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
    head += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    return head + b"data" + struct.pack("<I", len(data)) + data


class Sound:
    def __init__(self) -> None:
        self._cache: dict[str, bytes] = {}

    def play(self, kind: str) -> None:
        if not IS_WINDOWS:
            return
        if kind not in self._cache:
            self._cache[kind] = (build_chime((880.0, 1174.7, 1567.98), 1.8) if kind == "focus"
                                 else build_chime((523.25, 659.25, 783.99), 1.5))
        try:
            import winsound

            winsound.PlaySound(self._cache[kind], winsound.SND_MEMORY | winsound.SND_ASYNC)
        except Exception:
            pass


# ---------------------------------------------------------- Windows-API -----


def _user32():
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    u.GetParent.argtypes = [wintypes.HWND]
    u.GetParent.restype = wintypes.HWND
    u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.FlashWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    u.FindWindowW.restype = wintypes.HWND
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                      wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                      ctypes.POINTER(ctypes.c_size_t)]
    u.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    return u


def hwnd_of(win: tk.Misc):
    from ctypes import wintypes

    return _user32().GetParent(wintypes.HWND(win.winfo_id()))


def dwm_attr(hwnd, attr: int, value: int) -> None:
    try:
        import ctypes

        v = ctypes.c_int(value)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, ctypes.c_int(attr), ctypes.byref(v),
                                                   ctypes.sizeof(v))
    except Exception:
        pass


def make_widget_window(win: tk.Tk) -> None:
    """Rahmenloses Fenster, das trotzdem in der Taskleiste erscheint."""
    if not IS_WINDOWS:
        return
    try:
        u = _user32()
        hwnd = hwnd_of(win)
        ex = u.GetWindowLongPtrW(hwnd, -20)
        u.SetWindowLongPtrW(hwnd, -20, (ex & ~0x00000080) | 0x00040000)  # APPWINDOW
        st = u.GetWindowLongPtrW(hwnd, -16)
        u.SetWindowLongPtrW(hwnd, -16, st | 0x00020000 | 0x00080000)     # MINIMIZE, SYSMENU
        dwm_attr(hwnd, 33, 2)  # Windows 11: runde Ecken
        win.withdraw()

        def show():
            win.deiconify()
            win.lift()

        win.after(10, show)
    except Exception:
        pass


def dark_titlebar(win: tk.Misc, dark: bool) -> None:
    if not IS_WINDOWS:
        return
    win.update_idletasks()
    try:
        hwnd = hwnd_of(win)
        for attr in (20, 19):
            dwm_attr(hwnd, attr, 1 if dark else 0)
    except Exception:
        pass


MUTEX_NAME = "Local\\MinimalPomodoroTimer" + (
    "-test" if os.environ.get("POMODORO_DATA") else "")


def single_instance():
    """Mutex-Handle oder None, wenn schon ein Timer läuft (der wird nach vorn geholt)."""
    if not IS_WINDOWS:
        return True
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        # Nach einem Neustart durch den Wächter: warten, bis der alte Prozess weg ist
        wait_until = time.time() + (20 if "--restart" in sys.argv else 0)
        while True:
            handle = k32.CreateMutexW(None, False, MUTEX_NAME)
            if ctypes.get_last_error() != 183 or time.time() >= wait_until:
                break
            k32.CloseHandle(handle)
            time.sleep(0.5)
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            u = _user32()
            hwnd = u.FindWindowW("TkTopLevel", APP_NAME)
            if hwnd:
                u.ShowWindow(hwnd, 9)
                u.SetForegroundWindow(hwnd)
            return None
        return handle
    except Exception:
        return True


# ------------------------------------------------- Autostart/Verknüpfung ----

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def pythonw_path() -> Path:
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return cand if cand.exists() else exe


def launch_command() -> str:
    return f'"{pythonw_path()}" "{SCRIPT_PATH}"'


def autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    if not IS_WINDOWS:
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except OSError:
                pass


def _ps(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def create_shortcuts(remove: bool = False) -> bool:
    """Pomodoro.lnk im Startmenü und auf dem Desktop anlegen (oder entfernen)."""
    if not IS_WINDOWS:
        return False
    ico = ensure_icon()
    if remove:
        body = "if (Test-Path $p) { Remove-Item $p }"
    else:
        body = (
            "$s = $ws.CreateShortcut($p); "
            f"$s.TargetPath = {_ps(pythonw_path())}; "
            f"$s.Arguments = {_ps(chr(34) + str(SCRIPT_PATH) + chr(34))}; "
            f"$s.WorkingDirectory = {_ps(SCRIPT_PATH.parent)}; "
            + (f"$s.IconLocation = {_ps(ico)}; " if ico else "")
            + "$s.Description = 'Pomodoro Timer'; $s.Save()"
        )
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "foreach ($d in @([Environment]::GetFolderPath('Programs'), "
        "[Environment]::GetFolderPath('Desktop'))) { "
        "$p = Join-Path $d 'Pomodoro.lnk'; " + body + " }"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            creationflags=0x08000000, timeout=60, capture_output=True,
        )
        return res.returncode == 0
    except Exception:
        return False


# ------------------------------------------------------------- Lern-App -----


class PomodoroApp(tk.Tk):
    BASE_W, BASE_H = 300, 170

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_config()
        self.sound = Sound()
        self.pal = Palette(self.cfg)
        self._img_cache: dict = {}
        self.edit_window: EditWindow | None = None

        # phase: idle | focus | choice | pick | break
        self.phase = "idle"
        self.running = False
        self.break_kind = "short"
        self.duration = self.remaining = self.cfg["focus"] * 60.0
        self.deadline = 0.0
        self.focus_started: datetime | None = None
        self.focus_task: str | None = None
        self.focus_count = 0   # seit der letzten langen Pause
        self.hover = False
        self._tick_id = None
        self._press = None
        self._dragging = False
        self._last_cp = 0.0

        self._today = date.today()
        self._today_min = self._load_today()

        self.dpi = max(1.0, self.winfo_fpixels("1i") / 96.0)
        self._setup_fonts()
        self._setup_window()
        self._recover()
        self.render()
        self.after(1500, self.start_watchdog)

    def destroy(self) -> None:
        try:   # offene after()-Aufrufe abbrechen, sonst meckert Tcl beim Beenden
            for aid in self.tk.splitlist(self.tk.call("after", "info")):
                self.after_cancel(aid)
        except tk.TclError:
            pass
        super().destroy()

    def report_callback_exception(self, exc, val, tb) -> None:
        """Fehler in Klick-/Timer-Code protokollieren statt still zu verschwinden."""
        log_event("Fehler: " + "".join(traceback.format_exception(exc, val, tb)))

    # ------------------------------------------------------------ Setup --

    def _load_today(self) -> float:
        today = date.today()
        return max(0.0, sum(s["min"] for s in load_sessions() if s["_start"].date() == today))

    def refresh_today(self) -> None:
        """Nach manuellen Änderungen die Tageszeit neu einlesen."""
        self._today, self._today_min = date.today(), self._load_today()
        self.render()

    def _setup_fonts(self) -> None:
        fams = set(tkfont.families())

        def pick(cands, fb):
            return next((c for c in cands if c in fams), fb)

        self.f_ui = pick(["Segoe UI", "Inter", "Helvetica Neue", "DejaVu Sans"], "TkDefaultFont")
        self.f_semi = pick(["Segoe UI Semibold", "Segoe UI"], self.f_ui)
        self.f_light = pick(["Segoe UI Light", "Segoe UI Semilight", "Segoe UI"], self.f_ui)
        try:
            tkfont.nametofont("TkDefaultFont").configure(family=self.f_ui, size=9)
            tkfont.nametofont("TkMenuFont").configure(family=self.f_ui, size=9)
        except tk.TclError:
            pass

    @property
    def s(self) -> float:
        return float(self.cfg.get("scale", 1.0)) * self.dpi

    def _setup_window(self) -> None:
        self.title(APP_NAME)
        ico = ensure_icon()
        if ico:
            try:
                self.iconbitmap(default=str(ico))
            except tk.TclError:
                pass
        self.overrideredirect(True)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._apply_geometry(first=True)
        self.attributes("-topmost", bool(self.cfg["topmost"]))

        c = self.canvas
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_motion)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Button-3>", self._context_menu)
        c.bind("<Enter>", lambda e: self._set_hover(True))
        c.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<space>", lambda e: self.act("toggle" if self.phase in ("focus", "break") else "start"))
        self.bind("<Escape>", lambda e: self.act("back") if self.phase == "pick" else None)
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.after(20, lambda: make_widget_window(self))

    def _apply_geometry(self, first: bool = False) -> None:
        w, h = int(self.BASE_W * self.s), int(self.BASE_H * self.s)
        self.W, self.H = w, h
        self.canvas.configure(width=w, height=h, bg=self.pal.bg)
        self.configure(bg=self.pal.bg)
        pos = self.cfg.get("pos")
        if first:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            if pos and isinstance(pos, list) and len(pos) == 2:
                x = min(max(int(pos[0]), -w + 40), self.winfo_vrootwidth() - 40)
                y = min(max(int(pos[1]), 0), max(0, self.winfo_vrootheight() - 40))
            else:
                x, y = sw - w - 40, sh - h - 90
            self.geometry(f"{w}x{h}+{x}+{y}")
        else:
            self.geometry(f"{w}x{h}")

    def apply_look(self) -> None:
        self.pal = Palette(self.cfg)
        self._img_cache.clear()
        self._apply_geometry()
        self.render()

    # -------------------------------------------------------- Interaktion --

    def _set_hover(self, value: bool) -> None:
        if self.hover != value:
            self.hover = value
            self.render()

    def _on_press(self, e) -> None:
        self._press = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y())
        self._dragging = False

    def _on_motion(self, e) -> None:
        if not self._press:
            return
        x0, y0, wx, wy = self._press
        dx, dy = e.x_root - x0, e.y_root - y0
        if not self._dragging and abs(dx) + abs(dy) > 4:
            self._dragging = True
        if self._dragging:
            self.geometry(f"+{wx + dx}+{wy + dy}")

    def _on_release(self, e) -> None:
        if self._dragging:
            self.cfg["pos"] = [self.winfo_x(), self.winfo_y()]
            save_config(self.cfg)
        else:
            for item in reversed(self.canvas.find_overlapping(e.x - 1, e.y - 1, e.x + 1, e.y + 1)):
                for tag in self.canvas.gettags(item):
                    if tag.startswith("act:"):
                        self.act(tag[4:])
                        self._press = None
                        return
        self._press = None
        self._dragging = False

    def _menu(self) -> tk.Menu:
        p = self.pal
        return tk.Menu(self, tearoff=0, bg=p.soft, fg=p.fg, activebackground=p.hover,
                       activeforeground=p.fg, bd=0, relief="flat")

    def _context_menu(self, e) -> None:
        m = self._menu()
        if self.phase == "focus":
            m.add_command(label="Pomodoro stoppen", command=lambda: self.act("stop"))
            m.add_separator()
        m.add_command(label="Bearbeiten…", command=lambda: self.open_edit("timer"))
        m.add_command(label="Statistik…", command=lambda: self.open_edit("stats"))
        m.add_command(label="Zeit nachtragen…", command=lambda: self.open_edit("history"))
        m.add_command(label="Über", command=self.show_about)
        m.add_separator()
        m.add_command(label="Minimieren", command=self.minimize)
        m.add_command(label="Schließen", command=self.quit_app)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _task_menu(self) -> None:
        m = self._menu()
        var = tk.StringVar(value=self.cfg["active_task"])
        for t in self.cfg["tasks"]:
            m.add_radiobutton(label=t["name"], value=t["id"], variable=var,
                              command=lambda i=t["id"]: self.set_task(i))
        m.add_separator()
        m.add_command(label="Aufgaben bearbeiten…", command=lambda: self.open_edit("tasks"))
        try:
            m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            m.grab_release()

    def set_task(self, task_id: str) -> None:
        self.cfg["active_task"] = task_id
        save_config(self.cfg)
        self.render()

    def task_name(self, task_id: str | None = None) -> str:
        tid = task_id or self.cfg["active_task"]
        for t in self.cfg["tasks"]:
            if t["id"] == tid:
                return t["name"]
        return tid or ""

    def minimize(self) -> None:
        if IS_WINDOWS:
            try:
                _user32().ShowWindow(hwnd_of(self), 6)
                return
            except Exception:
                pass

    def show_about(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "Über", f"Pomodoro {VERSION}\n\nLinksklick: Start · Pause\nRechtsklick: Menü\n"
                    f"Ziehen: verschieben · Leertaste: Start/Pause\n\nDaten: {DATA_DIR}", parent=self)

    # ------------------------------------------------------------ Aktionen --

    def act(self, name: str) -> None:
        if name == "task":
            self._task_menu()
        elif name == "start":
            self.start_focus()
        elif name == "break":
            self.phase = "pick"
            self.render()
        elif name in ("short", "long"):
            self.start_break(name)
        elif name == "back":
            self.phase = "choice"
            self.render()
        elif name == "toggle":
            self.pause() if self.running else self.resume()
        elif name == "stop":
            self.stop_focus()
        elif name == "skip":
            self.skip_break()

    def start_focus(self) -> None:
        self.phase = "focus"
        self.duration = self.remaining = float(self.cfg["focus"]) * 60.0
        self.focus_started = datetime.now()
        self.focus_task = self.cfg["active_task"]
        self.resume()

    def start_break(self, kind: str) -> None:
        self.phase = "break"
        self.break_kind = kind
        if kind == "long":
            self.focus_count = 0
        self.duration = self.remaining = float(self.cfg[kind]) * 60.0
        self.resume()

    def resume(self) -> None:
        self.running = True
        self.deadline = clock() + self.remaining
        self.checkpoint()
        self.render()
        self._schedule()

    def pause(self) -> None:
        self.remaining = max(0.0, self.deadline - clock())
        self.running = False
        self._cancel_tick()
        self.checkpoint()
        self.render()

    def stop_focus(self) -> None:
        if self.running:
            self.remaining = max(0.0, self.deadline - clock())
        self._log_focus(done=False)
        self._cancel_tick()
        self.running = False
        self.phase = "idle"
        self.render()

    def skip_break(self) -> None:
        self._cancel_tick()
        self.running = False
        self.phase = "idle"
        write_current(None)
        self.render()

    def current_remaining(self) -> float:
        return max(0.0, self.deadline - clock()) if self.running else self.remaining

    def _log_focus(self, done: bool, start: datetime | None = None,
                   minutes: float | None = None, task: str | None = None,
                   recovered: bool = False) -> None:
        """Pomodoro in die Statistik schreiben und Zwischenstand löschen."""
        if minutes is None:
            minutes = (self.duration - self.current_remaining()) / 60.0
        start = start or self.focus_started or datetime.now()
        task = task or self.focus_task or self.cfg["active_task"]
        if minutes >= 1 or done:
            entry = {
                "start": start.isoformat(timespec="seconds"),
                "end": datetime.now().isoformat(timespec="seconds"),
                "min": round(minutes, 2), "task": task, "done": done,
            }
            if recovered:
                entry["recovered"] = True
            append_session(entry)
            if date.today() != self._today:
                self._today, self._today_min = date.today(), self._load_today()
            elif start.date() == self._today:
                self._today_min += minutes
        write_current(None)
        if self.edit_window is not None:
            self.edit_window.on_data_changed()

    # ------------------------------------------------------ Absturzschutz --

    def checkpoint(self) -> None:
        """Laufenden Pomodoro/Pause sichern - alle 30 s und bei jeder Änderung."""
        self._last_cp = clock()
        if self.phase not in ("focus", "break"):
            write_current(None)
            return
        write_current({
            "phase": self.phase,
            "break_kind": self.break_kind,
            "task": self.focus_task,
            "start": (self.focus_started or datetime.now()).isoformat(timespec="seconds"),
            "duration": self.duration,
            "running": self.running,
            "remaining": self.current_remaining(),
            "clock": clock(),
            "wall": time.time(),
            "focus_count": self.focus_count,
        })

    def _recover(self) -> None:
        """Nach Absturz, Hänger oder Ausschalten den letzten Stand übernehmen."""
        st = read_current()
        if not st:
            return
        try:
            phase = st["phase"]
            duration = float(st["duration"])
            remaining = float(st["remaining"])
            saved_clock = float(st["clock"])
            start = datetime.fromisoformat(st["start"])
            task = st.get("task") or self.cfg["active_task"]
            self.focus_count = int(st.get("focus_count", 0))
        except (KeyError, ValueError, TypeError):
            write_current(None)
            return

        now = clock()
        # Uhr startet nach einem Neustart bei 0; nach > 4 h gilt der Stand als veraltet
        rebooted = now < saved_clock or time.time() - float(st.get("wall", 0)) > 4 * 3600
        # vergangene (wache) Zeit seit dem letzten Speichern
        gap = 0.0 if rebooted else now - saved_clock
        if not st.get("running"):
            gap = 0.0
        rem_now = remaining - gap

        if rebooted:
            # Rechner war aus: nur bis zum letzten gesicherten Stand zählen
            if phase == "focus":
                self._log_focus(done=False, start=start, task=task, recovered=True,
                                minutes=(duration - remaining) / 60.0)
                log_event(f"Wiederhergestellt nach Neustart: {(duration - remaining) / 60:.1f} min")
            write_current(None)
            return

        if rem_now > 1:
            # Timer lief noch: einfach weiterlaufen lassen (bzw. pausiert lassen)
            self.phase = phase
            self.break_kind = st.get("break_kind", "short")
            self.duration, self.remaining = duration, rem_now
            self.focus_started, self.focus_task = start, task
            log_event(f"Fortgesetzt: {phase}, noch {rem_now / 60:.1f} min")
            if st.get("running"):
                self.after(50, self.resume)
            else:
                self.running = False
                self.checkpoint()
            return

        # Zeit ist abgelaufen, während das Programm weg war
        if phase == "focus":
            self._log_focus(done=True, start=start, task=task, minutes=duration / 60.0,
                            recovered=True)
            self.focus_count += 1
            self.phase = "choice"
            log_event("Pomodoro lief ab, während das Programm weg war - voll gezählt")
        else:
            self.phase = "idle"
        write_current(None)

    def start_watchdog(self) -> None:
        """Hintergrund-Wächter: hängt das Fenster, wird neu gestartet (ohne Datenverlust)."""
        if not IS_WINDOWS:
            return
        import threading

        try:
            hwnd = hwnd_of(self)
        except Exception:
            return

        def run() -> None:
            import ctypes

            u = _user32()
            fails = 0
            while True:
                time.sleep(10)
                res = ctypes.c_size_t()
                # SMTO_ABORTIFHUNG: kehrt sofort zurück, wenn Windows das Fenster als hängend sieht
                ok = u.SendMessageTimeoutW(hwnd, 0, 0, 0, 0x0002, 5000, ctypes.byref(res))
                fails = 0 if ok else fails + 1
                if fails >= 3:   # ~30 s keine Reaktion
                    self._restart_after_hang()

        threading.Thread(target=run, daemon=True, name="watchdog").start()

    def _restart_after_hang(self) -> None:
        try:
            import faulthandler

            if self.phase in ("focus", "break"):   # Zwischenstand ohne Tk sichern
                self.checkpoint()
            with EVENT_LOG.open("a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now().isoformat(timespec='seconds')}  HÄNGER erkannt "
                         f"(phase={self.phase}) - Neustart. Stack:\n")
                faulthandler.dump_traceback(file=fh, all_threads=True)
            subprocess.Popen([str(pythonw_path()), str(SCRIPT_PATH), "--restart"],
                             creationflags=0x00000008 | 0x00000200,  # DETACHED, NEW_GROUP
                             close_fds=True)
        finally:
            os._exit(3)

    # --------------------------------------------------------------- Takt --

    def _schedule(self) -> None:
        """Nur bei laufendem Timer, genau einmal pro Sekunde (energiesparend)."""
        self._cancel_tick()
        rem = self.deadline - clock()
        frac = rem - math.floor(rem)
        self._tick_id = self.after(max(20, int(frac * 1000) + 15), self._tick)

    def _cancel_tick(self) -> None:
        if self._tick_id:
            try:
                self.after_cancel(self._tick_id)
            except tk.TclError:
                pass
            self._tick_id = None

    def _tick(self) -> None:
        self._tick_id = None
        if not self.running:
            return
        self.remaining = self.deadline - clock()
        if self.remaining <= 0.05:
            self.remaining = 0.0
            self._finish()
            return
        if clock() - self._last_cp >= 30:
            self.checkpoint()
        self.render()
        self._schedule()

    def _finish(self) -> None:
        self.running = False
        if self.phase == "focus":
            self._log_focus(done=True)
            self.focus_count += 1
            self.phase = "choice"
            kind = "focus"
        else:
            self.phase = "idle"
            kind = "break"
            write_current(None)
        if self.cfg["sound"]:
            self.sound.play(kind)
        self._attention()
        self.render()

    def _attention(self) -> None:
        try:
            self.deiconify()
            if IS_WINDOWS:
                u = _user32()
                u.ShowWindow(hwnd_of(self), 9)
                u.FlashWindow(hwnd_of(self), True)
            self.lift()
            self.attributes("-topmost", True)
            if not self.cfg["topmost"]:
                self.after(400, lambda: self.attributes("-topmost", False))
        except tk.TclError:
            pass

    # ------------------------------------------------------------- Zeichnen --

    def _pill(self, w: int, h: int, fill: str) -> "tk.PhotoImage":
        key = (w, h, fill, self.pal.bg)
        if key not in self._img_cache:
            self._img_cache[key] = pill_photo(w, h, fill, self.pal.bg)
        return self._img_cache[key]

    def _button(self, cx, cy, w, h, text, action, primary=True) -> None:
        p, s = self.pal, self.s
        fill = p.accent if primary else p.tint
        tags = ("btn", f"act:{action}")
        self.canvas.create_image(cx, cy, image=self._pill(int(w * s), int(h * s), fill), tags=tags)
        self.canvas.create_text(cx, cy, text=text, fill=p.bg if primary else p.accent,
                                font=(self.f_semi, -int(14 * s)), tags=tags)

    def _link(self, x, y, text, action, anchor="sw") -> None:
        self.canvas.create_text(x, y, text=text, anchor=anchor, fill=self.pal.dim,
                                font=(self.f_ui, -int(11 * self.s)), tags=(f"act:{action}",))

    def today_minutes(self) -> float:
        if date.today() != self._today:
            self._today, self._today_min = date.today(), 0.0
        extra = 0.0
        if self.phase == "focus":
            rem = self.current_remaining()
            extra = (self.duration - max(0.0, rem)) / 60.0
        return self._today_min + extra

    def render(self) -> None:
        c, p, s = self.canvas, self.pal, self.s
        W, H = self.W, self.H
        c.delete("all")
        pad = int(14 * s)
        small = (self.f_ui, -int(11 * s))

        # oben links: Aufgabe bzw. Pausenart
        if self.phase == "break":
            label = "long break" if self.break_kind == "long" else "short break"
            c.create_text(pad, pad - 2, text=label, anchor="nw", fill=p.accent, font=small)
        else:
            c.create_text(pad, pad - 2, text=f"{self.task_name()}  ▾", anchor="nw",
                          fill=p.dim, font=small, tags=("act:task",))

        # unten rechts: Lernzeit heute
        c.create_text(W - pad, H - pad + 3, text=f"today {fmt_hm(self.today_minutes())}",
                      anchor="se", fill=p.dim, font=small)

        cx, cy = W / 2, H / 2 + 2 * s
        if self.phase == "idle":
            self._button(cx, cy, 124, 40, "Start", "start")
        elif self.phase == "choice":
            self._button(cx - 60 * s, cy, 108, 38, "Start", "start")
            self._button(cx + 60 * s, cy, 108, 38, "Break", "break", primary=False)
        elif self.phase == "pick":
            long_due = self.cfg["long_every"] > 0 and self.focus_count >= self.cfg["long_every"]
            self._button(cx - 60 * s, cy, 108, 38, f"Short · {fmt_min(self.cfg['short'])}",
                         "short", primary=not long_due)
            self._button(cx + 60 * s, cy, 108, 38, f"Long · {fmt_min(self.cfg['long'])}",
                         "long", primary=long_due)
            self._link(pad, H - pad + 3, "← back", "back")
        else:  # focus | break laufend oder pausiert
            rem = self.current_remaining()
            digits_col = (p.fg if self.phase == "focus" else p.accent) if self.running else p.dim
            c.create_text(cx, cy - 8 * s, text=fmt_clock(rem), fill=digits_col,
                          font=(self.f_light, -int(50 * s)), tags=("act:toggle",))
            # dünner Fortschrittsstrich
            x0, x1, y = cx - 70 * s, cx + 70 * s, cy + 30 * s
            prog = 0.0 if self.duration <= 0 else min(1.0, 1 - max(0.0, rem) / self.duration)
            c.create_line(x0, y, x1, y, fill=p.line, width=max(1, int(2 * s)))
            if prog > 0:
                c.create_line(x0, y, x0 + (x1 - x0) * prog, y, fill=p.accent,
                              width=max(1, int(2 * s)))
            if not self.running:
                c.create_text(cx, y + 12 * s, text="paused", fill=p.dim, font=small)
            if self.hover or not self.running:
                pause_txt = "pause" if self.running else "resume"
                self._link(pad, H - pad + 3, pause_txt, "toggle")
                end_txt, end_act = ("stop", "stop") if self.phase == "focus" else ("skip", "skip")
                off = int(len(pause_txt) * 6.5 * s) + int(14 * s)
                self._link(pad + off, H - pad + 3, end_txt, end_act)

    # --------------------------------------------------------- Bearbeiten --

    def open_edit(self, tab: str | None = None) -> None:
        if self.edit_window is not None:
            try:
                if tab:
                    self.edit_window.show_tab(tab)
                self.edit_window.deiconify()
                self.edit_window.lift()
                self.edit_window.focus_force()
                return
            except tk.TclError:
                self.edit_window = None
        self.edit_window = EditWindow(self, tab or self.cfg.get("edit_tab", "timer"))

    def quit_app(self) -> None:
        if self.phase == "focus":
            self.stop_focus()
        else:
            write_current(None)
        try:
            self.cfg["pos"] = [self.winfo_x(), self.winfo_y()]
        except tk.TclError:
            pass
        save_config(self.cfg)
        self._cancel_tick()
        log_event("Beendet (Schließen)")
        self.destroy()


# --------------------------------------------------------- Bearbeiten -------


class EditWindow(tk.Toplevel):
    TABS = [("timer", "Timer"), ("tasks", "Aufgaben"), ("look", "Look"), ("stats", "Statistik"),
            ("history", "Einträge")]

    def __init__(self, app: PomodoroApp, tab: str) -> None:
        super().__init__(app)
        self.app = app
        self.cfg = app.cfg
        self.title(f"{APP_NAME} · Bearbeiten")
        d = app.dpi
        self.geometry(f"{int(600 * d)}x{int(680 * d)}")
        self.minsize(int(560 * d), int(600 * d))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self._imgs: list = []
        self.tab = tab if tab in dict(self.TABS) else "timer"
        self.stats_filter: str | None = None
        self.build()

    # -- Gerüst

    @property
    def p(self) -> Palette:
        return self.app.pal

    def build(self) -> None:
        for child in self.winfo_children():
            child.destroy()
        self._imgs.clear()
        p = self.p
        self.configure(bg=p.bg)
        dark_titlebar(self, self.cfg["theme"] != "Papier")

        bar = tk.Frame(self, bg=p.bg)
        bar.pack(fill="x", padx=24, pady=(18, 0))
        for key, label in self.TABS:
            active = key == self.tab
            lab = tk.Label(bar, text=label, bg=p.bg, fg=p.fg if active else p.dim,
                           font=(self.app.f_semi if active else self.app.f_ui, 11),
                           cursor="hand2", padx=0)
            lab.pack(side="left", padx=(0, 22))
            lab.bind("<Button-1>", lambda e, k=key: self.show_tab(k))
        tk.Frame(self, bg=p.line, height=1).pack(fill="x", padx=24, pady=(10, 0))

        self.body = tk.Frame(self, bg=p.bg)
        self.body.pack(fill="both", expand=True, padx=24, pady=(14, 18))
        getattr(self, f"_tab_{self.tab}")(self.body)

    def show_tab(self, key: str) -> None:
        self.tab = key
        self.cfg["edit_tab"] = key
        save_config(self.cfg)
        self.build()

    def on_data_changed(self) -> None:
        if self.tab == "stats":
            try:
                self.build()
            except tk.TclError:
                pass

    def close(self) -> None:
        self.focus_set()  # offene Eingaben übernehmen
        save_config(self.cfg)
        self.app.edit_window = None
        self.destroy()

    # -- Bausteine

    def _section(self, parent, text, top=14) -> None:
        tk.Label(parent, text=text.upper(), bg=self.p.bg, fg=self.p.dim,
                 font=(self.app.f_semi, 8)).pack(anchor="w", pady=(top, 6))

    def _entry(self, parent, value, width=7) -> tk.Entry:
        p = self.p
        e = tk.Entry(parent, bg=p.soft, fg=p.fg, insertbackground=p.fg, relief="flat", bd=0,
                     highlightthickness=1, highlightbackground=p.line, highlightcolor=p.accent,
                     width=width, font=(self.app.f_ui, 10))
        e.insert(0, value)
        return e

    def _field(self, parent, label, value, on_change, width=7) -> tk.Entry:
        box = tk.Frame(parent, bg=self.p.bg)
        box.pack(side="left", padx=(0, 18))
        tk.Label(box, text=label, bg=self.p.bg, fg=self.p.dim,
                 font=(self.app.f_ui, 9)).pack(anchor="w")
        e = self._entry(box, value, width)
        e.pack(anchor="w", ipady=5, pady=(3, 0))
        e.bind("<KeyRelease>", lambda ev: self._validate(e, on_change))
        return e

    def _validate(self, entry: tk.Entry, on_change) -> None:
        ok = on_change(entry.get())
        entry.configure(highlightbackground=self.p.line if ok else "#e5533d",
                        highlightcolor=self.p.accent if ok else "#e5533d")

    def _toggle(self, parent, text, value: bool, on_change) -> None:
        p = self.p
        row = tk.Frame(parent, bg=p.bg, cursor="hand2")
        row.pack(anchor="w", pady=3)
        dot = tk.Label(row, text="●", bg=p.bg, font=(self.app.f_ui, 11))
        dot.pack(side="left")
        lab = tk.Label(row, text=text, bg=p.bg, fg=p.fg, font=(self.app.f_ui, 10))
        lab.pack(side="left", padx=(8, 0))
        state = {"v": bool(value)}

        def paint():
            dot.configure(text="●" if state["v"] else "○",
                          fg=p.accent if state["v"] else p.dim)

        def flip(_e=None):
            state["v"] = not state["v"]
            paint()
            on_change(state["v"])

        for w in (row, dot, lab):
            w.bind("<Button-1>", flip)
        paint()

    def _flat_button(self, parent, text, command, primary=False) -> tk.Label:
        p = self.p
        b = tk.Label(parent, text=text, bg=p.accent if primary else p.soft,
                     fg=p.bg if primary else p.fg, font=(self.app.f_ui, 9),
                     padx=14, pady=6, cursor="hand2")
        b.bind("<Button-1>", lambda e: command())
        return b

    def _hint(self, parent, text) -> tk.Label:
        lab = tk.Label(parent, text=text, bg=self.p.bg, fg=self.p.dim,
                       font=(self.app.f_ui, 8), justify="left", anchor="w",
                       wraplength=int(540 * self.app.dpi))
        lab.pack(anchor="w", pady=(4, 0))
        return lab

    # -- Tab: Timer

    def _tab_timer(self, body) -> None:
        self._section(body, "Dauer in Minuten", top=0)
        row = tk.Frame(body, bg=self.p.bg)
        row.pack(fill="x")

        def setter(key):
            def apply(text):
                try:
                    self.cfg[key] = parse_minutes(text)
                except ValueError:
                    return False
                save_config(self.cfg)
                if self.app.phase in ("idle", "choice", "pick"):
                    self.app.render()
                return True
            return apply

        self._field(row, "Pomodoro", fmt_min(self.cfg["focus"]), setter("focus"))
        self._field(row, "Kurze Pause", fmt_min(self.cfg["short"]), setter("short"))
        self._field(row, "Lange Pause", fmt_min(self.cfg["long"]), setter("long"))

        def set_every(text):
            try:
                n = int(text.strip())
                if not 0 <= n <= 20:
                    raise ValueError
            except ValueError:
                return False
            self.cfg["long_every"] = n
            save_config(self.cfg)
            return True

        self._field(row, "Lange Pause vorschlagen nach", str(self.cfg["long_every"]), set_every,
                    width=4)
        self._hint(body, "Nach jedem Pomodoro wählst du selbst: Start oder Break (short/long). "
                         "Die vorgeschlagene Pause ist hervorgehoben.")

        self._section(body, "Verhalten")

        def set_cfg(key, after=None):
            def apply(v):
                self.cfg[key] = v
                save_config(self.cfg)
                if after:
                    after(v)
            return apply

        self._toggle(body, "Signalton am Ende", self.cfg["sound"], set_cfg("sound"))
        self._toggle(body, "Immer im Vordergrund", self.cfg["topmost"],
                     set_cfg("topmost", lambda v: self.app.attributes("-topmost", v)))

        self._section(body, "Windows")

        def set_auto(v):
            try:
                set_autostart(v)
                status.configure(text="Startet ab jetzt bei der Anmeldung." if v
                                 else "Autostart ist aus.")
            except OSError as exc:
                status.configure(text=f"Ging nicht: {exc}")

        self._toggle(body, "Beim Windows-Start öffnen", autostart_enabled(), set_auto)
        b = self._flat_button(body, "Verknüpfung auf Desktop + Startmenü anlegen",
                              lambda: status.configure(
                                  text="Verknüpfungen angelegt. Tipp: im Startmenü rechtsklicken → "
                                       "„An Taskleiste anheften“." if create_shortcuts()
                                  else "Verknüpfungen konnten nicht angelegt werden."))
        b.pack(anchor="w", pady=(8, 0))
        status = self._hint(body, "")

        self._section(body, "Bedienung")
        self._hint(body, "Klick auf die Zeit: Pause/weiter  ·  Leertaste: Start/Pause\n"
                         "Widget ziehen: verschieben  ·  Rechtsklick: Menü  ·  "
                         "Klick oben links: Aufgabe wechseln")

    # -- Tab: Aufgaben

    def _tab_tasks(self, body) -> None:
        p = self.p
        self._section(body, "Aufgaben", top=0)
        self._hint(body, "Jeder Pomodoro zählt für die aktive Aufgabe. Ziel = Stunden pro Tag "
                         "(leer lassen = kein Ziel).")
        head = tk.Frame(body, bg=p.bg)
        head.pack(fill="x", pady=(12, 2))
        tk.Label(head, text="Name", bg=p.bg, fg=p.dim, font=(self.app.f_ui, 9),
                 width=22, anchor="w").pack(side="left")
        tk.Label(head, text="Ziel h/Tag", bg=p.bg, fg=p.dim, font=(self.app.f_ui, 9),
                 anchor="w").pack(side="left", padx=(8, 0))

        for task in self.cfg["tasks"]:
            row = tk.Frame(body, bg=p.bg)
            row.pack(fill="x", pady=3)
            active = task["id"] == self.cfg["active_task"]
            dot = tk.Label(row, text="●", bg=p.bg, fg=p.accent if active else p.line,
                           font=(self.app.f_ui, 10), cursor="hand2")
            dot.pack(side="left", padx=(0, 8))
            dot.bind("<Button-1>", lambda e, t=task: (self.app.set_task(t["id"]), self.build()))

            name = self._entry(row, task["name"], width=22)
            name.pack(side="left", ipady=5)

            def set_name(text, t=task):
                text = text.strip()
                if not text:
                    return False
                t["name"] = text
                save_config(self.cfg)
                self.app.render()
                return True

            name.bind("<KeyRelease>", lambda ev, e=name, f=set_name: self._validate(e, f))

            goal = self._entry(row, fmt_min(task.get("goal") or 0) if task.get("goal") else "",
                               width=6)
            goal.pack(side="left", padx=(8, 0), ipady=5)

            def set_goal(text, t=task):
                text = text.strip().replace(",", ".")
                if not text:
                    t["goal"] = 0.0
                else:
                    try:
                        v = float(text)
                        if not 0 <= v <= 24:
                            raise ValueError
                    except ValueError:
                        return False
                    t["goal"] = v
                save_config(self.cfg)
                return True

            goal.bind("<KeyRelease>", lambda ev, e=goal, f=set_goal: self._validate(e, f))

            if len(self.cfg["tasks"]) > 1:
                x = tk.Label(row, text="✕", bg=p.bg, fg=p.dim, font=(self.app.f_ui, 10),
                             cursor="hand2")
                x.pack(side="left", padx=(12, 0))
                x.bind("<Button-1>", lambda e, t=task: self._delete_task(t))

        add = self._flat_button(body, "+ Aufgabe", self._add_task)
        add.pack(anchor="w", pady=(12, 0))
        self._hint(body, "Gelöschte Aufgaben bleiben in der Statistik unter „Alle“ erhalten.")

    def _add_task(self) -> None:
        n = len(self.cfg["tasks"]) + 1
        ids = {t["id"] for t in self.cfg["tasks"]}
        tid = f"t{int(time.time())}"
        while tid in ids:
            tid += "x"
        self.cfg["tasks"].append({"id": tid, "name": f"task {n}", "goal": 0.0})
        save_config(self.cfg)
        self.build()

    def _delete_task(self, task) -> None:
        self.cfg["tasks"] = [t for t in self.cfg["tasks"] if t["id"] != task["id"]]
        if self.cfg["active_task"] == task["id"]:
            self.cfg["active_task"] = self.cfg["tasks"][0]["id"]
        save_config(self.cfg)
        self.app.render()
        self.build()

    # -- Tab: Look

    def _tab_look(self, body) -> None:
        p = self.p
        self._section(body, "Hintergrund", top=0)
        row = tk.Frame(body, bg=p.bg)
        row.pack(anchor="w")
        for name, bg, _fg, buttons in THEMES:
            cell = tk.Frame(row, bg=p.bg)
            cell.pack(side="left", padx=(0, 10))
            sel = name == self.cfg["theme"]
            img = disc_photo(40, bg, p.bg, p.fg if sel else p.line)
            self._imgs.append(img)
            lab = tk.Label(cell, image=img, bg=p.bg, cursor="hand2", bd=0)
            lab.pack()
            tk.Label(cell, text=name, bg=p.bg, fg=p.fg if sel else p.dim,
                     font=(self.app.f_ui, 8)).pack(pady=(2, 0))
            lab.bind("<Button-1>", lambda e, n=name, b=buttons: self._set_theme(n, b))

        self._section(body, "Button-Farbe (passend zum Hintergrund)", top=18)
        row2 = tk.Frame(body, bg=p.bg)
        row2.pack(anchor="w")
        for color in THEME_BY_NAME[self.cfg["theme"]][3]:
            sel = color.lower() == p.accent.lower()
            img = disc_photo(40, color, p.bg, p.fg if sel else None)
            self._imgs.append(img)
            lab = tk.Label(row2, image=img, bg=p.bg, cursor="hand2", bd=0)
            lab.pack(side="left", padx=(0, 10))
            lab.bind("<Button-1>", lambda e, c=color: self._set_button(c))

        self._section(body, "Größe", top=18)
        row3 = tk.Frame(body, bg=p.bg)
        row3.pack(anchor="w")
        for label, value in SIZES:
            sel = abs(self.cfg["scale"] - value) < 1e-6
            b = self._flat_button(row3, label, lambda v=value: self._set_scale(v), primary=sel)
            b.configure(padx=16)
            b.pack(side="left", padx=(0, 6))

        self._section(body, "Vorschau", top=18)
        self._hint(body, "Änderungen sind sofort im Widget zu sehen.")
        prev = tk.Canvas(body, width=300, height=110, bg=p.bg, highlightthickness=0)
        prev.pack(anchor="w", pady=(8, 0))
        bgc = THEME_BY_NAME[self.cfg["theme"]][1]
        pill = pill_photo(108, 38, p.accent, bgc)
        pill2 = pill_photo(108, 38, p.tint, bgc)
        frame = shape_photo(300, 110, p.bg, [
            (p.line, lambda px, py: _sdf_box(px, py, 150, 55, 150, 55, 12)),
            (bgc, lambda px, py: _sdf_box(px, py, 150, 55, 149, 54, 11))])
        self._imgs += [pill, pill2, frame]
        prev.create_image(150, 55, image=frame)
        prev.create_image(150 - 60, 55, image=pill)
        prev.create_text(150 - 60, 55, text="Start", fill=bgc, font=(self.app.f_semi, -14))
        prev.create_image(150 + 60, 55, image=pill2)
        prev.create_text(150 + 60, 55, text="Break", fill=p.accent, font=(self.app.f_semi, -14))

    def _set_theme(self, name, buttons) -> None:
        old_buttons = THEME_BY_NAME[self.cfg["theme"]][3]
        self.cfg["theme"] = name
        # gleiche Position in der Farbpalette beibehalten
        try:
            idx = old_buttons.index(self.cfg["button"])
        except ValueError:
            idx = 0
        self.cfg["button"] = buttons[min(idx, len(buttons) - 1)]
        save_config(self.cfg)
        self.app.apply_look()
        self.build()

    def _set_button(self, color) -> None:
        self.cfg["button"] = color
        save_config(self.cfg)
        self.app.apply_look()
        self.build()

    def _set_scale(self, value) -> None:
        self.cfg["scale"] = value
        save_config(self.cfg)
        self.app.apply_look()
        self.build()

    # -- Tab: Statistik

    def _tab_stats(self, body) -> None:
        p, f = self.p, self.app.f_ui
        sessions = load_sessions()
        names = {t["id"]: t["name"] for t in self.cfg["tasks"]}
        task = self.stats_filter
        today = date.today()

        # Filter-Chips
        chips = tk.Frame(body, bg=p.bg)
        chips.pack(anchor="w")
        options = [(None, "Alle")] + [(t["id"], t["name"]) for t in self.cfg["tasks"]]
        for tid, label in options:
            sel = tid == task
            b = tk.Label(chips, text=label, bg=p.accent if sel else p.soft,
                         fg=p.bg if sel else p.dim, font=(f, 9), padx=12, pady=4, cursor="hand2")
            b.pack(side="left", padx=(0, 6))
            b.bind("<Button-1>", lambda e, t=tid: self._set_filter(t))

        days = daily_totals(sessions, task)
        week_start = today - timedelta(days=today.weekday())
        week = sum(m for d, m in days.items() if d >= week_start)
        cur, best = streaks(days, today)
        s30, span30 = window_sum(days, today, 30)
        s56, span56 = window_sum(days, today, 56)
        active = sum(1 for m in days.values() if m >= 1)
        act_avg = (sum(days.values()) / active) if active else 0.0

        kpis = [
            (fmt_hm(days.get(today, 0.0)), "heute"),
            (fmt_hm(week), "diese Woche"),
            (f"{cur} {'Tag' if cur == 1 else 'Tage'}", f"Streak · Rekord {best}"),
            (fmt_hm(s30 / span30), f"Ø pro Tag ({span30} T)"),
            (fmt_hm(s56 / span56 * 7), "Ø pro Woche"),
            (str(active), f"aktive Tage · Ø {fmt_hm(act_avg)}"),
        ]
        grid = tk.Frame(body, bg=p.bg)
        grid.pack(fill="x", pady=(14, 0))
        for i, (value, caption) in enumerate(kpis):
            cell = tk.Frame(grid, bg=p.soft, padx=12, pady=8)
            cell.grid(row=i // 3, column=i % 3, sticky="nsew", padx=(0, 8), pady=(0, 8))
            tk.Label(cell, text=value, bg=p.soft, fg=p.fg, font=(self.app.f_semi, 15)).pack(anchor="w")
            tk.Label(cell, text=caption, bg=p.soft, fg=p.dim, font=(f, 8)).pack(anchor="w")
        for col in range(3):
            grid.columnconfigure(col, weight=1, uniform="k")

        # Heatmap + Tageszeit nebeneinander
        row = tk.Frame(body, bg=p.bg)
        row.pack(fill="x", pady=(6, 0))
        left = tk.Frame(row, bg=p.bg)
        left.pack(side="left", anchor="n")
        right = tk.Frame(row, bg=p.bg)
        right.pack(side="left", anchor="n", padx=(22, 0))

        weeks = 18
        tk.Label(left, text=f"LETZTE {weeks} WOCHEN", bg=p.bg, fg=p.dim,
                 font=(self.app.f_semi, 8)).pack(anchor="w", pady=(0, 6))
        cell, gap = 12, 3
        hm = tk.Canvas(left, width=22 + weeks * (cell + gap), height=7 * (cell + gap),
                       bg=p.bg, highlightthickness=0)
        hm.pack(anchor="w")
        levels = [blend(p.bg, p.accent, t) for t in (0.22, 0.42, 0.65, 0.85, 1.0)]
        first_monday = week_start - timedelta(weeks=weeks - 1)
        for wd, lab in ((0, "Mo"), (2, "Mi"), (4, "Fr"), (6, "So")):
            hm.create_text(0, wd * (cell + gap) + cell / 2, text=lab, anchor="w",
                           fill=p.faint, font=(f, 7))
        for w in range(weeks):
            for wd in range(7):
                d = first_monday + timedelta(weeks=w, days=wd)
                if d > today:
                    continue
                m = days.get(d, 0.0)
                if m <= 0:
                    col = p.line
                else:
                    h = m / 60
                    col = levels[0 if h < 1 else 1 if h < 2.5 else 2 if h < 4 else 3 if h < 6 else 4]
                x = 22 + w * (cell + gap)
                y = wd * (cell + gap)
                hm.create_rectangle(x, y, x + cell, y + cell, fill=col, width=0)
        legend = tk.Frame(left, bg=p.bg)
        legend.pack(anchor="w", pady=(4, 0))
        tk.Label(legend, text="0   <1h  <2.5h  <4h  <6h  6h+", bg=p.bg, fg=p.faint,
                 font=(f, 7)).pack(side="left")

        tk.Label(right, text="TAGESZEIT · 30 TAGE", bg=p.bg, fg=p.dim,
                 font=(self.app.f_semi, 8)).pack(anchor="w", pady=(0, 6))
        hist = hour_histogram(sessions, task, datetime.combine(today - timedelta(days=29),
                                                               datetime.min.time()))
        hw, hh = 216, 7 * (cell + gap) - 14
        hc = tk.Canvas(right, width=hw, height=hh + 14, bg=p.bg, highlightthickness=0)
        hc.pack(anchor="w")
        peak = max(hist) or 1.0
        bw = hw / 24
        for hr, v in enumerate(hist):
            x = hr * bw
            bh = max(1.5, hh * v / peak) if v > 0 else 1.5
            hc.create_rectangle(x + 1, hh - bh, x + bw - 1, hh, width=0,
                                fill=p.accent if v > 0 else p.line)
        for hr in (0, 6, 12, 18):
            hc.create_text(hr * bw + 1, hh + 3, text=f"{hr}", anchor="nw", fill=p.faint, font=(f, 7))
        if max(hist) > 0:
            top = max(range(24), key=lambda i: hist[i])
            tk.Label(right, text=f"am meisten um {top}–{top + 1} Uhr", bg=p.bg, fg=p.faint,
                     font=(f, 7)).pack(anchor="w", pady=(2, 0))

        # Tabelle pro Aufgabe
        tk.Label(body, text="PRO AUFGABE", bg=p.bg, fg=p.dim,
                 font=(self.app.f_semi, 8)).pack(anchor="w", pady=(16, 4))
        table = tk.Frame(body, bg=p.bg)
        table.pack(fill="x")
        heads = ["", "heute", "Woche", "Ø Tag", "Ø Woche", "gesamt", "Ziel heute"]
        for c, h in enumerate(heads):
            tk.Label(table, text=h, bg=p.bg, fg=p.faint, font=(f, 8),
                     anchor="w").grid(row=0, column=c, sticky="w", padx=(0, 14))
        task_ids = [t["id"] for t in self.cfg["tasks"]]
        extra = sorted({s.get("task") for s in sessions} - set(task_ids) - {None})
        rows = [(tid, names.get(tid, tid)) for tid in task_ids] + \
               [(tid, f"{tid} (gelöscht)") for tid in extra]
        for r, (tid, label) in enumerate(rows, start=1):
            d = daily_totals(sessions, tid)
            if not d and tid not in task_ids:
                continue
            a30, sp30 = window_sum(d, today, 30)
            a56, sp56 = window_sum(d, today, 56)
            goal = next((t.get("goal", 0) for t in self.cfg["tasks"] if t["id"] == tid), 0) or 0
            vals = [label, fmt_hm(d.get(today, 0)),
                    fmt_hm(sum(m for k, m in d.items() if k >= week_start)),
                    fmt_hm(a30 / sp30), fmt_hm(a56 / sp56 * 7), fmt_hm(sum(d.values()))]
            for c, v in enumerate(vals):
                tk.Label(table, text=v, bg=p.bg, fg=p.fg if c else p.fg,
                         font=(self.app.f_semi if c == 0 else f, 9),
                         anchor="w").grid(row=r, column=c, sticky="w", padx=(0, 14), pady=2)
            if goal:
                done = d.get(today, 0) / (goal * 60)
                bar = tk.Canvas(table, width=80, height=6, bg=p.bg, highlightthickness=0)
                bar.grid(row=r, column=6, sticky="w")
                bar.create_rectangle(0, 0, 80, 6, fill=p.line, width=0)
                bar.create_rectangle(0, 0, 80 * min(1.0, done), 6, fill=p.accent, width=0)
            else:
                tk.Label(table, text="–", bg=p.bg, fg=p.faint, font=(f, 9)).grid(
                    row=r, column=6, sticky="w")

        sel = [s for s in sessions if task is None or s.get("task") == task]
        finished = sum(1 for s in sel if s.get("done"))
        aborted = len(sel) - finished
        self._hint(body, f"{finished} Pomodoros abgeschlossen · {aborted} vorzeitig gestoppt "
                         f"(zählen mit ihrer echten Dauer)")

    # -- Tab: Einträge (manuell nachtragen / löschen)

    def _tab_history(self, body) -> None:
        p, f = self.p, self.app.f_ui
        today = date.today()
        self._section(body, "Zeit nachtragen", top=0)
        self._hint(body, "Für Lernzeit ohne Laptop. Eine negative Dauer (z. B. -30) zieht Zeit ab.")

        row = tk.Frame(body, bg=p.bg)
        row.pack(fill="x", pady=(8, 0))
        form = getattr(self, "_hist_form", {"day": "heute", "time": "", "dur": ""})
        entries = {}
        for key, label, width in (("day", "Datum", 11), ("time", "Uhrzeit", 7),
                                  ("dur", "Dauer (min oder 1:30)", 9)):
            box = tk.Frame(row, bg=p.bg)
            box.pack(side="left", padx=(0, 14))
            tk.Label(box, text=label, bg=p.bg, fg=p.dim, font=(f, 9)).pack(anchor="w")
            e = self._entry(box, form[key], width)
            e.pack(anchor="w", ipady=5, pady=(3, 0))
            entries[key] = e

        # Aufgabe wählen (Chips)
        task_row = tk.Frame(body, bg=p.bg)
        task_row.pack(anchor="w", pady=(10, 0))
        tk.Label(task_row, text="Aufgabe", bg=p.bg, fg=p.dim, font=(f, 9)).pack(side="left",
                                                                                padx=(0, 10))
        if getattr(self, "_hist_task", None) not in {t["id"] for t in self.cfg["tasks"]}:
            self._hist_task = self.cfg["active_task"]
        for t in self.cfg["tasks"]:
            sel = t["id"] == self._hist_task
            chip = tk.Label(task_row, text=t["name"], bg=p.accent if sel else p.soft,
                            fg=p.bg if sel else p.dim, font=(f, 9), padx=12, pady=4,
                            cursor="hand2")
            chip.pack(side="left", padx=(0, 6))
            chip.bind("<Button-1>", lambda e, i=t["id"]: (self._remember_form(entries),
                                                          setattr(self, "_hist_task", i),
                                                          self.build()))

        status = tk.Label(body, text=getattr(self, "_hist_status", ""), bg=p.bg, fg=p.dim,
                          font=(f, 9), anchor="w", justify="left")

        def add(_e=None):
            try:
                day = parse_day(entries["day"].get(), today)
            except ValueError:
                status.configure(text="Datum nicht verstanden (z. B. heute, gestern, 24.11.).",
                                 fg="#e5533d")
                return
            try:
                h, m = parse_clock_time(entries["time"].get())
            except ValueError:
                status.configure(text="Uhrzeit bitte als HH:MM (oder leer lassen).", fg="#e5533d")
                return
            try:
                minutes = parse_signed_duration(entries["dur"].get())
            except ValueError:
                status.configure(text="Dauer bitte in Minuten, z. B. 90, 1:30, 1,5h oder -30.",
                                 fg="#e5533d")
                return
            start = datetime.combine(day, datetime.min.time()).replace(hour=h, minute=m)
            end = start + timedelta(minutes=max(0.0, minutes))
            ok = append_session({
                "start": start.isoformat(timespec="seconds"),
                "end": end.isoformat(timespec="seconds"),
                "min": minutes, "task": self._hist_task, "done": True, "manual": True,
            })
            sign = "abgezogen" if minutes < 0 else "nachgetragen"
            self._hist_status = (f"{fmt_hm(abs(minutes))} {self.app.task_name(self._hist_task)} "
                                 f"am {day.strftime('%d.%m.%Y')} {sign}." if ok
                                 else "Konnte nicht gespeichert werden.")
            self._hist_form = {"day": entries["day"].get(), "time": "", "dur": ""}
            self.app.refresh_today()
            self.build()

        for e in entries.values():
            e.bind("<Return>", add)
        btn = self._flat_button(body, "Hinzufügen", add, primary=True)
        btn.pack(anchor="w", pady=(12, 0))
        status.pack(fill="x", pady=(6, 0))

        # Liste der letzten Einträge
        sessions = sorted(load_sessions(), key=lambda s: s["_start"], reverse=True)
        limit = getattr(self, "_hist_limit", 12)
        head = tk.Frame(body, bg=p.bg)
        head.pack(fill="x", pady=(18, 4))
        tk.Label(head, text="LETZTE EINTRÄGE", bg=p.bg, fg=p.dim,
                 font=(self.app.f_semi, 8)).pack(side="left")
        if getattr(self, "_hist_undo", None):
            undo = tk.Label(head, text="Rückgängig", bg=p.bg, fg=p.accent, font=(f, 9),
                            cursor="hand2")
            undo.pack(side="right")
            undo.bind("<Button-1>", lambda e: self._undo_delete())

        table = tk.Frame(body, bg=p.bg)
        table.pack(fill="x")
        if not sessions:
            tk.Label(table, text="Noch keine Einträge.", bg=p.bg, fg=p.faint,
                     font=(f, 9)).grid(row=0, column=0, sticky="w")
        for r, s in enumerate(sessions[:limit]):
            kind = ("manuell" if s.get("manual") else "wiederhergestellt" if s.get("recovered")
                    else "Pomodoro" if s.get("done") else "gestoppt")
            minutes = s["min"]
            dur = ("−" if minutes < 0 else "") + fmt_hm(abs(minutes))
            wd = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")[s["_start"].weekday()]
            cells = [f"{wd} {s['_start'].strftime('%d.%m.')}", s["_start"].strftime("%H:%M"),
                     self.app.task_name(s.get("task")), dur, kind]
            for c, text in enumerate(cells):
                tk.Label(table, text=text, bg=p.bg,
                         fg=p.fg if c in (2, 3) else p.dim, font=(f, 9),
                         anchor="w").grid(row=r, column=c, sticky="w", padx=(0, 16), pady=1)
            x = tk.Label(table, text="✕", bg=p.bg, fg=p.faint, font=(f, 9), cursor="hand2")
            x.grid(row=r, column=5, sticky="w")
            x.bind("<Enter>", lambda e, w=x: w.configure(fg="#e5533d"))
            x.bind("<Leave>", lambda e, w=x: w.configure(fg=p.faint))
            x.bind("<Button-1>", lambda e, s=s: self._delete_entry(s))
        if len(sessions) > limit:
            more = tk.Label(body, text=f"ältere anzeigen ({len(sessions) - limit} weitere)",
                            bg=p.bg, fg=p.dim, font=(f, 9), cursor="hand2")
            more.pack(anchor="w", pady=(6, 0))
            more.bind("<Button-1>", lambda e: (self._remember_form(entries),
                                               setattr(self, "_hist_limit", limit + 25),
                                               self.build()))

    def _remember_form(self, entries) -> None:
        self._hist_form = {k: e.get() for k, e in entries.items()}

    def _delete_entry(self, s) -> None:
        raw = {}

        def edit(lines):
            n = s["_line"]
            try:   # nur löschen, wenn die Zeile noch genau dieser Eintrag ist
                same = n < len(lines) and json.loads(lines[n]).get("start") == s["start"]
            except ValueError:
                same = False
            if same:
                raw["line"], raw["pos"] = lines[n], n
                return lines[:n] + lines[n + 1:]
            return lines

        if rewrite_sessions(edit) and raw:
            self._hist_undo = raw
            self._hist_status = (f"Eintrag gelöscht: {fmt_hm(abs(s['min']))} "
                                 f"{self.app.task_name(s.get('task'))} am "
                                 f"{s['_start'].strftime('%d.%m.%Y')}.")
            log_event(f"Eintrag gelöscht: {raw['line']}")
        self.app.refresh_today()
        self.build()

    def _undo_delete(self) -> None:
        raw = self._hist_undo
        if raw:
            rewrite_sessions(lambda lines: lines[:raw["pos"]] + [raw["line"]] + lines[raw["pos"]:])
            self._hist_status = "Wiederhergestellt."
        self._hist_undo = None
        self.app.refresh_today()
        self.build()

    def _set_filter(self, tid) -> None:
        self.stats_filter = tid
        self.build()


# ------------------------------------------------------------------ Main ----


def main() -> None:
    if "--install" in sys.argv:
        ensure_icon()
        ok = create_shortcuts()
        set_autostart(True)
        print("Verknüpfungen + Autostart eingerichtet." if ok
              else "Autostart an, Verknüpfungen fehlgeschlagen.")
        return
    if "--uninstall" in sys.argv:
        set_autostart(False)
        create_shortcuts(remove=True)
        return

    lock = single_instance()
    if lock is None:
        return
    # Protokoll: endete die letzte Sitzung ohne "Beendet", wurde sie von außen abgewürgt
    try:
        marks = [ln for ln in EVENT_LOG.read_text(encoding="utf-8").splitlines()
                 if "  Start v" in ln or "  Beendet" in ln or "HÄNGER" in ln]
        if marks and "  Start v" in marks[-1] and "--restart" not in sys.argv:
            log_event("Vorherige Sitzung endete unerwartet (Prozess beendet ohne Schließen)")
    except OSError:
        pass
    log_event(f"Start v{VERSION} (pid {os.getpid()}{', nach Hänger' if '--restart' in sys.argv else ''})")
    try:
        if IS_WINDOWS:
            try:
                import ctypes

                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
        app = PomodoroApp()
        app.mainloop()
    except SystemExit:
        raise
    except BaseException:
        report_error("Der Timer konnte nicht gestartet werden.",
                     traceback.format_exc() + f"\nPython: {sys.executable}\n{sys.version}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
