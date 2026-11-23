"""Public link: cloudflared stopping (or not connecting at launch) must not end the public link for the night.
Uses a fake cloudflared script, no network."""
import json, os, stat, sys, tempfile, time, unittest, importlib.util
from pathlib import Path
SERVER = sys.argv.pop(1) if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / "stage-manager/server.py")
tmp = tempfile.mkdtemp(prefix="ghl-tunnel-test-")
Path(tmp, "config.json").write_text(json.dumps({"port": 8790, "admin_pin": "1", "tunnel": False}))
os.environ.update(GHL_DATA=tmp, GHL_CONFIG=str(Path(tmp, "config.json")))
sys.argv = ["server.py", "--driver", "mod", "--no-game"]
spec = importlib.util.spec_from_file_location("srv", SERVER); srv = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)
sys.argv = ["check_tunnel.py"]


def fake_cloudflared(script):
    p = Path(tmp, "cloudflared-%d" % time.time_ns())
    p.write_text("#!/bin/bash\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class TunnelTest(unittest.TestCase):
    def setUp(self):
        srv.RUNTIME["tunnel_url"] = None
        srv.RUNTIME["lan_url"] = "http://10.0.0.2:8790"
        self.t = None

    def tearDown(self):
        if self.t:
            self.t.terminate()

    def test_a_dropped_link_comes_back_with_a_new_url(self):
        n = Path(tmp, "runs"); n.write_text("0")
        exe = fake_cloudflared('c=$(( $(cat "%s") + 1 )); echo $c > "%s"\n'
                               'echo "INF |  https://run-$c.trycloudflare.com  |"\nsleep 0.4\n' % (n, n))
        self.t = srv.Tunnel(exe, 8790, retry=0.1)
        self.assertTrue(wait_for(lambda: srv.RUNTIME["tunnel_url"] == "https://run-1.trycloudflare.com"))
        self.assertTrue(wait_for(lambda: srv.RUNTIME["tunnel_url"] is None))          # dropped: QR falls back to Wi-Fi
        self.assertTrue(wait_for(lambda: srv.RUNTIME["tunnel_url"] == "https://run-2.trycloudflare.com"))

    def test_no_internet_at_launch_keeps_trying(self):
        n = Path(tmp, "tries"); n.write_text("0")
        exe = fake_cloudflared('c=$(( $(cat "%s") + 1 )); echo $c > "%s"\n'
                               'if [ $c -lt 3 ]; then echo "ERR failed to request quick Tunnel"; exit 1; fi\n'
                               'echo "https://late.trycloudflare.com"; sleep 5\n' % (n, n))
        self.t = srv.Tunnel(exe, 8790, retry=0.1)
        self.assertTrue(wait_for(lambda: srv.RUNTIME["tunnel_url"] == "https://late.trycloudflare.com", 8))
        self.assertEqual(n.read_text().strip(), "3")

    def test_stopping_ends_cloudflared_and_does_not_restart_it(self):
        exe = fake_cloudflared('echo "https://stay.trycloudflare.com"; sleep 30\n')
        self.t = srv.Tunnel(exe, 8790, retry=0.1)
        self.assertTrue(wait_for(lambda: srv.RUNTIME["tunnel_url"] == "https://stay.trycloudflare.com"))
        proc = self.t.proc
        self.t.terminate()
        self.assertTrue(wait_for(lambda: proc.poll() is not None))
        time.sleep(0.5)
        self.assertIs(self.t.proc, proc)                                                 # no new cloudflared


if __name__ == "__main__":
    unittest.main(verbosity=1)
