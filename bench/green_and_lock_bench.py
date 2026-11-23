"""Sandbox before/after: green right after a call-up, and LOCK stalls from Stage Control polls.
usage: run.py SERVER_PY LABEL"""
import json, os, shutil, socket, subprocess, sys, threading, time, urllib.request, statistics
SERVER, LABEL = sys.argv[1], sys.argv[2]
SC = os.path.dirname(os.path.abspath(__file__))
W = os.path.join(SC, LABEL); shutil.rmtree(W, ignore_errors=True); os.makedirs(W + "/data")
LIVE = os.path.expanduser("~/Library/Application Support/com.jordanrein.GuitarHeroLive/GHL/stage-manager/bg-video")
shutil.copytree(LIVE, W + "/data/bg-video")   # (a read-only copy: the server reuses it, no 14-min render)
def free():
    for p in range(18820, 18999, 3):
        if subprocess.run(["lsof", "-nP", "-iTCP:%d" % p], capture_output=True).stdout: continue
        if subprocess.run(["lsof", "-nP", "-iUDP:%d" % (p + 1)], capture_output=True).stdout: continue
        return p
PORT = free()
cfg = {"port": PORT, "admin_pin": "1", "tunnel": False,
       "gameplay_bg": {"enabled": True, "template": "brand/gameplay-template.png", "output": W + "/bg-out.png",
                       "qr": {"x": 1400, "y": 360, "size": 336}}}
json.dump(cfg, open(W + "/config.json", "w"))
env = dict(os.environ, GHL_DATA=W + "/data", GHL_CONFIG=W + "/config.json")
srv = subprocess.Popen(["/usr/bin/python3", "-u", SERVER, "--driver", "mod", "--no-game", "--no-tunnel", "--no-display"],
                       cwd=W, env=env, stdout=open(W + "/server.log", "w"), stderr=subprocess.STDOUT)
B = "http://127.0.0.1:%d" % PORT
def get(path, pin=False):
    r = urllib.request.Request(B + path, headers={"X-Pin": "1"} if pin else {})
    return json.loads(urllib.request.urlopen(r, timeout=10).read())
def post(path, body, pin=False):
    h = {"Content-Type": "application/json"}
    if pin: h["X-Pin"] = "1"
    r = urllib.request.Request(B + path, data=json.dumps(body).encode(), headers=h)
    return json.loads(urllib.request.urlopen(r, timeout=10).read())
stop = False
try:
    for _ in range(100):
        try: get("/api/ping"); break
        except Exception: time.sleep(0.1)
    def mod():  # the game: polls 4x a second
        while not stop:
            try: get("/api/mod/state?scene=Main%20Menu&joined=1&players=1&version=1&home=0&pads=1&padname=x")
            except Exception: pass
            time.sleep(0.25)
    threading.Thread(target=mod, daemon=True).start()
    songs = [s["id"] for s in get("/api/public")["songs"]][:6]
    for i, n in enumerate(["Ann", "Bob", "Cat", "Dee", "Eve"]):
        post("/api/signup", {"name": n, "songId": songs[i], "device": "d%d" % i})
    time.sleep(8)   # startup stamp + first lobby render settle
    out = {"label": LABEL}
    # (1) call up + green at once, 3 times (each: call queue[0], then green until the song starts)
    trials = []
    for t in range(3):
        r = post("/api/display/press", {"button": "green"}); assert r.get("action") == "called", r
        t0 = time.time(); waits = 0
        time.sleep(0.6)   # the player sees their start screen (published), then presses green
        t1 = time.time()
        while True:
            r = post("/api/display/press", {"button": "green"})
            if r.get("action") == "start": break
            waits += 1; time.sleep(0.1)
            if time.time() - t1 > 12: break
        bgf = W + "/bg-out.png"
        stamped = os.path.exists(bgf) and os.path.getmtime(bgf) > t0
        trials.append({"dropped_greens": waits, "green_to_start_s": round(time.time() - t1, 2), "result": r.get("action"),
                       "stamp_done_before_start": stamped,
                       "stamp_s_after_callup": round(os.path.getmtime(bgf) - t0, 2) if stamped else None})
        post("/api/admin", {"action": "finish", "score": "1000"}, pin=True)
        time.sleep(4)
    out["green_after_callup"] = trials
    # (2) LOCK stalls: mod/state latency while 10 Stage Control phones poll
    def lat(path, n=60):
        xs = []
        for _ in range(n):
            a = time.perf_counter(); get(path); xs.append((time.perf_counter() - a) * 1000); time.sleep(0.02)
        xs.sort(); return {"p50": round(xs[len(xs)//2], 1), "p95": round(xs[int(len(xs)*.95)], 1), "max": round(xs[-1], 1)}
    out["mod_state_quiet_ms"] = lat("/api/mod/state?scene=Main%20Menu")
    busy = True
    def admin():
        while busy:
            try: get("/api/admin", pin=True)
            except Exception: pass
    ths = [threading.Thread(target=admin, daemon=True) for _ in range(10)]
    [t.start() for t in ths]; time.sleep(1)
    out["mod_state_with_10_admin_ms"] = lat("/api/mod/state?scene=Main%20Menu")
    busy = False; [t.join() for t in ths]
    print(json.dumps(out))
finally:
    stop = True
    srv.terminate()
    try: srv.wait(10)
    except Exception: srv.kill()
