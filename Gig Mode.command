#!/bin/bash
# GIG MODE (in-game): sign-ups + queue + scoreboard, screens drawn INSIDE the Guitar Hero Live app,
# driven by the guitar (green = call up / start, red twice = skip). Live QR stamped on the gameplay background.
# Close this window to stop gig mode (the original gameplay background is put back).
cd "$(dirname "$0")/stage-manager" || exit 1
printf '\033]0;Guitar Hero Live - GIG MODE\007'
echo "=== GUITAR HERO LIVE: GIG MODE (in-game) ==="
# Only one Gig Mode at a time. If one is already running (maybe without a window), ask before replacing it:
# restarting makes a new sign-up link, so never do it by accident mid-show.
GIG_PID=$(pgrep -f "server.py --driver" | head -1)  # a running Gig Mode (in-game or Chrome); other apps can use 8765
if [ -n "$GIG_PID" ]; then
  echo "Gig Mode is already running."
  read -n 1 -s -r -p "Press R to restart it (new sign-up link), any other key to leave it running. " k; echo
  if [ "$k" != "r" ] && [ "$k" != "R" ]; then exit 0; fi
  kill -TERM "$GIG_PID"
  for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$GIG_PID" 2>/dev/null || break; sleep 1; done
fi
echo "Close this window to stop gig mode."
exec /usr/bin/python3 -u server.py --driver mod
