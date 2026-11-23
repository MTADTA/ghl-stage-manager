import json, os, shutil, subprocess, sys, time, urllib.request
SERVER, LABEL = sys.argv[1], sys.argv[2]; SC = os.path.dirname(os.path.abspath(__file__)); W = os.path.join(SC, "run-" + LABEL)
shutil.rmtree(W, ignore_errors=True); os.makedirs(W + "/data")
shutil.copytree(os.path.expanduser("~/Library/Application Support/com.jordanrein.GuitarHeroLive/GHL/stage-manager/bg-video"), W + "/data/bg-video")
for p in range(18940, 18999, 3):
    if not subprocess.run(["lsof", "-nP", "-iTCP:%d" % p], capture_output=True).stdout and not subprocess.run(["lsof", "-nP", "-iUDP:%d" % (p+1)], capture_output=True).stdout: break
json.dump({"port": p, "admin_pin": "1", "tunnel": False, "gameplay_bg": {"enabled": True, "template": "brand/gameplay-template.png", "output": W + "/bg-out.png", "qr": {"x": 1400, "y": 360, "size": 336}}}, open(W + "/config.json", "w"))
env = dict(os.environ, GHL_DATA=W + "/data", GHL_CONFIG=W + "/config.json")
srv = subprocess.Popen(["/usr/bin/python3", "-u", SERVER, "--driver", "mod", "--no-game", "--no-tunnel", "--no-display"], cwd=W, env=env, stdout=open(W + "/server.log", "w"), stderr=subprocess.STDOUT)
B = "http://127.0.0.1:%d" % p
def get(path): return json.loads(urllib.request.urlopen(B + path, timeout=10).read())
def post(path, body): return json.loads(urllib.request.urlopen(urllib.request.Request(B + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-Pin": "1"}), timeout=10).read())
try:
    for _ in range(100):
        try: get("/api/ping"); break
        except Exception: time.sleep(0.1)
    songs = [s["id"] for s in get("/api/public")["songs"]]
    for i, n in enumerate(["Ann", "Bob", "Cat", "Dee"]): post("/api/signup", {"name": n, "songId": songs[i + 1], "device": "d%d" % i})
    time.sleep(10)
    for t in range(2):
        r = post("/api/display/press", {"button": "green"}); assert r["action"] == "called", r; t0 = time.time()
        while True:  # wait until the start screen is published (gate 1), as the player would see it
            time.sleep(0.05)
            if time.time() - t0 > 0.5: break
        a = time.time(); r = post("/api/display/press", {"button": "green"})
        print(LABEL, "trial", t, "green at +%.2f s after call-up -> %s after %.2f s" % (a - t0, r["action"], time.time() - a))
        if r["action"] == "start": post("/api/admin", {"action": "finish", "score": "100"})
        else: post("/api/admin", {"action": "back_to_queue"})
        time.sleep(6)
finally:
    srv.terminate()
    try: srv.wait(10)
    except Exception: srv.kill()
