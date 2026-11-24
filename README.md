# ghl-stage-manager

[![tests](https://github.com/MTADTA/ghl-stage-manager/actions/workflows/tests.yml/badge.svg)](https://github.com/MTADTA/ghl-stage-manager/actions/workflows/tests.yml)

The software that runs my live show where the audience plays a rhythm game against my band. People in the room scan a
QR code, pick a song on their phone and join a line. When their name comes up they take the guitar controller on
stage and play the song in Clone Hero while the band plays it for real. This repository is the part I built around the
game: the server that runs the night, the browser pages and the tests.

Not affiliated with or endorsed by Activision or the Clone Hero team. Guitar Hero and Guitar Hero Live are trademarks
of Activision. "Guitar Hero Live" appears in this code only because it is what I called my show night; this project
has nothing to do with Activision's game of that name. Clone Hero belongs to its developers. No game files and no game
code are in this repository.

## What it does

```
 audience phones            my phone                projector / game screen
 sign-up page               control panel (PIN)     lobby, start screen, results card
      |                          |                          ^
      v                          v                          |
 +---------------------------------------------------+     |
 |  stage-manager/server.py  (Python, stdlib only)   |-----+  rendered screens
 |  REST/JSON API - queue - scoreboard - public link |<----+  guitar buttons, scene, score
 +---------------------------------------------------+     |
      | reads only                                          |
      v                                              in-game overlay (C#, Unity)
 the game's Songs folder and score file              not in this repository, see below
```

- **Server** (`stage-manager/server.py`, about 3,000 lines, Python 3.9 standard library, no packages to install).
  An HTTP server with a JSON API: public state and sign-up (`/api/public`, `/api/signup`), a PIN-protected control
  panel (`/api/admin`), guitar button presses from the stage (`/api/display/press`) and endpoints the mod polls
  (`/api/mod/...`). It keeps the line, the night's Top 5 and the encore, filters rude names, opens an anonymous
  Cloudflare quick tunnel so phones on cellular can reach it, restarts that tunnel if it drops, and moves to another
  port if something else is answering on its own.
- **Browser pages** (`stage-manager/static/`): the phone sign-up page, the phone control panel, and the stage screen.
- **Game-driven lights** (`GameLights` in `server.py`): the game reports what's happening in the song over UDP, and
  the server turns it into lighting cues. In the room, momentary hits on the band's MaestroDMX controller over OSC,
  on top of the band's own show: a blinder for Star Power, a strobe for a new #1, and one dial (subtle, show, big)
  for how often the room reacts. On the guitar, its LEDs follow the song. "Rehearse the lights" plays every moment
  before doors, and a missing controller or guitar never touches the game.
- **Small Swift tools** (`stage-manager/tools/`): encode and check the looping stage videos, drive the guitar's LEDs.
- **Launchers** (the three `.command` files): how I start the show, the Chrome backup mode and practice on the show Mac.
- **Tests and benchmarks** (`tests/`, `bench/`).
- **Not here: the in-game overlay.** On show night a C# mod of mine (about 2,500 lines) runs inside a copy of the
  game. It draws the lobby, start screen and results card over the game, reads the guitar's green and red buttons so
  the show can be run from the stage, and reports the end-of-song score to the server through the `/api/mod/...`
  endpoints. I have not published it; the reason is under "What is left out". Without it the server still runs the
  whole sign-up, queue and scoreboard flow with the stage screen in a browser (`--driver chrome`), which is also my
  backup mode at a show.

## Timeline

This repository is one commit because the project was never under version control; I kept dated folder snapshots
instead, and I have not turned those into commits. The real dates:

- 2026-10-09: first working version.
- 2026-10-10: first show in front of an audience.
- 2026-10-12: bugs from that night written up (`docs/bugs-after-gig-2026-10-10.md`).
- 2026-10-15 to 2026-10-17: fix rounds (`docs/next-round-2026-10-15.md`). The main one: every screen change used to
  start a new headless Chrome to render it, and the server log showed 4.0 to 6.4 seconds per change on show night.
  Keeping one Chrome open and pushing state into it brought that to 0.2 to 0.7 seconds in testing on my Mac.
- 2026-11-19: performance pass (the scripts in `bench/` come from it).

The two logs in `docs/` are the working notes as written, with audience names and machine details removed.
`docs/run-of-show.md` is the operator's sheet I use on the night. All three mention the in-game mod, install scripts
and a "Show Check" that are not in this repository.

## Run it

macOS, with the system Python 3.9 or newer. To see the server and the sign-up flow with the included sample songs:

```
cd stage-manager
cp config.example.json config.json
python3 server.py --driver chrome --no-game --no-tunnel --no-display --no-bg
```

Use the address it prints: if port 8765 is busy it moves to 8775, then 8785. On the first run it also prints a
warning that it could not write `data/stage-port`; that file is only for the game and the warning can be ignored. It
prints the address and a generated admin PIN (leave `admin_pin` empty and one is created and saved to your local
`config.json`, which is git-ignored). Open `/signup` on that address for the phone page, `/admin` for the control
panel and `/display` for the stage screen. `sample-data/` holds three made-up songs, clearly labelled as sample data,
so the song list is not empty.

What I checked in this published tree: the server starts, lists the sample songs, accepts a sign-up through the API,
and serves the pages. I did not check how the pages look here, and they will look bare, because of what is left out
(next section). A real show also needs the game, a guitar controller, Google Chrome and `cloudflared`. The sign-up
page checks names against a block list of swear words and slurs (in `server.py`, with tests), because names go up on
the projector.

## Tests

Five suites, 65 tests, plain `unittest`, no game or network needed (they use fake songs, a fake tunnel program and a
stand-in lighting receiver):

```
for t in tests/check_*.py; do /usr/bin/python3 "$t"; done
```

All 65 passed in this tree on Python 3.9.6 on the day of the commit. They cover the queue and scoring rules, the
phone-first flow, the lighting cues, the port fallback and the tunnel restart. GitHub Actions runs all five suites on
every push (macOS, Python 3.12). The scripts in `bench/`
are the timing checks from the performance pass; they need the rendered stage videos from my show Mac, so they are a
record and do not run from this tree.

## What is left out, and why

- **Clone Hero itself and anything taken from it or any other game**: the app, its DLLs, asset files, and the note,
  highway and star sprites the stage screen uses (`stage-manager/static/gh/`). They are not mine to share. The pages
  still load without them but the highway art is missing, and the in-game screen capture waits for sprites that are
  not there. To run the full show you need your own Clone Hero install and your own art at those paths.
- **The in-game mod (C# source and built DLLs) and the scripts that install it into a copy of the game.** The mod
  is tied to the internals of one Clone Hero build, and those internals belong to the game's developers, whose
  licence does not allow modifying the game. So I am keeping that code private. The "Show Check" script went with it
  because it depends on the install scripts.
- **Fonts and the QR library**. The pages expect Bebas Neue and Space Mono (`static/fonts/`) and
  `static/vendor/qrcode.min.js` (qrcode-generator). Those are other people's work, so fetch them from their own
  sources. Without the QR library the QR codes do not draw.
- **My logo, the logo and icon templates (`stage-manager/brand/`), stage artwork, background templates and the
  rendered stage videos**: brand and media files, some large. The server's in-song background stamp
  (`gameplay_bg`, off in the example config) needs a template image from that folder.
- **Everything from real nights**: the sign-up line, names, scores, score files, song lists, logs and crash dumps.
- **My settings file**, which holds the control-panel PIN. `config.example.json` is the template.
- **Song charts and audio**. None were ever in the project; the server only reads the folder you point it at.
- **Snapshots, install backups, promo material and a 3D-printed trophy model** that lived in the same folder.

## How it was made

I design, direct, test and decide what ships; AI coding agents write most of the code.

## Credits

The LED report codes in `stage-manager/tools/santroller_leds.swift` come from the public PlasticBand documentation
(the codes YARG sends) and the Santroller project. I am not affiliated with either. The Instagram and Spotify glyphs on
the sign-up page are those companies' marks.

## Licence

All rights reserved. There is no open-source licence on this repository; it is published to be read. Game names,
trademarks and logos mentioned here belong to their owners.
