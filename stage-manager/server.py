#!/usr/bin/env python3
"""Guitar Hero Live stage manager.

Runs beside Clone Hero: audience sign-up page, queue, projector display,
phone control panel and nightly scoreboard. It only *reads* the game's files
(Songs folder, scoredata.bin); it never writes to the game install.
Python 3.9 standard library only.
"""
import configparser
import hashlib
import html
import json
import math
import os
import re
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = Path(os.environ.get("GHL_DATA", ROOT / "data"))
CONFIG_PATH = Path(os.environ.get("GHL_CONFIG", ROOT / "config.json"))
STATE_PATH = DATA / "state.json"

DEFAULT_CONFIG = {
    "port": 8765,
    "admin_pin": "",
    "songs_dir": "~/Clone Hero/Songs",
    "scoredata_path": "~/Library/Application Support/com.srylain.CloneHero/scoredata.bin",
    "game_app_name": "Clone Hero",
    "instagram": "jordanrein_",
    "spotify_url": "",
    "show_name": "Guitar Hero Live",
    "tunnel": True,
    "open_display": True,
    "max_queue": 40,
    "results_seconds": 15,
    "green_button": 0,
    "red_button": 1,
    # Gig mode stamps the live sign-up QR onto the gameplay background image
    # (your Canva design) before Clone Hero starts, so the QR is never stale.
    "gameplay_bg": {
        "enabled": True,
        "template": "brand/gameplay-template.png",
        "output": "~/Clone Hero/Custom/Image Backgrounds/SIGN UPS-2.png",
        "qr": {"x": 1400, "y": 360, "size": 336},  # the white box on brand/gameplay-template.png (gameplay-bg.html)
    },
    "launch_game": True,
    # Game-driven lights, off until tested on the hardware (phone: Stage Control). "guitar": the Santroller LEDs of the
    # Revival Kit (tools/santroller_leds); "maestro": accents on the band's MaestroDMX show over OSC (blinder / strobe
    # hits only: it never changes the band's palette or pattern, and never the fog).
    "lights": {"guitar": False, "maestro": False, "maestro_host": "maestro.local", "maestro_port": 7672, "strobe": True,
               "room": "show",        # how much the room lights join in: subtle / show / big (see Lights)
               "sp_look": "blinder"}, # Star Power on the room: a blinder hit, or "effect" = MaestroDMX's own Effect trigger held
    # "chrome": stage screen in a Chrome window next to Clone Hero.
    # "mod": screens drawn inside the Guitar Hero Live copy by GHLMod (controller-driven).
    "driver": "chrome",
    "audience_difficulty": "easy",  # in-game mod: falls back to the nearest difficulty a chart has
    # Held back from sign-ups: tonight's top scorer plays it as the encore (phone panel -> Encore). "" = none.
    "encore_song": "Jump",
    "game_app_path": "",
    # Song auto-load: keystroke steps sent to Clone Hero when the player
    # presses green. Off until calibrated against the real game.
    "autoload": {
        "enabled": False,
        "steps": ["activate", "wait 1.0"],
    },
}

# What each show driver runs. "mod" = the Guitar Hero Live copy with the in-game overlay
# (its own scores folder); "chrome" = the original Clone Hero + stage screen in Chrome.
DRIVERS = {
    "mod": {"driver": "mod", "game_app_name": "Guitar Hero Live",
            "game_app_path": "~/Documents/Guitar Hero Live/Guitar Hero Live.app",
            "scoredata_path": "~/Library/Application Support/com.jordanrein.GuitarHeroLive/scoredata.bin"},
    "chrome": {"driver": "chrome", "game_app_name": "Clone Hero", "game_app_path": "",
               "scoredata_path": "~/Library/Application Support/com.srylain.CloneHero/scoredata.bin"},
}

LOCK = threading.RLock()
BAD_PINS = {}  # wrong-PIN count per client; 8 strikes locks that client out until restart
RUNTIME = {"tunnel_url": None, "lan_url": None, "display_pid": None, "last_auto": None,
           "v": 0, "skip_armed_at": 0, "skip_armed_id": None, "sel_id": None, "frame": 0, "ui": int(time.time()), "mod_seen": 0, "mod_info": {}, "mod_cmds": [], "scene": "",
           # in-game screens: state version each was rendered for, the start-screen version, the background video
           "frame_v": -1, "ui_v": -1, "published_v": -1, "ready_v": 0, "vid": 0, "vid_path": "", "home_path": "", "bg_still": 0, "fx": {}}
FRAME_LOCK = threading.Lock()


# ---------------------------------------------------------------- config/state

def load_config():
    if os.environ.get("GHL_CONFIG") and not CONFIG_PATH.exists() and "--embedded" not in sys.argv:
        sys.exit("!! GHL_CONFIG points to a missing file (%s); refusing to start with defaults." % CONFIG_PATH)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    elif os.environ.get("GHL_CONFIG"):
        # started by the game on a fresh Mac (or after the settings folder was cleared): nothing writes this file
        # beforehand, so make it here (defaults + a new PIN) instead of leaving GIG on "Setting up the stage" forever
        print("== no settings file yet; created %s with the defaults" % CONFIG_PATH)
        save_json(CONFIG_PATH, cfg)
    if not cfg.get("admin_pin"):
        cfg["admin_pin"] = "%06d" % secrets.randbelow(1000000)
        save_json(CONFIG_PATH, cfg)
    return cfg


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, path)


def fresh_state():
    return {"signups_open": True, "queue": [], "current": None, "done": [],
            "skipped": [], "started": time.strftime("%Y-%m-%d %H:%M")}


def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except ValueError:
            shutil.copy(STATE_PATH, DATA / ("state-corrupt-%d.json" % time.time()))
    return fresh_state()


CONFIG = load_config()
if "--driver" in sys.argv:
    CONFIG.update(DRIVERS[sys.argv[sys.argv.index("--driver") + 1]])
if "--embedded" in sys.argv:
    CONFIG["game_app_path"] = os.environ.get("GHL_APP_PATH", CONFIG["game_app_path"])
    CONFIG["launch_game"] = False
STATE = load_state()


def save_state():
    maybe_encore()
    save_json(STATE_PATH, STATE)
    RUNTIME["v"] += 1


def mod_driver():
    return CONFIG.get("driver") == "mod"


# ---------------------------------------------------------------- songs

def chart_file(folder):
    for name in ("notes.mid", "notes.chart"):
        if (folder / name).exists():
            return folder / name
    return None


DIFFICULTIES = ("easy", "medium", "hard", "expert")


def chart_difficulties(chart):
    """Read guitar chart availability without loading audio or starting the game."""
    found = set()
    try:
        data = chart.read_bytes()
        if chart.suffix.lower() == ".chart":
            text = data.decode("utf-8-sig", errors="replace")
            for level, notes in re.findall(r"\[\s*(Easy|Medium|Hard|Expert)Single\s*\]\s*\{([^}]*)\}", text, re.S | re.I):
                if re.search(r"^\s*\d+\s*=\s*N\s+(?:[0-4]|7)\s+\d+", notes, re.M):
                    found.add(level.lower())
        elif data[:4] == b"MThd":
            def vlq(pos, end):
                value = 0
                for _ in range(4):
                    if pos >= end: raise ValueError("truncated MIDI")
                    b = data[pos]; pos += 1
                    value = (value << 7) | (b & 127)
                    if b < 128: return value, pos
                raise ValueError("invalid MIDI delta")
            pos = 8 + int.from_bytes(data[4:8], "big")
            while pos + 8 <= len(data):
                kind = data[pos:pos + 4]; size = int.from_bytes(data[pos + 4:pos + 8], "big")
                pos += 8; end = pos + size
                if end > len(data): raise ValueError("truncated MIDI track")
                if kind != b"MTrk": pos = end; continue
                track = ""; pitches = set(); status = 0
                while pos < end:
                    _, pos = vlq(pos, end)
                    if pos >= end: break
                    if data[pos] >= 128:
                        event = data[pos]; pos += 1
                        if event < 240: status = event
                    else:
                        event = status
                        if not event: raise ValueError("invalid MIDI running status")
                    if event == 255:
                        meta = data[pos]; pos += 1
                        n, pos = vlq(pos, end)
                        if pos + n > end: raise ValueError("truncated MIDI meta")
                        if meta == 3: track = data[pos:pos + n].decode("latin1").strip().upper()
                        pos += n
                    elif event in (240, 247):
                        n, pos = vlq(pos, end); pos += n; status = 0
                    elif event < 240:
                        n = 1 if event & 240 in (192, 208) else 2
                        if pos + n > end: raise ValueError("truncated MIDI note")
                        if event & 240 == 144 and data[pos + 1] > 0: pitches.add(data[pos])
                        pos += n
                    else: raise ValueError("unsupported MIDI event")
                if track in ("PART GUITAR", "T1 GEMS"):
                    for level, low in zip(DIFFICULTIES, (59, 71, 83, 95)):
                        if any(low <= pitch <= low + 5 for pitch in pitches): found.add(level)
                pos = end
    except (OSError, ValueError, IndexError):
        pass  # Native loader remains the final availability check for unusual charts.
    return [d for d in DIFFICULTIES if d in found]


def song_difficulties(song_id):
    return SONGS.get(song_id, {}).get("difficulties") or list(DIFFICULTIES)


def chosen_difficulty(song_id, wanted=None):
    levels = song_difficulties(song_id)
    wanted = str(wanted or CONFIG.get("audience_difficulty", "easy")).lower()
    if wanted not in DIFFICULTIES: wanted = "easy"
    index = DIFFICULTIES.index(wanted)
    return next((d for d in DIFFICULTIES[index:] if d in levels), levels[-1])


def scan_songs():
    songs = {}
    root = Path(os.path.expanduser(CONFIG["songs_dir"]))
    if not root.is_dir():
        print("!! Songs folder not found:", root)
        return songs
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        chart = chart_file(folder)
        if not chart:
            continue
        ini = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            ini.read(folder / "song.ini", encoding="utf-8-sig")
        except Exception:
            pass
        sec = next((ini[s] for s in ini.sections() if s.lower() == "song"), {})
        # Clone Hero keys its score file by the MD5 of the chart file.
        sid = hashlib.md5(chart.read_bytes()).hexdigest()
        art = next((folder / n for n in ("album.jpg", "album.png", "album.jpeg")
                    if (folder / n).exists()), None)
        songs[sid] = {
            "id": sid,
            "name": strip_tags(sec.get("name", folder.name)),
            "artist": strip_tags(sec.get("artist", "")),
            "length": int(sec.get("song_length", 0) or 0),
            "folder": str(folder),
            "art": str(art) if art else None,
            "difficulties": chart_difficulties(chart),
        }
    return songs


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


SONGS = scan_songs()
ENCORE_ID = next((sid for sid, s in SONGS.items() if s["name"].lower() == str(CONFIG.get("encore_song") or "").strip().lower()), None)


# ---------------------------------------------------------------- scoredata.bin

def read_scoredata():
    """Parse Clone Hero's scoredata.bin -> {chart_md5: {plays, best:{score,stars,percent}}}.

    Layout (little endian): int32 version, int32 count, then per song:
    16-byte chart md5, u8 instrument count, u8 play count, and 18 bytes per
    instrument: u16 instrument, 2 bytes, u8 difficulty, u16 hit, u16 total,
    u8 stars, 4 bytes, int32 score.
    """
    path = Path(os.path.expanduser(CONFIG["scoredata_path"]))
    try:
        data = path.read_bytes()
        count = int.from_bytes(data[4:8], "little")
        pos, out = 8, {}
        for _ in range(count):
            h = data[pos:pos + 16].hex()
            n_inst, plays = data[pos + 16], data[pos + 17]
            pos += 18
            best = None
            for _ in range(n_inst):
                b = data[pos:pos + 18]
                pos += 18
                total = int.from_bytes(b[7:9], "little") or 1
                entry = {
                    "instrument": int.from_bytes(b[0:2], "little"),
                    "difficulty": b[4],
                    "percent": round(100 * int.from_bytes(b[5:7], "little") / total),
                    "stars": b[9],
                    "score": int.from_bytes(b[14:18], "little"),
                }
                if best is None or entry["score"] > best["score"]:
                    best = entry
            out[h] = {"plays": plays, "best": best}
        if pos != len(data):
            print("!! scoredata.bin layout unexpected; score auto-detect disabled")
            return None
        return out
    except FileNotFoundError:
        return {}
    except Exception as e:
        print("!! could not read scoredata.bin:", e)
        return None


def refresh_turn_score(entry, scores, exact_play=False):
    """Refresh this turn from its pre-song snapshot, including a later score-file write."""
    if entry.get("native"):
        # The game's own end-of-song numbers for this play (see mod_result): the score file can't improve on them.
        if entry.get("result") == entry["native"]:
            return False
        entry["result"] = dict(entry["native"])
        return True
    snap = entry.get("snapshot")
    record = (scores or {}).get(entry["songId"]) if scores is not None else None
    if snap is None or not record or record["plays"] <= snap["plays"]:
        return False
    # A completed turn may only claim its own play, never a later player of that chart.
    if exact_play and record["plays"] != snap["plays"] + 1:
        return False
    before = snap["best"]["score"] if snap.get("best") else -1
    best = record.get("best") or {}
    if best.get("score", -1) > before:
        result = {"score": best["score"], "stars": best.get("stars"),
                  "percent": best.get("percent"), "auto": True}
    else:
        result = {"score": None, "auto": False, "best": before if before >= 0 else None}
    previous = entry.get("result") or {}
    # An incomplete/replaced file must not erase a score already captured for this play.
    if previous.get("auto") and (result.get("score") is None or previous["score"] > result["score"]):
        result = previous
    entry["result"] = result
    return True


def score_watcher():
    """Collect scores and recover saved finished turns; briefly reconcile delayed writes."""
    while True:
        time.sleep(1)
        try:
            score_pass()
        except Exception as e:
            print("!! score watcher:", e)
            time.sleep(1)


def score_pass():
    """One pass of score_watcher (its own function so one bad pass can't end score capture for the night)."""
    with LOCK:
        stamp = time.time()
        cur = STATE["current"]
        # Unlike an in-memory Timer, this deadline also survives a server restart.
        if (cur and cur["phase"] == "finished"
                and stamp - cur.get("finishedAt", stamp) >= CONFIG["results_seconds"]
                and (not mod_driver() or RUNTIME["scene"] != "Gameplay")):
            finish_turn_now(cur)
        cur = STATE["current"]
        pending, changed = [], False
        for entry in STATE["done"]:
            until = entry.get("scorePendingUntil")
            if until is not None and until <= stamp:
                entry.pop("scorePendingUntil", None)
                changed = True
            elif until is not None:
                pending.append(entry)
        active = cur and cur["phase"] in ("playing", "finished")
        scores = read_scoredata() if active or pending else None
        if active:
            previous = dict(cur.get("result") or {})
            if refresh_turn_score(cur, scores):
                if cur["phase"] == "playing":
                    cur["phase"] = "finished"
                    cur["finishedAt"] = stamp
                    changed = True
                    print("== song finished:", cur["name"], cur["result"])
                changed = changed or previous != cur["result"]
        for entry in pending:
            previous = dict(entry.get("result") or {})
            if refresh_turn_score(entry, scores, exact_play=True):
                result = entry["result"]
                if result.get("auto"):
                    entry.update(score=result["score"], stars=result.get("stars"),
                                 percent=result.get("percent"), scoreSource="auto")
                changed = changed or previous != result
        if changed:
            save_state()


def auto_finalize(entry_id):
    with LOCK:
        cur = STATE["current"]
        if (cur and cur["id"] == entry_id and cur["phase"] == "finished"
                and (not mod_driver() or RUNTIME["scene"] != "Gameplay")):
            finish_turn_now(cur)
    activate_display()


def same_folder(a, b):
    try:
        return bool(a and b) and os.path.realpath(os.path.expanduser(a)) == os.path.realpath(os.path.expanduser(b))
    except (OSError, ValueError):
        return False


def mod_result(body):
    """The show song that just ended, as the game itself scored it (the mod reads the results screen's numbers).
    Clone Hero only writes scoredata.bin for a new best on that chart, so a repeat song (or a soundcheck play earlier in
    the night) used to leave the turn without a score. Only accepted for the turn on stage, playing that very song."""
    try:
        score = int(body.get("score"))
    except (TypeError, ValueError):
        return {"ok": False, "reason": "no score"}
    if score < 0:
        return {"ok": False, "reason": "no score"}
    folder = str(body.get("folder") or "")
    with LOCK:
        now = time.time()
        cur = STATE["current"]
        target = cur if cur and cur.get("phase") in ("playing", "finished") else None
        if target is None and STATE["done"]:
            last = STATE["done"][-1]  # the turn was already finished (song_ended / the phone): fill in its score
            typed = last.get("score") is not None and last.get("scoreSource") == "manual"  # a score typed on the phone wins
            if now - (last.get("finishedAt") or 0) < 90 and not typed and not last.get("native"):
                target = last
        if target is None:
            return {"ok": False, "reason": "no show turn"}
        if not same_folder((SONGS.get(target["songId"]) or {}).get("folder"), folder):
            return {"ok": False, "reason": "different song"}
        hit, total = int(body.get("hit") or 0), int(body.get("total") or 0)
        native = {"score": score, "stars": int(body.get("stars") or 0),
                  "percent": round(100 * hit / total) if total > 0 else None, "auto": True, "native": True}
        target["native"] = native
        if target is cur:
            target["result"] = dict(native)
            if cur["phase"] == "playing":
                cur["phase"] = "finished"
                cur["finishedAt"] = now
        else:
            target.update(score=score, stars=native["stars"], percent=native["percent"], scoreSource="native")
            target["result"] = dict(native)
            target.pop("scorePendingUntil", None)
        save_state()
        print("== score from the game:", target["name"], score, "(%d stars)" % native["stars"])
        board = leaderboard()  # (this turn may not be on it yet: a new #1 beats everyone who is)
        LIGHTS.song_over((not board or score >= board[0]["score"]) and not target.get("encore"), native["stars"])
        return {"ok": True}


def finish_turn_now(cur):
    """Finish the on-stage turn: take the score Clone Hero saved for it (if any) and bring on the lobby."""
    if cur["phase"] in ("playing", "finished"):
        refresh_turn_score(cur, read_scoredata())
    res = cur.get("result") or {}
    finalize_current(res.get("score"), automatic=True)
    print("== turn finished:", cur["name"], res.get("score") if res.get("score") is not None else "(needs score)")


def song_ended(entry_id):
    """The game left Gameplay (song over or quit). Give Clone Hero a few seconds to save the score, then finish the
    turn, so the lobby is already showing the next player when the results screen hands over."""
    for _ in range(8):
        time.sleep(1)
        with LOCK:
            cur = STATE["current"]
            if not cur or cur["id"] != entry_id:
                return  # already finished (score watcher / phone panel)
            if mod_driver() and RUNTIME["scene"] == "Gameplay":
                return  # a transient scene exit/restart resumed this same turn
            if cur["phase"] == "finished":  # the score watcher found the score
                finish_turn_now(cur)
                return
    with LOCK:
        cur = STATE["current"]
        if (cur and cur["id"] == entry_id and cur["phase"] in ("playing", "finished")
                and (not mod_driver() or RUNTIME["scene"] != "Gameplay")):
            finish_turn_now(cur)


def finalize_current(score, stars=None, automatic=False):
    cur = STATE["current"]
    res = cur.get("result") or {}
    cur.update({
        "score": score,
        "stars": stars if stars is not None else res.get("stars"),
        "percent": res.get("percent"),
        "scoreSource": "auto" if automatic and res.get("auto") and score == res.get("score") else "manual",
        "finishedAt": cur.get("finishedAt") or time.time(),
    })
    cur["phase"] = "done"
    if automatic:
        cur["scorePendingUntil"] = time.time() + 30  # delayed/partial file writes, bounded to this play
    else:
        cur.pop("scorePendingUntil", None)
    STATE["done"].append(cur)
    STATE["current"] = None
    save_state()


# ---------------------------------------------------------------- mac automation

def osa(script):
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
        if r.returncode:
            print("!! osascript:", r.stderr.strip())
        return r.returncode == 0
    except Exception as e:
        print("!! osascript failed:", e)
        return False


def activate_display():
    if mod_driver():
        return  # the overlay lives inside the game
    pid = RUNTIME.get("display_pid")
    if pid:
        if osa('tell application "System Events" to set frontmost of (first process whose unix id is %d) to true' % pid):
            return
    osa('tell application "Google Chrome" to activate')


KEYCODES = {"return": 36, "enter": 36, "escape": 53, "esc": 53, "tab": 48, "space": 49,
            "delete": 51, "left": 123, "right": 124, "down": 125, "up": 126,
            "f1": 122, "f2": 120, "f3": 99, "f4": 118}


def load_song_in_game(song):
    """Bring Clone Hero forward and (if calibrated) run the keystroke steps that open the song."""
    app = CONFIG["game_app_name"]
    al = CONFIG.get("autoload") or {}
    steps = al.get("steps") if al.get("enabled") else ["activate"]
    for step in steps:
        cmd, _, arg = step.partition(" ")
        arg = arg.replace("{song}", song["name"]).replace("{artist}", song["artist"])
        if cmd == "activate":
            osa('tell application "%s" to activate' % app)
        elif cmd == "wait":
            time.sleep(float(arg or 0.5))
        elif cmd == "key":
            name, _, times = arg.partition("*")
            for _ in range(int(times or 1)):
                osa('tell application "System Events" to key code %d' % KEYCODES[name.lower()])
                time.sleep(0.12)
        elif cmd == "type":
            safe = arg.replace("\\", "\\\\").replace('"', '\\"')
            osa('tell application "System Events" to keystroke "%s"' % safe)
        else:
            print("!! unknown autoload step:", step)


def game_process_name():
    path = CONFIG.get("game_app_path")
    return Path(os.path.expanduser(path)).stem if path else CONFIG["game_app_name"]


def game_running():
    return subprocess.run(["pgrep", "-x", game_process_name()], capture_output=True).returncode == 0


_GAME_RUNNING_SEEN = [False, 0.0]


def game_running_cached(max_age=5):
    """game_running() for status labels: Stage Control asks every 2 s, and pgrep under LOCK held up the game's polls
    and guitar presses ~14 ms each time. Call it BEFORE taking LOCK."""
    if time.time() - _GAME_RUNNING_SEEN[1] > max_age:
        _GAME_RUNNING_SEEN[:] = [game_running(), time.time()]
    return _GAME_RUNNING_SEEN[0]


def show_game_running():
    """Is the show app itself (not an old copy) still running? ps by path; if ps fails, say no (the old behaviour)."""
    main = main_game_bundle()
    return main in [str(Path(b).resolve()) for b in running_game_bundles()]


def running_game_bundles():
    """The .app folder of every running game process with our name. Every copy of the app (the snapshots too) has the
    same name and bundle id, so Spotlight/Launchpad can open an OLD copy: its old mod and server bring back the 10/10
    lag (4 s press lock, one Chrome per image) and it re-renders the shared stage videos. Found by path, never name."""
    try:
        out = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    tail = "/Contents/MacOS/" + game_process_name()
    return sorted({line.strip()[:-len(tail)] for line in out.splitlines() if line.strip().endswith(tail)})


def main_game_bundle():
    path = CONFIG.get("game_app_path") or DRIVERS["mod"]["game_app_path"]
    return str(Path(os.path.expanduser(path)).resolve())


def other_game_copies():
    """Running game copies that are not the show app (resolved paths)."""
    main = main_game_bundle()
    return [b for b in running_game_bundles() if str(Path(b).resolve()) != main]


def warn_other_copies():
    others = other_game_copies()
    for b in others:
        print("!! AN OLD COPY OF THE GAME IS OPEN: %s" % b)
        print("!!   it runs the old, laggy show software. Quit it (Cmd-Q) and open %s" % main_game_bundle())
    RUNTIME["old_copy"] = "; ".join(others)
    RUNTIME["old_copies"] = others
    return others


# ---------------------------------------------------------------- queue logic

def taken_song_ids():
    ids = {e["songId"] for e in STATE["queue"]} | {e["songId"] for e in STATE["done"]}
    if STATE["current"]:
        ids.add(STATE["current"]["songId"])
    return ids


def clean_name(s, n):
    s = re.sub(r"[\x00-\x1f<>]", "", str(s or "")).strip()
    return re.sub(r"\s+", " ", s)[:n]


# Block list for audience sign-up names, which are shown on the projector in front of the room.
# Whole words only: legitimate names such as Cassidy, Bass and Scunthorpe
# must not be caught by a substring filter. Separators catch obvious bypasses.
_NAME_TERMS = (
    "fuck", "fucks", "fucker", "fuckers", "fucking", "fucked",
    "motherfucker", "motherfuckers", "motherfucking", "fuckyou", "fuckoff",
    "shit", "shits", "shitty", "shithead", "shitheads", "bullshit",
    "bitch", "bitches", "bitchy", "asshole", "assholes", "cunt", "cunts",
    "dickhead", "dickheads", "cocksucker", "cocksuckers", "slut", "sluts",
    "whore", "whores", "nigger", "niggers", "nigga", "niggas",
    "faggot", "faggots", "retard", "retards", "retarded", "poop",
)
_NAME_PATTERN = re.compile(r"(?<![a-z0-9])(?:" + "|".join(
    r"[^a-z0-9]*".join("(?:" + c + r"|[*#]+)" if c in "aeiou" else re.escape(c)
                       for c in word) for word in _NAME_TERMS
) + r"|f[*#]+(?:c[^a-z0-9]*)?k)(?![a-z0-9])")
_NAME_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a",
                           "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})


def inappropriate_name(name):
    text = "".join(c for c in unicodedata.normalize("NFKD", str(name or "")).casefold()
                   if not unicodedata.combining(c) and unicodedata.category(c) != "Cf")
    return bool(_NAME_PATTERN.search(text) or _NAME_PATTERN.search(text.translate(_NAME_LEET)))


def public_name(name):
    return "Rock Star" if inappropriate_name(name) else name


def make_entry(name, song_id, ig="", device="", difficulty=None, lefty=False):
    song = SONGS[song_id]
    e = {"id": uuid.uuid4().hex[:10], "name": name, "ig": ig, "device": device,
         "songId": song_id, "song": song["name"], "artist": song["artist"],
         "signedUpAt": time.time(), "difficulty": chosen_difficulty(song_id, difficulty)}
    if lefty:
        e["lefty"] = True  # plays left-handed: the game flips the highway for their song only
    return e


def signup(body):
    name = clean_name(body.get("name"), 24)
    ig = clean_name(body.get("ig"), 30).lstrip("@")
    ig = re.sub(r"[^A-Za-z0-9._]", "", ig)
    song_id = str(body.get("songId", ""))
    device = clean_name(body.get("device"), 40)
    with LOCK:
        if not STATE["signups_open"]:
            return {"error": "Sign-ups are closed right now."}
        if not name:
            return {"error": "Please enter your name."}
        if inappropriate_name(name):
            return {"error": "Please use a stage-friendly name."}
        if inappropriate_name(ig):
            return {"error": "Please use a stage-friendly Instagram handle, or leave it blank."}
        if song_id not in SONGS:
            return {"error": "Pick a song."}
        if song_id == ENCORE_ID:
            return {"error": "That one's the encore, saved for tonight's top scorer!", "taken": True}
        if device and any(e.get("device") == device for e in STATE["queue"]):
            return {"error": "You're already in line! Watch the big screen for your name."}
        # one turn per phone per night (a skipped no-show may sign up again; the band can still add walk-ups)
        played = STATE["done"] + ([STATE["current"]] if STATE["current"] else [])
        if device and any(e.get("device") == device for e in played):
            return {"error": "You've already had your turn tonight. Thanks for rocking with us!"}
        if song_id in taken_song_ids():
            return {"error": "Someone just grabbed that song. Pick another!", "taken": True}
        if len(STATE["queue"]) >= CONFIG["max_queue"]:
            return {"error": "The line is full for now. Try again soon!"}
        e = make_entry(name, song_id, ig, device, body.get("difficulty"),
                       body.get("lefty") is True and CONFIG.get("lefty_signup", False))  # (only while the option is on)
        STATE["queue"].append(e)
        save_state()
        print("++ signup:", name, "-", e["song"])
        return {"ok": True, "id": e["id"], "position": len(STATE["queue"]) + (1 if STATE["current"] else 0) - 1,
                "song": e["song"], "artist": e["artist"], "difficulty": e["difficulty"]}


def selected_index():
    """The lobby highlight (moved with strum up/down from the stage). Follows the person, not the slot, so a sign-up
    or removal elsewhere in the line never moves it to someone else; falls back to the front of the line."""
    q = STATE["queue"]
    sel = RUNTIME.get("sel_id")
    return next((i for i, e in enumerate(q) if e["id"] == sel), 0) if q else -1


def call_next(index=0):
    if STATE["current"] or not STATE["queue"]:
        return False
    index = index if 0 <= index < len(STATE["queue"]) else 0
    cur = STATE["queue"].pop(index)
    RUNTIME["sel_id"] = None  # (after their song the lobby highlights the front of the line again)
    cur["difficulty"] = chosen_difficulty(cur["songId"], cur.get("difficulty"))
    cur["phase"] = "ready"
    cur["calledAt"] = time.time()
    STATE["current"] = cur
    save_state()
    RUNTIME["ready_v"] = RUNTIME["v"]  # the in-game start screen must be on screen before green can start the song
    LIGHTS.called_up()
    return True


def go():
    cur = STATE["current"]
    if not cur or cur["phase"] != "ready":
        return False
    scores = read_scoredata()
    cur["snapshot"] = (scores or {}).get(cur["songId"], {"plays": 0, "best": None}) if scores is not None else None
    cur["phase"] = "playing"
    cur["startedAt"] = time.time()
    cur["gameplaySeen"] = False  # set when the game reports the Gameplay scene; until then green can (re)start it
    save_state()
    song = SONGS.get(cur["songId"])
    if song and not mod_driver():
        threading.Thread(target=load_song_in_game, args=(song,), daemon=True).start()
    return True


def skip_next(index=0):
    """Red twice in the lobby: that person isn't here. Their song goes back up for grabs."""
    if STATE["current"] or not STATE["queue"]:
        return False
    index = index if 0 <= index < len(STATE["queue"]) else 0
    e = STATE["queue"].pop(index)
    e["skippedAt"] = time.time()
    STATE["skipped"].append(e)
    if RUNTIME.get("sel_id") == e["id"]:
        RUNTIME["sel_id"] = None
    save_state()
    print("-- skipped:", e["name"], "-", e["song"], "(song reopened)")
    return True


def remove_current():
    """Red twice on the start screen: the called-up player has gone. Off the line, and their song reopens."""
    cur = STATE["current"]
    if not cur or cur.get("phase") != "ready":
        return False
    for k in ("phase", "snapshot", "result", "calledAt", "startedAt"):
        cur.pop(k, None)
    cur["skippedAt"] = time.time()
    STATE["skipped"].append(cur)
    STATE["current"] = None
    save_state()
    print("-- removed from the start screen:", cur["name"], "-", cur["song"], "(song reopened)")
    return True


REMOVE_CONFIRM_S = 8  # red twice within this many seconds, on the same person


def armed_for(entry_id, now):
    return bool(entry_id) and RUNTIME.get("skip_armed_id") == entry_id and now - RUNTIME["skip_armed_at"] < REMOVE_CONFIRM_S


def arm_remove(entry_id, now):
    RUNTIME["skip_armed_at"] = now
    RUNTIME["skip_armed_id"] = entry_id
    RUNTIME["v"] += 1  # the stage screen shows the "press red again" warning


def disarm_remove():
    if RUNTIME.get("skip_armed_id"):
        RUNTIME["v"] += 1  # the warning goes away
    RUNTIME["skip_armed_at"] = 0
    RUNTIME["skip_armed_id"] = None


def press(button):
    """Guitar button from the stage.
    Lobby: strum up/down (or yellow/blue) moves the highlight through the line, green calls the highlighted person up,
    red twice removes them (their song reopens).
    Start screen: green starts the song, red twice removes the player (their song reopens)."""
    cur = STATE["current"]
    now = time.time()
    home = RUNTIME.get("mod_info", {}).get("home") == "1"
    if button in ("up", "down"):
        # Browsing is exempt from the debounce (fast strums are the point) and never starts anything.
        if cur or not STATE["queue"] or home:
            return {"ok": False, "action": "none"}
        q, i = STATE["queue"], selected_index()
        j = max(0, min(len(q) - 1, i + (1 if button == "down" else -1)))
        if j == i:
            return {"ok": False, "action": "none"}  # already at the end of the line
        RUNTIME["sel_id"] = q[j]["id"]
        disarm_remove()
        RUNTIME["v"] += 1
        return {"ok": True, "action": "moved", "index": j}
    if now - RUNTIME.get("last_press", 0) < 0.4:  # debounce double-presses from the stage
        return {"ok": False, "action": "debounced"}
    RUNTIME["last_press"] = now
    if mod_driver() and overlay_mode() == "results":
        if button != "green":
            return {"ok": False, "action": "none"}
        RUNTIME["results_hold_until"] = 0  # card done
        if cur and cur["phase"] in ("playing", "finished"):
            finish_turn_now(cur)
        RUNTIME["v"] += 1
        return {"ok": call_next(selected_index()) if STATE["queue"] else True, "action": "called" if STATE["current"] else "lobby"}
    if button in ("yellow", "blue") and cur and cur["phase"] == "ready" and not home:
        return {"ok": False, "action": "none"}  # choose on the phone; avoid repeated full stage-screen renders
    if button == "blue":
        if RUNTIME.get("scene") != "Main Menu" or not home:
            return {"ok": False, "action": "none"}
        if 0 < now - RUNTIME.get("reset_armed_at", 0) < 8:
            RUNTIME["reset_armed_at"] = 0
            return {**admin_action({"action": "reset_night", "confirm": "RESET"}), "action": "reset"}
        RUNTIME["reset_armed_at"] = now
        return {"ok": True, "action": "reset_armed"}
    RUNTIME["reset_armed_at"] = 0
    if not cur:
        if not STATE["queue"]:
            return {"ok": False, "action": "none"}
        i = selected_index()
        picked = STATE["queue"][i]
        if button == "green":
            disarm_remove()
            return {"ok": call_next(i), "action": "called"}
        if button == "red":
            if picked.get("encore"):
                return {"ok": rotate_encore(False, i), "action": "encore_next"}
            if armed_for(picked["id"], now):
                disarm_remove()
                return {"ok": skip_next(i), "action": "skipped"}
            arm_remove(picked["id"], now)
            return {"ok": True, "action": "skip_armed"}
    elif (cur["phase"] == "playing" and mod_driver() and not cur.get("gameplaySeen")
          and RUNTIME["scene"] != "Gameplay" and button == "green" and now - cur.get("startedAt", 0) > 3):
        # marked as playing but the game never got to the song (e.g. started from the phone): green starts it
        song = SONGS.get(cur["songId"]) or {}
        return {"ok": True, "action": "start", "folder": song.get("folder"), "name": cur["name"],
                "difficulty": chosen_difficulty(cur["songId"], cur.get("difficulty")), "lefty": bool(cur.get("lefty"))}
    elif cur["phase"] == "ready":
        if button == "green":
            # In-game: a second green while the start screen is still rendering would start the song before the
            # player ever saw the how-to. Wait for it (up to 10 s, so a render problem never blocks the show).
            if mod_driver() and RUNTIME["published_v"] < RUNTIME["ready_v"] and now - cur.get("calledAt", 0) < 10:
                return {"ok": False, "action": "wait"}
            # ...and the in-song picture must already say who's next after them (not them), up to 4 s
            if (mod_driver() and RUNTIME.get("bg_watching") and now - cur.get("calledAt", 0) < 4
                    and RUNTIME.get("bg_done_key") != gameplay_bg_key()[0]):
                return {"ok": False, "action": "wait"}
            disarm_remove()
            ok = go()
            song = SONGS.get(cur["songId"]) or {}
            return {"ok": ok, "action": "start", "folder": song.get("folder"), "name": cur["name"],
                "difficulty": chosen_difficulty(cur["songId"], cur.get("difficulty")), "lefty": bool(cur.get("lefty"))}
        if button == "red":
            if cur.get("encore"):
                return {"ok": rotate_encore(True), "action": "encore_next"}
            if armed_for(cur["id"], now):
                disarm_remove()
                return {"ok": remove_current(), "action": "removed"}
            arm_remove(cur["id"], now)
            return {"ok": True, "action": "remove_armed"}
    return {"ok": False, "action": "none"}


def back_to_queue():
    cur = STATE["current"]
    if not cur:
        return False
    for k in ("phase", "snapshot", "result", "calledAt", "startedAt"):
        cur.pop(k, None)
    STATE["queue"].insert(0, cur)
    STATE["current"] = None
    save_state()
    return True


def fresh_scores():
    """Move the game's saved high scores aside (kept in data/scoredata-backups) so every play tonight is a first
    play and gets captured automatically. Returns a note, or "" when there was nothing to move."""
    src = Path(os.path.expanduser(CONFIG["scoredata_path"]))
    files = [f for f in (src, src.with_name("scoredata_backup.bin")) if f.exists()]
    if not files:
        return ""
    dst = DATA / "scoredata-backups" / time.strftime("%Y%m%d-%H%M%S")
    dst.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, dst / f.name)
        f.unlink()
    return "old scores saved in data/scoredata-backups/%s" % dst.name


def stage_fx_flag(name="stage-fx"):
    """Flag files the mod checks at each song start (in its own Application Support folder)."""
    return (DATA / name) if SANDBOX else Path(os.path.expanduser(CONFIG["scoredata_path"])).parent / "GHL" / name


def admin_action(body):
    a = body.get("action")
    with LOCK:
        if a == "call_next":
            ok = call_next()
            if ok:
                activate_display()
            return {"ok": ok}
        if a == "go":
            cur = STATE["current"]
            ok = go() or bool(cur and cur.get("phase") == "playing" and not cur.get("gameplaySeen"))
            song = SONGS.get(cur["songId"]) if cur else None
            if ok and mod_driver() and song:
                RUNTIME["mod_cmds"].append("start:" + song["folder"])  # the game loads it, like green on the guitar
            return {"ok": ok}
        if a == "back_to_queue":
            return {"ok": back_to_queue()}
        if a == "skip_next":
            return {"ok": skip_next()}
        if a == "finish":
            if not STATE["current"] or STATE["current"]["phase"] not in ("playing", "finished"):
                return {"error": "Nobody is playing."}
            score = body.get("score")
            score = int(score) if str(score or "").strip().isdigit() else None
            finalize_current(score, body.get("stars"))
            threading.Thread(target=activate_display, daemon=True).start()
            return {"ok": True}
        if a == "skip":
            cur = STATE["current"]
            if cur:
                cur["skippedAt"] = time.time()
                STATE["skipped"].append(cur)
                STATE["current"] = None
                save_state()
            return {"ok": True}
        if a == "remove":
            before = len(STATE["queue"])
            STATE["queue"] = [e for e in STATE["queue"] if e["id"] != body.get("id")]
            save_state()
            return {"ok": len(STATE["queue"]) < before}
        if a == "move":
            q = STATE["queue"]
            i = next((i for i, e in enumerate(q) if e["id"] == body.get("id")), None)
            j = None if i is None else i + (-1 if body.get("dir") == "up" else 1)
            if i is not None and 0 <= j < len(q):
                q[i], q[j] = q[j], q[i]
                save_state()
            return {"ok": True}
        if a == "add":
            sid = body.get("songId")
            name = clean_name(body.get("name"), 24)
            ig = clean_name(body.get("ig"), 30).lstrip("@")
            if sid not in SONGS or not name:
                return {"error": "Need a name and a song."}
            if inappropriate_name(name):
                return {"error": "Please use a stage-friendly name."}
            if inappropriate_name(ig):
                return {"error": "Please use a stage-friendly Instagram handle, or leave it blank."}
            if sid in taken_song_ids() and not body.get("force"):
                return {"error": "That song is already taken tonight."}
            if sid == ENCORE_ID and not body.get("force"):
                return {"error": "That's the encore song: use the Encore button."}
            STATE["queue"].append(make_entry(name, sid, ig, difficulty=body.get("difficulty")))
            save_state()
            return {"ok": True}
        if a == "set_difficulty":
            entries = STATE["queue"] + ([STATE["current"]] if STATE["current"] else [])
            e = next((e for e in entries if e["id"] == body.get("id")), None)
            if not e or e.get("phase") not in (None, "ready"):
                return {"error": "Difficulty can be changed before the song starts."}
            e["difficulty"] = chosen_difficulty(e["songId"], body.get("difficulty"))
            save_state()
            # Choice travels in the start response: no need to gate green on a new screenshot.
            return {"ok": True, "difficulty": e["difficulty"]}
        if a == "set_score":
            for e in STATE["done"]:
                if e["id"] == body.get("id"):
                    s = str(body.get("score", "")).strip()
                    e["score"] = int(s) if s.isdigit() else None
                    e["scoreSource"] = "manual"
                    e.pop("scorePendingUntil", None)  # an explicit phone correction wins over late writes
                    save_state()
                    return {"ok": True}
            return {"error": "Not found."}
        if a == "delete_done":
            STATE["done"] = [e for e in STATE["done"] if e["id"] != body.get("id")]
            save_state()
            return {"ok": True}
        if a == "encore":
            # End of the night: tonight's top scorer plays the encore. They go to the front of the line, so the lobby
            # announces them and green calls them up as usual.
            if not ENCORE_ID:
                return {"error": "No encore song is set."}
            if any(e.get("encore") for e in STATE["queue"]) or (STATE["current"] or {}).get("encore"):
                return {"error": "The encore is already lined up."}
            board = leaderboard()
            if not board:
                return {"error": "No scores yet, so there's no winner for the encore."}
            win = board[0]
            e = make_entry(win["name"], ENCORE_ID, win.get("ig", ""))
            e.update(encore=True, winnerId=win["id"])
            STATE["encore_started"] = True
            STATE["queue"].insert(0, e)
            save_state()
            print("** encore lined up:", win["name"], "-", e["song"])
            return {"ok": True, "name": win["name"], "song": e["song"]}
        if a == "toggle_signups":
            STATE["signups_open"] = not STATE["signups_open"]
            save_state()
            return {"ok": True}
        if a == "toggle_stage_fx":
            flag = stage_fx_flag()
            if flag.exists(): flag.unlink()
            else:
                flag.parent.mkdir(parents=True, exist_ok=True)
                flag.write_text("enabled\n")
            return {"ok": True, "enabled": flag.exists()}
        if a == "toggle_speakers":  # moving speakers in songs (the stage lights must be on too); applies next song
            flag = stage_fx_flag("stage-fx-speakers")
            if flag.exists(): flag.unlink()
            else:
                flag.parent.mkdir(parents=True, exist_ok=True)
                flag.write_text("enabled\n")
            return {"ok": True, "enabled": flag.exists()}
        if a in ("toggle_guitar_lights", "toggle_maestro"):
            lights = {**DEFAULT_CONFIG["lights"], **(CONFIG.get("lights") or {})}
            key = "guitar" if a == "toggle_guitar_lights" else "maestro"
            lights[key] = not lights[key]
            CONFIG["lights"] = lights
            save_json(CONFIG_PATH, CONFIG)
            RUNTIME["lights_status"] = LIGHTS.status()
            return {"ok": True, "enabled": lights[key]}
        if a == "toggle_lefty_signup":  # the "I play left-handed" box on the sign-up page
            CONFIG["lefty_signup"] = not CONFIG.get("lefty_signup", False)
            save_json(CONFIG_PATH, CONFIG)
            return {"ok": True, "enabled": CONFIG["lefty_signup"]}
        if a == "toggle_results_card":  # the branded results card after show songs (off: Clone Hero's own results)
            CONFIG["results_card"] = not CONFIG.get("results_card", True)
            save_json(CONFIG_PATH, CONFIG)
            return {"ok": True, "enabled": CONFIG["results_card"]}
        if a == "set_room":  # the room-lights dial: off / subtle / show / big
            level = str(body.get("level") or "off")
            if level not in ("off",) + Lights.ORDER:
                return {"error": "bad level"}
            lights = {**DEFAULT_CONFIG["lights"], **(CONFIG.get("lights") or {})}
            lights["maestro"] = level != "off"
            if level != "off":
                lights["room"] = level
            CONFIG["lights"] = lights
            save_json(CONFIG_PATH, CONFIG)
            RUNTIME["lights_status"] = LIGHTS.status()
            return {"ok": True, "level": level}
        if a in ("toggle_strobe", "toggle_sp_look"):
            lights = {**DEFAULT_CONFIG["lights"], **(CONFIG.get("lights") or {})}
            if a == "toggle_strobe":
                lights["strobe"] = not lights["strobe"]
            else:
                lights["sp_look"] = "blinder" if lights.get("sp_look") == "effect" else "effect"
            CONFIG["lights"] = lights
            save_json(CONFIG_PATH, CONFIG)
            return {"ok": True}
        if a in ("test_lights", "rehearse_lights"):
            ok = LIGHTS.rehearse()
            return {"ok": ok, "error": None if ok else "Not during a song (or a rehearsal is already running)."}
        if a == "toggle_overlay":
            RUNTIME["overlay_paused"] = not RUNTIME.get("overlay_paused")
            return {"ok": True}
        if a == "mod_cmd":  # debug: ask the in-game mod to e.g. take a screenshot or simulate a button
            RUNTIME["mod_cmds"].append(str(body.get("cmd", ""))[:200])
            return {"ok": True}
        if a == "show_display":
            threading.Thread(target=activate_display, daemon=True).start()
            return {"ok": True}
        if a == "show_game":
            threading.Thread(target=osa, args=('tell application "%s" to activate' % CONFIG["game_app_name"],),
                             daemon=True).start()
            return {"ok": True}
        if a == "reset_night":
            if body.get("confirm") != "RESET":
                return {"error": 'Type RESET to confirm.'}
            archive = DATA / "archive"
            archive.mkdir(exist_ok=True)
            save_json(archive / ("night-%s.json" % time.strftime("%Y%m%d-%H%M%S")), STATE)
            STATE.clear()
            STATE.update(fresh_state())
            save_state()
            return {"ok": True}
        if a == "fresh_scores":
            # Move Clone Hero's saved high scores aside so every play tonight is
            # a "first play" and gets captured automatically. Reversible.
            if game_running():
                return {"error": "Quit Clone Hero first, then try again."}
            note = fresh_scores()
            return {"ok": True, "note": note or "Already fresh."}
    return {"error": "Unknown action."}


# ---------------------------------------------------------------- views

def leaderboard():
    """Tonight's scores, best first (the encore is a victory lap: it doesn't count)."""
    return sorted((e for e in STATE["done"] if e.get("score") is not None and not e.get("encore")), key=lambda e: -e["score"])


def maybe_encore():
    """Once the closed line is finished, offer the encore to tonight's top scorer."""
    if (not ENCORE_ID or STATE["signups_open"] or STATE["queue"] or STATE["current"]
            or STATE.get("encore_started") or any(e.get("encore") for e in STATE["done"])):
        return False
    board = leaderboard()
    if not board:
        return False
    winner = board[0]
    entry = make_entry(winner["name"], ENCORE_ID, winner.get("ig", ""))
    entry.update(encore=True, winnerId=winner["id"])
    STATE["queue"].append(entry)
    STATE["encore_started"] = True
    return True


def rotate_encore(ready, index=0):
    q = STATE["queue"]
    index = index if 0 <= index < len(q) else 0
    old = STATE["current"] if ready else (q[index] if q else None)
    board = leaderboard()
    if not old or not old.get("encore") or not board:
        return False
    index = next((i for i, e in enumerate(board) if e["id"] == old.get("winnerId") or
                  (not old.get("winnerId") and e["name"] == old["name"])), 0)
    winner = board[(index + 1) % len(board)]
    entry = make_entry(winner["name"], ENCORE_ID, winner.get("ig", ""))
    entry.update(encore=True, winnerId=winner["id"])
    if ready:
        entry.update(phase="ready", calledAt=time.time())
        STATE["current"] = entry
    else:
        q[index] = entry
        if RUNTIME.get("sel_id") == old["id"]:
            RUNTIME["sel_id"] = entry["id"]
    save_state()
    if ready: RUNTIME["ready_v"] = RUNTIME["v"]
    return True


def public_view():
    with LOCK:
        taken = taken_song_ids()
        board = leaderboard()
        cur = STATE["current"]
        return {
            "show": CONFIG["show_name"],
            "signupsOpen": STATE["signups_open"],
            "showFinished": bool(not STATE["signups_open"] and not STATE["queue"] and not cur
                                 and any(e.get("encore") if ENCORE_ID else not e.get("encore")
                                         for e in STATE["done"])),
            "songs": [{"id": s["id"], "name": s["name"], "artist": s["artist"], "taken": s["id"] in taken, "art": bool(s["art"]),
                       "difficulties": song_difficulties(s["id"])}
                      for s in sorted(SONGS.values(), key=lambda s: (s["artist"].lower(), s["name"].lower()))
                      if s["id"] != ENCORE_ID]
                     + ([{"id": "encore", "name": "Mystery encore", "artist": "", "taken": True, "encore": True, "art": False}]
                        if ENCORE_ID else []),
            "queue": [{"id": e["id"], "name": public_name(e["name"]), "ig": "" if inappropriate_name(e.get("ig")) else e.get("ig", ""), "encore": bool(e.get("encore")),
                       "difficulty": chosen_difficulty(e["songId"], e.get("difficulty")),
                       **({"song": "Mystery encore", "artist": "", "songId": ""} if e.get("encore") else
                          {"song": e["song"], "artist": e["artist"], "songId": e["songId"]})} for e in STATE["queue"]],
            "current": None if not cur else {
                "id": cur["id"], "name": public_name(cur["name"]),
                **({"song": "Mystery encore", "artist": "", "songId": ""} if cur.get("encore") and cur["phase"] == "ready" else
                   {"song": cur["song"], "artist": cur["artist"], "songId": cur["songId"]}), "ig": "" if inappropriate_name(cur.get("ig")) else cur.get("ig", ""), "phase": cur["phase"], "result": cur.get("result"),
                "encore": bool(cur.get("encore")), "lefty": bool(cur.get("lefty")),
                "difficulty": chosen_difficulty(cur["songId"], cur.get("difficulty")),
                "difficulties": song_difficulties(cur["songId"])},
            "encore": bool(ENCORE_ID),  # (the song stays a secret)
            "leaderboard": [{"id": e["id"], "name": public_name(e["name"]), "song": e["song"], "score": e["score"],
                             "stars": e.get("stars"), "ig": "" if inappropriate_name(e.get("ig")) else e.get("ig", "")} for e in board],
            "played": len(STATE["done"]),
            "completedIds": [e["id"] for e in STATE["done"]],
            "links": {"instagram": CONFIG["instagram"], "spotify": CONFIG["spotify_url"]},
            "signupUrl": RUNTIME["tunnel_url"] or RUNTIME["lan_url"],
            "buttons": {"green": CONFIG["green_button"], "red": CONFIG["red_button"]},
            "selected": selected_index() if not cur else -1,
            "leftyOption": bool(CONFIG.get("lefty_signup", False)),
            "results": results_card() if mod_driver() and overlay_mode() == "results" else None,
            "hint": remove_hint(),
        }


def remove_hint():
    """The red-twice warning, naming the person the next red removes ("[red]" = the red gem on the stage screen)."""
    now, cur = time.time(), STATE["current"]
    if cur and cur.get("phase") == "ready" and armed_for(cur["id"], now):
        return "Remove %s? Press [red] again" % public_name(cur["name"])
    if not cur and STATE["queue"]:
        picked = STATE["queue"][selected_index()]
        if armed_for(picked["id"], now):
            return "Remove %s? Press [red] again" % public_name(picked["name"])
    return ""


def admin_view():
    running = game_running_cached()
    with LOCK:
        v = public_view()
        v["state"] = STATE
        v["tunnelUrl"] = RUNTIME["tunnel_url"]
        v["lanUrl"] = RUNTIME["lan_url"]
        v["autoload"] = CONFIG["autoload"]
        v["gameRunning"] = running
        v["driver"] = CONFIG.get("driver")
        v["stageFx"] = stage_fx_flag().exists()
        v["speakers"] = stage_fx_flag("stage-fx-speakers").exists()
        v["lights"] = {**DEFAULT_CONFIG["lights"], **(CONFIG.get("lights") or {})}
        v["lightsNote"] = RUNTIME.get("lights_note")
        v["resultsCard"] = CONFIG.get("results_card", True)
        v["leftySignup"] = CONFIG.get("lefty_signup", False)
        v["encoreSong"] = {"song": SONGS[ENCORE_ID]["name"], "artist": SONGS[ENCORE_ID]["artist"]} if ENCORE_ID else None
        v["mod"] = {"connected": time.time() - RUNTIME["mod_seen"] < 5, "paused": bool(RUNTIME.get("overlay_paused")),
                    **RUNTIME["mod_info"]}
        v["health"] = health()
        return v


def health():
    """One glance before doors (and mid-show): is everything that the show needs actually up?"""
    info = RUNTIME["mod_info"]
    url = RUNTIME["tunnel_url"] or RUNTIME["lan_url"]
    try:
        pads = int(info.get("pads", "-1"))
    except ValueError:
        pads = -1
    return {"game": time.time() - RUNTIME["mod_seen"] < 5,
            "guitar": {"count": pads, "name": info.get("padname", ""), "joined": info.get("joined") == "1"},
            "link": "public" if RUNTIME["tunnel_url"] else "wifi" if RUNTIME["lan_url"] else "none",
            "art": bool(RUNTIME.get("bg_stamped")) and RUNTIME.get("bg_url") == url,
            "lights": LIGHTS.status(),
            "oldCopy": RUNTIME.get("old_copy", "")}  # an old copy of the game is open (laggy): quit it


# ---------------------------------------------------------------- in-game overlay frames

def results_card():
    """The show turn that just ended, for the big screen's results card (overlay "results")."""
    cur, rid = STATE["current"], RUNTIME.get("results_for")
    e = cur if cur and cur["id"] == rid else next((x for x in reversed(STATE["done"]) if x["id"] == rid), None)
    if not e:
        return None
    res = e.get("result") or {}
    score = e.get("score") if e.get("phase") == "done" else res.get("score")
    board = leaderboard()
    rank = next((i + 1 for i, x in enumerate(board) if x["id"] == e["id"]), None)
    nxt = STATE["queue"][0] if STATE["queue"] else None
    return {"id": e["id"], "name": public_name(e["name"]), "song": e["song"], "artist": e["artist"], "score": score,
            "stars": e.get("stars") if e.get("phase") == "done" else res.get("stars"), "rank": rank, "of": len(board),
            "encore": bool(e.get("encore")), "next": (public_name(nxt["name"]) if nxt and not nxt.get("encore") else None)}


def overlay_mode():
    if RUNTIME.get("overlay_paused"):
        return "none"
    # Right after a show song: the branded results card for results_seconds (Clone Hero's own results stay underneath)
    if (RUNTIME["scene"] == "EndOfSong" and time.time() < RUNTIME.get("results_hold_until", 0)
            and CONFIG.get("results_card", True) and RUNTIME.get("results_for")):
        return "results"
    cur = STATE["current"]
    if cur:
        return "start" if cur["phase"] == "ready" else "none"  # a song is on (its score is collected right after)
    # Between players. Right after a song its results screen stays up for results_seconds, then the lobby (already
    # showing the next player and the new score) takes over; leaving the results sooner brings the lobby at once.
    if RUNTIME["scene"] == "EndOfSong" and time.time() < RUNTIME.get("results_hold_until", 0):
        return "none"
    return "lobby"


def frame_renderer(port):
    """Re-render the in-game stage screen whenever the state or the sign-up link changes; the mod draws it.
    Each render = 1 still composite (fallback) + the UI layer as two poses that the mod crossfades at 60 fps
    ("rest": the whole UI; "peak": only the accents, i.e. the on-air dot lit and the green prompt glowing).
    Rendered by the capture Chrome that stays open (fast); if that fails, by one Chrome launch per image (slow, the
    original path). A failed render is retried, never skipped, so the lobby can't get stuck without a frame."""
    out = DATA / "frame.png"
    done_key, fails, retry_at, qr_url, fast_retry_at = None, 0, 0.0, None, 0.0
    while True:
        time.sleep(0.05)
        if RUNTIME["scene"] == "Gameplay":
            # never render on the show laptop mid-song: the capture pages are frozen (no timers, no CPU) until the song
            # ends, then thawed at once, so the results card is on screen within a fraction of a second
            if CAPTURE["chrome"] and not CAPTURE["chrome"].dead:
                CAPTURE["chrome"].freeze(True)
            continue
        if CAPTURE["chrome"] and CAPTURE["chrome"].frozen:
            CAPTURE["chrome"].freeze(False)
        try:
            with LOCK:  # the state and its version, taken together: what the capture shows is exactly version v
                v = RUNTIME["v"]
                cur = STATE["current"]
                # a song is starting: the game hides the stage screen ('none'), so a capture now is never shown and
                # only competes with the song load (two full-HD PNG decodes in the game). The results card bumps v.
                if mod_driver() and cur and cur.get("phase") == "playing" and overlay_mode() == "none":
                    continue
                view = public_view()
        except Exception as e:
            print("!! stage-screen state:", e)
            time.sleep(1)
            continue
        url = RUNTIME["tunnel_url"] or RUNTIME["lan_url"]
        # (the QR must follow the public link once the tunnel is up; the results card ends on a timer)
        key = (v, bool(view.get("hint")), url, view.get("results") is not None)
        if key == done_key or time.time() < retry_at or not os.path.exists(CHROME):
            continue
        view["v"] = v
        tmp = DATA / "frame-tmp.png"
        serial = RUNTIME["ui"] + 1
        ui_dir = DATA / "ui" / str(serial)
        try:
            ui_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print("!! stage-screen folder:", e)
            time.sleep(1)
            continue
        started = time.time()
        how = "fast"
        try:
            fx = None
            if time.time() >= fast_retry_at:
                try:
                    if CAPTURE["chrome"] is None:
                        CAPTURE["chrome"] = CaptureChrome(port)
                    fx = CAPTURE["chrome"].capture(view, {"still": tmp, "rest": ui_dir / "00.png", "peak": ui_dir / "01.png"})
                except Exception as e:
                    print("!! fast stage-screen render failed (%s); using one Chrome per image for a minute" % e)
                    fast_retry_at = time.time() + 60
                    if CAPTURE["chrome"]:
                        CAPTURE["chrome"].stop()
                    fx = None
            if fx is None:
                how = "slow"
                fx = {}
                base = "http://127.0.0.1:%d/display?frame=1" % port
                cur = STATE["current"]
                fx_job = None
                if cur and cur.get("phase") == "ready":  # start screen: where the Star Power sparks play in-game
                    fx_job = threading.Thread(target=lambda: fx.update(page_fx(base + "&layer=ui&pose=rest")), daemon=True)
                    fx_job.start()
                render_many([
                    (base, tmp, {"reduced": True, "min_bytes": 20000}),
                    (base + "&layer=ui&pose=rest", ui_dir / "00.png", {"reduced": True, "transparent": True, "min_bytes": 20000}),
                    (base + "&layer=ui&pose=peak", ui_dir / "01.png", {"reduced": True, "transparent": True, "min_bytes": 100}),
                ])
                if fx_job:
                    fx_job.join(50)
            with FRAME_LOCK:
                ok_still = tmp.exists() and tmp.stat().st_size > 20000
                if ok_still:
                    os.replace(tmp, out)
                    RUNTIME["frame"] += 1
                    RUNTIME["frame_v"] = v
                ok_ui = all((ui_dir / ("%02d.png" % i)).exists() for i in range(UI_FRAMES)) and (ui_dir / "00.png").stat().st_size > 20000
                if ok_ui:
                    RUNTIME["fx"] = fx
                    RUNTIME["ui"] = serial
                    RUNTIME["ui_v"] = v
                    RUNTIME["ui_ready"] = True
                    for old in (DATA / "ui").iterdir():  # keep the current + previous set only
                        if old.name.isdigit() and int(old.name) < serial - 1:
                            shutil.rmtree(old, ignore_errors=True)
                else:
                    shutil.rmtree(ui_dir, ignore_errors=True)
            if ok_still and ok_ui:
                done_key, fails = key, 0
                RUNTIME["published_v"] = v
                took = time.time() - started
                if url != qr_url:
                    qr_url = url
                    print("== in-game lobby ready (%.1f s), QR -> %s" % (took, url))
                elif how == "slow" or took > 1.5:
                    print("== stage screen v%d ready in %.1f s (%s)" % (v, took, how))
                continue
            fails += 1
        except Exception as e:
            fails += 1
            print("!! frame render failed:", e)
        retry_at = time.time() + min(10, 2 * fails)
        if fails in (1, 3) or fails % 30 == 0:
            print("!! in-game screen render failed (try %d); retrying" % fails)


class CaptureChrome:
    """One headless Chrome kept open for the in-game stage screens, driven over the DevTools protocol on a pipe
    (--remote-debugging-pipe: fd 3 in, fd 4 out, NUL-separated JSON; no websocket needed).

    Launching Chrome per screenshot cost 4-6 s per screen change on the show laptop, and the guitar waited on every one.
    Here the three capture pages (composite still, UI rest pose, UI peak pose) stay loaded; each state is pushed into
    all three (display-ui.js ghlCapture) and captured, typically well under a second. The pages draw exactly the pushed
    state, so the version a capture reports is the version on it. Any failure: the caller falls back to chrome_shot."""
    PAGES = (("still", "", False), ("rest", "&layer=ui&pose=rest", True), ("peak", "&layer=ui&pose=peak", True))

    def __init__(self, port):
        self.port, self.proc, self.wf, self.sessions = port, None, None, {}
        self.lock = threading.Lock()   # one capture at a time
        self.wlock = threading.Lock()  # writes to the pipe
        self.pending, self.nid, self.dead, self.frozen = {}, 0, True, False
        self.profile = DATA / "capture-chrome-profile"

    def start(self):
        import fcntl
        self.stop()
        shutil.rmtree(self.profile, ignore_errors=True)
        to_r, to_w = os.pipe()
        from_r, from_w = os.pipe()

        def fds():  # (fcntl F_DUPFD first: a pipe end may itself sit on fd 3 or 4)
            r, w = fcntl.fcntl(to_r, fcntl.F_DUPFD, 10), fcntl.fcntl(from_w, fcntl.F_DUPFD, 10)
            os.dup2(r, 3)
            os.dup2(w, 4)
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", "--remote-debugging-pipe", "--hide-scrollbars", "--window-size=1920,1080",
             "--force-prefers-reduced-motion", "--user-data-dir=%s" % self.profile, "--no-first-run",
             "--no-default-browser-check", "--disable-extensions", "--mute-audio",
             # the capture pages are background tabs: without these their timers run ~1 s late (0.7 s per screen)
             "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
             "--disable-backgrounding-occluded-windows", "about:blank"],
            preexec_fn=fds, pass_fds=(3, 4), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(to_r)
        os.close(from_w)
        self.wf = os.fdopen(to_w, "wb", buffering=0)
        self.dead, self.pending = False, {}
        threading.Thread(target=self._reader, args=(os.fdopen(from_r, "rb", buffering=0),), daemon=True,
                         name="capture-chrome").start()
        self.sessions = {}
        for name, extra, transparent in self.PAGES:
            tid = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
            sid = self.call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
            self.call("Emulation.setDeviceMetricsOverride", {"width": 1920, "height": 1080, "deviceScaleFactor": 1, "mobile": False}, sid)
            self.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "reduce"}]}, sid)
            if transparent:
                self.call("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}}, sid)
            self.sessions[name] = sid
        for name, extra, _ in self.PAGES:
            self.call("Page.navigate", {"url": "http://127.0.0.1:%d/display?frame=1&live=1%s" % (self.port, extra)}, self.sessions[name])
        deadline = time.time() + 20
        for name in self.sessions:  # each page reveals itself once every font, sprite and stylesheet is in
            while self.evaluate(name, "!!window.ghlCapture && document.body.style.visibility === ''") is not True:
                if time.time() > deadline:
                    raise RuntimeError("capture page %s did not load" % name)
                time.sleep(0.1)

    def _reader(self, rf):
        buf = b""
        while True:
            try:
                chunk = rf.read(1 << 20)
            except OSError:
                chunk = b""
            if not chunk:
                break
            buf += chunk
            while b"\0" in buf:
                raw, buf = buf.split(b"\0", 1)
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                waiter = self.pending.pop(msg.get("id"), None)
                if waiter:
                    waiter[1].append(msg)
                    waiter[0].set()
        self.dead = True
        for ev, _ in list(self.pending.values()):
            ev.set()

    def call(self, method, params=None, session=None, timeout=10):
        if self.dead or not self.wf:
            raise RuntimeError("capture Chrome is not running")
        with self.wlock:
            self.nid += 1
            mid = self.nid
            ev, box = threading.Event(), []
            self.pending[mid] = (ev, box)
            msg = {"id": mid, "method": method, "params": params or {}}
            if session:
                msg["sessionId"] = session
            self.wf.write(json.dumps(msg).encode() + b"\0")
        if not ev.wait(timeout) or not box:
            self.pending.pop(mid, None)
            raise RuntimeError("capture Chrome: no answer to " + method)
        if "error" in box[0]:
            raise RuntimeError("capture Chrome: %s %s" % (method, box[0]["error"].get("message")))
        return box[0].get("result", {})

    def evaluate(self, page, expression, timeout=10):
        r = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True},
                      self.sessions[page], timeout)
        if r.get("exceptionDetails"):
            raise RuntimeError("capture page %s: %s" % (page, r["exceptionDetails"].get("text")))
        return r.get("result", {}).get("value")

    def capture(self, state, outs):
        """Push `state` into every page, then screenshot each to outs[page]. Returns the start-screen effect rects."""
        import base64
        with self.lock:
            if self.dead or not self.proc or self.proc.poll() is not None:
                self.start()
            arg = json.dumps(state)
            results, errors = {}, []

            def push(name):
                try:
                    results[name] = self.evaluate(name, "window.ghlCapture(%s)" % arg)
                except Exception as e:
                    errors.append(e)
            jobs = [threading.Thread(target=push, args=(n,), daemon=True) for n in self.sessions]
            for j in jobs:
                j.start()
            for j in jobs:
                j.join(15)
            if errors or len(results) != len(self.sessions):
                raise errors[0] if errors else RuntimeError("capture pages did not answer")
            shots = {}

            def shoot(name):
                try:
                    # the composite still is only a fallback (shown when the animated layers can't be): JPEG is
                    # several times quicker to encode and send than a 1.6 MB PNG; the UI layers need PNG transparency
                    fmt = {"format": "jpeg", "quality": 92} if name == "still" else {"format": "png"}
                    shots[name] = base64.b64decode(self.call("Page.captureScreenshot", fmt, self.sessions[name], 15)["data"])
                except Exception as e:
                    errors.append(e)
            jobs = [threading.Thread(target=shoot, args=(n,), daemon=True) for n in outs]
            for j in jobs:
                j.start()
            for j in jobs:
                j.join(20)
            if errors or len(shots) != len(outs):
                raise errors[0] if errors else RuntimeError("capture screenshots did not finish")
            for name, path in outs.items():
                tmp = path.with_name(".cap-" + path.name)
                tmp.write_bytes(shots[name])
                os.replace(tmp, path)
            fx = (results.get("rest") or {}).get("fx") or {}
            return {k: v for k, v in fx.items() if isinstance(v, list) and len(v) == 4}

    def freeze(self, on):
        """Freeze the capture pages during a song (timers and rendering stop: no CPU), thaw them right after."""
        if self.dead or on == self.frozen:
            return
        try:
            with self.lock:
                for sid in self.sessions.values():
                    self.call("Page.setWebLifecycleState", {"state": "frozen" if on else "active"}, sid, 5)
            self.frozen = on
        except Exception as e:
            print("!! capture Chrome %s: %s; restarting it" % ("freeze" if on else "thaw", e))
            self.stop()

    def stop(self):
        self.dead = True
        self.frozen = False
        p, self.proc = self.proc, None
        if self.wf:
            try:
                self.wf.close()
            except OSError:
                pass
            self.wf = None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()


CAPTURE = {"chrome": None}


def osc_message(address, *args):
    """One OSC 1.0 message: bools as T/F tags (no data), ints, floats, strings."""
    def pad(b):
        return b + b"\0" * (4 - len(b) % 4)
    tags, data = ",", b""
    for a in args:
        if a is True: tags += "T"
        elif a is False: tags += "F"
        elif isinstance(a, int): tags += "i"; data += a.to_bytes(4, "big", signed=True)
        elif isinstance(a, float): tags += "f"; data += struct.pack(">f", a)
        else: tags += "s"; data += pad(str(a).encode())
    return pad(address.encode()) + pad(tags.encode()) + data


class Lights:
    """Game-driven lights: the guitar in the player's hands and the band's MaestroDMX, on top of the band's own show.
    The mod sends what's happening in the song (UDP 127.0.0.1:<stage port + 1>, one datagram per change:
    "ghl sp=1 fill=200 mult=4 solo=0 combo=57 hit=57 beat=120 beats=480"); the server adds the show's own moments.

    THE ROOM (MaestroDMX): momentary hits only, never the palette, pattern, fog or a blackout, and never on a miss.
      - One dial, "room": subtle / show / big. It sets how often the room may speak (spacing, hits per song) and which
        moments count: subtle = called up, Star Power, a new #1; show = + the first note, every 100th note of a
        streak, a solo and a clean solo, the song's last beats, five stars; big = all of it, closer together.
      - The strobe is kept for one thing, a new #1 (and "strobe": False turns even that into a blinder).
      - Star Power: a blinder hit, or ("sp_look": "effect") MaestroDMX's own Effect trigger held for the whole Star
        Power, whatever the band set that trigger to.
    THE GUITAR (Santroller, the Revival Kit): what shows depends on the guitar's own LED setup, so three dialects are
    spoken at once and the guitar shows whichever it's wired for: YARG's game feedback (star power, meter,
    multiplier, solo, miss), the Rock Band stage-kit LEDs (8 LEDs in four colours) and the RGB indicator LED.
      - Asleep on the stand between players (a slow breath); awake from call-up to the song (a chase: the guitar
        looks alive for the walk-up); the game's own state in the song, with a pulse once Star Power is ready (a
        hint for first-timers); a gold victory lap for five stars or a new #1, then back to sleep.
    "Rehearse the lights" plays every moment at the current level, so the look is judged in the room before doors.
    Everything fails silent: a missing guitar or an unreachable MaestroDMX never touches the game or the stage."""
    G_FILL, G_SP, G_MULT, G_SOLO, G_MISS = 0x08, 0x09, 0x0A, 0x0B, 0x0C
    K_BLUE, K_GREEN, K_YELLOW, K_RED = 0x20, 0x40, 0x60, 0x80
    KIT = (K_BLUE, K_GREEN, K_YELLOW, K_RED)
    RGB_MULT = {1: (36, 36, 36), 2: (0, 190, 70), 3: (255, 200, 0), 4: (255, 100, 0)}
    RGB_SP, RGB_MISS, RGB_GOLD, RGB_GO, RGB_OFF = (30, 120, 255), (255, 20, 20), (255, 190, 40), (0, 220, 80), (0, 0, 0)
    PALETTE = ((43, 217, 107), (255, 59, 59), (255, 214, 10), (26, 147, 254), (255, 138, 31))  # the five frets
    LEVELS = {"subtle": (6.0, 4, 1), "show": (3.0, 9, 3), "big": (2.0, 14, 5)}  # spacing (s), hits per song, finale beats
    ORDER = ("subtle", "show", "big")

    def __init__(self):
        self.helper, self.helper_at, self.guitars = None, 0.0, 0
        self.last = {}
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.maestro_addr, self.maestro_at, self.accent_at = None, 0.0, 0.0
        self.lock = threading.Lock()
        self.scene, self.scene_at, self.scene_thread = "sleep", time.time(), None
        self.song = {}          # this song: hits used, streak milestone, solo, finale, over...
        self.now = {}           # the latest game fields (the Star Power-ready pulse reads them)
        self.rehearsing = False
        self.speed = 1.0        # (tests run the rehearsal and the holds fast)

    def cfg(self):
        return {**DEFAULT_CONFIG["lights"], **(CONFIG.get("lights") or {})}

    def level(self):
        lv = self.cfg().get("room")
        return lv if lv in self.LEVELS else "show"

    def status(self):
        c = self.cfg()
        if not c["guitar"] and not c["maestro"]:
            return "off"
        bits = []
        if c["guitar"]:
            bits.append("guitar" if self.guitars > 0 else "no guitar")
        if c["maestro"]:
            bits.append("MaestroDMX" if self.maestro_addr else "MaestroDMX not found")
        return "ok" if all(b in ("guitar", "MaestroDMX") for b in bits) else ", ".join(bits)

    # ---- guitar: the helper process
    def _helper(self):
        if self.helper and self.helper.poll() is None:
            return self.helper
        if time.time() - self.helper_at < 10:
            return None
        self.helper_at = time.time()
        exe = ROOT / "tools" / "santroller_leds"
        if not exe.exists():
            return None
        try:
            self.helper = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
            threading.Thread(target=self._read_helper, args=(self.helper,), daemon=True).start()
            self.last = {}
        except OSError as e:
            print("!! guitar lights:", e)
            self.helper = None
        return self.helper

    def _read_helper(self, proc):
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("ready "):
                self.guitars = int(line.split()[1] or 0)
            elif line.startswith("guitar "):
                self.guitars += 1; print("== guitar lights:", line[7:])
            elif line.startswith("gone "):
                self.guitars = max(0, self.guitars - 1)
            elif line.startswith("failed") or line.startswith("open failed"):
                print("!! guitar lights:", line)

    def _line(self, line, key, value, force=False):
        """One command to the guitar, only when it changes (the helper is a pipe; the guitar is a USB report)."""
        if not self.cfg()["guitar"]:
            return
        with self.lock:
            if not force and self.last.get(key) == value:
                return
            h = self._helper()
            if not h:
                return
            try:
                h.stdin.write(line + "\n")
                h.stdin.flush()
                self.last[key] = value
            except OSError:
                self.helper = None

    def guitar(self, command, value, force=False):
        value = max(0, min(255, int(value)))
        self._line("%d %d" % (command, value), command, value, force)

    def kit(self, colour, mask):
        self._line("%d %d" % (colour, mask & 0xFF), colour, mask & 0xFF)

    def rgb(self, colour):
        r, g, b = (max(0, min(255, int(v))) for v in colour)
        self._line("rgb %d %d %d" % (r, g, b), "rgb", (r, g, b))

    def _chase(self, i, colour, fill=None, mult=None, rgb=None):
        """One step of a running light: LED i of one stage-kit colour, plus the feedback and RGB looks."""
        for c in self.KIT:
            self.kit(c, (1 << (i % 8)) if c == colour else 0)
        if fill is not None:
            self.guitar(self.G_FILL, fill)
        if mult is not None:
            self.guitar(self.G_MULT, mult)
        if rgb is not None:
            self.rgb(rgb)

    def guitar_rest(self):
        """The game's look, cleared (between songs and at a song start)."""
        for c, v in ((self.G_SP, 0), (self.G_FILL, 0), (self.G_MULT, 1), (self.G_SOLO, 0), (self.G_MISS, 0)):
            self.guitar(c, v)
        for c in self.KIT:
            self.kit(c, 0)

    # ---- guitar: scenes (a thread at 16 steps/s; nothing runs until the first scene is set)
    TICK = 1 / 16
    RIPPLE = (0x18, 0x3C, 0x7E, 0xFF, 0x7E, 0x3C, 0x18, 0x00)  # out from the middle LEDs and back

    def set_scene(self, name):
        self.scene, self.scene_at = name, time.time()
        if self.scene_thread is None:
            self.scene_thread = threading.Thread(target=self._scene_loop, daemon=True, name="lights-scene")
            self.scene_thread.start()

    def _scene_loop(self):
        while True:
            time.sleep(self.TICK)
            try:
                if self.cfg()["guitar"]:
                    self._scene_step(time.time() - self.scene_at)
            except Exception as e:
                print("!! guitar lights:", e)

    @staticmethod
    def breath(t, period):
        """0 -> 1 -> 0, smoothly, once per period."""
        return 0.5 - 0.5 * math.cos(2 * math.pi * (t % period) / period)

    def comet(self, colour, head, length=3, mirror=False):
        """A head LED with a tail, in one stage-kit colour (mirror: a second comet opposite it)."""
        mask = 0
        for k in range(length):
            mask |= 1 << ((head - k) % 8)
            if mirror:
                mask |= 1 << ((head - k + 4) % 8)
        for c in self.KIT:
            self.kit(c, mask if c == colour else 0)

    def _scene_step(self, age):
        s = self.scene
        if s == "sleep":
            # on the stand: a slow 6 s breath in one fret colour at a time, and a soft comet doing a lap every 3 s
            b = self.breath(age, 6.0)
            col = self.PALETTE[int(age // 6) % 5]
            self.rgb(tuple(v * (0.04 + 0.26 * b) for v in col))
            self.guitar(self.G_FILL, int(96 * b) // 4 * 4)
            self.guitar(self.G_MULT, 1)
            self.comet(self.KIT[int(age // 3) % 4], int(age / 0.375))
        elif s == "wake":
            cur = STATE["current"]
            if not self.rehearsing and (age > 180 or not cur or cur.get("phase") != "ready"):
                self.set_scene("sleep")  # (removed from the start screen, or never came up)
                return
            # a chase running through the four colours, the meter charging up, the multiplier counting, green blinks
            step = int(age * 8)
            self._chase(step, self.KIT[(step // 8) % 4], fill=int((age % 1.0) * 255) // 32 * 32, mult=1 + (step // 2) % 4,
                        rgb=self.RGB_GO if step % 4 < 2 else self.RGB_OFF)
        elif s == "song":
            self._song_step(age)
        elif s == "bow":
            if age < 3.0:  # the victory lap: gold, full meter, x4
                step = int(age * 8)
                self._chase(step, self.K_YELLOW, fill=255, mult=4, rgb=self.RGB_GOLD if step % 2 else (120, 80, 0))
            else:
                self.guitar_rest(); self.set_scene("sleep")
        elif s == "rest":
            if age < 1.0:  # the meter runs down, then sleep
                self.guitar(self.G_FILL, int(255 * (1.0 - age)) // 32 * 32)
            else:
                self.guitar_rest(); self.set_scene("sleep")

    def _song_step(self, age):
        """In the song the game drives the look, except: Star Power ready (the meter throbs, the indicator leans blue,
        a blue ripple), and Star Power itself (an ignition, then two blue comets racing while it runs)."""
        n, song, now = self.now, self.song, time.time()
        sp, fill = n.get("sp") == "1", int(n.get("fill", 0))
        mult_rgb = self.RGB_MULT.get(min(4, max(1, int(n.get("mult", 1)))), self.RGB_MULT[1])
        look = "sp" if sp else "ready" if fill >= 128 else "plain"
        if look != song.get("look"):
            song["look"] = look
            if look == "plain":  # the scene hands the guitar back to the game's own values
                for c in self.KIT:
                    self.kit(c, 0)
                self.guitar(self.G_FILL, fill)
                self.rgb(mult_rgb)
        if look == "sp":
            t = now - song.get("sp_at", now)
            if t < 0.1:    # ignition: everything on, white
                for c in self.KIT:
                    self.kit(c, 0xFF)
                self.rgb((255, 255, 255)); self.guitar(self.G_FILL, 255)
            elif t < 0.6:  # a blue shockwave out from the middle and back
                k = min(len(self.RIPPLE) - 1, int((t - 0.1) / 0.0625))
                for c in self.KIT:
                    self.kit(c, self.RIPPLE[k] if c == self.K_BLUE else 0)
                self.rgb(self.RGB_SP)
            else:          # while it runs: two blue comets racing; the indicator blue, white on every beat
                self.comet(self.K_BLUE, int(t * 10), length=2, mirror=True)
                self.rgb((255, 255, 255) if now - song.get("beat_at", 0) < 0.08 else self.RGB_SP)
                self.guitar(self.G_FILL, fill)
        elif look == "ready":
            b = self.breath(age, 1.2)
            self.guitar(self.G_FILL, max(0, fill - int(56 * b)) // 4 * 4)
            self.rgb(tuple(m + (sp_ - m) * b for m, sp_ in zip(mult_rgb, self.RGB_SP)))
            k = int((age % 1.2) / 0.15)
            for c in self.KIT:
                self.kit(c, self.RIPPLE[k] if c == self.K_BLUE else 0)

    # ---- MaestroDMX
    def _maestro(self):
        c = self.cfg()
        if time.time() - self.maestro_at > 30:  # (re)resolve off the hot path, at most every 30 s
            self.maestro_at = time.time()
            def resolve():
                try:
                    self.maestro_addr = (socket.gethostbyname(c["maestro_host"]), int(c["maestro_port"]))
                except OSError:
                    self.maestro_addr = None
            threading.Thread(target=resolve, daemon=True).start()
        return self.maestro_addr

    def osc(self, address, *args):
        addr = self._maestro()
        if addr:
            try:
                self.sock.sendto(osc_message(address, *args), addr)
            except OSError:
                pass

    def accent(self, kind, hold=0.35, level="subtle", spaced=True, force=False):
        """A momentary hit on the band's lights. `level` is the smallest room level this moment belongs to; hits are
        spaced out and capped per song (the level's numbers), unless `spaced` is off (a designed sequence, like the
        finale) or `force` (the rehearsal). The only strobe is a new #1, and "strobe": False vetoes even that."""
        c = self.cfg()
        if not c["maestro"]:
            return False
        if not force:
            lv = self.level()
            if self.ORDER.index(lv) < self.ORDER.index(level):
                return False
            gap, budget, _ = self.LEVELS[lv]
            if spaced and (time.time() - self.accent_at < gap or self.song.get("hits", 0) >= budget):
                return False
            self.song["hits"] = self.song.get("hits", 0) + 1
        if kind == "strobe" and not c["strobe"]:
            kind = "blinder"
        self.accent_at = time.time()
        path = "/triggers/" + kind
        self.osc(path, True)
        threading.Timer(hold * self.speed, lambda: self.osc(path, False)).start()
        return True

    def double(self, level="show", force=False):
        """Two quick blinder pops (ba-dum)."""
        if self.accent("blinder", 0.14, level=level, force=force):
            threading.Timer(0.34 * self.speed, lambda: self.accent("blinder", 0.14, force=True)).start()

    # ---- the show's moments
    def called_up(self):
        self.accent("blinder", 0.4)
        self.set_scene("wake")

    def idle(self):
        self.set_scene("sleep")

    def song_start(self):
        self.song = {"started": time.time(), "hit_at": 0.0}
        self.now = {}
        self.guitar_rest()
        self.rgb(self.RGB_MULT[1])
        self.set_scene("song")

    def game(self, fields):
        """One update from the mod during a show song."""
        if not self.song.get("started"):
            self.song_start()
        song, now = self.song, time.time()
        self.now = fields
        sp, solo = fields.get("sp") == "1", fields.get("solo") == "1"
        mult = max(1, min(8, int(fields.get("mult", 1))))
        # Star Power
        if sp and not song.get("sp"):
            song["sp_at"] = now  # (the guitar's ignition runs from here)
            if self.cfg().get("sp_look") == "effect" and self.accent("effect", 20.0):
                song["effect"] = True
            else:
                self.accent("blinder", 0.5)
        if not sp and song.get("sp") and song.pop("effect", False):
            self.osc("/triggers/effect", False)
        song["sp"] = sp
        self.guitar(self.G_SP, 1 if sp else 0)
        self.guitar(self.G_MULT, mult)
        fill = int(fields.get("fill", 0))
        if not sp and fill < 128:  # (otherwise the scene animates the meter, the indicator and the LEDs)
            self.guitar(self.G_FILL, fill)
            self.rgb(self.RGB_MULT.get(min(4, mult), self.RGB_MULT[1]))
        # notes: the first one lights the room ("you're in"), then every 100th of a streak (50 on a big night)
        hit = int(fields.get("hit", 0))
        if hit != song.get("hit"):
            song["hit"], song["hit_at"] = hit, now
            if hit > 0 and not song.get("first"):
                song["first"] = True
                self.accent("blinder", 0.25, level="show")
        combo = int(fields.get("combo", 0))
        step = 50 if self.level() == "big" else 100
        if combo // step > song.get("streak", 0):
            song["streak"] = combo // step
            if self.accent("blinder", 0.2, level="show"):
                self._sparkle()
        # a solo: a pop going in, two coming out if nothing was missed
        if solo and not song.get("solo"):
            song["solo_clean"] = True
            self.accent("blinder", 0.3, level="show")
        if not solo and song.get("solo") and song.get("solo_clean"):
            self.double()
        song["solo"] = solo
        self.guitar(self.G_SOLO, 1 if solo else 0)
        # a miss: the guitar only (the player's own feedback, as YARG does it); the room never punishes
        if fields.get("miss") == "1":
            song["solo_clean"] = False
            self.guitar(self.G_MISS, 1, force=True)
            self.rgb(self.RGB_MISS)
            threading.Timer(0.15 * self.speed, lambda: self.guitar(self.G_MISS, 0, force=True)).start()
        # the finale: pops on the song's last beats and a longer hit on the final one, while notes are still coming
        beat, beats = int(fields.get("beat", 0)), int(fields.get("beats", 0))
        if beats and beat != song.get("beat"):
            song["beat"], song["beat_at"] = beat, now
            left = beats - beat
            if 0 <= left < self.LEVELS[self.level()][2] and now - song.get("hit_at", 0) < 8 and left not in song.setdefault("finale", set()):
                song["finale"].add(left)
                self.accent("blinder", 0.6 if left == 0 else 0.12, level="subtle", spaced=False)

    def _sparkle(self):
        for c in self.KIT:
            self.kit(c, 0xFF)
        threading.Timer(0.12 * self.speed, lambda: [self.kit(c, 0) for c in self.KIT]).start()

    def song_over(self, new_top, stars=0):
        """The song's score is in: five stars get a double pop, a new #1 the night's one strobe; the guitar takes a
        victory lap for either, or just winds down."""
        if self.song.get("over"):
            return
        self.song["over"] = True
        if self.song.pop("effect", False):
            self.osc("/triggers/effect", False)
        self.guitar_rest()
        lv = self.level()
        if stars >= 5:
            self.double()
        if new_top:
            self.accent_at = 0  # (the night's moment: never spaced out by the five-star pops)
            self.accent("strobe", {"subtle": 0.8, "show": 1.2, "big": 2.0}[lv])
        self.set_scene("bow" if (new_top or stars >= 5) else "rest")

    def song_left(self):
        """The game left the song. If no score follows (a quit), wind the guitar down anyway."""
        threading.Timer(3.0 * self.speed, lambda: self.song_over(False, 0)).start()

    def test(self):
        return self.rehearse()

    REHEARSAL = (
        ("Asleep on the stand", "idle", 3.0),
        ("Called up: a blinder, the guitar wakes", "called_up", 3.5),
        ("Song start, first note: a pop", "first", 2.0),
        ("Star Power ready: the meter throbs, a blue ripple", "ready", 3.6),
        ("Star Power: ignition on the guitar, blinder (or the Effect) in the room", "sp", 1.0),
        ("A solo: a pop; a clean solo: a double pop", "solo", 3.0),
        ("100-note streak: a pop and a sparkle", "streak", 2.5),
        ("The last beats: pops on the beat, then the final hit", "finale", 3.5),
        ("Five stars + new #1: double pop, the strobe, a victory lap", "over", 4.5),
        ("Back to sleep", "idle", 0.5),
    )

    def rehearse(self):
        """Stage Control's "Rehearse the lights": every moment, at the current room level, with the guitar along."""
        if self.rehearsing or RUNTIME.get("scene") == "Gameplay":
            return False
        self.rehearsing = True
        threading.Thread(target=self._rehearse, daemon=True, name="lights-rehearsal").start()
        return True

    def _rehearse(self):
        try:
            for n, (label, step, pause) in enumerate(self.REHEARSAL, 1):
                RUNTIME["lights_note"] = "Rehearsal %d/%d: %s" % (n, len(self.REHEARSAL), label)
                self.accent_at = 0
                self.song.pop("hits", None)
                self._rehearse_step(step)
                time.sleep(pause * self.speed)
        finally:
            RUNTIME.pop("lights_note", None)
            self.rehearsing = False

    def _rehearse_step(self, step):
        g = lambda **f: self.game({"sp": "0", "fill": "0", "mult": "1", "solo": "0", "combo": "0", "hit": "0", **{k: str(v) for k, v in f.items()}})
        if step == "idle":
            self.song = {}
            self.idle()
        elif step == "called_up":
            self.called_up()
        elif step == "first":
            self.song_start()
            g(hit=1, combo=1)
        elif step == "ready":
            g(hit=20, combo=20, mult=2, fill=160)
        elif step == "sp":
            for k in range(6):  # 120 BPM: the indicator flashes white on each beat
                g(hit=30 + k, combo=30 + k, mult=8, fill=160 - k * 16, sp=1, beat=10 + k, beats=200)
                time.sleep(0.5 * self.speed)
        elif step == "solo":
            g(hit=40, combo=40, mult=4, solo=1)
            time.sleep(1.2 * self.speed)
            self.accent_at = 0  # (a real solo is longer than the spacing; this one isn't)
            g(hit=50, combo=50, mult=4, solo=0)
        elif step == "streak":
            g(hit=100, combo=100, mult=4)
        elif step == "finale":
            n = self.LEVELS[self.level()][2]
            for k in range(n, -1, -1):  # 120 BPM
                g(hit=120 + n - k, combo=120, mult=4, beat=200 - k, beats=200)
                time.sleep(0.5 * self.speed)
        elif step == "over":
            self.song_over(True, 5)


LIGHTS = Lights()


def lights_listener():
    """Game events from the mod (UDP on 127.0.0.1, the stage port + 1: a datagram per change, never blocking the game)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", int(RUNTIME.get("port") or CONFIG["port"]) + 1))
    except OSError as e:
        print("!! game events: port in use (%s); game-driven lights off" % e)
        return
    while True:
        try:
            data, _ = sock.recvfrom(512)
            parts = data.decode("ascii", "replace").split()
            if not parts or parts[0] != "ghl":
                continue
            fields = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
            LIGHTS.game(fields)
        except Exception as e:
            print("!! game event:", e)
        RUNTIME["lights_status"] = LIGHTS.status()


def stop_capture():
    if CAPTURE["chrome"]:
        CAPTURE["chrome"].stop()


RENDER_SLOTS = threading.Semaphore(3)  # lobby / start screen renders (always get their own slots)
VIDEO_SLOTS = threading.Semaphore(4)   # background video frames (only when the design changed)


def chrome_shot(url, out, size="1920,1080", transparent=False, reduced=False, min_bytes=5000, tries=3, slots=None):
    """Screenshot `url` to `out` with headless Chrome; retries; returns True when the file is good."""
    args = [CHROME, "--headless=new", "--hide-scrollbars", "--window-size=" + size, "--virtual-time-budget=4000"]
    if reduced:
        args.append("--force-prefers-reduced-motion")
    if transparent:
        args.append("--default-background-color=00000000")
    for _ in range(tries):
        with (slots or RENDER_SLOTS):
            try:
                if out.exists():
                    out.unlink()
                subprocess.run(args + ["--screenshot=%s" % out, url], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=45)
            except Exception:
                pass
        if out.exists() and out.stat().st_size >= min_bytes:
            return True
    return False


def page_fx(url):
    """Ask the page where its in-game effects sit (display-ui.js writes them to <body data-fx>)."""
    args = [CHROME, "--headless=new", "--hide-scrollbars", "--window-size=1920,1080", "--virtual-time-budget=4000",
            "--force-prefers-reduced-motion", "--dump-dom", url]
    try:
        with RENDER_SLOTS:
            dom = subprocess.run(args, capture_output=True, text=True, timeout=45).stdout
        m = re.search(r'data-fx="([^"]*)"', dom)
        fx = json.loads(html.unescape(m.group(1))) if m else {}
        return {k: v for k, v in fx.items() if isinstance(v, list) and len(v) == 4}
    except Exception:
        return {}


def render_many(jobs):
    """jobs: list of (url, out, kwargs). Renders in parallel (bounded by the slots); returns all-ok."""
    results = []
    threads = [threading.Thread(target=lambda j=j: results.append(chrome_shot(j[0], j[1], **j[2])), daemon=True) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return len(results) == len(jobs) and all(results)


BG_LOOP_S = 7.2   # must match display.html LOOP_S
VIDEO_FPS = 60    # in-game stage background: one seamless 60 fps video loop, played by Unity's VideoPlayer
VIDEO_MBPS = 40   # high bitrate: the stage is dark and gradients band at low bitrates
UI_FRAMES = 2     # in-game UI layer: rest + peak pose (the mod crossfades them)
ENCODER = ROOT / "tools" / "encode_loop"  # Swift/AVFoundation image sequence -> H.264 (tools/encode_loop.swift)
BG_VIDEO = DATA / "bg-video"


def design_fingerprint():
    """Hash of everything the background is drawn from; unchanged design = reuse the rendered video."""
    h = hashlib.md5(("video|%d|%s|1920x1080|%d" % (VIDEO_FPS, BG_LOOP_S, VIDEO_MBPS)).encode())
    for f in sorted([STATIC / "display.html", STATIC / "brand.css"] + list((STATIC / "gh").glob("*")) + [STATIC / "jr-logo.png"]):
        if f.is_file():
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()


BG_VARIANTS = {"stage": "", "home": "&home=1"}  # lobby/start screens, and the home screen's wider stage


SANDBOX = (bool(os.environ.get("GHL_DATA")) and "--embedded" not in sys.argv) or "--no-game" in sys.argv  # test runs: never touch the real game's files


def publish_bg_video(ver):
    """Hand the videos to the game. It plays them from its own Application Support folder: macOS guards ~/Documents
    and would freeze the game at launch with a permission prompt (re-asked after every re-sign of the app)."""
    game_dir = (DATA if SANDBOX else Path(os.path.expanduser(CONFIG["scoredata_path"])).parent / "GHL") / "video"
    keep = {"%s-%d.mp4" % (k, ver) for k in BG_VARIANTS}
    paths = {}
    for kind in BG_VARIANTS:
        src = BG_VIDEO / ("%s-%d.mp4" % (kind, ver))
        if not src.exists():
            continue
        dst = game_dir / src.name
        try:
            game_dir.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                tmp = dst.with_name(".tmp-" + dst.name)
                shutil.copy2(src, tmp)
                os.replace(tmp, dst)
            paths[kind] = str(dst)
        except OSError as e:
            print("!! could not copy the background video for the game:", e)
            paths[kind] = str(src)
    try:
        for old in game_dir.glob("*.mp4"):
            if old.name not in keep:
                old.unlink()  # (a game still playing one keeps its open copy)
    except OSError:
        pass
    RUNTIME["vid_path"] = paths.get("stage", "")
    RUNTIME["home_path"] = paths.get("home", "")
    RUNTIME["bg_still"] = ver
    RUNTIME["vid"] = ver


def render_bg_video(port):
    """The animated stage background (stage set + note highway) as one seamless 60 fps loop, plus a still of it
    (shown until the video plays). Rendered once per design, then reused."""
    stamp = BG_VIDEO / "design.txt"
    fp = design_fingerprint()
    why = "no stage video rendered yet"
    try:
        old_fp, ver = stamp.read_text().split()[:2]
        missing = [n for n in ["%s-%s.mp4" % (k, ver) for k in BG_VARIANTS] + ["still.jpg"] if not (BG_VIDEO / n).exists()]
        if old_fp == fp and not missing:
            publish_bg_video(int(ver))
            print("== in-game background video ready (reused, design unchanged)")
            return
        why = ("design changed (%s -> %s)" % (old_fp[:8], fp[:8])) if old_fp != fp else ("missing " + ", ".join(missing))
        if (BG_VIDEO / ("stage-%s.mp4" % ver)).exists():
            publish_bg_video(int(ver))  # the design changed: keep showing the previous video until the new one is in
    except (OSError, ValueError):
        pass
    # 864 headless-Chrome frames is minutes of full CPU next to the game: only ever render OUR page. (11/18: an old copy
    # of the app bound a port another app shadowed, and rendered that app's page over and over.)
    for attempt in range(4):  # (a slow answer under startup load is not a squatter: only skip when it never reaches us)
        if ghl_is_me(port):
            break
        time.sleep(2)
    else:
        print("!! not rendering the stage videos: port %d does not reach this Gig Mode (%s)" % (port, port_owner(port)))
        return
    if not ENCODER.exists():
        print("!! video encoder missing (stage-manager/tools/encode_loop): in-game screens stay still")
        return
    frames = DATA / "bg-video-frames"
    BG_VIDEO.mkdir(parents=True, exist_ok=True)
    n = int(round(BG_LOOP_S * VIDEO_FPS))
    started = time.time()
    ver = int(time.time())
    print("== rendering the in-game background videos (%d frames each; a few minutes, only after a design change): %s" % (n, why))
    try:
        for kind, extra in BG_VARIANTS.items():
            shutil.rmtree(frames, ignore_errors=True)
            frames.mkdir(parents=True, exist_ok=True)
            base = "http://127.0.0.1:%d/display?layer=bg%s" % (port, extra)
            still = BG_VIDEO / ("%s-still-new.jpg" % kind)
            jobs = [(base + "&t=%.6f" % (i / n), frames / ("%03d.png" % i), {"reduced": True, "min_bytes": 50000, "slots": VIDEO_SLOTS})
                    for i in range(n)]
            jobs.append((base + "&t=0.37", still, {"reduced": True, "min_bytes": 20000, "slots": VIDEO_SLOTS}))
            render_many(jobs)
            missing = [i for i in range(n) if not (frames / ("%03d.png" % i)).exists()]
            if len(missing) > n // 20 or not still.exists():
                print("!! %s video: %d of %d frames failed; in-game screens stay still" % (kind, len(missing), n))
                return
            for i in missing:  # a rare failed frame: reuse the one before it so the loop never stalls
                for k in range(1, n):
                    src = frames / ("%03d.png" % ((i - k) % n))
                    if src.exists():
                        shutil.copy(src, frames / ("%03d.png" % i))
                        break
            video = BG_VIDEO / ("%s-%d.mp4" % (kind, ver))
            r = subprocess.run([str(ENCODER), str(frames), str(VIDEO_FPS), str(video), "1", str(VIDEO_MBPS)],
                               capture_output=True, text=True, timeout=900)
            if r.returncode or not video.exists():
                print("!! %s video encode failed:" % kind, (r.stdout + r.stderr).strip()[-300:])
                return
        os.replace(BG_VIDEO / "stage-still-new.jpg", BG_VIDEO / "still.jpg")
        os.replace(BG_VIDEO / "home-still-new.jpg", BG_VIDEO / "home-still.jpg")
        for old in BG_VIDEO.glob("*.mp4"):
            if not old.name.endswith("-%d.mp4" % ver):
                old.unlink()  # (a game still playing one keeps its open copy until it switches)
        stamp.write_text("%s %d" % (fp, ver))
        publish_bg_video(ver)
        print("== in-game background videos ready (%.0f s)" % (time.time() - started))
    except Exception as e:
        print("!! background video render failed (in-game screens stay still):", e)
    finally:
        shutil.rmtree(frames, ignore_errors=True)


def mod_state(query):
    RUNTIME["mod_seen"] = time.time()
    RUNTIME["mod_info"] = {k: v[0] for k, v in query.items() if k in ("scene", "joined", "players", "version", "home", "pads", "padname")}
    scene = RUNTIME["mod_info"].get("scene", "")
    with LOCK:
        if scene != RUNTIME["scene"]:
            print("== game scene:", RUNTIME["scene"] or "-", "->", scene)
            left_gameplay = RUNTIME["scene"] == "Gameplay"
            RUNTIME["prev_scene"] = RUNTIME["scene"]
            RUNTIME["scene"] = scene
            cur = STATE["current"]
            if RUNTIME["mod_info"].get("home") != "1": RUNTIME["reset_armed_at"] = 0
            if (scene == "Main Menu" and cur and (RUNTIME.get("prev_scene") in ("Gameplay", "EndOfSong")
                    or (not RUNTIME.get("prev_scene") and time.time() - cur.get("startedAt", time.time()) > 15))
                    and cur["phase"] in ("playing", "finished")):
                finish_turn_now(cur)
                cur = STATE["current"]
            if scene == "Gameplay" and cur and cur["phase"] == "playing":
                LIGHTS.song_start()
            if left_gameplay:
                LIGHTS.song_left()
            if left_gameplay and cur and cur["phase"] in ("playing", "finished"):
                # Song over (or quit): results stay up for results_seconds, the turn is finished right away.
                RUNTIME["results_hold_until"] = time.time() + CONFIG["results_seconds"]
                RUNTIME["results_for"] = cur["id"]
                RUNTIME["v"] += 1  # the results card
                threading.Thread(target=song_ended, args=(cur["id"],), daemon=True).start()
        if scene in ("Gameplay", "EndOfSong") and STATE["current"] and STATE["current"].get("phase") == "playing":
            STATE["current"]["gameplaySeen"] = True  # the song really reached the game (never restart it)
        cur = STATE["current"]
        song = SONGS.get(cur["songId"]) if cur else None
        cmds, RUNTIME["mod_cmds"] = RUNTIME["mod_cmds"], []
        return {"v": RUNTIME["v"], "frame": RUNTIME["frame"], "fv": RUNTIME["frame_v"],
                "ui": RUNTIME["ui"] if RUNTIME.get("ui_ready") else 0, "uin": UI_FRAMES, "uv": RUNTIME["ui_v"],
                "vid": RUNTIME["vid"], "vidpath": RUNTIME["vid_path"], "homepath": RUNTIME["home_path"], "still": RUNTIME["bg_still"], "bgs": BG_LOOP_S,
                "sparks": RUNTIME["fx"].get("sparks") or [], "clip": RUNTIME["fx"].get("clip") or [],
                "overlay": overlay_mode() if mod_driver() else "none",
                "phase": cur["phase"] if cur else "", "folder": song["folder"] if song else "",
                "difficulty": chosen_difficulty(cur["songId"], cur.get("difficulty")) if cur else CONFIG.get("audience_difficulty", "easy"),
                "hostReady": bool(RUNTIME.get("host_ready")), "hostV": RUNTIME.get("host_v", 0), "cmds": cmds}


# ---------------------------------------------------------------- http

MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml",
        ".webp": "image/webp", ".woff2": "font/woff2"}
PAGES = {"/": "signup.html", "/signup": "signup.html", "/display": "display.html", "/admin": "admin.html"}


class Handler(BaseHTTPRequestHandler):
    server_version = "GHLive/1.0"

    def log_message(self, fmt, *args):
        pass

    def is_local(self):
        # cloudflared connects from localhost too, but always adds Cf-Connecting-Ip.
        return self.client_address[0] in ("127.0.0.1", "::1") and not self.headers.get("Cf-Connecting-Ip")

    def is_admin(self):
        who = self.headers.get("Cf-Connecting-Ip") or self.client_address[0]
        if BAD_PINS.get(who, 0) >= 8:
            return False
        ok = secrets.compare_digest(self.headers.get("X-Pin", ""), str(CONFIG["admin_pin"]))
        if not ok and self.headers.get("X-Pin") is not None:
            BAD_PINS[who] = BAD_PINS.get(who, 0) + 1
        return ok

    def send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        try:
            data = path.read_bytes()
        except OSError:
            return self.send(404, {"error": "not found"})
        self.send(200, data, MIME.get(path.suffix.lower(), "application/octet-stream"))

    def body(self):
        n = min(int(self.headers.get("Content-Length") or 0), 20000)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path.rstrip("/") or "/"
        if p in PAGES:
            return self.send_file(STATIC / PAGES[p])
        if p == "/host-setup":
            return self.send_file(STATIC / "host-setup.html") if self.is_local() else self.send(403, {"error": "local only"})
        if p == "/api/mod/hostsetup.png":
            return self.send_file(DATA / "hostsetup.png") if self.is_local() else self.send(403, {"error": "local only"})
        if p == "/api/public":
            return self.send(200, public_view())
        if p == "/api/ping":  # "is this Gig Mode?" (another app can hold the port)
            return self.send(200, {"app": "ghl-stage", "pid": os.getpid()})
        if p == "/api/admin":
            if not self.is_admin():
                return self.send(403, {"error": "bad pin"})
            return self.send(200, admin_view())
        if p == "/api/mod/state" and self.is_local():
            return self.send(200, mod_state(parse_qs(u.query)))
        if p == "/api/mod/frame.png" and self.is_local():
            with FRAME_LOCK:
                return self.send_file(DATA / "frame.png")
        m = re.fullmatch(r"/api/mod/ui/(\d+)/(\d{2})\.png", p)
        if m and self.is_local():
            return self.send_file(DATA / "ui" / m.group(1) / (m.group(2) + ".png"))
        if p == "/api/mod/bgstill.jpg" and self.is_local():
            return self.send_file(BG_VIDEO / "still.jpg")
        if p == "/api/mod/homestill.jpg" and self.is_local():
            return self.send_file(BG_VIDEO / "home-still.jpg")
        if p == "/api/local-admin-link" and self.is_local():
            base = RUNTIME["tunnel_url"] or RUNTIME["lan_url"]
            return self.send(200, {"url": "%s/admin#pin=%s" % (base, CONFIG["admin_pin"])})
        if p.startswith("/art/"):
            s = SONGS.get(p[5:])
            if s and s["art"]:
                return self.send_file(Path(s["art"]))
            return self.send(404, {"error": "no art"})
        if p.startswith("/static/"):
            f = (STATIC / p[8:]).resolve()
            if STATIC.resolve() in f.parents and f.is_file():
                return self.send_file(f)
        self.send(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        b = self.body()
        if p == "/api/signup":
            return self.send(200, signup(b))
        if p == "/api/mod/result" and self.is_local():
            return self.send(200, mod_result(b))
        if p == "/api/admin":
            if not self.is_admin():
                return self.send(403, {"error": "bad pin"})
            return self.send(200, admin_action(b))
        if p.startswith("/api/display/"):
            if not (self.is_local() or self.is_admin()):
                return self.send(403, {"error": "display controls are local only"})
            act = p.rsplit("/", 1)[1]
            with LOCK:
                if act == "advance":
                    return self.send(200, {"ok": call_next()})
                if act == "go":
                    return self.send(200, {"ok": go()})
                if act == "skip":
                    return self.send(200, {"ok": skip_next()})
            held = wait_for_bg_stamp() if act == "press" and b.get("button") == "green" else None
            with LOCK:
                if act == "press":
                    cur = STATE["current"]
                    if held and not (cur and cur["id"] == held and cur["phase"] == "ready"):
                        # the host skipped / sent them back / called someone else while the green was held: it was
                        # meant for that start screen only (ok=false: the game counts the next press at once)
                        return self.send(200, {"ok": False, "action": "none"})
                    return self.send(200, press(b.get("button")))
                if act == "back":
                    cur = STATE["current"]
                    return self.send(200, {"ok": bool(cur and cur["phase"] == "ready" and back_to_queue())})
                if act == "buttons":
                    for k in ("green_button", "red_button"):
                        if isinstance(b.get(k), int):
                            CONFIG[k] = b[k]
                    save_json(CONFIG_PATH, CONFIG)
                    return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})


# ---------------------------------------------------------------- startup helpers

def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def start_tunnel(port):
    exe = shutil.which("cloudflared") or next(
        (p for p in ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared") if os.path.exists(p)), None)
    if not exe:
        print("!! cloudflared not installed - phones must use venue Wi-Fi:", RUNTIME["lan_url"])
        return None
    return Tunnel(exe, port)


class Tunnel:
    """cloudflared's quick tunnel (the public sign-up link). If it stops, or the Mac had no internet at launch, it is
    started again (5 s, then backing off to a minute): the QR shows the Wi-Fi link meanwhile and the new public link as
    soon as it's up. A quick tunnel's link changes when it restarts, so the screens follow RUNTIME["tunnel_url"]."""

    def __init__(self, exe, port, retry=5.0):
        self.cmd = [exe, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:%d" % port]
        self.retry, self.proc, self.stopping = retry, None, False
        threading.Thread(target=self.run, daemon=True, name="tunnel").start()

    def run(self):
        delay = self.retry
        while not self.stopping:
            started = time.time()
            try:
                self.proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            except OSError as e:
                print("!! cloudflared could not start (%s) - phones must use venue Wi-Fi: %s" % (e, RUNTIME["lan_url"]))
                return
            if self.stopping:  # (stopped while it was starting)
                self.proc.terminate()
                return
            for line in self.proc.stdout:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m and RUNTIME["tunnel_url"] != m.group(0):
                    RUNTIME["tunnel_url"] = m.group(0)
                    print("\n== PUBLIC SIGN-UP LINK:", RUNTIME["tunnel_url"])
                    print("== PHONE CONTROL PANEL: %s/admin#pin=%s\n" % (RUNTIME["tunnel_url"], CONFIG["admin_pin"]))
            self.proc.wait()
            if self.stopping:
                return
            if RUNTIME["tunnel_url"]:
                print("!! the public link stopped working (cloudflared exited); phones on the venue Wi-Fi still work: %s."
                      " Starting a new public link..." % RUNTIME["lan_url"])
            RUNTIME["tunnel_url"] = None
            delay = self.retry if time.time() - started > 120 else min(delay * 2, 60.0)
            time.sleep(delay)

    def terminate(self):
        self.stopping = True
        proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()


CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def gameplay_bg_info():
    """What the in-song picture shows besides the QR: the score to beat tonight and who's up next (names as public)."""
    with LOCK:
        board, q = leaderboard(), STATE["queue"]
        top = board[0] if board else None
        nxt = q[0] if q else None
        return {"top": {"name": public_name(top["name"]), "score": top["score"]} if top else None,
                "next": (None if not nxt else "Tonight's #1 (encore)" if nxt.get("encore") else public_name(nxt["name"]))}


def render_gameplay_bg(url, info=None):
    """Stamp the gameplay background: the template, the live sign-up QR, and under the lockup the score to beat
    tonight and who's up next. Clone Hero reads this file at the start of every song, so a fresh stamp between songs
    is what the next song shows."""
    bg = CONFIG.get("gameplay_bg") or {}
    template = ROOT / bg.get("template", "")
    if not bg.get("enabled") or not template.is_file() or not url:
        return False
    if not os.path.exists(CHROME):
        print("!! Chrome not found; gameplay background QR not updated")
        return False
    out = Path(os.path.expanduser(bg["output"]))
    q = bg["qr"]
    work = DATA / "bg-render"
    work.mkdir(parents=True, exist_ok=True)
    html = work / "bg.html"
    fonts = (ROOT / "brand" / "boot").resolve()
    html.write_text("""<!doctype html><meta charset=utf-8>
<style>@font-face{font-family:B;src:url("%s")}@font-face{font-family:M;font-weight:700;src:url("%s")}
html,body{margin:0;width:1920px;height:1080px;overflow:hidden;background:#000}
img{position:absolute;inset:0;width:1920px;height:1080px}
#q{position:absolute;left:%dpx;top:%dpx;width:%dpx;height:%dpx;background:#fff;padding:14px;box-sizing:border-box}
#q svg{width:100%%;height:100%%;display:block}
#live{position:absolute;left:150px;top:790px;width:440px;text-align:center;color:#f4efe6}
#live .eb{font:700 17px M;letter-spacing:.2em;color:#ffd60a}
#live .nm{font:64px/0.95 B;letter-spacing:.02em;margin-top:8px;white-space:nowrap;text-shadow:0 4px 18px rgba(0,0,0,.7)}
#live .sc{font:700 24px M;margin-top:4px;color:#f4efe6}
#live .nx{margin-top:22px;padding-top:16px;border-top:2px solid rgba(244,239,230,.18)}
#live .nx .eb{color:#8fd6ff}#live .nx .nm{font-size:46px}</style>
<img src="%s"><div id=q></div><div id=live></div>
<script src="%s"></script>
<script>var c=qrcode(0,"M");c.addData(%s);c.make();
document.getElementById("q").innerHTML=c.createSvgTag({cellSize:8,margin:0,scalable:true});
var I=%s,L=document.getElementById("live");
function add(p,cls,t){var e=document.createElement("div");e.className=cls;e.textContent=t;p.appendChild(e);return e}
var a=document.createElement("div");L.appendChild(a);
if(I.top){add(a,"eb","#1 TONIGHT \u00b7 SCORE TO BEAT");add(a,"nm",I.top.name);add(a,"sc",I.top.score.toLocaleString("en-US")+" pts")}
else{add(a,"eb","#1 TONIGHT");add(a,"nm","Could be you")}
if(I.next){var n=document.createElement("div");n.className="nx";L.appendChild(n);add(n,"eb","UP NEXT");add(n,"nm",I.next)}
document.fonts.ready.then(function(){[].forEach.call(L.querySelectorAll(".nm"),function(e){var s=parseFloat(getComputedStyle(e).fontSize);
while(e.scrollWidth>L.clientWidth&&s>26){s-=2;e.style.fontSize=s+"px"}})});
</script>
""" % ((fonts / "Bebas-Neue-400.woff2").as_uri(), (fonts / "Space-Mono-700.woff2").as_uri(),
       q["x"], q["y"], q["size"], q["size"], template.resolve().as_uri(),
       (STATIC / "vendor" / "qrcode.min.js").resolve().as_uri(), json.dumps(url),
       json.dumps(info or {"top": None, "next": None})))
    shot = work / "bg.png"
    if shot.exists():
        shot.unlink()
    subprocess.run([CHROME, "--headless=new", "--hide-scrollbars", "--allow-file-access-from-files",
                    "--window-size=1920,1080", "--virtual-time-budget=2000",
                    "--screenshot=%s" % shot, html.resolve().as_uri()],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    if not shot.exists() or shot.stat().st_size < 10000:
        print("!! gameplay background render failed")
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(".ghl-tmp-" + out.name)
    shutil.copy2(shot, tmp)
    os.replace(tmp, out)
    first = not RUNTIME.get("bg_stamped")
    RUNTIME["bg_stamped"] = True
    if first or url != RUNTIME.get("bg_url"):
        print("== gameplay background QR ->", url)
    RUNTIME["bg_url"] = url
    return True


def bg_stamp_pending():
    """press() would answer green with "wait" only because the in-song picture is still being stamped (call LOCKed)."""
    cur = STATE["current"]
    return bool(cur and cur["phase"] == "ready" and mod_driver() and RUNTIME.get("bg_watching")
                and RUNTIME["published_v"] >= RUNTIME["ready_v"] and time.time() - cur.get("calledAt", 0) < 4
                and RUNTIME.get("bg_done_key") != gameplay_bg_key()[0])


def wait_for_bg_stamp(limit=4.1):
    """Green pressed right after a call-up used to be dropped ("wait") for up to 4 s while a fresh Chrome stamped the
    in-song picture, so the player had to press again: the show felt laggy. Hold the press instead, OUTSIDE LOCK
    (the stamp needs it), and answer as soon as the picture is ready, or when press()'s own 4 s gate (from the call-up)
    opens: a press always comes after the call-up, so the hold never passes 4 s (the game's press deadline is 6 s).
    Returns the id of the player it was held for (None if it never waited)."""
    held = None
    end = time.time() + limit
    while time.time() < end:
        with LOCK:
            if not bg_stamp_pending():
                return held
            held = STATE["current"]["id"]
        time.sleep(0.05)
    return held


def gameplay_bg_key():
    url = RUNTIME["tunnel_url"] or RUNTIME["lan_url"]
    info = gameplay_bg_info()
    return (url, json.dumps(info, sort_keys=True)), url, info


def gameplay_bg_watcher():
    """Keep the in-song picture current: re-stamp when the sign-up link, the score to beat or the next player changes.
    Never while a song plays (the file is read when the next one starts). Lobby changes wait a few seconds (sign-ups
    come in bursts); a player called up is stamped at once, and their green waits for it (press(): "wait")."""
    last, fails, retry_at, tried_call = 0.0, 0, 0.0, None
    while True:
        time.sleep(0.25)
        cur = STATE["current"]
        # (a new call-up gets one immediate try even while failures are backing off: their green waits for it)
        fresh_call = bool(cur and cur.get("phase") == "ready" and cur.get("calledAt") != tried_call)
        if RUNTIME["scene"] == "Gameplay" or (time.time() < retry_at and not fresh_call):
            continue
        try:
            key, url, info = gameplay_bg_key()
        except Exception as e:
            print("!! gameplay background:", e)
            time.sleep(1)
            continue
        if key == RUNTIME.get("bg_done_key"):
            continue
        cur = STATE["current"]
        urgent = bool(cur and cur.get("phase") == "ready")
        if not urgent and time.time() - last < 3:
            continue
        last = time.time()
        if urgent:
            tried_call = cur.get("calledAt")
        ok = False
        try:
            ok = render_gameplay_bg(url, info)
            if ok:
                RUNTIME["bg_done_key"] = key
        except Exception as e:
            print("!! gameplay background:", e)
        if ok:
            fails = 0
        else:  # back off: 2, 4, 6 ... up to 30 s (it never stops trying, and green stops waiting after 4 s anyway)
            fails += 1
            retry_at = time.time() + min(30, 2 * fails)


def restore_gameplay_bg():
    """Put the original background back so Practice Mode / plain Clone Hero look exactly as before."""
    bg = CONFIG.get("gameplay_bg") or {}
    template = ROOT / bg.get("template", "")
    if bg.get("enabled") and template.is_file() and RUNTIME.get("bg_stamped"):
        out = Path(os.path.expanduser(bg["output"]))
        tmp = out.with_name(".ghl-tmp-" + out.name)
        shutil.copy2(template, tmp)
        os.replace(tmp, out)
        print("== original gameplay background restored")


GAME_LOGS = Path.home() / "Library/Logs/srylain Inc_/Clone Hero"  # Unity's Player.log (the game copy shares it)


def keep_game_logs(names=("Player-prev.log", "Player.log")):
    """Unity overwrites Player.log on every launch (and Player-prev.log on the next), so a show's log (controller
    connects/disconnects, errors) is gone two launches later. Keep copies in DATA/game-logs, named by when that game run
    STARTED (a later copy of the same run replaces the earlier one); the newest 30 are kept."""
    if SANDBOX:
        return
    try:
        dst = DATA / "game-logs"
        dst.mkdir(parents=True, exist_ok=True)
        for name in names:
            f = GAME_LOGS / name
            if f.is_file() and f.stat().st_size > 0:
                st = f.stat()  # by when the game run began (Unity makes a new Player.log per launch)
                stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(getattr(st, "st_birthtime", st.st_mtime)))
                shutil.copy2(f, dst / ("%s-%s" % (stamp, "Player.log")))
        for old in sorted(dst.glob("*-Player.log"))[:-30]:
            old.unlink()
    except OSError as e:
        print("!! could not keep the game logs:", e)


def open_display(port):
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    url = "http://localhost:%d/display" % port
    if not os.path.exists(chrome):
        subprocess.Popen(["open", url])
        return
    profile = DATA / "display-browser-profile"
    proc = subprocess.Popen([chrome, "--user-data-dir=%s" % profile, "--kiosk", "--app=%s" % url,
                             "--no-first-run", "--disable-session-crashed-bubble",
                             "--autoplay-policy=no-user-gesture-required"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    RUNTIME["display_pid"] = proc.pid


def render_host_setup(port):
    """Retry a failed private setup render and replace a LAN QR when the tunnel arrives."""
    retry_at = 0
    while True:
        time.sleep(1)
        url = RUNTIME["tunnel_url"] or RUNTIME["lan_url"]
        if not url or (RUNTIME.get("host_ready") and RUNTIME.get("host_url") == url):
            continue
        if time.time() < retry_at or RUNTIME.get("scene") == "Gameplay":
            continue  # leave the live song's CPU resources alone
        try:
            tmp = DATA / "hostsetup-tmp.png"
            if chrome_shot("http://127.0.0.1:%d/host-setup" % port, tmp, reduced=True, min_bytes=10000):
                os.replace(tmp, DATA / "hostsetup.png")
                RUNTIME["host_url"] = url
                RUNTIME["host_v"] = RUNTIME.get("host_v", 0) + 1
                RUNTIME["host_ready"] = True
                retry_at = 0
            else:
                print("!! host setup render failed; retrying")
                retry_at = time.time() + 5
        except Exception as e:
            print("!! host setup:", e)
            retry_at = time.time() + 5


def ghl_on_port(port):
    """True if a Gig Mode stage server already answers on this port."""
    import http.client
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        c.request("GET", "/api/ping")
        r = c.getresponse()
        return r.status == 200 and json.loads(r.read().decode("utf-8")).get("app") == "ghl-stage"
    except Exception:
        return False


def ghl_is_me(port):
    """True if 127.0.0.1:port (what the game and the screen renders use) answers as THIS Gig Mode process."""
    import http.client
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        c.request("GET", "/api/ping")
        r = c.getresponse()
        d = json.loads(r.read().decode("utf-8")) if r.status == 200 else {}
        return d.get("app") == "ghl-stage" and d.get("pid") == os.getpid()
    except Exception:
        return False


def port_owner(port):
    """'Python (pid 123)' for whatever listens on the port, for the log."""
    try:
        out = subprocess.run(["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-Fpc"], capture_output=True,
                             text=True, timeout=5).stdout.split()
        pid = next((x[1:] for x in out if x.startswith("p")), "?")
        cmd = next((x[1:] for x in out if x.startswith("c")), "another app")
        return "%s (pid %s)" % (cmd, pid)
    except Exception:
        return "another app"


def reaches_us(server, port):
    """macOS lets another app listen on 127.0.0.1:port next to our 0.0.0.0:port, and that app then gets every local
    connection (the game, the tunnel, the screen renders). Connect once and see if it lands in our own queue."""
    import select
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect(("127.0.0.1", port))
        mine = probe.getsockname()
        deadline = time.time() + 1.0
        while time.time() < deadline:
            ready, _, _ = select.select([server.socket], [], [], max(0.0, deadline - time.time()))
            if not ready:
                return False
            conn, peer = server.socket.accept()
            conn.close()
            if peer == mine:
                return True
        return False
    except OSError:
        return False
    finally:
        probe.close()


def bind_stage_port():
    """The stage server on the configured port (8765), or 8775, 8785... if another app holds it. The port in use is
    written to GHL/stage-port, which the game reads. Exits if Gig Mode itself is already running."""
    base = int(CONFIG["port"])
    for port in range(base, base + 60, 10):  # (each port + 1 is the game-events socket)
        try:
            server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        except OSError:
            if ghl_on_port(port):
                sys.exit("!! Gig Mode is already running (port %d). Close the other Gig Mode window first." % port)
            print("!! port %d is used by %s; trying %d" % (port, port_owner(port), port + 10))
            continue
        if reaches_us(server, port):
            if port != base:
                print("== Gig Mode is on port %d (the game follows it)" % port)
            try:
                stage_fx_flag("stage-port").write_text("%d\n" % port)
            except OSError as e:
                print("!! could not write the stage port for the game:", e)
            return server, port
        server.server_close()
        print("!! port %d on this Mac is answered by %s; trying %d" % (port, port_owner(port), port + 10))
    sys.exit("!! no free port for Gig Mode (%d-%d are all in use by other apps)" % (base, base + 50))


class StampedLog:
    """stage-manager.log had no times, so a lag report could not be lined up with ghlmod.log or the game's log.
    Prefix every line with the date and time (print writes the text and the newline separately: stamp after \\n)."""
    def __init__(self, stream):
        self.stream, self.at_start = stream, True

    def write(self, text):
        out = []
        for part in text.splitlines(True):
            if self.at_start and part:
                out.append(time.strftime("%m-%d %H:%M:%S "))
            out.append(part)
            self.at_start = part.endswith("\n")
        return self.stream.write("".join(out))

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def main():
    # Each in-game render opens ~45 connections at once (page, fonts, sprites); Python's default listen backlog
    # of 5 drops some and Chrome captures a half-styled page.
    ThreadingHTTPServer.request_queue_size = 128
    if "--embedded" in sys.argv:  # the game's own start writes to stage-manager.log: give every line its time
        sys.stdout, sys.stderr = StampedLog(sys.stdout), StampedLog(sys.stderr)
    server, port = bind_stage_port()
    RUNTIME["port"] = port
    RUNTIME["lan_url"] = "http://%s:%d" % (lan_ip(), port)

    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)  # Terminal window closed
    # Serve right away: the in-game screen renderers load their pages from this server.
    threading.Thread(target=server.serve_forever, daemon=True, name="http").start()
    tunnel = None
    try:
        print("Guitar Hero Live stage manager  [%s]" % ("IN-GAME (Guitar Hero Live app)" if mod_driver() else "CHROME stage screen + Clone Hero"))
        print("  songs loaded:", len(SONGS))
        print("  display:     http://localhost:%d/display" % port)
        print("  wifi signup: %s" % RUNTIME["lan_url"])
        print("  admin PIN:   %s   (control panel: /admin)" % CONFIG["admin_pin"])
        try:  # which copy is running (several copies of the app exist; an old one brings the old lag back)
            print("  running:     %s  (server %s, %s)" % (ROOT, hashlib.md5(Path(__file__).read_bytes()).hexdigest()[:8],
                                                         time.strftime("%Y-%m-%d %H:%M:%S")))
        except OSError:
            pass
        if mod_driver() and not SANDBOX:
            warn_other_copies()
        threading.Thread(target=score_watcher, daemon=True).start()
        threading.Thread(target=lights_listener, daemon=True).start()
        keep_game_logs()
        if mod_driver():
            shutil.rmtree(DATA / "ui", ignore_errors=True)  # UI frames depend on tonight's state: always fresh
            threading.Thread(target=frame_renderer, args=(port,), daemon=True).start()
            threading.Thread(target=render_bg_video, args=(port,), daemon=True).start()
            if os.environ.get("GHL_FROM_GAME") and game_running():
                # started by GIG in the game: hand the screen straight back to the game (this window keeps the links).
                # Bring back the copy that is actually open: `open` on the show app while an OLD copy is the one
                # running would start a second game on top of it (seen 11/18: two games at once).
                running = running_game_bundles()
                main_app = main_game_bundle()
                target = main_app if main_app in [str(Path(b).resolve()) for b in running] else (running[0] if running else main_app)
                subprocess.Popen(["open", target])
        tunnel = start_tunnel(port) if CONFIG["tunnel"] and "--no-tunnel" not in sys.argv else None
        if tunnel:
            for _ in range(40):  # the public link usually appears within ~5-15 s
                if RUNTIME["tunnel_url"]:
                    break
                time.sleep(0.5)
        if mod_driver():
            threading.Thread(target=render_host_setup, args=(port,), daemon=True).start()
        if "--no-bg" not in sys.argv:
            try:  # a Chrome that hangs at launch (60 s timeout) once stopped the whole stage server here
                key, url, info = gameplay_bg_key()
                if render_gameplay_bg(url, info):
                    RUNTIME["bg_done_key"] = key
            except Exception as e:
                print("!! gameplay background:", e)
            RUNTIME["bg_watching"] = True
            threading.Thread(target=gameplay_bg_watcher, daemon=True).start()
        if mod_driver() and not SANDBOX and not game_running():
            # The Guitar Hero Live copy is only used for shows: start every night with an empty high-score table, so
            # Clone Hero saves every audience score (it only writes a new best) and the Top 5 fills in by itself.
            note = fresh_scores()
            if note:
                print("== fresh high-score table for tonight (%s)" % note)
        if CONFIG.get("launch_game") and "--no-game" not in sys.argv and not game_running():
            app = os.path.expanduser(CONFIG.get("game_app_path") or "")
            subprocess.Popen(["open", app] if app else ["open", "-a", CONFIG["game_app_name"]])
            time.sleep(6)
        if CONFIG["open_display"] and not mod_driver() and "--no-display" not in sys.argv:
            open_display(port)
        startup = time.time()
        copies_at = time.time()
        while True:
            time.sleep(1)
            if mod_driver() and not SANDBOX and time.time() - copies_at > 15:
                copies_at = time.time()
                if other_game_copies() != RUNTIME.get("old_copies", []):
                    warn_other_copies()
            if "--embedded" in sys.argv and time.time() - startup > 60 and time.time() - RUNTIME.get("mod_seen", 0) > 20:
                # The mod went quiet. After the Mac sleeps the wall clock jumps, so that alone stopped the server
                # (and changed the public link) 3 times on 11/18 with the game still open. Stop only if it's gone.
                if not show_game_running():
                    print("== game closed; stopping bundled stage manager")
                    break
                if not RUNTIME.get("quiet_warned"):
                    RUNTIME["quiet_warned"] = True
                    print("!! the game has not checked in for 20 s but is still open (Mac asleep?); staying up")
            elif RUNTIME.get("quiet_warned") and time.time() - RUNTIME.get("mod_seen", 0) < 5:
                RUNTIME["quiet_warned"] = False
                print("== the game checked in again")
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel:
            tunnel.terminate()
        stop_capture()
        restore_gameplay_bg()
        keep_game_logs()
        print("stopped.")


if __name__ == "__main__":
    main()
