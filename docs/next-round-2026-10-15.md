# Guitar Hero Live: round 2 + 3 (2026-10-15)

> Working log from the project, kept as written between me and the coding agents, with audience names, machine-specific
> details and private file references removed. Folders it mentions (snapshots, install backups, the game copy) are not in this repository.

Installed: round 3h, mod DLL `0ef96e23…`.
- **Before a show:** double-click **Show Check.command** in this folder. It only looks and changes nothing, and takes about 20 s. It checks:
  - the install, signature and stage videos;
  - Chrome and cloudflared;
  - port 8765, internet and the guitar;
  - leftover test settings, disk space, and last show's line;
  - all server tests, run against the installed server.
- **Undo one release at a time:** `/usr/bin/python3 mod-src/install_release.py --rollback`. The order is round 3h → 3g → 3f → 3e → 3d → 3c → 3b → 3 → round 2 → bug-fix → 10/10.
- **Stack of installs:** `install-backups/RELEASES.txt`.
- **Full snapshot before all of this:** `SNAPSHOT-working-2026-10-15-pre-bugfix.noindex/` (renamed 11/19 so Spotlight stops opening its old, laggy app).

## Round 2: tested here, safe to use

**Stage art**
- **Drum kit:** redrawn at real sizes (22" kick, 12/13" racks, 16" floor tom, 14" snare and hats, 20" ride, two 18" crashes). It's mirrored for balance and plays on the highway's beat.
- **Speakers:** they move like real drivers (cap, cone and surround at their own depth) and no longer glow. Lobby and home videos were re-rendered.
- **In-song picture:** the backline is symmetrical (bass rig at x 724, 4x12 at 960, kit at 1196).
- **In-song panel:** under the logo, "#1 TONIGHT · SCORE TO BEAT" and "UP NEXT". It's re-stamped between songs, because Clone Hero reloads the picture at every song start.
- **Lobby when nobody's in line:** cover art of the songs still up for grabs.

**Phone (sign-up page)**
- **On deck:** a yellow "You're on deck" banner when you're next, and a green "Head to the stage now" when called up. The tab title changes too.
- **Alert me when I'm next:** sound, a buzz on Android, and the screen stays awake. iPhones can't vibrate from a web page.
- **Result card:** after their turn, players get a 1080×1350 card with name, song, score, stars and "#2 of 14 tonight", which they can share straight to a story or save.

**Stage Control health strip:** Game · Guitar (named) · Public link · In-song art · Lights.

## Round 3: built and unit-tested, needs one check in the real game

Everything here falls back to the round 2 behaviour if anything is off.

- **Results card:** a branded card for 15 s after a show song (name, score, stars, rank tonight, up next).
  - Green on the card calls the next player.
  - It uses the game's own end-of-song score.
  - Phone switch: **Results card**, ON by default.
- **Star Power:** the in-song light beams turn blue while it's active (read live from the game).
- **Moving in-song speakers:** they follow the song's audio, with the chart's beats as a fallback. Phone switch **Moving speakers**, **OFF** until we've seen it in the real game.
- **Lefty players:** a sign-up tick box flips the game for their song only. It's restored at the menu, on quit, and after a crash.
- **Lights:** both phone switches are **OFF** until tested on the hardware.
  - **Guitar lights:** your Revival Kit (Santroller) gets YARG's game events: star power, meter, multiplier, solo and miss.
  - **MaestroDMX hits:** a blinder on call-up and Star Power, and a strobe burst for a new #1, rate-limited and on top of the band's show. It never touches the palette, pattern or fog.

## Round 3c: Gig Mode no longer depends on port 8765 being free (tested in the real app)
- **What happened:** during testing, another app (a Claude session's web server) was using 127.0.0.1:8765.
  - macOS still let Gig Mode start, but every local connection went to the other app instead: the game's polls, the screen renders and the public link.
  - The game showed "Warming up the amps" forever. This could happen at a show.
- **Now:**
  - Gig Mode checks that the port really reaches itself. If not, it moves to 8775 (then 8785…) and writes the port to `GHL/stage-port`, and the game follows it within 2 s.
  - Tested with the other app still on 8765: Gig Mode came up on 8775, the game found it 4 s after launch, and a second copy correctly said "already running".
- **Launchers:**
  - Gig Mode, Practice Mode, the Chrome backup and the installer now recognise Gig Mode by its process.
  - Before, Practice Mode would have offered to kill whatever app held 8765.
- **Public link:** the tunnel now points at 127.0.0.1 instead of "localhost".

## Round 3d: the public link survives a dropout (tested with a fake cloudflared, then in the real app)
- **Public link:**
  - If cloudflared stops mid-show, or the Mac had no internet at launch, it's started again: after 5 s, then backing off to once a minute.
  - Meanwhile the QR shows the Wi-Fi link. The new public link goes onto the lobby QR and the in-song picture by itself.
  - Before, the public link was gone for the night and the QR kept showing a dead link.
- **Players' phones:**
  - After ~16 s without reaching the stage, the phone shows "Can't reach the stage. You keep your place in line. Watch the big screen for your name."
  - It clears by itself when the connection comes back (checked in a phone-sized browser).
- **Your phone panel:** a new public link means Stage Control's link changes too. Run **Show Check** on the Mac and it prints the current one. Or use the Wi-Fi link if your phone is on the same Wi-Fi: `http://<this Mac's Wi-Fi address>:8765/admin` (8775 if Gig Mode moved).
- **Seen in a sandbox render:** the results card (name, song, the game's score, stars, "#1 of 1 tonight", up next) and the in-song panel ("#1 tonight · score to beat", "up next").

## Round 3e: tonight's Top 5 as a story image (Stage Control)
- **Where:** under the Scoreboard, **Tonight's Top 5 as a story image** draws a 1080×1920 card on your phone.
- **What's on it:** your logo lockup, the Top 5 (public names, songs, scores, stars), "N players rocked with the band", @jordanrein_, the show and the date.
- **Then:** Share (straight to a story, on phones that can share files) or Save image. The encore doesn't count, same as the scoreboard.
- **Checked** in a phone-sized browser with 7 players, including a very long name.

## Round 3h: the drum kit, back to the 10/10 look (redrawn from your notes, 10/17)
- **The look:** the 10/10 kit's size, place and dark silhouette. Only the kick head with your logo catches the eye, as it did on 10/10. The kit area measures exactly as bright as the original's.
- **Every piece a drummer has, overlapping the way a real kit does from the front:**
  - the kick in front, with two rack toms mounted on it;
  - the snare (drummer's left) and the floor tom (drummer's right) just behind the kick, partly hidden by it;
  - the hi-hat with its pedal and a crash beyond the snare, and the ride over the floor tom.
- **The 10/10 cymbal spread is kept,** so the in-song picture still clears the QR sticker.
- **New stage videos and a re-captured in-song picture.** The kick, the beat-synced kick/snare/hi-hat motion and the moving speakers are unchanged.

## Round 3f: the lights, designed as a show (needs the hardware rehearsal)
Both outputs stay **OFF** until you've rehearsed them in the room. What's new is how they behave once on.

**The room (MaestroDMX), accents only.** Momentary hits on top of the band's show. It never touches the palette, pattern, fog or brightness, and there's no blackout. It never reacts to a miss.
- **One dial, "Room hits":** Off / Subtle / Show / Big night. The dial sets which moments count and how often the room may speak (Subtle: one hit per 6 s and 4 per song; Show: 3 s and 9; Big night: 2 s and 14).
- **The moments:**
  - **Called up:** a blinder pop as the name hits the screen (Subtle+).
  - **First note:** a short pop the moment the player's first note lands, so the room is theirs (Show+).
  - **Streaks:** a pop at every 100th note in a row (50 on a Big night). The crowd learns that a flash means 100 (Show+).
  - **Star Power:** a blinder hit (Subtle+). Or **Star Power look: Maestro's Effect**: whatever you've set MaestroDMX's own Effect trigger to is held for the whole Star Power, so the band chooses the look.
  - **Solo:** a pop going in; a double pop (ba-dum) coming out if nothing was missed in it (Show+).
  - **The finale:** short pops on the song's last beats (Show: 3, Big night: 5, Subtle: just the last), then a longer hit on the final beat. It only runs while notes are still coming, so a long fade-out stays quiet.
  - **Five stars:** a double pop (Show+). **New #1:** the night's only strobe (Subtle: 0.8 s, Show: 1.2 s, Big: 2 s). **Strobe for a new #1: OFF** turns it into a blinder.
- **Encore:** still waits on your OK for a blackout. MaestroDMX has a momentary blackout trigger, so it's a one-line change once you say yes.

**The guitar (Revival Kit / Santroller).** What shows depends on the guitar's own LED setup, so it's told the same thing in all three languages it might be wired for: YARG's game feedback (star power, meter, multiplier, solo, miss), the Rock Band stage-kit LEDs (8 LEDs in four colours) and the RGB indicator LED. Whatever it's wired for will show; the rest is ignored.
- **Asleep on the stand** between players (round 3g): a slow six-second breath, in one fret colour at a time, and a soft three-LED comet doing a lap every three seconds. Sixteen steps a second, so it glides rather than blinks.
- **Awake at call-up:** a chase through the four colours, the meter charging up, the multiplier counting, green blinking, until the song starts. The guitar looks alive for the walk-up.
- **In the song:** the game's own state.
  - **Star Power ready** (round 3g): the meter throbs just under full, the indicator leans from the multiplier colour into blue and back, and a blue ripple runs out from the middle LEDs, every 1.2 s. A wordless hint for first-timers to tilt the guitar.
  - **Star Power fires** (round 3g): an ignition. Every LED on, white, for a tenth of a second, then a blue shockwave out from the middle and back (half a second), then two blue comets race around the guitar for as long as Star Power lasts, and the indicator flashes white on every beat of the song. When it runs out, the guitar hands back to the game's own look.
- **A miss:** a red blink on the guitar only (the player's private feedback, as YARG does it).
- **After the song:** a 3-second gold victory lap for five stars or a new #1; otherwise the meter winds down. Then back to sleep.
- **Every 100-note streak:** a sparkle (all LEDs for a blink).

**Rehearse the lights (30 s).** In Stage Control. Plays every moment at the current dial setting, with the guitar along, and tells you on the phone which moment is playing. Run it with the band's MaestroDMX show on, before doors, and pick the dial setting you like. It refuses to run during a song.

**Unknowns until the rehearsal:** which of the three languages the Revival Kit's LEDs listen to (none means its LEDs aren't mapped to game events; your call in the RCM tool); whether MaestroDMX registers a 0.12 s blinder pop (if not, the finale pops read as the final hit only); what your Effect trigger is set to.

## Tested live in the background (round 3c, no keystrokes)
- **Game events → lights:** Star Power on gave one MaestroDMX blinder (pressed, then released after 0.5 s), and a repeat didn't re-trigger it. Tested against a stand-in receiver, not the real MaestroDMX.
- **Health strip:** shows the game, the public link, the in-song art and the lights.
- **Clean quit:** the game closed in 1 s, the stage server stopped and restored the gameplay background, and there were no crash reports.
- **Not seen live this time:**
  - The game stayed paused while it wasn't the front app, even with App Nap off and `pause_on_focus_lost = 0`.
  - Its clock stopped at 6 s, and I don't bring the game to the front while you might be using the Mac.
  - So the blue beams, moving speakers, lefty flip and native score still need the hands-on check below.

## The 10-minute hands-on check (guitar plugged in, MaestroDMX on the same Wi-Fi)
1. Open the app normally. Gig Mode should start on its own, and the phone health strip should show a green **Guitar** with the guitar's name.
2. Play one song on GIG to the end.
   - The **results card** should come up with the right score.
   - The stage-manager log should show `== score from the game:`.
   - Green on the card should call the next player.
3. During that song, hit Star Power. The **beams go blue**.
4. Turn **Moving speakers** ON and play part of a song. The cones should move with the music, and the painted speakers must not show behind them.
5. **Guitar lights** ON, **Room hits** → Show, then **Rehearse the lights**, with the band's MaestroDMX show running. The first time after each install, macOS may ask to let the guitar-light helper use the guitar; allow it. Watch the phone for which moment is playing. If the guitar never reacts, its LEDs aren't mapped to game events (RCM tool / Santroller configurator), which is your call. Try Subtle and Big night too, and pick one.
6. Sign up a test player with **left-handed** ticked and start their song. The highway should be flipped, and it should be back to normal afterwards.
7. Quit with ⌘Q. The game should close within a couple of seconds.

## Not built yet
- **Encore reveal:** the design is proposed, and it waits for your OK on taking over the room lights for a blackout.
