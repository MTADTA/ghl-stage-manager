"""Lights: OSC to a fake MaestroDMX on localhost, guitar commands to a recorder. No hardware, no game."""
import json, os, socket, sys, tempfile, threading, time, unittest, importlib.util
from pathlib import Path
SERVER = sys.argv.pop(1) if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / "stage-manager/server.py")
tmp = tempfile.mkdtemp(prefix="ghl-lights-test-")
Path(tmp, "config.json").write_text(json.dumps({"port": 8790, "admin_pin": "1", "tunnel": False}))
os.environ.update(GHL_DATA=tmp, GHL_CONFIG=str(Path(tmp, "config.json")))
sys.argv = ["server.py", "--driver", "mod", "--no-game"]
spec = importlib.util.spec_from_file_location("srv", SERVER); srv = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)
sys.argv = ["check_lights.py"]


class Rec:
    def __init__(self): self.lines = []
    def write(self, s): self.lines.append(s.strip())
    def flush(self): pass


def ev(**f):
    base = {"sp": "0", "fill": "0", "mult": "1", "solo": "0", "combo": "0", "hit": "0"}
    base.update({k: str(v) for k, v in f.items()})
    return base


class LightsTest(unittest.TestCase):
    def setUp(self):
        self.fake = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.fake.bind(("127.0.0.1", 0)); self.fake.settimeout(0.6)
        srv.CONFIG["lights"] = {"guitar": True, "maestro": True, "maestro_host": "127.0.0.1", "maestro_port": self.fake.getsockname()[1],
                                "strobe": True, "room": "show", "sp_look": "blinder"}
        srv.RUNTIME["scene"] = "Main Menu"
        self.L = srv.Lights()
        self.L.speed = 0.05
        self.L.maestro_addr, self.L.maestro_at = ("127.0.0.1", self.fake.getsockname()[1]), time.time()
        self.rec = Rec()
        self.L.helper = type("H", (), {"stdin": self.rec, "poll": lambda s: None})()
        self.L.song_start()
        self.rec.lines.clear()

    def tearDown(self): self.fake.close()

    def osc(self):
        msgs = []
        try:
            while True: msgs.append(self.fake.recv(512))
        except socket.timeout:
            return msgs

    def hits(self):
        return [m.split(b"\0")[0].decode() for m in self.osc() if b",T" in m]  # the "on" halves only

    def test_osc_encoding(self):
        self.assertEqual(srv.osc_message("/triggers/blinder", True), b"/triggers/blinder\0\0\0,T\0\0")
        self.assertEqual(srv.osc_message("/global/brightness", 0.5), b"/global/brightness\0\0,f\0\0" + b"\x3f\x00\x00\x00")

    def test_star_power_lights_guitar_and_hits_room_once(self):
        self.L.game(ev(sp=1, fill=200, mult=8, hit=5, combo=5))
        self.L.game(ev(sp=1, fill=180, mult=8, hit=6, combo=6))          # still on: no second hit
        self.assertIn("9 1", self.rec.lines); self.assertIn("10 8", self.rec.lines)
        self.assertEqual(self.rec.lines.count("9 1"), 1)
        time.sleep(0.8)                                                        # after the ignition: the meter and blue
        self.assertIn("8 180", self.rec.lines); self.assertIn("rgb 30 120 255", self.rec.lines)
        on = self.hits()
        self.assertEqual(on, ["/triggers/blinder"])                        # (the first note is within the 3 s spacing)

    def test_the_room_only_speaks_on_good_moments_and_at_its_level(self):
        self.L.game(ev(hit=1, combo=1)); self.assertEqual(self.hits(), ["/triggers/blinder"])   # first note (show level)
        self.L.accent_at = 0
        self.L.game(ev(hit=2, combo=0, miss=1)); self.assertEqual(self.hits(), [])               # a miss: never
        self.assertIn("12 1", self.rec.lines); self.assertIn("rgb 255 20 20", self.rec.lines)      # ...but the guitar shows it
        srv.CONFIG["lights"]["room"] = "subtle"
        self.L.song_start(); self.L.game(ev(hit=1, combo=1)); self.assertEqual(self.hits(), [])  # first note is not subtle
        self.L.game(ev(hit=2, combo=2, sp=1)); self.assertEqual(self.hits(), ["/triggers/blinder"])

    def test_streaks_solos_and_spacing(self):
        self.L.game(ev(hit=99, combo=99)); self.hits(); self.L.accent_at = 0                        # (the first-note pop)
        self.L.game(ev(hit=100, combo=100)); self.assertEqual(self.hits(), ["/triggers/blinder"])  # 100-note streak
        self.assertIn("32 255", self.rec.lines)                                                      # the sparkle
        self.L.game(ev(hit=150, combo=150)); self.assertEqual(self.hits(), [])                       # 150: nothing, 200 would
        self.L.accent_at = 0
        self.L.game(ev(hit=160, combo=160, solo=1)); self.assertEqual(self.hits(), ["/triggers/blinder"])  # solo in
        self.L.accent_at = 0
        self.L.game(ev(hit=170, combo=170, solo=0)); time.sleep(0.1)
        self.assertEqual(self.hits(), ["/triggers/blinder", "/triggers/blinder"])                   # clean solo: ba-dum
        self.L.accent_at = 0
        self.L.game(ev(hit=180, combo=180, solo=1)); self.hits()
        self.L.game(ev(hit=181, combo=0, miss=1, solo=1)); self.L.accent_at = 0
        self.L.game(ev(hit=182, combo=1, solo=0)); self.assertEqual(self.hits(), [])                 # missed in the solo: no bow

    def test_per_song_budget(self):
        for k in range(1, 20):   # 19 streak milestones with the spacing reset each time
            self.L.accent_at = 0
            self.L.game(ev(hit=k * 100, combo=k * 100))
        self.assertEqual(len(self.hits()), 9)      # show: 9 hits per song (the first note + 8 milestones), then quiet
        self.L.accent_at = 0
        self.L.game(ev(hit=2000, combo=2000, beat=200, beats=200))          # the finale is never budgeted out
        self.assertEqual(self.hits(), ["/triggers/blinder"])

    def test_finale_pops_on_the_last_beats_only_while_notes_still_come(self):
        for b in range(190, 201):
            self.L.game(ev(hit=b, combo=b, beat=b, beats=200))
        self.assertEqual(len(self.hits()), 1 + 3)                           # first note + beats 198, 199, 200
        self.L.song_start()
        self.L.game(ev(hit=10, combo=10, beat=100, beats=200)); self.hits()
        self.L.song["hit_at"] = time.time() - 30                            # a long outro: no notes for 30 s
        for b in range(196, 201):
            self.L.game(ev(hit=10, combo=10, beat=b, beats=200))
        self.assertEqual(self.hits(), [])

    def test_song_over_five_stars_new_top_and_the_only_strobe(self):
        self.L.song_over(True, 5); time.sleep(0.1)
        on = self.hits()
        self.assertEqual(on.count("/triggers/blinder"), 2); self.assertEqual(on.count("/triggers/strobe"), 1)
        self.assertEqual(self.L.scene, "bow")
        self.L.song_over(True, 5); self.assertEqual(self.hits(), [])         # once per song
        srv.CONFIG["lights"]["strobe"] = False
        self.L.song_start(); self.L.song_over(True, 3); self.assertEqual(self.hits(), ["/triggers/blinder"])  # vetoed strobe
        self.assertEqual(self.L.scene, "bow")
        self.L.song_start(); self.L.song_over(False, 4); self.assertEqual(self.L.scene, "rest")

    def test_star_power_as_maestro_effect(self):
        srv.CONFIG["lights"]["sp_look"] = "effect"
        self.L.game(ev(sp=1, fill=200, mult=8, hit=5, combo=5))
        self.assertEqual(self.osc()[0], srv.osc_message("/triggers/effect", True))
        self.L.game(ev(sp=0, fill=0, mult=4, hit=30, combo=30))
        self.assertIn(srv.osc_message("/triggers/effect", False), self.osc())

    def test_guitar_scenes_sleep_wake_and_bow(self):
        self.L.rehearsing = True  # (no one is on stage in this test)
        self.L.set_scene("sleep"); time.sleep(0.9)
        self.assertIn("32 7", self.rec.lines)                                 # the comet (3 LEDs) on its first lap
        rgbs = [l for l in self.rec.lines if l.startswith("rgb ")]
        self.assertGreater(len(rgbs), 6)                                       # the breath: many small steps
        self.assertTrue(all(int(l.split()[1]) <= 80 for l in rgbs))            # ...all of them soft
        self.rec.lines.clear(); self.L.set_scene("wake"); time.sleep(0.7)
        self.assertGreater(len([l for l in self.rec.lines if l.startswith("8 ")]), 2)   # the meter charging
        self.assertIn("rgb 0 220 80", self.rec.lines)
        self.rec.lines.clear(); self.L.set_scene("bow"); time.sleep(0.5)
        self.assertIn("8 255", self.rec.lines); self.assertIn("10 4", self.rec.lines); self.assertIn("rgb 255 190 40", self.rec.lines)

    def test_star_power_ready_throbs_and_ignition_bursts(self):
        self.L.game(ev(hit=20, combo=20, mult=2, fill=160)); time.sleep(0.8)
        fills = [int(l.split()[1]) for l in self.rec.lines if l.startswith("8 ")]
        self.assertGreater(len(set(fills)), 4); self.assertTrue(all(104 <= f <= 160 for f in fills))   # the throb
        self.assertTrue({"32 24", "32 60", "32 126"} & set(self.rec.lines))                            # the ripple
        self.rec.lines.clear()
        self.L.game(ev(hit=30, combo=30, mult=8, fill=160, sp=1)); time.sleep(0.15)
        self.assertTrue({"32 255", "64 255", "96 255", "128 255"} <= set(self.rec.lines))             # ignition: all on
        self.assertIn("rgb 255 255 255", self.rec.lines)
        time.sleep(0.5)
        self.assertIn("32 24", self.rec.lines); self.assertIn("64 0", self.rec.lines)                  # the shockwave, blue only
        self.rec.lines.clear(); time.sleep(0.4)
        self.assertTrue(any(l.startswith("32 ") and l != "32 0" for l in self.rec.lines))              # the comets
        self.L.game(ev(hit=31, combo=31, mult=8, fill=140, sp=1, beat=5, beats=200)); time.sleep(0.1)
        self.assertIn("rgb 255 255 255", self.rec.lines)                                               # white on the beat
        self.rec.lines.clear()
        self.L.game(ev(hit=60, combo=60, mult=4, fill=0, sp=0)); time.sleep(0.15)
        self.assertIn("32 0", self.rec.lines); self.assertIn("rgb 255 100 0", self.rec.lines)         # back to the game

    def test_off_means_silent(self):
        srv.CONFIG["lights"] = {"guitar": False, "maestro": False}
        self.rec.lines.clear()
        self.L.game(ev(sp=1, mult=4, hit=1, combo=1)); self.L.called_up(); self.L.song_over(True, 5)
        time.sleep(0.3)
        self.assertEqual(self.rec.lines, []); self.assertEqual(self.osc(), [])
        self.assertEqual(self.L.status(), "off")

    def test_rehearsal_plays_every_moment_and_refuses_mid_song(self):
        self.assertTrue(self.L.rehearse())
        self.assertFalse(self.L.rehearse())                                  # one at a time
        for _ in range(100):
            if not self.L.rehearsing: break
            time.sleep(0.05)
        self.assertFalse(self.L.rehearsing); self.assertNotIn("lights_note", srv.RUNTIME)
        on = self.hits()
        # called up, first note, Star Power, solo in, solo out x2, streak, finale x3, five stars x2 = 12 pops, 1 strobe
        self.assertEqual(on.count("/triggers/blinder"), 12); self.assertEqual(on.count("/triggers/strobe"), 1)
        self.assertIn("9 1", self.rec.lines)
        self.assertTrue({"rgb 255 190 40", "rgb 120 80 0"} & set(self.rec.lines))   # the victory lap's gold
        srv.RUNTIME["scene"] = "Gameplay"
        self.assertFalse(self.L.rehearse())


if __name__ == "__main__":
    unittest.main(verbosity=1)
