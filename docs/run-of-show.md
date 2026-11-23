# Guitar Hero Live: Run of Show

> The operator's sheet for a show night, as used. The launchers it names are the `.command` files at the top of this repository. The backup folder it mentions is not published.

## The three launchers (double-click)
| Launcher | What it runs | When |
|---|---|---|
| **Practice Mode** | Original Clone Hero, exactly as before | Rehearsal, or the last-resort fallback |
| **Gig Mode** | *Guitar Hero Live* app with the screens **inside the game**, run from the guitar | The show (if soundcheck passes) |
| **Gig Mode (Chrome backup)** | Original Clone Hero + the stage screen in a full-screen Chrome window | If in-game mode misbehaves |

The original Clone Hero in /Applications is never modified. Full backup: `BACKUP-gig-safe-2026-10-09/`.

If Gig Mode is already running when you double-click a launcher, it asks first: **Gig Mode** → press **R** to restart it
(new sign-up link) or any other key to keep the running one; **Practice Mode** → press **S** to stop Gig Mode first.
The first time Terminal runs Gig Mode it may ask for access to the Documents folder: click **Allow**.
Keep heavy apps (e.g. DaVinci Resolve) closed during the show; the in-game screens render on this Mac.

## Soundcheck: pick the mode (10 minutes, guitar + projector connected)
1. Connect to the internet (venue Wi-Fi or phone hotspot). Plug in the guitar.
2. Make sure Guitar Hero Live is **closed**, then double-click **Gig Mode**. The Terminal shows the **public sign-up
   link** and your **phone control panel link** (PIN). The link is new every time Gig Mode starts, so open the
   control-panel link on your phone after starting. Guitar Hero Live opens by itself with its own boot screen.
   (If the app ever quits on launch, just open "Guitar Hero Live" again.)
3. On the main menu: press **Start** on the guitar to join (your profile is picked automatically), then choose **GIG**.
   The lobby takes over the screen. (**QUICKPLAY** = plain Clone Hero with no overlay, for troubleshooting;
   **SETTINGS** = calibration / controller setup.)
4. Sign up from your phone (**Wi-Fi off**, to prove cellular works). You appear as **Up Next**.
5. On the guitar: **green** → start screen → **green** → the song starts. Play a few notes, then let it finish.
6. Within about 15 s of the results screen, the lobby comes back and your score is on **Tonight's Top 5**.
   - All good → that's the show mode. On your phone tap *Reset night*. Then quit Guitar Hero Live (⌘Q), close
     the Gig Mode window, and double-click **Gig Mode** again: a Gig Mode start with the game closed begins with an
     empty high-score table, so the soundcheck score can't block an audience score. (New public link → re-open
     the phone panel link from the Terminal.)
   - Anything off → close the Terminal window and use **Gig Mode (Chrome backup)** instead.
   - Still off → **Practice Mode** + the old Google Form QR.

## During the show (in-game mode)
- **Lobby** (shown between players): QR, Up Next ("Get ready, you'll be called up shortly"), on-deck list, Top 5.
- **Green** on the guitar: calls the next person up and shows the how-to + Star Power showmanship tip.
- **Green** again: their song starts. **Red** on the start screen: back to the lobby (they stay first in line).
- **Red twice** in the lobby: skip the next person (no-show). Their song opens back up for sign-ups.
- After each song, the score is captured automatically and the lobby returns. If the phone shows **needs score**,
  type it in from the results screen.
- Phone panel: reorder/remove people, add walk-ups, fix scores, close sign-ups.
- Audience difficulty is set by `audience_difficulty` in `stage-manager/config.json` (default easy). Songs without
  that difficulty use the nearest one they have.

## If something looks stuck
- **GIG shows "Setting up the stage…":** Gig Mode wasn't running (or is still starting). The game starts it by
  itself: a Terminal window opens and the lobby appears a few seconds later. Press **red** for the plain song list.
- **Black screen or frozen at launch:** look for a macOS dialog (it can sit behind the game) and answer it.
  The game no longer reads your Documents folder, so the "access Documents" prompt shouldn't appear any more.
- **Game quit or crashed:** open "Guitar Hero Live" again (Gig Mode keeps running). Press Start, choose GIG; the line
  and Top 5 are kept. A song cut off mid-way shows **needs score** on the phone.
- Mod log for troubleshooting: `~/Library/Application Support/com.jordanrein.GuitarHeroLive/GHL/ghlmod.log`.

## Encore (end of the night)
"Jump" is held back from sign-ups: phones show it only as **Mystery encore · saved for tonight's top scorer**, and the
lobby's Top 5 says "#1 plays the mystery encore" (the song is never named in public).
When you're ready: phone panel → **Encore: <winner> plays Jump**. The winner goes to the front of the line
("Encore · tonight's #1" in the lobby). **Green** calls them up and the start screen reveals the song; **green** again
starts it. The encore doesn't count toward Top 5. (Change the song with `encore_song` in `stage-manager/config.json`.)

## After the show
Phone → *Reset night* archives the line and scoreboard to `stage-manager/data/archive/`.
Closing the Gig Mode window restores the original gameplay background.
