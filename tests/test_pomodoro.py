"""Tests für den Pomodoro-Timer.

    python -m unittest discover -s tests

Die GUI-Tests brauchen ein Display und werden sonst übersprungen.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("POMODORO_DATA", tempfile.mkdtemp())

import pomodoro as p  # noqa: E402


class TempData(unittest.TestCase):
    """Jeder Test bekommt einen leeren Datenordner."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        d = Path(self._dir.name)
        self._old = {k: getattr(p, k) for k in
                     ("DATA_DIR", "CONFIG_PATH", "SESSIONS_PATH", "CURRENT_PATH", "EVENT_LOG",
                      "ICON_PATH", "LOG_PATH")}
        p.DATA_DIR = d
        p.CONFIG_PATH = d / "config.json"
        p.SESSIONS_PATH = d / "sessions.jsonl"
        p.CURRENT_PATH = d / "current.json"
        p.EVENT_LOG = d / "ereignisse.log"
        p.ICON_PATH = d / "pomodoro.ico"
        p.LOG_PATH = d / "fehler.log"

    def tearDown(self):
        for k, v in self._old.items():
            setattr(p, k, v)
        self._dir.cleanup()


class TestParsing(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(p.parse_minutes("90"), 90)
        self.assertEqual(p.parse_minutes("1,5"), 1.5)
        self.assertEqual(p.parse_minutes("1:30"), 1.5)
        for bad in ["", "abc", "0", "-5", "2000"]:
            with self.assertRaises(ValueError, msg=bad):
                p.parse_minutes(bad)

    def test_signed_duration(self):
        self.assertEqual(p.parse_signed_duration("90"), 90)
        self.assertEqual(p.parse_signed_duration("1:30"), 1.5)
        self.assertEqual(p.parse_signed_duration("1,5h"), 90)
        self.assertEqual(p.parse_signed_duration("2h"), 120)
        self.assertEqual(p.parse_signed_duration("-30"), -30)
        self.assertEqual(p.parse_signed_duration("45 min"), 45)
        for bad in ["", "-", "0", "abc", "30h"]:
            with self.assertRaises(ValueError, msg=bad):
                p.parse_signed_duration(bad)

    def test_day(self):
        today = date(2026, 11, 24)
        self.assertEqual(p.parse_day("heute", today), today)
        self.assertEqual(p.parse_day("", today), today)
        self.assertEqual(p.parse_day("gestern", today), date(2026, 11, 23))
        self.assertEqual(p.parse_day("20.11.", today), date(2026, 11, 20))
        self.assertEqual(p.parse_day("20.11.26", today), date(2026, 11, 20))
        self.assertEqual(p.parse_day("2026-11-01", today), date(2026, 11, 1))
        self.assertEqual(p.parse_day("28.12.", date(2027, 1, 3)), date(2026, 12, 28))
        for bad in ["25.11.2026", "morgen", "32.1.", "1.2.3.4"]:
            with self.assertRaises(ValueError, msg=bad):
                p.parse_day(bad, today)

    def test_clock_time(self):
        self.assertEqual(p.parse_clock_time(""), (12, 0))
        self.assertEqual(p.parse_clock_time("9:05"), (9, 5))
        self.assertEqual(p.parse_clock_time("14.30"), (14, 30))
        self.assertEqual(p.parse_clock_time("7"), (7, 0))
        with self.assertRaises(ValueError):
            p.parse_clock_time("25:00")

    def test_formatting(self):
        self.assertEqual(p.fmt_hm(45), "45m")
        self.assertEqual(p.fmt_hm(125), "2h 05m")
        self.assertEqual(p.fmt_clock(1500), "25:00")
        self.assertEqual(p.fmt_clock(3725), "1:02:05")
        self.assertEqual(p.fmt_min(2.5), "2:30")


def sess(start, minutes, task="study", **kw):
    s = {"start": start.isoformat(), "min": minutes, "task": task, "_start": start}
    s.update(kw)
    return s


class TestStats(unittest.TestCase):
    def test_daily_totals_and_negative(self):
        d = datetime(2026, 11, 20, 9)
        ss = [sess(d, 25), sess(d, 25, "work"), sess(d, -10), sess(d + timedelta(days=1), 30),
              sess(d + timedelta(days=1), -60)]
        days = p.daily_totals(ss)
        self.assertEqual(days[date(2026, 11, 20)], 40)
        self.assertNotIn(date(2026, 11, 21), days)        # nie unter 0
        self.assertEqual(p.daily_totals(ss, "work"), {date(2026, 11, 20): 25})

    def test_streaks(self):
        today = date(2026, 11, 24)
        days = {today - timedelta(days=i): 30 for i in (0, 1, 2, 5, 6, 7, 8)}
        self.assertEqual(p.streaks(days, today), (3, 4))
        # heute noch nichts gemacht -> Streak bis gestern zählt
        days.pop(today)
        self.assertEqual(p.streaks(days, today)[0], 2)

    def test_hour_histogram_splits_hours(self):
        ss = [sess(datetime(2026, 11, 24, 9, 40), 50), sess(datetime(2026, 11, 24, 10, 0), -10)]
        hist = p.hour_histogram(ss, None, datetime(2026, 11, 1))
        self.assertAlmostEqual(hist[9], 20)
        self.assertAlmostEqual(hist[10], 20)

    def test_window_sum_short_history(self):
        today = date(2026, 11, 24)
        days = {today: 60, today - timedelta(days=1): 30}
        self.assertEqual(p.window_sum(days, today, 30), (90, 2))


class TestStorage(TempData):
    def test_append_load_rewrite(self):
        for m in (25, 50, 10):
            self.assertTrue(p.append_session({"start": "2026-11-24T10:00:00", "min": m,
                                              "task": "study"}))
        ss = p.load_sessions()
        self.assertEqual([s["min"] for s in ss], [25, 50, 10])
        self.assertEqual([s["_line"] for s in ss], [0, 1, 2])
        p.rewrite_sessions(lambda lines: lines[:1] + lines[2:])
        self.assertEqual([s["min"] for s in p.load_sessions()], [25, 10])

    def test_corrupt_lines_are_skipped(self):
        p.SESSIONS_PATH.write_text('{"start": "2026-11-24T10:00:00", "min": 5}\nkaputt\n',
                                   encoding="utf-8")
        self.assertEqual(len(p.load_sessions()), 1)

    def test_current_roundtrip(self):
        p.write_current({"phase": "focus"})
        self.assertEqual(p.read_current(), {"phase": "focus"})
        p.write_current(None)
        self.assertIsNone(p.read_current())

    def test_icon(self):
        path = p.ensure_icon()
        data = path.read_bytes()
        self.assertEqual(data[:4], b"\x00\x00\x01\x00")
        self.assertIn(b"\x89PNG", data)


def _display_available():
    try:
        r = p.tk.Tk()
        r.destroy()
        return True
    except p.tk.TclError:
        return False


@unittest.skipUnless(_display_available(), "kein Display")
class TestApp(TempData):
    def make_app(self, **cfg):
        cfg.setdefault("sound", False)
        p.CONFIG_PATH.write_text(json.dumps(cfg), encoding="utf-8")
        app = p.PomodoroApp()
        app.start_watchdog = lambda: None
        self.addCleanup(lambda: app.winfo_exists() and app.destroy())
        app.update()
        return app

    def test_flow_focus_choice_break(self):
        app = self.make_app(focus=25)
        app.act("start")
        self.assertEqual((app.phase, app.running), ("focus", True))
        self.assertIsNotNone(p.read_current())
        app.deadline = p.clock() - 1          # Zeit ist um
        app._tick()
        self.assertEqual(app.phase, "choice")
        self.assertIsNone(p.read_current())
        self.assertEqual(p.load_sessions()[0]["min"], 25)
        app.act("break")
        app.act("long")
        self.assertEqual((app.phase, app.break_kind), ("break", "long"))
        app.act("skip")
        self.assertEqual(app.phase, "idle")

    def test_stop_counts_real_time(self):
        app = self.make_app(focus=25)
        app.act("start")
        app.deadline -= 5 * 60                 # 5 Minuten gelernt
        app.act("stop")
        self.assertAlmostEqual(p.load_sessions()[0]["min"], 5, places=1)
        self.assertGreater(app.today_minutes(), 4.9)

    def test_recover_running_focus(self):
        p.write_current({"phase": "focus", "task": "study", "start": datetime.now().isoformat(),
                         "duration": 1500, "running": True, "remaining": 1200,
                         "clock": p.clock() - 60, "wall": time.time() - 60})
        app = self.make_app()
        self.assertEqual(app.phase, "focus")
        self.assertAlmostEqual(app.remaining, 1140, delta=2)   # Ausfallzeit zählt mit

    def test_recover_after_reboot_counts_until_checkpoint(self):
        p.write_current({"phase": "focus", "task": "work",
                         "start": (datetime.now() - timedelta(hours=1)).isoformat(),
                         "duration": 1500, "running": True, "remaining": 900,
                         "clock": p.clock() + 10_000, "wall": time.time() - 3600})
        app = self.make_app()
        self.assertEqual(app.phase, "idle")
        s = p.load_sessions()[0]
        self.assertEqual((s["min"], s["task"], s.get("recovered")), (10, "work", True))

    def test_recover_expired_focus_counts_fully(self):
        p.write_current({"phase": "focus", "task": "study", "start": datetime.now().isoformat(),
                         "duration": 1500, "running": True, "remaining": 30,
                         "clock": p.clock() - 120, "wall": time.time() - 120})
        app = self.make_app()
        self.assertEqual(app.phase, "choice")
        self.assertEqual(p.load_sessions()[0]["min"], 25)

    def test_manual_entry_tab(self):
        app = self.make_app()
        app.open_edit("history")
        ew = app.edit_window
        ew.update()
        p.append_session({"start": datetime.now().replace(hour=8).isoformat(timespec="seconds"),
                          "min": 45.0, "task": "study", "done": True, "manual": True})
        app.refresh_today()
        self.assertEqual(app.today_minutes(), 45)
        entry = p.load_sessions()[0]
        ew._delete_entry(entry)
        self.assertEqual(p.load_sessions(), [])
        self.assertEqual(app.today_minutes(), 0)
        ew._undo_delete()
        self.assertEqual(len(p.load_sessions()), 1)
        for tab, _ in p.EditWindow.TABS:   # alle Tabs bauen sich fehlerfrei auf
            ew.show_tab(tab)
            ew.update()


if __name__ == "__main__":
    unittest.main()
