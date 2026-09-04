/* Samsung AVPlay, for a television that will not play a film in a <video> element.
 *
 * A Tizen set decodes HEVC in an MKV without breaking sweat - the panel does it for
 * every other app on the machine - but the browser engine inside it refuses the
 * container, so the web client falls back to asking the server to re-encode a file the
 * television could have played untouched. AVPlay is the way in: Samsung's own player,
 * hardware decoding, and it takes a URL.
 *
 * Everything here is behind one test. Off a Samsung television `there` is false, no
 * object is created, and the client plays exactly as it did before.
 *
 * NOT YET TESTED ON A TELEVISION. The API calls follow Samsung's documentation; the
 * behaviour on a real set is unproven, which is why nothing else changes when it is
 * missing.
 */
(function () {
  const api = (typeof window !== "undefined" && window.webapis && window.webapis.avplay)
    ? window.webapis.avplay : null;

  const TZ = {
    there: !!api,
    ready: false,          // an object element exists and the player is open
    ended: null,           // set by the caller: what to do when the film finishes
    error: null,
  };
  window.TZ = TZ;
  if (!api) return;

  /* AVPlay draws into an <object>, not a <video>: the video plane sits behind the page
     and the object says where the hole in the page is. */
  function surface() {
    let el = document.getElementById("tzplayer");
    if (el) return el;
    el = document.createElement("object");
    el.id = "tzplayer";
    el.type = "application/avplayer";
    el.style.cssText = "position:absolute;inset:0;width:100%;height:100%;" +
      "background:#000";
    const player = document.getElementById("player") || document.body;
    player.insertBefore(el, player.firstChild);
    return el;
  }

  const listeners = {
    onbufferingstart: function () { TZ.buffering = true; },
    onbufferingcomplete: function () { TZ.buffering = false; },
    onstreamcompleted: function () {
      TZ.stop();
      if (typeof TZ.ended === "function") TZ.ended();
    },
    onerror: function (kind) {
      TZ.error = String(kind);
      if (window.toast) window.toast("The television could not play that: " + kind);
    },
    onevent: function () {},
    ondrmevent: function () {},
    onsubtitlechange: function () {},
  };

  /**
   * Open a URL and start playing at `fromMs`.
   *
   * The whole sequence is Samsung's: open, set the display area, prepare, seek, play.
   * Preparing is what talks to the decoder, so an unsupported file fails there rather
   * than silently showing nothing.
   */
  TZ.open = function open(url, fromMs) {
    try {
      TZ.stop();
      const el = surface();
      api.open(url);
      const r = el.getBoundingClientRect();
      api.setDisplayRect(Math.round(r.left), Math.round(r.top),
                         Math.round(r.width), Math.round(r.height));
      api.setDisplayMethod("PLAYER_DISPLAY_MODE_LETTER_BOX");
      api.setListener(listeners);
      api.prepare();
      if (fromMs > 0) {
        try { api.seekTo(Math.round(fromMs)); } catch (e) {}
      }
      api.play();
      TZ.ready = true;
      TZ.error = null;
      return true;
    } catch (e) {
      TZ.error = String(e && e.message ? e.message : e);
      TZ.ready = false;
      return false;
    }
  };

  TZ.play = function () { try { api.play(); } catch (e) {} };
  TZ.pause = function () { try { api.pause(); } catch (e) {} };
  TZ.paused = function () {
    try { return api.getState() !== "PLAYING"; } catch (e) { return true; }
  };
  /** Where it is, in seconds - the same units the rest of the client speaks. */
  TZ.at = function () {
    try { return (api.getCurrentTime() || 0) / 1000; } catch (e) { return 0; }
  };
  TZ.length = function () {
    try { return (api.getDuration() || 0) / 1000; } catch (e) { return 0; }
  };
  TZ.seek = function (seconds) {
    try { api.seekTo(Math.max(0, Math.round(seconds * 1000))); } catch (e) {}
  };
  TZ.stop = function () {
    try { api.stop(); } catch (e) {}
    try { api.close(); } catch (e) {}
    TZ.ready = false;
    const el = document.getElementById("tzplayer");
    if (el && el.parentElement) el.parentElement.removeChild(el);
  };

  /* The set's own keys, which the page never sees unless they are asked for. */
  try {
    ["MediaPlayPause", "MediaPlay", "MediaPause", "MediaStop",
     "MediaRewind", "MediaFastForward"].forEach(function (k) {
      try { window.tizen.tvinputdevice.registerKey(k); } catch (e) {}
    });
  } catch (e) {}
})();
