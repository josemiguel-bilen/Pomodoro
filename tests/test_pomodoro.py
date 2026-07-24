"""Tests fuer den Pomodoro-Timer.

    python3 -m unittest discover -s tests

Die GUI-Tests brauchen ein Display (unter Linux z. B. via Xvfb) und werden
sonst automatisch uebersprungen.
"""

import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pomodoro as p  # noqa: E402


class TestParsing(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(p.parse_minutes("90"), 90)
        self.assertEqual(p.parse_minutes(" 25 "), 25)
        self.assertEqual(p.parse_minutes("1,5"), 1.5)
        self.assertEqual(p.parse_minutes("1:30"), 1.5)
        self.assertAlmostEqual(p.parse_minutes("0:30"), 0.5)

    def test_minutes_invalid(self):
        for bad in ["", "abc", "0", "-5", "2000"]:
            with self.assertRaises(ValueError, msg=bad):
                p.parse_minutes(bad)

    def test_plan_simple(self):
        steps = p.parse_plan("4x90/20")
        self.assertEqual(len(steps), 8)
        self.assertEqual([s["kind"] for s in steps[:4]],
                         ["focus", "break", "focus", "break"])
        self.assertEqual(steps[0]["minutes"], 90)
        self.assertEqual(steps[1]["minutes"], 20)
        self.assertEqual(steps[0]["rounds"], 4)
        self.assertEqual([s["round"] for s in steps], [1, 1, 2, 2, 3, 3, 4, 4])

    def test_plan_variants(self):
        self.assertEqual(len(p.parse_plan("4 X 90 / 20")), 8)
        self.assertEqual(len(p.parse_plan("4*25/5")), 8)
        self.assertEqual(len(p.parse_plan("25/5")), 2)  # ohne Anzahl = 1 Runde

    def test_plan_multiblock(self):
        steps = p.parse_plan("4x50/10 + 1x50/30")
        self.assertEqual(len(steps), 10)
        self.assertEqual(steps[0]["rounds"], 5)
        self.assertEqual(steps[-1]["minutes"], 30)
        self.assertEqual(steps[-1]["round"], 5)
        # Komma und Semikolon trennen genauso
        self.assertEqual(len(p.parse_plan("4x50/10, 1x50/30")), 10)
        self.assertEqual(len(p.parse_plan("4x50/10; 1x50/30")), 10)

    def test_plan_decimal_and_clock(self):
        steps = p.parse_plan("2x1:30/0:30")
        self.assertEqual(steps[0]["minutes"], 1.5)
        self.assertEqual(steps[1]["minutes"], 0.5)

    def test_plan_invalid(self):
        for bad in ["", "   ", "abc", "4x90", "90/", "/20", "0x90/20", "99x1/1"]:
            with self.assertRaises(ValueError, msg=bad):
                p.parse_plan(bad)

    def test_describe_plan(self):
        text = p.describe_plan(p.parse_plan("4x90/20"))
        self.assertIn("4 Runden", text)
        self.assertIn("6 h", text)          # 4 * 90 min Fokus
        self.assertIn("1 h 20 min", text)   # 4 * 20 min Pause


class TestFormatting(unittest.TestCase):
    def test_clock(self):
        self.assertEqual(p.fmt_clock(0), "00:00")
        self.assertEqual(p.fmt_clock(59), "00:59")
        self.assertEqual(p.fmt_clock(60), "01:00")
        self.assertEqual(p.fmt_clock(1500), "25:00")
        self.assertEqual(p.fmt_clock(5400), "1:30:00")
        self.assertEqual(p.fmt_clock(-3), "00:00")
        self.assertEqual(p.fmt_clock(24.2), "00:25")  # laufende Sekunde aufrunden

    def test_minutes_and_duration(self):
        self.assertEqual(p.fmt_minutes(90), "90")
        self.assertEqual(p.fmt_minutes(5.5), "5:30")
        self.assertEqual(p.fmt_duration(45), "45 min")
        self.assertEqual(p.fmt_duration(60), "1 h")
        self.assertEqual(p.fmt_duration(95), "1 h 35 min")


class TestSound(unittest.TestCase):
    def test_wav_header(self):
        wav = p.build_chime(seconds=0.2, rate=8000)
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")
        data_size = struct.unpack("<I", wav[40:44])[0]
        self.assertEqual(data_size, 8000 * 0.2 * 2)
        self.assertEqual(len(wav), 44 + data_size)


def _tk_available():
    try:
        import tkinter

        root = tkinter.Tk()
        root.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(_tk_available(), "kein Display / kein Tk verfuegbar")
class TestApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p.CONFIG_DIR = Path(self.tmp.name)
        p.CONFIG_PATH = Path(self.tmp.name) / "config.json"
        self.app = p.PomodoroApp()
        self.app.cfg["sound"] = False
        self.app.update()

    def tearDown(self):
        self.app.destroy()
        self.tmp.cleanup()

    def test_start_pause_reset(self):
        self.app.apply_mode("focus", 25)
        self.app.start()
        self.assertEqual(self.app.state_name, "running")
        self.app.pause()
        self.assertEqual(self.app.state_name, "paused")
        self.assertGreater(self.app.remaining, 1400)
        self.app.reset()
        self.assertEqual(self.app.state_name, "idle")
        self.assertEqual(self.app.remaining, 25 * 60)

    def test_focus_end_offers_choice(self):
        self.app.set_plan(None)
        self.app.apply_mode("focus", 25)
        self.app.start()
        self.app.finish()
        self.assertEqual(self.app.state_name, "choice")
        self.assertEqual(self.app.focus_done, 1)
        self.assertEqual(self.app.cfg["stats"]["sessions"], 1)

    def test_custom_break_lengths(self):
        self.app.cfg["short"] = 10
        self.app.cfg["long"] = 25
        self.app.set_plan(None)
        self.app.apply_mode("focus", 25)
        self.app.finish()
        self.app.choose_break("short")
        self.assertEqual(self.app.mode, "short")
        self.assertEqual(self.app.duration, 10 * 60)
        self.assertEqual(self.app.state_name, "running")

        self.app.finish()  # Pause vorbei -> zurueck in den Fokus
        self.assertEqual(self.app.mode, "focus")

        self.app.finish()
        self.app.choose_break("long")
        self.assertEqual(self.app.duration, 25 * 60)

    def test_auto_start_after_break_can_be_off(self):
        self.app.cfg["auto_start_after_break"] = False
        self.app.set_plan(None)
        self.app.apply_mode("short", 5)
        self.app.finish()
        self.assertEqual(self.app.mode, "focus")
        self.assertEqual(self.app.state_name, "idle")

    def test_plan_runs_through(self):
        self.app.cfg["plans"].append({"name": "Test", "spec": "2x25/5"})
        self.app.set_plan("Test")
        self.assertEqual(self.app.duration, 25 * 60)
        self.assertEqual(len(self.app.plan_steps), 4)

        self.app.start()
        self.app.finish()                       # Fokus 1 vorbei
        self.assertEqual(self.app.state_name, "choice")
        step = self.app.current_step()
        self.assertEqual((step["kind"], step["minutes"]), ("break", 5))

        self.app.choose_plan_break()            # Plan-Pause uebernehmen
        self.assertEqual(self.app.duration, 5 * 60)
        self.app.finish()                       # Pause vorbei -> Fokus 2
        self.assertEqual(self.app.mode, "focus")
        self.assertEqual(self.app.duration, 25 * 60)
        self.assertEqual(self.app.current_step()["round"], 2)

        self.app.finish()                       # Fokus 2 vorbei
        self.app.choose_focus()                 # Pause ueberspringen -> Plan zu Ende
        self.assertEqual(self.app.plan_index, 0)
        self.assertEqual(self.app.state_name, "idle")
        self.assertEqual(self.app.cfg["stats"]["sessions"], 2)

    def test_plan_90_20(self):
        self.app.set_plan("Deep Work")          # 4x90/20 aus den Vorgaben
        self.assertEqual(self.app.duration, 90 * 60)
        for expected_round in (1, 2, 3, 4):
            self.assertEqual(self.app.current_step()["round"], expected_round)
            self.app.finish()                   # Fokus
            self.assertEqual(self.app.state_name, "choice")
            self.assertEqual(self.app.current_step()["minutes"], 20)
            self.app.choose_plan_break()
            self.app.finish()                   # Pause
        self.assertEqual(self.app.plan_index, 0)  # Plan durch, zurueck auf Anfang
        self.assertEqual(self.app.cfg["stats"]["sessions"], 4)

    def test_long_break_suggested_every_n(self):
        self.app.set_plan(None)
        self.app.cfg["long_every"] = 4
        for _ in range(3):
            self.app.apply_mode("focus", 25)
            self.app.finish()
            self.app.choose_break("short")
            self.app.finish()
        self.assertEqual(self.app.focus_done, 3)
        self.app.apply_mode("focus", 25)
        self.app.finish()
        self.assertEqual(self.app.focus_done % self.app.cfg["long_every"], 0)

    def test_skip_does_not_count(self):
        self.app.set_plan(None)
        self.app.apply_mode("focus", 25)
        self.app.start()
        self.app.skip()
        self.assertEqual(self.app.state_name, "choice")
        self.assertEqual(self.app.cfg["stats"]["sessions"], 0)

    def test_config_roundtrip(self):
        self.app.cfg["focus"] = 42
        p.save_config(self.app.cfg)
        self.assertEqual(p.load_config()["focus"], 42)

    def test_settings_window_applies(self):
        win = p.SettingsWindow(self.app)
        win.e_focus.delete(0, "end")
        win.e_focus.insert(0, "1:30")
        win.e_short.delete(0, "end")
        win.e_short.insert(0, "10")
        win._apply()
        self.assertEqual(self.app.cfg["focus"], 1.5)
        self.assertEqual(self.app.cfg["short"], 10)

    def test_settings_window_rejects_bad_input(self):
        win = p.SettingsWindow(self.app)
        win.e_focus.delete(0, "end")
        win.e_focus.insert(0, "kaese")
        win._apply()
        self.assertNotEqual(self.app.cfg["focus"], "kaese")
        self.assertTrue(win.error.cget("text"))
        win.destroy()

    def test_settings_saves_plan(self):
        win = p.SettingsWindow(self.app)
        win.e_name.insert(0, "Mein Plan")
        win.e_spec.insert(0, "3x45/15")
        win._save_plan()
        names = [pl["name"] for pl in self.app.cfg["plans"]]
        self.assertIn("Mein Plan", names)
        win._update_preview()
        self.assertIn("3 Runden", win.preview.cget("text"))
        win._delete_plan()
        self.assertNotIn("Mein Plan", [pl["name"] for pl in self.app.cfg["plans"]])
        win.destroy()


if __name__ == "__main__":
    unittest.main()
