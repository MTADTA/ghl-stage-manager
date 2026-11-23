# Guitar Hero Live: bugs from the 10/10 gig (logged 10/12)

> Working log from the project, kept as written between me and the coding agents, with audience names, machine-specific
> details and private file references removed. Folders it mentions (snapshots, install backups, the game copy) are not in this repository.

The show went well.

## STATUS 10/15: fixed and installed (bug-fix release)
- **Installed:** mod DLL `7972cca1…`, plus the bundled server/static/brand files and new stage videos.
  - **Undo:** `/usr/bin/python3 mod-src/install_bugfix_release.py --rollback`, which puts the 10/10 release back exactly (tested).
  - **Full snapshot before any change:** `SNAPSHOT-working-2026-10-15-pre-bugfix/` (renamed `…pre-bugfix.noindex` 11/19 so Spotlight stops opening its old app: that was the 11/18 "still laggy").
- **1 Lag:** FIXED.
  - One headless Chrome now stays open (DevTools over a pipe, `CaptureChrome` in server.py). Each state is pushed into 3 open pages and captured.
  - Screen change is 0.2–0.7 s (was 4–6.4 s). The first screen after launch is 1–3 s.
  - The old one-Chrome-per-image path is the automatic fallback.
  - The capture Chrome is closed during songs and reopens during the results hold.
  - The guitar press lock is 1.5 s max (was 4 s), and the server debounce is 0.4 s (was 1 s).
- **2 Red:** FIXED.
  - Red twice (within 8 s, on the same person) removes them and reopens their song, in the lobby AND on the start screen.
  - The warning names the person ("Remove X? Press [red] again"). It no longer breaks names containing "red".
- **3 Browse:** FIXED.
  - Strum up/down (Rewired actions 5/14) or yellow/blue moves a highlight through the line in the lobby. Green calls the highlighted person, and red twice removes them. Everyone else keeps their place.
  - All presses go to the server in order on one thread.
  - The strum read has its own guard, so a guitar's strum mapping can never stop green/red/START.
- **4 Controller:** HARDENED (cause still unconfirmed).
  - Every connect/disconnect is logged in ghlmod.log, along with who got the controller.
  - A red banner shows if the stage player loses their guitar, and a reconnected guitar is given back to the stage player if nobody else has it.
  - The server keeps copies of Player.log in `GHL/stage-manager/game-logs`.
  - **Also fixed:** ⌘Q could hang the game at 100% CPU or crash it (the mono_crash files). The mod's threads now stop before Unity shuts down.
- **5 Drums:** FIXED, then redrawn again 10/17 (round 3h) to the 10/10 kit's size and dark look, with the snare and floor tom tucked behind the kick like a real kit. Details in NEXT-ROUND-2026-10-15.md.
  - New kit with a snare, round toms, a 3-leg floor tom, tripods and two crashes.
  - Kick/snare/cymbals now play on the highway's beat.
  - The in-song picture moves the kit left (kitx=1196) so the snare isn't under the QR. The GHLStageFx kick glow was moved with it.
- **6 Notes vs lines:**
  - **Lobby:** FIXED. The notes were spaced a little differently from the beat lines; now every note sits on a line, moved by transform like the lines. Kick/snare follow the same beat.
  - **In-song:** Clone Hero's notes were seen sitting on its beat lines in consecutive frames. The patterned highway art underneath could not be judged; the stage lights are ruled out (the highway is opaque).
- **7 Needs score:** FIXED in code, not yet seen live.
  - At the results screen the mod sends the game's own numbers to `/api/mod/result` (PlayerStats: TotalScore, stars, notes). The score file is now only a fallback.
  - A live bot test could not reach the results screen: the game is frozen by macOS when not in front.
- **Tests:**
  - 13 new server tests plus Codex's 25 phone-first suite all pass.
  - Codex's song-end suite: 19/20, where the 1 is the intended red-on-start-screen change.
  - Live in the real app, with the mod's test commands (no keyboard focus): browse, red-twice in the lobby and on the start screen, green start, in-song art, clean quit.
- **Jordan's hands-on checklist (guitar):**
  - Open the app normally (not from the background) and confirm Gig Mode starts by itself within a few seconds. The final build's self-start was only seen working with an earlier build.
  - Strum to browse.
  - Red twice on a person.
  - START twice to leave GIG.
  - Play one song to the results screen, then look for `== score from the game:` in stage-manager.log.
  - Switch the guitar off mid-song: you should see the red banner, then "Guitar back" when it reconnects.
- **Test hooks added to the mod** (admin-PIN commands only): `hostdone` and `results`. Sending `results` mid-song would end a live turn with its score so far. With `gigtest`, the server no longer sees the home screen.

---

**Evidence sources:**
- `~/Library/Application Support/com.jordanrein.GuitarHeroLive/GHL/ghlmod.log`
- `GHL/stage-manager/stage-manager.log`
- `~/Library/Logs/srylain Inc_/Clone Hero/Player-prev.log`. This holds the session that ended 00:21. Unity overwrites it after two launches, so copy it right after a show.

## 1. Lag on the stage screen (HIGH)
- **Symptom:** the stage screen reacts slowly. Green/red feel delayed, and the start screen appears seconds after green.
- **Evidence:** every in-game screen change waits for a headless-Chrome render. The `in-game lobby ready` times in stage-manager.log were 4.0–6.4 s; one was 1.8 s.
- **Why:**
  - `frame_renderer` (server.py) renders 3 Chrome screenshots per state change: still, UI rest and UI peak. The start screen adds a `--dump-dom` pass.
  - The mod polls every 250 ms, then decodes one texture per frame.
  - After a press, the mod locks input until the new screen arrives (at most 4 s).
  - The server answers `wait` to green while the start screen is still rendering.
- **Fix direction:** designed but never built at the time of this log (a private review note).
  - Pre-render the next player's start screen and the after-song lobby, so a press only swaps in a finished image.
  - Alternatives: keep one Chrome running (DevTools protocol) instead of launching it per shot, or draw the changing text natively in the mod.
  - Also measure the game-side frame time with the stage lights on (mod `perf` command) to rule them out.

## 2. Red should remove the player and reopen their song (MEDIUM, behaviour change)
- **Now:**
  - In the lobby, red twice = skip, which already reopens the song. It happened 6 times on the night (`-- skipped: … (song reopened)`).
  - On the start screen, red sends the player back to the FRONT of the line (`press()` → `back_to_queue()`).
  - With the encore lined up, red rotates to the next ranked player.
- **Wanted:** red removes the person and makes their song available again, including from the start screen. Keep a confirm, such as press twice, so one accidental press can't remove someone.
- **Where:** `server.py` `press()` (the ready + red branch, and `skip_next`), plus the start-screen help text in `static/display-ui.js`.

## 3. Browse the line from the guitar (MEDIUM, feature)
- **Wanted:** when the next person isn't available but is still around, move through the line and call someone else up, without removing anyone.
- **Sketch:**
  - Strum up/down (or yellow/blue) moves a highlight in the lobby's Up next / On deck list, and green calls the highlighted person.
  - The server needs a "selected" index in the lobby state.
  - The mod must forward those buttons in the lobby. Yellow/blue sending was removed for lag, so reconsider it together with #1.
- **Evidence of need:** from 00:22 on, the host left GIG 5 times (START twice, logged `GIG left with START`) and played songs from QUICKPLAY. Those plays have no turn or score in the stage manager.

## 4. Controller stopped responding (HIGH, cause not confirmed)
- **Evidence:**
  - 00:20:05: Everlong started.
  - 00:20:32: the show pause menu came up.
  - The game was then quit normally: Player-prev.log ends with Unity's shutdown memory report, and there's no crash report.
  - 00:21:57: the game relaunched. The server log shows `Gameplay -> UnpackingScene`. The new backend exited with "already running" (expected), and the original server kept the session.
- **Hypotheses, most likely first:**
  1. **Wireless guitar dropout.** Clone Hero opens the pause menu by itself when a controller disconnects mid-song, and a disconnected guitar can't resume it. Check the receiver and batteries. The macOS log no longer covered 00:20 when checked.
  2. **The trimmed show pause menu** (`GuardPauseMenu` shrinks BaseMenu names, labels and boxes) stops taking input in some state. It worked on the other pauses that night (23:59, 00:08, 00:36, 01:13).
  3. **Input blocking.** The mod disables all Rewired maps while the stage screen is up. A controller that reconnects while blocked gets new maps, and `Unblock()` only re-enables the maps it recorded.
- **Next time:** note the time and the screen that was up, and copy Player.log after the show.

## 5. Drum kit looks unnatural and has no snare (LOW, visual)
- **Where it shows:** the lobby/start-screen background video, the home-screen video, and the in-song background (`brand/gameplay-stage.png` is a capture of the same stage drawing).
- **What's drawn now** (`static/display.html`, the "drum kit on a low riser" SVG, ~line 505): a kick, a floor tom, two rack toms, a hi-hat and two cymbals. **There is no snare.**
- **Why it looks off:**
  - The toms are flat black rectangles. The rack toms have their head ellipse at the bottom, so they read as upside-down boxes, and they float with no mounts.
  - The floor tom is a box on two splayed legs, so it reads as a stool.
  - The kick is drawn face-on (a round head with the logo), while everything else is side-on. It has no shell depth and no spurs.
  - The cymbal stands are bare lines with no tripods. There's no throne and no hi-hat pedal.
- **Fix direction:**
  - Redraw the kit in one consistent 3/4 view: a snare on a stand between the hi-hat and the kick, round toms with top heads and hoops, a floor tom on three legs, tripod stands, and a throne behind.
  - Keep the kick head and logo where they are (x 1308, y 494), because `GHLStageFx.cs` (`Kick`) puts its in-song glow there. If the kick moves, update those coordinates too.
  - Then re-render the lobby and home loops in the sandbox server (~6–12 min, see the render notes), run `tools/check_video`, and re-capture `brand/gameplay-stage.png`.
  - Optional: give the snare its own hit on beats 2 and 4 in the animation (it's synced to the kick, 16 per loop).

## 6. Notes move at a different speed than the lines on the track (MEDIUM, cause not found yet)
- **Reported:** "in gig mode, the notes are moving at a different speed than the lines on the track." Which screen hasn't been confirmed yet: the lobby/start-screen highway, or the real highway during a song.
- **Ruled out so far (checked 10/13):**
  - **Lobby/start-screen video** (`data/bg-video/stage-1791652257.mp4`): frames 100 and 104 were compared. Notes and lines moved the same distance (~15 px mid-track, ~25–27 px near the strike line). The code also gives both the same speed: `display.html` `Highway.render()` moves the board by 2w per loop and the notes by R·h = 2w. Notes and lines live in one video, so the in-game player can't split them either.
  - **Song speed:** gig songs reset song speed to 100% (`ResetSongOptions`), and the log never recorded a change, so they ran at normal speed.
  - **Custom highway:** none. `profiles.ini` has `highway_name = default`, and `Custom/Highways` is empty.
  - **Game clock:** the mod never touches `timeScale` or the frame rate.
- **What could still explain it:**
  1. **Lobby highway: the notes don't sit on the lines.** Note spacing is `R/round(R/0.13)` (~0.13 of the track height), while the line spacing is ~1/8 (0.125). Every note sits at a different point between two lines, so they can look out of step even though they move together. Fix: put the notes on the beat grid, with one slot per line (or half-line) in `Highway.build()`. This needs a background re-render.
  2. **In-song: the stage lights** (the `GHL/stage-fx` file is present, so they're on). Coloured beams behind the highway swing once per bar and flash on every beat. Clone Hero's highway is partly see-through, so the beams could read as lines moving at their own speed. Test: play one song with Stage lights OFF (Stage Control) and compare.
  3. **In-song: Clone Hero's own beat lines** are drawn from the chart's tempo map. A chart with a bad tempo map makes the lines drift against the notes. If so, it would be song-specific and happen in QUICKPLAY too.
- **Next time:** note which screen and which song, or take a 5-second phone video.

## 7. Also seen in the logs
- **"Needs score" turns:** four players on the night.
  - One of them reached the results screen but no score was captured. Clone Hero only writes a score when it beats the saved best for that song, so a song played earlier in the night with a higher score writes nothing.
  - Possible fix: read the score from the results screen in-game (native) instead of from the score file.
- **No turn recorded:** several songs were played outside the line (the QUICKPLAY plays, see #3).
- **First lobby QR:** after each launch, the first lobby render used the Wi-Fi link (http://<LAN address>…) for a few seconds before the public link arrived. This is by design, and the screen updates itself.
