"""Stage port: another app on 127.0.0.1:<port> must not capture Gig Mode's traffic. No game, no tunnel."""
import json, os, socket, sys, tempfile, threading, unittest, importlib.util
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
SERVER = sys.argv.pop(1) if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / "stage-manager/server.py")
tmp = tempfile.mkdtemp(prefix="ghl-port-test-")
Path(tmp, "config.json").write_text(json.dumps({"port": 8790, "admin_pin": "1", "tunnel": False}))
os.environ.update(GHL_DATA=tmp, GHL_CONFIG=str(Path(tmp, "config.json")))
sys.argv = ["server.py", "--driver", "mod", "--no-game"]
spec = importlib.util.spec_from_file_location("srv", SERVER); srv = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)
sys.argv = ["check_port.py"]


def free_base():
    """A port p with p, p+10 and p+20 free (the fallback steps)."""
    for p in range(18700, 19700, 37):
        try:
            for q in (p, p + 10, p + 20):
                s = socket.socket(); s.bind(("0.0.0.0", q)); s.close()
            return p
        except OSError:
            continue
    raise RuntimeError("no free test ports")


class Squatter(BaseHTTPRequestHandler):  # another app's static server: 404 for everything
    def do_GET(self): self.send_error(404, "File not found")
    def log_message(self, *a): pass


class PortTest(unittest.TestCase):
    def setUp(self):
        self.base = free_base()
        srv.CONFIG["port"] = self.base
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            if getattr(s, "_running", False):
                s.shutdown()
            s.server_close()

    def bind(self):
        s, port = srv.bind_stage_port()
        self.servers.append(s)
        return s, port

    def serve(self, s):
        s._running = True
        threading.Thread(target=s.serve_forever, daemon=True).start()

    def test_free_port_is_used_and_written_for_the_game(self):
        s, port = self.bind()
        self.assertEqual(port, self.base)
        self.assertEqual(srv.stage_fx_flag("stage-port").read_text().strip(), str(self.base))

    def test_app_on_loopback_moves_gig_mode_on(self):
        squat = HTTPServer(("127.0.0.1", self.base), Squatter); self.servers.append(squat); self.serve(squat)
        s, port = self.bind()
        self.assertEqual(port, self.base + 10)
        self.assertEqual(srv.stage_fx_flag("stage-port").read_text().strip(), str(self.base + 10))

    def test_gig_mode_already_running_is_not_started_twice(self):
        s, port = self.bind(); self.serve(s)
        self.assertTrue(srv.ghl_on_port(port))
        with self.assertRaises(SystemExit) as e:
            self.bind()
        self.assertIn("already running", str(e.exception))

    def test_other_app_is_not_mistaken_for_gig_mode(self):
        squat = HTTPServer(("127.0.0.1", self.base), Squatter); self.servers.append(squat); self.serve(squat)
        self.assertFalse(srv.ghl_on_port(self.base))


if __name__ == "__main__":
    unittest.main(verbosity=1)
