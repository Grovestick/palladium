/* The first run.
 *
 * A new install is a working server pointed at nothing: no folders, no TMDB key, and
 * on most machines no ffmpeg. Until those exist there is nothing to draw, so this
 * takes the screen and walks through them - and gets out of the way for good once the
 * library has something in it.
 *
 * Everything here goes through the endpoints the settings page already uses. The only
 * two of its own are /setup/state, which says what is still missing, and
 * /setup/ffmpeg, which fetches one.
 */
(function () {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s === undefined || s === null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

  let state = null;
  let picking = "";        // "movies" or "tv" while the folder picker is open
  let at = "";             // the folder it is looking at
  let listing = null;      // what is in that folder
  let up = null;           // the folder above it
  let watching = null;

  const ask = async (path, body) => {
    const r = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return r.json();
  };

  /** Is there anything to set up? Asked once, before the library is drawn. */
  window.needsSetup = async function needsSetup() {
    try {
      state = await ask("/setup/state");
    } catch (e) {
      return false;                  // a server that cannot answer is not a new one
    }
    if (state.done) return false;    // been through it; an empty library is their own
    return !state.folders.length || !state.tmdb ||
           !((state.films || 0) + (state.episodes || 0));
  };

  window.openSetup = function openSetup() {
    if ($("#setup")) return;
    const box = document.createElement("div");
    box.id = "setup";
    document.body.appendChild(box);
    document.body.classList.add("setting-up");
    draw();
    // the scan, and the ffmpeg fetch, finish in their own time
    watching = setInterval(async () => {
      const was = JSON.stringify(state);
      try {
        state = await ask("/setup/state");
      } catch (e) { return; }
      if (JSON.stringify(state) !== was && !picking) draw();
    }, 1500);
  };

  function shut(reload) {
    clearInterval(watching);
    document.body.classList.remove("setting-up");
    const box = $("#setup");
    if (box) box.remove();
    if (reload) location.reload();
  }

  const step = (n, title, done, inner) =>
    "<div class='sstep" + (done ? " done" : "") + "'>" +
    "<div class='sno'>" + (done ? "✓" : n) + "</div>" +
    "<div class='sbody'><h3>" + esc(title) + "</h3>" + inner + "</div></div>";

  function draw() {
    const box = $("#setup");
    if (!box) return;
    const s = state || {};
    const folders = s.folders || [];
    const found = (s.films || 0) + (s.episodes || 0);
    const fetching = s.fetching || {};

    let html = "<div class='swrap'><h1>Palladium</h1>" +
      "<p class='slead'>Three things, and it is yours. None of them leave this " +
      "machine.</p>";

    // ---- 1. the folders ----------------------------------------------------------
    let one = "<p class='snote'>Point it at the folders your films and series are " +
      "in. It reads them and never writes to them.</p><div class='sfolders'>";
    if (!folders.length) {
      one += "<span class='snone'>none yet</span>";
    } else {
      folders.forEach((f) => {
        one += "<span class='sfolder'>" + esc(f) +
          "<button class='sdrop' data-drop='" + esc(f) + "'>×</button></span>";
      });
    }
    one += "</div><div class='srow'>" +
      "<button class='btn ghost kind' data-pick='movies'>Add a films folder</button>" +
      "<button class='btn ghost kind' data-pick='tv'>Add a series folder</button>" +
      "</div>";
    if (picking) one += picker();
    html += step(1, "Where your films and series are", folders.length > 0, one);

    // ---- 2. the key --------------------------------------------------------------
    const two = "<p class='snote'>Posters, titles and years come from TMDB. The key " +
      "is free: sign in, then Settings → API. It is kept on this machine only." +
      "</p><div class='srow'>" +
      "<input id='skey' type='text' placeholder='TMDB API key' " +
      "autocomplete='off' spellcheck='false'>" +
      "<button class='btn ghost kind' id='ssavekey'>Save</button>" +
      "<a class='slink' href='https://www.themoviedb.org/settings/api' " +
      "target='_blank' rel='noreferrer'>Get one</a></div>";
    html += step(2, "A TMDB key", !!s.tmdb, two);

    // ---- 3. ffmpeg ---------------------------------------------------------------
    let three;
    if (s.ffmpeg) {
      three = "<p class='snote'>Found: <span class='mono'>" + esc(s.ffmpeg) +
        "</span><br>Transcoding with <b>" + esc(s.engine || "the processor") +
        "</b>.</p>";
    } else if (fetching.busy) {
      three = "<p class='snote'>" + esc(fetching.said || "Fetching…") +
        "</p><div class='sbar'><i></i></div>";
    } else {
      three = "<p class='snote'>ffmpeg plays what a device cannot play for itself. " +
        "About 80 MB, fetched once." +
        (fetching.said ? "<br><b>" + esc(fetching.said) + "</b>" : "") +
        "</p><div class='srow'>" +
        "<button class='btn ghost kind' id='sgetff'>Fetch ffmpeg</button>" +
        "<span class='snote'>or put one on PATH and press below</span></div>";
    }
    html += step(3, "ffmpeg", !!s.ffmpeg, three);

    // ---- 4. the door -------------------------------------------------------------
    const four = "<p class='snote'>Without one, anybody on your network is you: they " +
      "can see the library, change what everyone watches with, and hand out " +
      "invitations. With one, only this machine gets in without asking." +
      "</p><div class='srow'>" +
      "<input id='spw' type='password' placeholder='a password worth having' " +
      "autocomplete='new-password'>" +
      "<button class='btn ghost kind' id='ssavepw'>Set it</button></div>" +
      (s.password ? "<p class='snote'><b>A password is set.</b> Change it in " +
       "Settings.</p>" : "");
    html += step(4, "A password, if this machine is not only yours", !!s.password,
                 four);

    // ---- and then the scan -------------------------------------------------------
    const ready = folders.length && s.tmdb;
    let last = "";
    if (s.scanning) {
      last = "<p class='snote'>Reading your folders… " +
        (found ? found + " found so far" : "") + "</p><div class='sbar'><i></i></div>";
    } else if (found) {
      last = "<p class='snote'><b>" + s.films + "</b> films and <b>" + s.episodes +
        "</b> episodes.</p>";
    }
    html += "<div class='sdone'>" + last + "<div class='srow'>" +
      "<button class='btn ghost kind primary' id='sscan'" +
      (ready && !s.scanning ? "" : " disabled") + ">" +
      (found ? "Scan again" : "Scan my folders") + "</button>" +
      "<button class='btn ghost kind' id='sfinish'" + (found ? "" : " disabled") +
      ">Open the library</button>" +
      "<button class='btn ghost kind quiet' id='sskip'>Skip - I will do this in " +
      "Settings</button></div></div></div>";

    box.innerHTML = html;
    wire();
  }

  /** The folder browser: one level at a time, starting at the drives. */
  function picker() {
    let html = "<div class='spicker'><div class='spath'>" +
      (at ? "<button class='btn ghost kind' data-goto='" + esc(up || "") + "'>↑ up" +
            "</button>" : "") +
      "<button class='btn ghost kind' data-goto=''>Drives</button>" +
      "<span class='mono'>" + esc(at || "") + "</span>" +
      (at ? "<button class='btn ghost kind primary' data-take='" + esc(at) +
            "'>Use this folder</button>" : "") +
      "<button class='btn ghost kind quiet' data-shut='1'>Cancel</button></div>";
    if (!listing) {
      html += "<div class='snote'>Reading…</div>";
    } else if (!listing.length) {
      html += "<div class='snote'>Nothing in here.</div>";
    } else {
      html += "<div class='sdirs'>";
      listing.forEach((d) => {
        html += "<button class='sdir' data-goto='" + esc(d.path) + "'>" +
          esc(d.name) + "</button>";
      });
      html += "</div>";
    }
    return html + "</div>";
  }

  async function browse(path) {
    at = path || "";
    listing = null;
    draw();
    try {
      const said = await ask("/library/browse", { path: at });
      // the server answers with whole paths; the button shows the last part of one
      listing = (said.dirs || []).map((p) => ({
        name: String(p).replace(/[\/]+$/, "").split(/[\/]/).pop() || p,
        path: p,
      }));
      at = said.path !== undefined ? said.path : at;
      up = said.parent;
    } catch (e) {
      listing = [];
    }
    draw();
  }

  function wire() {
    const box = $("#setup");
    if (!box) return;

    box.querySelectorAll("[data-pick]").forEach((b) => {
      b.onclick = () => { picking = b.dataset.pick; browse(""); };
    });
    box.querySelectorAll("[data-goto]").forEach((b) => {
      b.onclick = () => browse(b.dataset.goto);
    });
    box.querySelectorAll("[data-shut]").forEach((b) => {
      b.onclick = () => { picking = ""; listing = null; draw(); };
    });
    box.querySelectorAll("[data-take]").forEach((b) => {
      b.onclick = async () => {
        const cfg = await ask("/library/config", {});
        const list = (cfg[picking] || []).slice();
        if (list.indexOf(b.dataset.take) < 0) list.push(b.dataset.take);
        await ask("/library/config", { [picking]: list });
        picking = "";
        listing = null;
        state = await ask("/setup/state");
        draw();
      };
    });
    box.querySelectorAll("[data-drop]").forEach((b) => {
      b.onclick = async () => {
        const cfg = await ask("/library/config", {});
        const gone = b.dataset.drop;
        for (const list of ["movies", "tv", "mixed"]) {
          const kept = (cfg[list] || []).filter((p) => p !== gone);
          if (kept.length !== (cfg[list] || []).length) {
            await ask("/library/config", { [list]: kept });
          }
        }
        state = await ask("/setup/state");
        draw();
      };
    });

    const key = box.querySelector("#ssavekey");
    if (key) {
      key.onclick = async () => {
        const said = box.querySelector("#skey").value.trim();
        if (!said) return;
        await ask("/library/config", { tmdb_key: said });
        state = await ask("/setup/state");
        draw();
      };
    }
    const ff = box.querySelector("#sgetff");
    if (ff) {
      ff.onclick = async () => {
        ff.disabled = true;
        ff.textContent = "Fetching…";
        await ask("/setup/ffmpeg", {});
        state = await ask("/setup/state");
        draw();
      };
    }
    const pw = box.querySelector("#ssavepw");
    if (pw) {
      pw.onclick = async () => {
        const said = box.querySelector("#spw").value;
        if (!said.trim()) return;
        const out = await ask("/password", { password: said });
        if (!out.ok) return;
        state = await ask("/setup/state");
        draw();
      };
    }
    const scan = box.querySelector("#sscan");
    if (scan) {
      scan.onclick = async () => {
        scan.disabled = true;
        await ask("/library/scan", {});
        state = await ask("/setup/state");
        draw();
      };
    }
    const finish = box.querySelector("#sfinish");
    if (finish) {
      finish.onclick = async () => {
        await ask("/setup/done", {});
        shut(true);
      };
    }
    const skip = box.querySelector("#sskip");
    if (skip) {
      skip.onclick = async () => {
        await ask("/setup/done", {});
        shut(false);
      };
    }
  }
})();
