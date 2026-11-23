// ?nohwy=1: the stage without the note highway (used to build the gameplay background, where Clone Hero draws its own)
if (new URLSearchParams(location.search).has("nohwy")) document.documentElement.classList.add("nohwy");
// In-game UI layer helpers (?frame=1). Reports where the Star Power sparks sit on the start screen, so the game
// plays Clone Hero's spark flipbook right there at full frame rate. Kept out of display.html so UI tweaks don't
// force a re-render of the background video (server.py fingerprints display.html for that).
(function () {
  if (!new URLSearchParams(location.search).has("frame")) return;
  // fractions of the 16:9 stage (x, y from its top-left, width, height): independent of the render size
  const rect = (el) => {
    if (!el || !el.offsetParent) return null;
    const s = document.getElementById("stage").getBoundingClientRect(), b = el.getBoundingClientRect();
    return [(b.left - s.left) / s.width, (b.top - s.top) / s.height, b.width / s.width, b.height / s.height].map((v) => +v.toFixed(5));
  };
  const fx = () => {
    const start = document.getElementById("start");
    const on = start && !start.hidden;
    return on ? { sparks: rect(document.getElementById("s-sparks")), clip: rect(document.querySelector("#start .show")) } : {};
  };
  window.ghlFx = fx;
  const report = () => { document.body.dataset.fx = JSON.stringify(fx()); };
  setInterval(report, 200);
  report();
})();

// An encore can be offered to another ranked player when the winner has left, so it's "your victory lap", not
// "tonight's top scorer". Patched at the end of every render (the outermost wrapper below): a 100 ms timer used to
// race the in-game capture, so the start screen showed the old wording about half the time.
const fixEncoreEyebrow = () => {
  for (const el of document.querySelectorAll('.eyebrow')) {
    if (el.textContent.includes("Encore · tonight's")) el.textContent = 'Encore · your victory lap';
  }
};

// Extend the screen renderer in this UI-only script so cached background videos stay valid.
(function () {
  const baseRender = render;
  render = function () {
    baseRender();
    if (!S) return;
    const cur = S.current;
    if (cur?.phase === 'ready') {
      const press = document.querySelector('#start .press');
      let control = document.getElementById('s-difficulty');
      if (!control) {
        control = document.createElement('div');
        control.id = 's-difficulty';
        press.before(control);
      }
      const levels = ['easy', 'medium', 'hard', 'expert'];
      const available = cur.difficulties?.length ? cur.difficulties : levels;
      setHTML(control, `<div class="difficulty"><span>Difficulty</span>${cur.lefty ? '<span class="level selected lefty">Lefty · flipped</span>' : ''}${levels.map((d) => `<span class="level${d === cur.difficulty ? ' selected' : ''}${available.includes(d) ? '' : ' unavailable'}">${d[0].toUpperCase() + d.slice(1)}</span>`).join('')}</div><div class="difficulty-help">${available.length === 1 ? 'This chart supports ' + esc(available[0][0].toUpperCase() + available[0].slice(1)) + ' only' : 'Chosen at phone signup · host can change it in Stage Control'}</div>`);
      const who = document.getElementById('s-who');
      if (who) who.style.fontSize = Math.min(parseFloat(who.style.fontSize) || 10.5, 10.5) + 'cqw';
      setHTML(document.querySelector('#start .nope'), cur.encore
        ? 'Winner has left? Press <span class="gem r"></span> red for the next ranked player.'
        : 'Player has left? Press <span class="gem r"></span> red twice to take them off the line (their song opens up again).');
    }
    const q = S.queue || [], sel = Math.max(0, Math.min(q.length - 1, S.selected || 0));
    const legend = document.getElementById('legend');
    const browse = q.length > 1 ? '<span><span class="strum">▲▼</span>Strum = pick</span>' : '';
    if (legend && !legend.hidden) setHTML(legend, q[sel]?.encore
      ? '<span><span class="gem g"></span>Reveal winner</span><span><span class="gem r"></span>Next ranked player</span>' + browse
      : '<span><span class="gem g"></span>Call up</span><span><span class="gem r"></span>Twice = remove</span>' + browse);
  };
})();

// Lobby: the highlighted person (strum up/down from the stage, server `selected`) is shown big: green calls them up and
// red twice removes them. The chips show who else is in line, with their place in it.
lobbyCard = function (q) {
  const sel = Math.max(0, Math.min(q.length - 1, (S && S.selected) || 0)), n = q[sel];
  const others = q.map((e, i) => i).filter((i) => i !== sel);
  const from = Math.max(0, Math.min(sel - 2, others.length - 4));  // two either side of the highlight
  const shown = others.slice(from, from + 4);
  const chips = shown.length ? `<div class="ondeck"><span class="lab eyebrow">${sel === 0 ? "On deck" : "Also in line"}</span>${shown.map((i, k) => `<span class="chip" style="--i:${k + 1}"><em>#${i + 1}</em><b>${esc(q[i].name)}</b>${esc(q[i].song)}</span>`).join("")}</div>` : "";
  return { cls: "abs upnext" + (sel > 0 ? " picked" : ""), html: `<div class="eyebrow"><span class="dot"></span>${n.encore ? "Encore · your victory lap" : sel === 0 ? "Up next" : `Up next · #${sel + 1} in line`}</div><div class="who head rough spray" style="font-size:${fitName(n.name, 6.4, 24)}">${letters(n.name)}</div>
      <div class="what">${esc(n.song)}${n.artist ? ` <small>/ ${esc(n.artist)}</small>` : ""}</div>
      ${n.ig ? `<div class="ig">@${esc(n.ig)}</div>` : ""}
      <div><span class="getready">Get ready, you'll be called up shortly</span></div>${chips}` };
};

// Attract mode: nobody in line and sign-ups open. Under "Ready to rock?", the songs still up for grabs as album
// covers, so the room sees what they could play. (A static grid, so the in-game screen isn't re-rendered on a timer.)
(function () {
  const baseRender = render;
  render = function () {
    baseRender();
    if (!S) return;
    const card = document.getElementById('upnext');
    let box = document.getElementById('attract');
    const on = !S.current && !(S.queue || []).length && S.signupsOpen && !S.showFinished;
    if (!on) { if (box) box.remove(); return; }
    if (!box || box.parentNode !== card) { box = document.createElement('div'); box.id = 'attract'; card.appendChild(box); box._h = null; }
    const open = (S.songs || []).filter((x) => !x.taken && !x.encore);
    const shown = open.slice(0, 8);
    setHTML(box, shown.length ? `<div class="lab eyebrow">Up for grabs</div><div class="grid">${shown.map((x, i) =>
      `<div class="tile" style="--i:${i}">${x.art ? `<img src="/art/${x.id}" alt="">` : `<span class="ph"></span>`}<b>${esc(x.name)}</b></div>`).join('')}</div>`
      + (open.length > shown.length ? `<div class="more">+${open.length - shown.length} more on your phone</div>` : '') : '');
  };
})();

// Results card (server overlay "results"): the 15 s after a show song, instead of Clone Hero's own results screen.
// The player's name, their score and stars, where it puts them tonight, and who's up next.
(function () {
  const baseRender = render;
  render = function () {
    baseRender();
    if (!S) return;
    const r = S.results, stage = document.getElementById('stage');
    let box = document.getElementById('results');
    stage.classList.toggle('resulting', !!r);
    if (!r) { if (box) box.hidden = true; return; }
    if (!box) {
      box = document.createElement('section');
      box.id = 'results'; box.className = 'abs results';
      stage.appendChild(box);
    }
    box.hidden = false;
    const top = r.rank === 1 && r.of > 1, n = Math.max(0, Math.min(7, r.stars || 0));
    const eyebrow = r.encore ? 'Encore · victory lap' : top ? 'New #1 tonight' : 'Big hand for';
    setHTML(box, `<div class="burst"></div><div class="card">
      <div class="eyebrow${top ? ' gold' : ''}">${eyebrow}</div>
      <div class="who head rough spray" style="font-size:${fitName(r.name, 9, 80)}">${letters(r.name)}</div>
      <div class="what">${esc(r.song)} <small>/ ${esc(r.artist)}</small></div>
      ${r.score != null ? `<div class="score head">${fmt(r.score)}</div><div class="pts">points</div>
        ${n ? `<div class="bigstars">${'<i></i>'.repeat(n)}</div>` : ''}
        ${r.rank ? `<div class="rank${top ? ' gold' : ''}">#${r.rank} of ${r.of} tonight</div>` : ''}`
        : `<div class="tally">Tallying the score…</div>`}
      ${r.next ? `<div class="next"><span class="eyebrow">Up next</span><b class="head">${esc(r.next)}</b><span class="go"><span class="gem g"></span> call them up</span></div>` : ''}
    </div>`);
  };
})();

// Live capture page (?live=1): the server's capture Chrome keeps this page open and pushes each state into it with
// ghlCapture(), then screenshots it. The page draws exactly the pushed state (its own 1 s refresh gets that same state
// back), so the version on the screenshot is the version the server asked for.
(function () {
  if (!new URLSearchParams(location.search).has("live")) return;
  document.documentElement.classList.add("live-capture");
  // (capture tabs are background tabs: real animation frames never fire there, so nothing here may wait on one)
  window.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 500);  // no 60 Hz gamepad polling
  let pushed = null;
  const realFetch = window.fetch.bind(window);
  window.fetch = (url, opts) => (pushed && String(url).indexOf("/api/public") === 0)
    ? Promise.resolve(new Response(JSON.stringify(pushed), { headers: { "Content-Type": "application/json" } }))
    : realFetch(url, opts);
  const settle = () => new Promise((ok) => setTimeout(ok, 30));
  window.ghlCapture = async (state) => {
    pushed = state;
    S = state;
    render();
    // everything the screen shows (album art, sprites) must be loaded before the screenshot
    await Promise.all([...document.images].map((im) => (im.complete ? 0 : new Promise((ok) => {
      im.addEventListener("load", ok, { once: true }); im.addEventListener("error", ok, { once: true }); setTimeout(ok, 1500);
    }))));
    await document.fonts.ready;
    await settle();
    return { v: state.v, fx: window.ghlFx ? window.ghlFx() : {} };
  };
})();

// Chrome stage screen (the backup driver): the same press logic as the in-game stage, plus arrow keys to browse.
(function () {
  if (FRAME_MODE || LAYER) return;
  const send = (button) => fetch("/api/display/press", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ button }) }).catch(() => {}).then(refresh);
  act = function (kind) { if (S) send(kind); };
  document.addEventListener("keydown", (e) => {
    if (!$("debug").hidden) return;
    if (e.key === "ArrowUp") send("up");
    else if (e.key === "ArrowDown") send("down");
  });
})();

// End of the night (server `showFinished`: sign-ups closed, line empty, encore played): the lobby becomes a thank-you
// screen with a follow QR. The sign-up sticker, Up next and the button legend make way; Tonight's top 5 stays as the
// final standings. A reset (new session) clears showFinished and the normal lobby comes back.
(function () {
  const baseRender = render;
  render = function () {
    baseRender();
    fixEncoreEyebrow();
    if (!S) return;
    const done = !!S.showFinished && !S.current;
    document.getElementById('stage').classList.toggle('finished', done);
    let box = document.getElementById('thanks');
    if (!done) { if (box) box.hidden = true; return; }
    if (!box) {
      box = document.createElement('div');
      box.id = 'thanks';
      box.innerHTML = '<div class="abs thanks-msg"><div class="eyebrow">That’s a wrap</div>'
        + '<div class="head rough spray big">Thanks for rocking with us</div>'
        + '<div class="next">Stay tuned for the next show</div></div>'
        + '<div class="abs sticker follow" id="follow"><h2 class="head rough">Follow<br>Jordan Rein</h2>'
        + '<div class="qr" id="followqr"></div><div class="cap" id="followcap"></div></div>';
      document.getElementById('lobby').appendChild(box);
    }
    box.hidden = false;
    const handle = String((S.links && S.links.instagram) || '').trim().replace(/^@+/, '');
    document.getElementById('follow').hidden = !handle;
    if (handle && box.dataset.qr !== handle) {
      document.getElementById('followqr').innerHTML = qrSvg('https://instagram.com/' + encodeURIComponent(handle));
      setHTML(document.getElementById('followcap'), 'SCAN TO FOLLOW<br><span>@' + esc(handle) + ' on Instagram</span>');
      box.dataset.qr = handle;
    }
  };
})();
