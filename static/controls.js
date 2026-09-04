/* Palladium's own transport controls.

   The browser's native bar was doing three things we could not live with: it cannot
   show a timecode before the stream has loaded (so a resume looked like 0:00), it
   swallows clicks to toggle playback (so double-click-to-fullscreen paused the film),
   and Chrome re-asserts it on its own terms. This bar is plain DOM, so the idle logic
   in app.js governs it completely. */

(function () {
  const $ = (s) => document.querySelector(s);
  const v = $("#video");
  const bar = { play: $("#c-play"), now: $("#c-now"), dur: $("#c-dur"),
                scrub: $("#c-scrub"), fill: $("#c-fill"), buf: $("#c-buf"),
                mute: $("#c-mute"), vol: $("#c-vol"), fs: $("#c-fs") };
  let scrubbing = false;
  /* Volume is remembered per channel layout: a 5.1 mix and a stereo downmix sit at
     very different loudness, so one remembered level suits neither. */
  const volKey = () => (window.S && window.S.channels >= 6 ? "vol6" : "vol2");
  const readVol = () => {
    const v0 = parseInt(localStorage.getItem("palladium-" + volKey()), 10);
    return isNaN(v0) ? 100 : Math.max(0, Math.min(100, v0));
  };
  const saveVol = (pct) => localStorage.setItem("palladium-" + volKey(), String(pct));
  let priming = false;      // holding the resume target until the stream starts

  /* the play glyph becomes a spinner whenever the picture is waiting on data:
     the initial load, a seek, or a mid-film stall */
  const setBusy = (on) => bar.play.classList.toggle("busy", !!on);

  ["waiting", "stalled", "seeking"].forEach((e) => v.addEventListener(e, () => setBusy(true)));
  ["playing", "canplay", "seeked", "pause", "error"].forEach((e) =>
    v.addEventListener(e, () => setBusy(false)));

  const clk = (s) => {
    s = Math.max(0, Math.floor(s || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
  };

  /* the player calls this the moment you press Resume, before any media exists,
     so the bar reads the position it is heading for rather than 0:00 */
  /* called when a stream starts: applies the level remembered for this layout */
  window.ctlApplyVolume = function () {
    const pct = readVol();
    bar.vol.value = pct;
    v.volume = pct / 100;
    v.muted = pct === 0;
    bar.mute.innerHTML = v.muted ? "&#128263;" : "&#128266;";
    bar.vol.title = pct + "%" + (window.S && window.S.channels >= 6 ? " (5.1)" : " (stereo)");
  };

  window.ctlPrime = function (offset, duration, base) {
    // a GPU stream starts its own clock at zero, so the bar adds the offset back
    window.__ctlBase = base || 0;
    window.__ctlDur = duration || 0;
    priming = offset > 0;
    setBusy(true);                      // nothing is decoded yet
    bar.now.textContent = clk(offset);
    bar.dur.textContent = clk(duration);
    const pct = duration ? (offset / duration) * 100 : 0;
    bar.fill.style.width = pct + "%";
    bar.buf.style.width = "0%";
    bar.scrub.value = Math.round(pct * 10);
    bar.play.innerHTML = "&#10074;&#10074;";
  };

  function render() {
    const base = window.__ctlBase || 0;
    // the library's metadata is the authority on length: a session's manifest
    // advertises a placeholder (2:00:00 for a 1:43 film), which moved the scrubber
    const dur = window.__ctlDur || (v.duration && isFinite(v.duration) ? v.duration : 0);
    // the element reports 0:00 while the first segment loads; keep showing where
    // playback is heading instead of snapping the display back to the start
    if (priming) {
      if (v.currentTime > 0.05) { priming = false; setBusy(false); }
      else {
        if (v.buffered.length && dur) {
          bar.buf.style.width = (v.buffered.end(v.buffered.length - 1) / dur) * 100 + "%";
        }
        return;
      }
    }
    if (!scrubbing) {
      const pos = base + (window.playAt ? window.playAt() : v.currentTime);
      const pct = dur ? (pos / dur) * 100 : 0;
      bar.fill.style.width = pct + "%";
      bar.scrub.value = Math.round(pct * 10);
      bar.now.textContent = clk(pos);
    }
    if (dur) bar.dur.textContent = clk(dur);
    if (v.buffered.length) {
      const end = base + v.buffered.end(v.buffered.length - 1);
      bar.buf.style.width = (dur ? (end / dur) * 100 : 0) + "%";
    }
    bar.play.innerHTML = v.paused ? "&#9654;" : "&#10074;&#10074;";
  }

  v.addEventListener("timeupdate", render);
  v.addEventListener("progress", render);
  v.addEventListener("durationchange", render);
  v.addEventListener("play", render);
  v.addEventListener("pause", render);

  const toggle = () => { if (v.paused) v.play().catch(() => {}); else v.pause(); };
  bar.play.onclick = toggle;

  /* one click plays/pauses, two go fullscreen - so the first click of a double
     waits long enough to be cancelled */
  let clickTimer = null;
  v.addEventListener("click", () => {
    clearTimeout(clickTimer);
    clickTimer = setTimeout(toggle, 240);
  });
  v.addEventListener("dblclick", () => {
    clearTimeout(clickTimer);          // never toggles playback
    bar.fs.click();
  });

  bar.scrub.addEventListener("input", () => {
    scrubbing = true;
    const dur = v.duration && isFinite(v.duration) ? v.duration : 0;
    const t = (bar.scrub.value / 1000) * (window.__ctlDur || dur);
    bar.now.textContent = clk(t);
    bar.fill.style.width = (bar.scrub.value / 10) + "%";
  });
  bar.scrub.addEventListener("change", () => {
    const base = window.__ctlBase || 0;
    // the library's metadata is the authority on length: a session's manifest
    // advertises a placeholder (2:00:00 for a 1:43 film), which moved the scrubber
    const dur = window.__ctlDur || (v.duration && isFinite(v.duration) ? v.duration : 0);
    const target = (bar.scrub.value / 1000) * dur;
    scrubbing = false;
    if (!dur) return;
    // Only what has actually been transcoded can be played: the manifest advertises
    // the whole film, but the transcoder produced video from the start point on, and a
    // live GPU encode has no timeline behind it at all. Landing outside that window
    // means asking the server to start again from there.
    let inBuffer = false;
    for (let i = 0; i < v.buffered.length; i++) {
      if (target >= base + v.buffered.start(i) - 1 && target <= base + v.buffered.end(i) - 0.5) {
        inBuffer = true;
        break;
      }
    }
    if (inBuffer) {
      v.currentTime = target - base;
      return;
    }
    if (window.ctlSeek) {
      setBusy(true);
      bar.now.textContent = clk(target);
      window.ctlSeek(target);
    }
  });

  bar.vol.addEventListener("input", () => {
    const pct = +bar.vol.value;
    v.volume = pct / 100;
    v.muted = pct === 0;
    bar.mute.innerHTML = v.muted ? "&#128263;" : "&#128266;";
    bar.vol.title = pct + "%";
    volPct.textContent = pct + "%";        // readable while you drag
    volPct.classList.add("on");
    clearTimeout(volPct._t);
    volPct._t = setTimeout(() => volPct.classList.remove("on"), 1200);
    saveVol(pct);
  });
  const volPct = document.getElementById("c-volpct");
  bar.mute.onclick = () => {
    v.muted = !v.muted;
    bar.mute.innerHTML = v.muted ? "&#128263;" : "&#128266;";
    bar.vol.value = v.muted ? 0 : Math.round(v.volume * 100);
  };

  /**
   * Full screen, in the three ways browsers spell it.
   *
   * An iPhone has none of them for an ordinary element: only a video can go full
   * screen there, and only through webkitEnterFullscreen. Calling the standard name
   * threw, which took the whole handler down and left the button dead.
   */
  bar.fs.onclick = () => {
    const box = $("#player");
    const open = document.fullscreenElement || document.webkitFullscreenElement;
    if (open) {
      const shut = document.exitFullscreen || document.webkitExitFullscreen;
      if (shut) { const r = shut.call(document); if (r && r.catch) r.catch(() => {}); }
      return;
    }
    const go = box.requestFullscreen || box.webkitRequestFullscreen ||
               box.webkitRequestFullScreen;
    if (go) {
      const r = go.call(box);
      if (r && r.catch) r.catch(() => {});
      return;
    }
    // an iPhone: the video itself, in the system player
    const v = $("#video");
    if (v && v.webkitEnterFullscreen) v.webkitEnterFullscreen();
    else toast("This browser will not go full screen");
  };

  document.addEventListener("keydown", (e) => {
    if (!window.S || document.activeElement === $("#search")) return;
    if (e.key === " ") { e.preventDefault(); toggle(); }
    if (e.key === "m" || e.key === "M") bar.mute.click();
    if (e.key === "ArrowRight") v.currentTime += e.shiftKey ? 60 : 10;
    if (e.key === "ArrowLeft") v.currentTime -= e.shiftKey ? 60 : 10;
  });
})();
