#!/bin/bash
# PRACTICE MODE: plain Clone Hero exactly as before. No sign-ups, no stage screen.
cd "$(dirname "$0")" || exit 1
printf '\033]0;Guitar Hero Live - PRACTICE MODE\007'
GIG_PID=$(pgrep -f "server.py --driver" | head -1)  # a running Gig Mode (in-game or Chrome); other apps can use 8765
if [ -n "$GIG_PID" ]; then
  echo "Gig Mode is running."
  read -n 1 -s -r -p "Press S to stop it and open Practice Mode, any other key to cancel. " k; echo
  if [ "$k" != "s" ] && [ "$k" != "S" ]; then exit 1; fi
  kill -TERM "$GIG_PID"   # (it puts the normal gameplay background back as it stops)
  for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$GIG_PID" 2>/dev/null || break; sleep 1; done
fi
# Make sure the gameplay background is the original (gig mode stamps a live QR onto it).
T="stage-manager/brand/gameplay-template.png"; B="$HOME/Clone Hero/Custom/Image Backgrounds/SIGN UPS-2.png"
[ -f "$T" ] && ! cmp -s "$T" "$B" && cp "$T" "$B" && echo "Restored the original gameplay background."
open "/Applications/Clone Hero.app"
echo "Clone Hero (original) is starting. You can close this window."
