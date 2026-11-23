Sandbox benches from the 2026-11-19 perf sweep (not part of the test suites in tests/).
Each starts its own stage server on a free port 18820-18999 with its own data/config in the folder you run it from
(gameplay background written there, a copy of the live bg-video so no re-render), and stops it at the end.
  /usr/bin/python3 green_and_lock_bench.py <server.py> <label>   green right after a call-up; game poll latency with 10 Stage Control pollers
  /usr/bin/python3 slow_stamp_bench.py <server.py> <label>       green when the in-song picture is slow (use a copy of server.py with a sleep in render_gameplay_bg)
  /usr/bin/python3 host_skip_during_hold.py <server.py>           host Skip / Back to line while a green is held
Run them from a scratch folder (they create subfolders next to themselves: copy the folder to /tmp first).

Note for this published copy: the benches copy the rendered stage videos from the show Mac's game data folder,
which is not in this repository, so they do not run as they are. They are here as a record of what was measured.
