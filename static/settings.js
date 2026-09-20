/* Settings: four screens behind one gear.

     Library    which folders are indexed, the metadata key, and scanning
     People     who may watch this library from outside, and their links
     Friends    whose libraries appear alongside this one
     Subtitles  the default track language

   Kept out of app.js because it talks to a different set of endpoints entirely -
   the library's own /library/* and /invites rather than the browsing endpoints. The
   folder picker walks the server's filesystem, since the browser cannot hand us a
   real path.
*/

(function () {
  const $ = (s) => document.querySelector(s);
  //: a newline for the questions asked before anything irreversible happens
  const BR = String.fromCharCode(10);
  let cfg = null;
  let picking = null;          // which list the folder picker is adding to
  let tab = "quality";          // Settings: the first tab, and the one most opened

  const OWNER_TABS = [["quality", "Settings"],
                      ["library", "Library"],
                      // its own tab: qBittorrent, where downloads land and what is
                      // offered from each pack is a subject of its own, and it sat at
                      // the foot of the folder lists
                      ["torrents", "Torrents"],
                      ["people", "Users"], ["subs", "Subtitles"],
                      ["now", "Now playing"], ["log", "Log"],
                      ["reports", "Reports"],
                      ["remote", "Remote computer"]];
  // a guest is a visitor, not an administrator: no folders, nobody to invite, and no
  // friends of ours to browse - only the two screens that are theirs
  // The other server has no tab of its own: what it is and what it holds is a box
  // under Settings, beside everything else about how films reach a screen.
  const GUEST_TABS = [["quality", "Settings"], ["subs", "Subtitles"],
                      ["reports", "Reports"]];
  const tabs = () => (CFG && CFG.guest ? GUEST_TABS : OWNER_TABS);

  /* Two kinds of setting, and only one of them belongs to a machine.
   *
   * What a person keeps - how their subtitles look, what language they want, the
   * skin, their quality ceilings - is theirs on every screen and every server they
   * open. Changing servers must not change any of it, and reading it from whichever
   * machine happens to be selected would show them somebody else's.
   *
   * What a machine keeps - its folders, its port, what it follows, when it sleeps -
   * belongs to that machine, and picking a server and then being shown this one's
   * is answering a question nobody asked. Those go to the server that is open, with
   * its key, and it decides whether the asker may change anything.
   */
  const MACHINE_PATHS = ["/machine", "/library/config", "/library/folders",
                         "/library/scan", "/library/numbering", "/follow", "/update",
                         "/wiring", "/traffic", "/copied", "/watching", "/watchlog",
                         "/stats", "/feedback", "/invites", "/gpu", "/standby",
                         "/where", "/copies", "/notice"];

  const where = (path) => {
    const s = window.PD_ON || null;
    if (!s) return path;
    const bare = path.split("?")[0];
    if (!MACHINE_PATHS.some((p) => bare === p || bare.startsWith(p + "/"))) {
      return path;                       // this person's own, wherever they are
    }
    const u = new URL(s.origin.replace(/\/$/, "") + path, location.href);
    if (s.token) u.searchParams.set("t", s.token);
    return u.toString();
  };

  const post = async (path, body) =>
    (await fetch(where(path),
                 { method: "POST", headers: { "Content-Type": "application/json" },
                   body: JSON.stringify(body || {}) })).json();
  const get = async (path) => (await fetch(where(path))).json();

  const block = (title) => {
    const el = document.createElement("div");
    el.className = "setblock";
    if (title) el.innerHTML = "<h3>" + title + "</h3>";
    return el;
  };

  /* ---------------- library ---------------- */

  function row(path, list) {
    const el = document.createElement("div");
    el.className = "folder";
    el.innerHTML = '<span class="p"></span>' +
      '<span class="state"></span><button class="rm" title="Remove">&#10005;</button>';
    el.querySelector(".p").textContent = path;
    const ok = cfg.exists ? cfg.exists[path] !== false : true;
    const st = el.querySelector(".state");
    st.textContent = ok ? "" : "not found";
    st.className = "state" + (ok ? "" : " bad");
    el.querySelector(".rm").onclick = async () => {
      cfg[list] = cfg[list].filter((p) => p !== path);
      cfg = await post("/library/config", { [list]: cfg[list] });
      render();
    };
    return el;
  }

  function folderList(list, label) {
    const box = block(label);
    const items = document.createElement("div");
    (cfg[list] || []).forEach((p) => items.appendChild(row(p, list)));
    if (!(cfg[list] || []).length) {
      const none = document.createElement("div");
      none.className = "note";
      none.textContent = "No folders yet.";
      items.appendChild(none);
    }
    box.appendChild(items);
    const add = document.createElement("div");
    add.className = "addrow";
    add.innerHTML = '<input type="text" placeholder="D:\\Media\\Films or paste a path">' +
      '<button class="btn ghost browse">Browse&hellip;</button>' +
      '<button class="btn add">Add</button>';
    const input = add.querySelector("input");
    add.querySelector(".add").onclick = async () => {
      const p = input.value.trim();
      if (!p) return;
      if (!(cfg[list] || []).includes(p)) cfg[list] = (cfg[list] || []).concat([p]);
      cfg = await post("/library/config", { [list]: cfg[list] });
      render();
    };
    add.querySelector(".browse").onclick = () => openPicker(list);
    input.onkeydown = (e) => { if (e.key === "Enter") add.querySelector(".add").click(); };
    box.appendChild(add);
    return box;
  }

  /* The same walk, for anything that wants one path back: a folder to save into, or
     a torrent file to add. `ending` lists files that end that way alongside the
     folders; `onPick` is handed whichever was chosen and closes the picker. */
  /* The box the picker draws in. It was made by the library pane alone, so every
     other pane asked for one that was not there and the button did nothing at all.
     It is fixed to the window, so it can hang off the body wherever it is wanted. */
  function pickerBox() {
    let wrap = document.querySelector("#picker");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.id = "picker";
      wrap.className = "hidden";
      document.body.appendChild(wrap);
    }
    return wrap;
  }

  async function pickOne(path, ending, onPick) {
    const data = await post("/library/browse", { path: path || "", files: ending || "" });
    const wrap = pickerBox();
    wrap.classList.remove("hidden");
    wrap.innerHTML = "";
    const head = document.createElement("div");
    head.className = "pickhead";
    head.innerHTML = "<span></span>" +
      '<button class="btn ghost up">Up</button>' +
      (ending ? "" : '<button class="btn use">Use this folder</button>') +
      '<button class="btn ghost close">Cancel</button>';
    head.querySelector("span").textContent = data.path || "This computer";
    head.querySelector(".up").onclick = () => pickOne(data.parent || "", ending, onPick);
    head.querySelector(".up").disabled = !data.path;
    head.querySelector(".close").onclick = () => wrap.classList.add("hidden");
    if (!ending) {
      head.querySelector(".use").onclick = () => {
        if (!data.path) return;
        wrap.classList.add("hidden");
        onPick(data.path);
      };
    }
    wrap.appendChild(head);
    const list_el = document.createElement("div");
    list_el.className = "picklist";
    (data.dirs || []).forEach((d) => {
      const b = document.createElement("button");
      b.className = "pickitem";
      b.textContent = d.replace(/\$/, "");
      b.onclick = () => pickOne(d, ending, onPick);
      list_el.appendChild(b);
    });
    (data.files || []).forEach((f) => {
      const b = document.createElement("button");
      b.className = "pickitem";
      b.textContent = "▸ " + f.split(/[\/]/).pop();
      b.onclick = () => { wrap.classList.add("hidden"); onPick(f); };
      list_el.appendChild(b);
    });
    if (!(data.dirs || []).length && !(data.files || []).length) {
      const none = document.createElement("div");
      none.className = "note";
      none.textContent = data.error || "Nothing here.";
      list_el.appendChild(none);
    }
    wrap.appendChild(list_el);
  }

  /* the picker walks the server's drives, because a browser file input hands back a
     sandboxed name rather than a usable path */
  async function openPicker(list, path) {
    picking = list;
    const data = await post("/library/browse", { path: path || "" });
    const wrap = pickerBox();
    wrap.classList.remove("hidden");
    wrap.innerHTML = "";
    const head = document.createElement("div");
    head.className = "pickhead";
    head.innerHTML = "<span></span>" +
      '<button class="btn ghost up">Up</button>' +
      '<button class="btn use">Use this folder</button>' +
      '<button class="btn ghost close">Cancel</button>';
    head.querySelector("span").textContent = data.path || "This computer";
    head.querySelector(".up").onclick = () => openPicker(list, data.parent || "");
    head.querySelector(".up").disabled = !data.path;
    head.querySelector(".close").onclick = () => wrap.classList.add("hidden");
    head.querySelector(".use").onclick = async () => {
      if (!data.path) return;
      if (!(cfg[picking] || []).includes(data.path))
        cfg[picking] = (cfg[picking] || []).concat([data.path]);
      cfg = await post("/library/config", { [picking]: cfg[picking] });
      wrap.classList.add("hidden");
      render();
    };
    wrap.appendChild(head);
    const list_el = document.createElement("div");
    list_el.className = "picklist";
    (data.dirs || []).forEach((d) => {
      const b = document.createElement("button");
      b.className = "pickitem";
      b.textContent = d.replace(/\\$/, "");
      b.onclick = () => openPicker(list, d);
      list_el.appendChild(b);
    });
    if (!(data.dirs || []).length) {
      const none = document.createElement("div");
      none.className = "note";
      none.textContent = data.error || "Nothing inside this folder.";
      list_el.appendChild(none);
    }
    wrap.appendChild(list_el);
  }

  /* Which library opens by default, and which machine does the work when a file
     cannot be played as it is. Both used to sit in the top bar. */
  async function panePlayback(main) {
    const playback = await get("/settings");
    const box = block("Playback");
    box.innerHTML +=
      "<div class='note'>Which library this page opens, and what re-encodes a film " +
      "when the player cannot take it as it is.</div>";

    const row = (label, options, current, note, onPick) => {
      const el = document.createElement("div");
      el.className = "addrow subrow";
      el.innerHTML = "<span class='sublabel'>" + label + "</span>";
      options.forEach(([value, text]) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" + (current === value ? " on" : "");
        b.textContent = text;
        b.onclick = async () => { await onPick(value); render(); };
        el.appendChild(b);
      });
      box.appendChild(el);
      if (note) {
        const n = document.createElement("div");
        n.className = "note";
        n.style.margin = "2px 0 6px 82px";
        n.innerHTML = note;
        box.appendChild(n);
      }
    };

    /* One episode into the next, unless told otherwise. */
    const auto = document.createElement("div");
    auto.className = "addrow subrow";
    auto.innerHTML = "<span class='sublabel'>Next episode</span>";
    [[true, "Play automatically"], [false, "Wait for me"]].forEach(([value, text]) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" + (!!playback.autoNext === value ? " on" : "");
      b.textContent = text;
      b.onclick = async () => {
        await post("/settings", { autoNext: value });
        render();
      };
      auto.appendChild(b);
    });

    /* Which clock everything that prints a time is read by. */
    const clock = document.createElement("div");
    clock.className = "addrow subrow";
    clock.innerHTML = "<span class='sublabel'>Time</span>";
    [["24", "24-hour"], ["12", "12-hour"]].forEach(([value, text]) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" + (CLOCK === value ? " on" : "");
      b.textContent = text;
      b.onclick = async () => {
        CLOCK = value;
        await post("/settings", { clock: value });
        render();
      };
      clock.appendChild(b);
    });
    box.appendChild(auto);
    box.appendChild(clock);

    /* How long that takes. Nought starts the next one the moment the credits end;
       five is long enough to reach for the remote and stop it. */
    const wait = playback.nextDelay === undefined ? 5 : playback.nextDelay;
    if (playback.autoNext) {
      const delay = document.createElement("div");
      delay.className = "addrow subrow";
      delay.innerHTML = "<span class='sublabel'>After</span>";
      [0, 1, 2, 3, 4, 5].forEach((n) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" + (wait === n ? " on" : "");
        b.textContent = n === 0 ? "At once" : n + "s";
        b.onclick = async () => {
          await post("/settings", { nextDelay: n });
          render();
        };
        delay.appendChild(b);
      });
      box.appendChild(delay);
    }

    /* The same level from one release to the next, where one is far off it. */
    const even = document.createElement("div");
    even.className = "addrow subrow";
    even.innerHTML = "<span class='sublabel'>Volume</span>";
    [[true, "Even it out"], [false, "As it was made"]].forEach(([value, text]) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" + (!!playback.evenVolume === value ? " on" : "");
      b.textContent = text;
      b.onclick = async () => {
        await post("/settings", { evenVolume: value });
        render();
      };
      even.appendChild(b);
    });
    box.appendChild(even);
    const evenNote = document.createElement("div");
    evenNote.className = "note";
    evenNote.style.margin = "2px 0 6px 82px";
    evenNote.textContent = playback.evenVolume
      ? "Every measured file is brought to the same level, by up to 8 dB. Sound " +
        "passed through untouched to an amplifier cannot be changed at all, and a " +
        "file is measured the first time the machine is quiet enough to read it."
      : "Every release plays at whatever level it was mixed at.";
    box.appendChild(evenNote);

    const note = document.createElement("div");
    note.className = "note";
    note.style.margin = "2px 0 6px 82px";
    note.textContent = playback.autoNext
      ? (wait === 0 ? "The next episode starts as the credits end."
         : wait + " second" + (wait === 1 ? "" : "s") +
           " after an episode ends, with a chance to stop it.")
      : "An episode ends and stays ended.";
    box.appendChild(note);
    main.appendChild(box);
  }

  /**
   * When the disks are read.
   *
   * On a clock, or when something new lands in one of the folders below - the second
   * waits two minutes for the writing to stop, because reading a torrent halfway
   * through gets its duration wrong and has to be done again.
   */
  function scanning() {
    const box = block("Scanning");
    box.innerHTML += "<div class='note'>When to look for new films and episodes.</div>";

    const row = document.createElement("div");
    row.className = "addrow";
    [[0, "Never"], [60, "Every hour"], [360, "Every 6 hours"], [1440, "Once a day"]]
      .forEach(([mins, label]) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" +
                      ((cfg.scanEvery || 0) === mins ? " on" : "");
        b.textContent = label;
        b.onclick = async () => {
          cfg = await post("/library/config", { scanEvery: mins });
          render();
        };
        row.appendChild(b);
      });
    box.appendChild(row);

    // "when to scan" and "scan now" are the same subject, and were two boxes with a
    // screenful between them
    const doit = document.createElement("div");
    doit.className = "addrow";
    doit.innerHTML =
      '<button class="btn go">Scan now</button>' +
      '<button class="btn ghost quick">Index only (skip probing)</button>' +
      '<span class="note" id="scanstate"></span>';
    doit.querySelector(".go").onclick = () => startScan(true);
    doit.querySelector(".quick").onclick = () => startScan(false);
    box.appendChild(doit);

    // The files have not changed, so a scan skips them: a title the parser once read
    // wrong stays wrong until every name is read again.
    const again = document.createElement("div");
    again.className = "addrow";
    again.innerHTML =
      '<button class="btn ghost">Read the filenames again</button>' +
      '<span class="note">For titles the scanner got wrong. Nothing is downloaded ' +
      'or re-probed.</span>';
    again.querySelector("button").onclick = async () => {
      const r = await post("/library/reparse", {});
      toast(r.error || "Reading every filename again");
      poll();
    };
    box.appendChild(again);

    const watch = document.createElement("div");
    watch.className = "addrow";
    const w = document.createElement("button");
    w.className = "btn ghost kind" + (cfg.scanOnChange ? " on" : "");
    w.textContent = cfg.scanOnChange ? "Watching the folders" : "Watch the folders";
    w.onclick = async () => {
      cfg = await post("/library/config", { scanOnChange: !cfg.scanOnChange });
      render();
    };
    watch.appendChild(w);
    box.appendChild(watch);

    const how = document.createElement("div");
    how.className = "note";
    how.innerHTML =
      "Watching notices a finished download by itself - it waits two minutes for the " +
      "file to stop growing, then reads it. Nothing needs setting in qBittorrent. " +
      "To be explicit instead, point its <i>Run external program on torrent " +
      "completion</i> at:<br><code>curl http://" +
      (location.host || "xxx.xxx.xxx:8765") + "/library/scan</code>";
    box.appendChild(how);
    return box;
  }

  /**
   * The colour of the thing.
   *
   * One for the server rather than one each: it is what Palladium looks like, and
   * it should look the same on the television as it does here. It sat under
   * Subtitles, which is where it was written rather than where anyone would look
   * for it - it is not about subtitles at all.
   */
  function accentBlock(main, data) {
      // guests take it as they find it: they may choose their own, but not the
      // server's, and the button that sets everyone's is not theirs to press
      const guest = CFG && CFG.guest;
      if ((data.accents || []).length) {
        const look = block("Colour");
        look.innerHTML +=
          "<div class='note'>Accent colour for this page, the TV app and the phone " +
          "app. Per viewer — other accounts keep their own.</div>";
        const row = document.createElement("div");
        row.className = "addrow subrow swatches";
        const paint = (code, name, everyone) => async () => {
          const said = await post("/settings",
                                  everyone ? { accent: code, everyone: true }
                                           : { accent: code });
          if (said && said.accent) {
            data.accent = said.accent;
            data.accentDefault = said.accentDefault || data.accentDefault;
            if (window.paintAccent) paintAccent(said.accent);
            toast(everyone ? "Everyone starts with " + name : "Colour: " + name);
            render();
          }
        };
        data.accents.forEach((c) => {
          const b = document.createElement("button");
          b.className = "swatch" + (c.code === data.accent ? " on" : "");
          b.style.background = c.code;
          b.title = c.code === "#e5a00d" ? "Gold, don't use this" : c.name;
          b.setAttribute("aria-label", c.name);
          b.onclick = paint(c.code, c.name, false);
          row.appendChild(b);
        });
        look.appendChild(row);
        // A colour of one's own is a decision, and every decision needs its way back.
        const back = document.createElement("div");
        back.className = "addrow subrow";
        if (data.accentMine) {
          const b = document.createElement("button");
          b.className = "btn ghost kind";
          b.textContent = "Use the server's colour";
          b.onclick = paint("", "the server's", false);
          back.appendChild(b);
        }
        // and the owner decides what somebody meets before they have chosen
        if (!guest) {
          const d = document.createElement("button");
          d.className = "btn ghost kind";
          d.textContent = data.accent === data.accentDefault
            ? "This is everyone's default" : "Make this everyone's default";
          d.disabled = data.accent === data.accentDefault;
          d.onclick = paint(data.accent, "this", true);
          back.appendChild(d);
        }
        if (back.children.length) look.appendChild(back);
        main.appendChild(look);
      }
  }

  /**
   * Seasons the files count differently from the season itself.
   *
   * A release that counts a double-length premiere as two is one ahead of the
   * database for the rest of the season. The scanner puts the episodes on the numbers
   * their titles say, which is right nearly always and worth being able to overrule.
   */
  async function numberingBlock(main) {
    let said = { seasons: [] };
    try {
      said = await get("/library/numbering");
    } catch (e) { return; }
    if (!(said.seasons || []).length) return;
    const box = block("Episode numbering");
    box.innerHTML +=
      "<div class='note'>These releases number the episodes differently from the " +
      "database. Auto puts each episode on the number its own title says, which is " +
      "what the file is called against what the season holds.</div>";
    said.seasons.forEach((s) => {
      const row = document.createElement("div");
      row.className = "addrow subrow";
      row.innerHTML = "<span class='sublabel'>" + esc(s.show) + "</span>" +
        "<span class='dim'>Season " + s.season + " &middot; files are " +
        (s.shift > 0 ? "+" + s.shift : s.shift) + " &middot; " + s.files +
        " of " + s.total + "</span>";
      const pick = document.createElement("select");
      // two answers, because there are two: the titles decide, or the filenames do
      [["auto", "Auto (by title)"], ["files", "Follow the files"]].forEach(([v, t]) => {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = t;
        o.selected = (s.mode || "auto") === v;
        pick.appendChild(o);
      });
      pick.onchange = async () => {
        pick.disabled = true;
        const back = await post("/library/numbering",
                                { key: s.key, season: s.season, mode: pick.value });
        pick.disabled = false;
        toast(back && back.moved
          ? back.moved + " episode" + (back.moved === 1 ? "" : "s") + " renumbered"
          : "Nothing moved");
        render();
      };
      row.appendChild(pick);
      box.appendChild(row);
    });
    main.appendChild(box);
  }

  /* Films offered from torrent packs, fetched through qBittorrent one at a time. */
  async function downloadsBlock() {
    const box = block("Torrents");
    let said = {};
    try {
      said = await get("/torrents");
    } catch (e) {
      said = {};
    }
    const cfg = said.config || {};
    const qb = said.qbittorrent || {};
    box.innerHTML += "<div class='note'>Films and programmes a pack can give are " +
      "shown alongside the library with a download mark, before they are here. " +
      "Download on one fetches that film alone through qBittorrent; the rest of " +
      "the pack is left alone. Every download is under Log, Downloads, with who " +
      "asked for it.</div>";
    const field = (label, value, hint, key, secret) => {
      const r = document.createElement("div");
      r.className = "addrow subrow";
      r.innerHTML = "<span class='sublabel'>" + label + "</span>";
      const input = document.createElement("input");
      input.type = secret ? "password" : "text";
      input.value = value || "";
      input.placeholder = hint || "";
      input.onchange = async () => {
        await post("/torrents/config", { [key]: input.value.trim() });
        toast(label + " saved");
        render();
      };
      r.appendChild(input);
      box.appendChild(r);
    };
    field("qBittorrent", cfg.url, "http://127.0.0.1:8080", "url");
    field("User", cfg.user, "empty when qBittorrent trusts this machine", "user");
    field("Password", "", cfg.hasPassword ? "saved - type to change" : "", "password", true);
    const saveRow = document.createElement("div");
    saveRow.className = "addrow subrow";
    saveRow.innerHTML = "<span class='sublabel'>Save into</span>";
    const pick = document.createElement("select");
    const folders = (said.folders || []).slice();
    if (cfg.saveTo && folders.indexOf(cfg.saveTo) < 0) folders.unshift(cfg.saveTo);
    folders.forEach((f) => {
      const o = document.createElement("option");
      o.value = f;
      o.textContent = f;
      o.selected = f === cfg.saveTo;
      pick.appendChild(o);
    });
    pick.onchange = async () => {
      await post("/torrents/config", { saveTo: pick.value });
      toast("Saving into " + pick.value);
    };
    saveRow.appendChild(pick);
    // and anywhere else. The list above is the library's own folders, which is not
    // where downloads necessarily go - a second drive with room on it is not a place
    // films are indexed from.
    const elsewhere = document.createElement("button");
    elsewhere.className = "btn ghost";
    elsewhere.textContent = "Browse…";
    elsewhere.onclick = () => pickOne(cfg.saveTo || "", "", async (where) => {
      await post("/torrents/config", { saveTo: where });
      toast("Saving into " + where);
      render();
    });
    saveRow.appendChild(elsewhere);
    box.appendChild(saveRow);
    if (said.free !== null && said.free !== undefined) {
      const room = document.createElement("div");
      room.className = "note";
      room.textContent = said.free + " GB free on " + (cfg.saveTo || "that drive") + ".";
      box.appendChild(room);
    }
    // the next episode, fetched while the one before it is still on. Two buttons, the
    // chosen one lit: the same control every other setting on these pages uses. A
    // checkbox here was the browser's own, which matches nothing else on the page.
    const nextRow = document.createElement("div");
    nextRow.className = "addrow subrow";
    nextRow.innerHTML = "<span class='sublabel'>Next episode</span>";
    [[true, "Keep it ready"], [false, "Only when asked"]].forEach(([value, text]) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" + (!!cfg.nextEpisode === value ? " on" : "");
      b.textContent = text;
      b.onclick = async () => {
        await post("/torrents/config", { nextEpisode: value });
        render();
      };
      nextRow.appendChild(b);
    });
    box.appendChild(nextRow);
    const why = document.createElement("div");
    why.className = "note";
    why.textContent = cfg.nextEpisode
      ? "Half way through an episode, the one after it is asked for from the pack it " +
        "came in - if a pack has it and this machine does not."
      : "Episodes are fetched only when somebody asks for one.";
    box.appendChild(why);
    // connected or not, at a glance: a green dot while Palladium reaches qBittorrent
    const state = document.createElement("div");
    state.className = "qbstate " + (qb.ok ? "on" : "off");
    state.innerHTML = "<i class='qbdot'></i><span></span>";
    state.querySelector("span").textContent = qb.ok
      ? "Connected to qBittorrent " + qb.version
      : "Not connected to qBittorrent: " + (qb.why || "no reply");
    box.appendChild(state);
    // the packs, in the same box: add one, what has come of each, and take one away
    const packsHead = document.createElement("div");
    packsHead.className = "sublabel addinhead";
    packsHead.textContent = "Packs";
    box.appendChild(packsHead);

    // a pack: its .torrent file, by its path on this computer or found by
    // browsing to it, starting where packs load themselves from
    const addRow = document.createElement("div");
    addRow.className = "addrow subrow";
    addRow.innerHTML = "<span class='sublabel'>Add a pack</span>";
    const pathBox = document.createElement("input");
    pathBox.type = "text";
    pathBox.placeholder = "C:\\Users\\...\\pack.torrent";
    const addPath = document.createElement("button");
    addPath.className = "btn ghost";
    addPath.textContent = "Add";
    const choose = document.createElement("button");
    choose.className = "btn ghost";
    choose.textContent = "Browse…";
    const added = (r) => {
      toast(r && r.ok
        ? (r.already ? "That pack is already here"
           : r.films + " films from " + r.name +
             (r.refused ? " - but qBittorrent cannot load it: " + r.refused : ""))
        : (r && r.why) || "Could not add it");
      render();
    };
    addPath.onclick = async () => {
      if (!pathBox.value.trim()) return;
      added(await post("/torrents/add", { path: pathBox.value.trim() }));
    };
    // Opens on the folder packs load themselves from, and walks anywhere from there.
    // It reads this machine's own folders rather than the browser's: a file box hands
    // back a sandboxed name, and the server is the one that has to open the file.
    choose.onclick = () => pickOne(said.packFolder || "", ".torrent", async (file) => {
      pathBox.value = file;
      added(await post("/torrents/add", { path: file }));
    });
    addRow.append(pathBox, addPath, choose);
    box.appendChild(addRow);

    (said.packs || []).forEach((p) => {
      const r = document.createElement("div");
      r.className = "addrow";
      r.style.alignItems = "center";
      const what = document.createElement("span");
      what.style.flex = "1 1 auto";
      what.style.whiteSpace = "pre-line";
      what.textContent = p.name + "\n" +
        p.downloaded + " / " + p.films + " downloaded  \u00b7  " + p.downloadedGb + " GB" +
        (p.downloading ? "  \u00b7  " + p.downloading + " downloading" : "") + "\n" +
        p.offered + " on offer  \u00b7  " + p.held + " already in the library  \u00b7  " +
        p.gb + " GB in the pack\n" +
        p.matched + " matched" + (p.waiting ? ", " + p.waiting + " still to look up" : "") +
        (p.unmatched ? ", " + p.unmatched + " not found" : "");
      if (p.refused) {
        const bad = document.createElement("div");
        bad.className = "note bad";
        bad.textContent = "qBittorrent cannot load this pack: " + p.refused +
          ". Its films cannot be downloaded from it.";
        what.appendChild(bad);
      }
      // qBittorrent has stopped on this pack - usually because the drive its files
      // sit on went away for a moment. The server reads them again once by itself;
      // this is the press that says try it again anyway.
      if (p.halted) {
        const stop = document.createElement("div");
        stop.className = "note bad";
        stop.textContent = p.haltedWhy || "qBittorrent has stopped on this pack.";
        what.appendChild(stop);
      }
      if (p.halted) {
        const again = document.createElement("button");
        again.className = "btn ghost";
        again.textContent = "Read files again";
        again.onclick = async () => {
          again.disabled = true;
          again.textContent = "Reading…";
          try {
            const said = await post("/torrents/recheck", { hash: p.hash });
            toast(said && said.rechecked ? "qBittorrent is reading its files again"
                                         : "qBittorrent would not take that");
          } catch (e) {
            toast("Could not ask qBittorrent");
          }
          render();
        };
        r.appendChild(again);
      }
      const rm = document.createElement("button");
      rm.className = "btn ghost bad";
      rm.textContent = "Remove";
      rm.onclick = async () => {
        if (!confirm("Stop offering the films in " + p.name + "? What was downloaded stays.")) return;
        await post("/torrents/remove", { hash: p.hash });
        render();
      };
      r.append(what, rm);
      box.appendChild(r);
    });
    if ((said.packs || []).length) {
      const n = document.createElement("div");
      n.className = "note";
      n.textContent = (said.offered || 0) + " films on offer that the library does not hold.";
      box.appendChild(n);
    }
    // Who asked for what. The weekly total per person is already beside their limit
    // under Users, which answers "how much" and not "what" - and "what" is the half
    // somebody actually wants when they are looking at a month of downloads.
    const who = document.createElement("div");
    who.className = "note";
    who.style.marginTop = "12px";
    who.textContent = "Reading what has been fetched…";
    box.appendChild(who);
    get("/torrents/log").then((log) => {
      const since = Date.now() / 1000 - 30 * 86400;
      const rows = (log.downloads || []).filter(
        (d) => (d.when || 0) >= since && d.state !== "failed" && d.state !== "cancelled");
      who.textContent = "";
      if (!rows.length) { who.textContent = "Nothing fetched in the last month."; return; }
      const head = document.createElement("div");
      head.className = "sublabel";
      head.textContent = "Fetched in the last month";
      who.appendChild(head);
      const by = {};
      rows.forEach((d) => {
        const name = String(d.who || "somebody").trim() || "somebody";
        (by[name] = by[name] || []).push(d);
      });
      Object.keys(by).sort((a, b) => by[b].length - by[a].length).forEach((name) => {
        const theirs = by[name].sort((a, b) => (b.when || 0) - (a.when || 0));
        const gb = theirs.reduce((n, d) => n + (d.size || 0) / 1e9, 0);
        const line = document.createElement("div");
        line.className = "note";
        line.style.marginTop = "6px";
        line.textContent = name + "  ·  " + theirs.length + " films, " +
          gb.toFixed(1) + " GB";
        who.appendChild(line);
        theirs.slice(0, 12).forEach((d) => {
          const one = document.createElement("div");
          one.className = "note";
          one.style.marginLeft = "12px";
          const when = d.when
            ? new Date(d.when * 1000).toLocaleDateString(undefined,
                { day: "numeric", month: "short" })
            : "";
          one.textContent = [when, d.title, d.year || "",
                             ((d.size || 0) / 1e9).toFixed(1) + " GB",
                             d.state === "done" ? "" : d.state]
            .filter(Boolean).join("  ·  ");
          who.appendChild(one);
        });
        if (theirs.length > 12) {
          const more = document.createElement("div");
          more.className = "note";
          more.style.marginLeft = "12px";
          more.textContent = "and " + (theirs.length - 12) + " more";
          who.appendChild(more);
        }
      });
    }).catch(() => { who.textContent = "Could not read what has been fetched."; });
    return box;
  }

  async function paneLibrary(main) {
    await panePlayback(main);
    await numberingBlock(main);
    accentBlock(main, await get("/settings"));
    const stats = await get("/library/status");
    const bar = block("Index");
    bar.classList.add("stats");
    bar.innerHTML += "<div class='statgrid'>" +
      [["Films", stats.movies], ["Shows", stats.shows], ["Episodes", stats.episodes],
       ["Files", stats.files], ["Probed", stats.probed],
       ["Identified", stats.identified], ["Downloadable", stats.offered || 0]]
        .map(([k, v]) => "<div><b>" + v + "</b><span>" + k + "</span></div>").join("") +
      "</div>";
    /* Two different titles that came out with the same key. One poster then stands
       for both, which is not something a number in the grid above can show. The cure
       is correcting a year, so the titles are named. */
    (stats.clashes || []).slice(0, 6).forEach((said) => {
      const row = document.createElement("div");
      row.className = "note";
      row.textContent = "Two titles under one key - " + said +
        ". Correct a year on one of them and scan again.";
      bar.appendChild(row);
    });
    main.appendChild(bar);

    /* What a folder is in decides what its files are: a film folder yields films, a
       series folder yields episodes. Mixed is for a folder that genuinely holds both -
       a download folder - where the filename has to decide instead. */
    main.appendChild(scanning());
    main.appendChild(folderList("movies", "Film folders"));
    main.appendChild(folderList("tv", "Series folders"));
    main.appendChild(folderList("mixed", "Mixed folders"));
    const rule = document.createElement("div");
    rule.className = "note";
    rule.style.margin = "-6px 0 18px";
    rule.innerHTML =
      "One list per folder. <b>Film</b> folders give films, <b>series</b> folders give " +
      "episodes, <b>mixed</b> reads the filename. An inner folder beats an outer one.";
    main.appendChild(rule);
    const key = block("Metadata");
    key.innerHTML +=
      "<div class='note'>Posters, plots and IMDb ids come from TMDB. IMDb itself has " +
      "no public API, and this key is per-server - if you are running your own " +
      "Palladium you need your own, which is free and takes a couple of minutes:" +
      "<ol class='steps'>" +
      "<li>Make an account at <b>themoviedb.org</b> and confirm the email.</li>" +
      "<li>Open <b>Settings \u2192 API</b> from the avatar menu, top right.</li>" +
      "<li>Request an <b>API Key</b>, choose <b>Developer</b>, and accept the terms.</li>" +
      "<li>For the form: personal use is fine \u2014 a type of <i>Personal</i>, any " +
      "name, and <b>http://localhost</b> as the URL are all accepted.</li>" +
      "<li>Copy the <b>API Key (v3 auth)</b> \u2014 32 characters \u2014 and paste it " +
      "below.</li></ol>" +
      "Without a key the library still works, but titles come from filenames alone and " +
      "there are no posters.</div>" +
      '<div class="addrow"><input id="tmdbkey" type="text" placeholder="TMDB API key">' +
      '<button class="btn save">Save</button></div>';
    key.querySelector("#tmdbkey").value = cfg.tmdb_key || "";
    key.querySelector(".save").onclick = async () => {
      cfg = await post("/library/config", { tmdb_key: $("#tmdbkey").value.trim() });
      toast("Metadata key saved");
    };
    main.appendChild(key);

    const pick = document.createElement("div");
    pick.id = "picker";
    pick.className = "hidden";
    main.appendChild(pick);
    poll();
  }

  /* ---------------- people: who may watch this library ---------------- */

  /* Everybody who watches here, and what the other server should keep for them. */
  function paneUsers(main, data) {
    const box = block("What is kept for whom");
    box.innerHTML +=
      "<div class='note'>When another server follows this one, these say whose " +
      "half-watched films it keeps a copy of and whose watchlist. Nothing is kept " +
      "for a person with both off.<br>Owner marks whoever sits at this machine: their " +
      "viewing is what its own screens file, so one person is not a guest in a " +
      "browser and the machine itself on their own network.</div>";
    const list = document.createElement("div");
    // whoever owns the machine is a viewer too, and has the same answers to give -
    // unless they watch under a key of their own, in which case that row is them
    // and a second one called "You" would be the same person twice
    if (!(data.me || {}).token) {
      list.appendChild(cacheRow(Object.assign(
        { name: data.ownerName || "You", token: "me" }, data.me || {})));
    }
    // Every key, in one list, each saying what it is for. Splitting them into three
    // lists put the same question in three places; a key is one thing with a role on
    // it, and the role is what somebody sets.
    // Grouped by what a key is for and alphabetical inside each group: a list of a
    // dozen is read by looking for a name, not by remembering what was made when.
    const RANK = ["owner", "cache", "admin", "user"];
    const GROUP = { owner: "Owner", admin: "Admin", user: "Users", cache: "Cache" };
    const place = (w) => {
      const at = RANK.indexOf(w.role || "user");
      return at < 0 ? RANK.length : at;
    };
    const people = (data.people || []).slice().sort((a, b) =>
      place(a) - place(b) ||
      String(a.name || "").localeCompare(String(b.name || "")));
    let group = null;
    people.forEach((w) => {
      const role = w.role || "user";
      if (role !== group) {
        group = role;
        const head = document.createElement("div");
        head.className = "note";
        // space above each group but the first. No rule of its own: every row
        // already carries one underneath it, and the two drew as a double line.
        head.style.cssText = "letter-spacing:.08em;text-transform:uppercase;" +
          (list.children.length ? "margin:20px 0 4px" : "margin:4px 0 4px");
        head.textContent = GROUP[role] || role;
        list.appendChild(head);
      }
      // a person is a person whatever they may do here: only a key for a machine
      // wears the machine row, with its gigabyte ceiling and its "may copy"
      const machine = role === "cache";
      const row = machine ? machineRow(w) : cacheRow(w);
      row.appendChild(roleBox(w));
      list.appendChild(row);
    });
    box.appendChild(list);
    main.appendChild(box);

    // and a way to make a key for a machine from the same page the keys are read on
    const make = block("A key for another server");
    const makeRow = document.createElement("div");
    makeRow.className = "addrow";
    makeRow.style.alignItems = "center";
    const named = document.createElement("input");
    named.type = "text";
    named.placeholder = "what that machine is called";
    named.style.flex = "1 1 auto";
    makeRow.appendChild(named);
    {
      const b = document.createElement("button");
      b.className = "btn ghost";
      b.textContent = "Keeps copies";
      b.onclick = async () => {
        const who = named.value.trim();
        if (!who) { toast("Give the machine a name first"); return; }
        await post("/invites", { name: who, kind: "machine" });
        toast("A key for " + who);
        named.value = "";
        render();
      };
      makeRow.appendChild(b);
    }
    make.appendChild(makeRow);
    make.innerHTML += "<div class='note'>Keeps copies: that machine reads this " +
      "library and holds copies of it, so it can answer while this server is off.</div>";
    main.appendChild(make);
  }

  /* What a key is for: a viewer, somebody with the run of the place, or a server
     that keeps copies. */
  function roleBox(who) {
    const sel = document.createElement("select");
    sel.className = "btn ghost";
    // hard against the right edge and one width, so it lands in the same place on
    // a row for a person and a row for a machine - they carry different controls
    // before it, and the select was sizing itself to its longest word
    sel.style.marginLeft = "auto";
    sel.style.width = "8.4em";
    [["user", "User"], ["owner", "Owner"], ["admin", "Admin"], ["cache", "Cache"]]
      .forEach(([id, label]) => {
        const o = document.createElement("option");
        o.value = id;
        o.textContent = label;
        if ((who.role || "user") === id) o.selected = true;
        sel.appendChild(o);
      });
    sel.onchange = async () => {
      if (sel.value === "owner" &&
          !confirm("Give " + (who.name || "that key") + " the run of this server? " +
                   "It could then change the library and the keys, this one " +
                   "included.")) {
        sel.value = who.role || "user";
        return;
      }
      await post("/invites/role", { token: who.token, role: sel.value });
      render();
    };
    return sel;
  }

  /* A key belonging to a server rather than to a person: what it may hold, whether
     it may copy at all, and a way to take it back. */
  function machineRow(who) {
    // Built like a person's row - the same name column, the same note under it, the
    // same boxes - because a list where one row is laid out differently reads as a
    // different kind of thing altogether.
    const el = document.createElement("div");
    el.className = "person";
    el.innerHTML = '<div class="pmeta"><b></b><span class="note"></span></div>';
    el.querySelector("b").textContent = who.name || "a machine";
    el.querySelector(".note").textContent =
      (who.mayCopy === false ? "may read, no cache" : "may keep a cache");

    // a person's row carries three buttons before its first box and this one has
    // two, so a gap makes up the difference and the boxes share a column
    const gap = document.createElement("span");
    gap.style.cssText = "flex:0 0 250px";
    el.appendChild(gap);

    const cap = document.createElement("label");
    cap.className = "note capgb";
    cap.textContent = "Keep ";
    const gb = document.createElement("input");
    gb.type = "text";
    gb.inputMode = "numeric";
    gb.value = who.cap ? String(who.cap) : "";
    gb.placeholder = "no limit";
    gb.onchange = async () => {
      const n = Math.max(0, parseFloat(gb.value.replace(",", ".")) || 0);
      await post("/follow/key", { set: who.token, cap: n });
      render();
    };
    cap.appendChild(gb);
    cap.appendChild(document.createTextNode(" GB"));

    const may = document.createElement("button");
    may.className = "btn ghost kind" + (who.mayCopy === false ? "" : " on");
    may.textContent = who.mayCopy === false ? "Read only" : "May cache";
    may.onclick = async () => {
      await post("/follow/key", { set: who.token, mayCopy: who.mayCopy === false });
      render();
    };
    el.appendChild(may);
    el.appendChild(cap);

    const off = document.createElement("button");
    off.className = "btn ghost bad";
    off.textContent = "Revoke";
    off.onclick = async () => {
      if (!confirm("Revoke the key for " + (who.name || "that machine") +
                   "? It keeps what it has already and can take no more.")) return;
      await post("/follow/key", { remove: true, only: who.token });
      render();
    };
    el.appendChild(off);
    return el;
  }

  function cacheRow(who) {
    const el = document.createElement("div");
    el.className = "person";
    el.innerHTML = '<div class="pmeta"><b></b><span class="note"></span></div>' +
      // "kind" is what carries the on colour: without it the two buttons saved the
      // setting and looked exactly the same afterwards
      // Each button says what it actually keeps, because none of these are obvious
      // from two words: what is on the tin is a name, and the rule behind it decides
      // how much of somebody else's disk this fills.
      '<button class="btn ghost kind cdeck" title="What they are part-way through, ' +
      'by programme rather than by episode: an episode finished hands over to the ' +
      'next one, and the one before it is kept as well. As many episodes ahead as ' +
      'the Episodes and Hours settings allow. Anything they have put aside on ' +
      'Continue watching is dropped.">Continue watching</button>' +
      '<button class="btn ghost kind clist" title="Everything on their watchlist, ' +
      'and for a programme the next unwatched episodes rather than all of them - as ' +
      'many as Episodes ahead allows, unless Whole list is on. Programmes on ' +
      'shelves they made count too; the films on those shelves do not.">' +
      'Watchlist</button>' +
      // and their shuffle: the hat's next draws, so a round carries on when this
      // machine is off. The server has always read this flag and there was no way
      // to set it, so only the owner's shuffle was ever kept.
      // the last dozen the house watched, and the episodes after them
      '<button class="btn ghost kind clately" title="The last dozen things watched ' +
      'by the people this is on for, and the episodes following them. A shuffle ' +
      'draw does not count as watching: nobody chose it, and it is no reason to ' +
      'copy the rest of a series.">Watched lately</button>' +
      '<button class="btn ghost kind ccasual" title="The ten their hat has already ' +
      'drawn, and anything left part-way in a shuffle, so a round carries on when ' +
      'this machine is off. Only what is drawn - a shuffled shelf is not stocked in ' +
      'season order.">Shuffle</button>' +
      // whether this person is handed the address this machine answers to on its
      // own network. They always have the way in from outside, which is the one
      // that works from where they are; the other is inside somebody's house.
      '<button class="btn ghost kind clan" title="Hand them the address this server ' +
      'answers to on the home network, which is the quicker way in while they are ' +
      'in the house. They always have the address from outside, which works ' +
      'anywhere.">Home address</button>' +
      // gigabytes a week: copied to the other machine for them, and downloaded by them
      '<label class="note capgb">Sync <input class="csync" type="text" ' +
      'inputmode="decimal" placeholder="no limit"> GB a week</label>' +
      '<label class="note capgb">Download <input class="cdown" type="text" ' +
      'inputmode="decimal" placeholder="no limit"> GB a week</label>' +
      '';
    const cost = who.cost || {};
    // what this person costs the other server, so the dear ones can be turned off
    const bill = cost.files
      ? cost.files + " files, " + cost.gb + " GB (" + cost.deck +
        " half-watched, " + cost.list + " on the list" +
        ")"
      : "nothing to keep";
    // the name on the key, and the one they go by if they have chosen one
    el.querySelector("b").textContent = who.name +
      (who.shown && who.shown !== who.name ? "  ·  goes by " + who.shown : "") +
      (who.you ? "  (you)" : "");
    el.querySelector(".note").textContent = (who.token === "me" || who.you
      ? "this machine's owner"
      : (who.lastSeen
          ? "last watched " + new Date(who.lastSeen * 1000).toLocaleDateString()
          : "not used yet")) + " · " + bill;
    [["cdeck", "cacheDeck"], ["clist", "cacheList"],
     ["ccasual", "cacheCasual"], ["clately", "cacheLately"],
     ["clan", "shareLan"]].forEach(([css, name]) => {
      const b = el.querySelector("." + css);
      if (who[name]) b.classList.add("on");
      b.onclick = async () => {
        b.disabled = true;
        const body = { token: who.token || "me" };
        body[name] = !who[name];
        const back = await post("/invites/cache", body);
        b.disabled = false;
        // what the server now holds, not what was pressed: the row redraws from it
        const said = who.token === "me"
          ? (back && back.me) || {}
          : ((back && back.people) || []).find((p) => p.token === who.token) || {};
        who[name] = !!said[name];
        b.classList.toggle("on", !!who[name]);
      };
    });
    [["csync", "syncGbWeek", "copied to the other machine"],
     ["cdown", "downloadGbWeek", "downloaded"]].forEach(([css, name, what]) => {
      const box = el.querySelector("." + css);
      box.value = Number(who[name]) > 0 ? String(who[name]) : "";
      // and what they have had of it this week, beside the limit
      if (name === "downloadGbWeek") {
        const used = document.createElement("span");
        used.className = "capused";
        box.parentNode.appendChild(used);
        weekDownloads().then((by) => {
          const gb = by[String(who.name || "").trim().toLowerCase()] || 0;
          used.textContent = "\u00b7 " + gb.toFixed(1) + " GB this week";
        });
      }
      box.onchange = async () => {
        const body = { token: who.token || "me" };
        body[name] = Math.max(0, parseFloat(box.value.replace(",", ".")) || 0);
        await post("/invites/cache", body);
        who[name] = body[name];
        toast(body[name] ? who.name + ": " + body[name] + " GB a week " + what
                         : who.name + ": no limit on what is " + what);
      };
    });
    return el;
  }

  /* What each person has downloaded in the last seven days, by name, counted the way the
     weekly limit counts it: whole films asked for, failed and cancelled ones aside. */
  let weekBook = null;
  /* What a download is called. A programme's name is the same on every episode of
     it, so which episode goes wherever a download is named. */
  function downloadName(d, fallback) {
    const name = d.title || fallback || "";
    if (!d.episode) return name + (d.year ? " (" + d.year + ")" : "");
    return name + "  S" + String(d.season || 0).padStart(2, "0") +
      "E" + String(d.episode).padStart(2, "0") +
      (d.episodeName ? "  " + d.episodeName : "");
  }

  function weekDownloads() {
    if (!weekBook) {
      weekBook = get("/torrents/log").then((said) => {
        const since = Date.now() / 1000 - 7 * 86400;
        const by = {};
        (said.downloads || []).forEach((d) => {
          if ((d.when || 0) < since || d.state === "failed" || d.state === "cancelled") return;
          const who = String(d.who || "").trim().toLowerCase();
          by[who] = (by[who] || 0) + (d.size || 0) / 1e9;
        });
        return by;
      }).catch(() => ({}));
      setTimeout(() => { weekBook = null; }, 30000);
    }
    return weekBook;
  }

  function personRow(who, refresh) {
    const el = document.createElement("div");
    el.className = "person";
    const seen = who.lastSeen
      ? "last watched " + new Date(who.lastSeen * 1000).toLocaleString()
      : "not used yet";
    el.innerHTML =
      '<div class="pmeta"><b></b><span class="note"></span></div>' +
      '<input class="plink" readonly>' +
      '<button class="btn mail">Email</button>' +
      '<button class="btn ghost send">Share&hellip;</button>' +
      '<button class="btn ghost copy">Copy</button>' +
      '<button class="btn ghost rm" title="Revoke">&#10005;</button>';
    // the person at this machine is one of these rows, and it is worth saying which
    // the name on the key, and the one they go by if they have chosen one
    el.querySelector("b").textContent = who.name +
      (who.shown && who.shown !== who.name ? "  ·  goes by " + who.shown : "") +
      (who.you ? "  (you)" : "");
    if (who.you) el.querySelector("b").classList.add("isyou");
    el.querySelector(".note").textContent = [
      who.you ? "the person at this machine" : "",
      who.code ? "code " + who.code : "",
      who.email, seen,
      // which build they are on, as their app last said: a fault reported from a
      // three-week-old one is a different conversation from a fault on today's
      who.app ? "on " + who.app : "",
      who.expires ? "expires " + new Date(who.expires * 1000).toLocaleDateString() : "",
    ].filter(Boolean).join(" \u00b7 ");
    const field = el.querySelector(".plink");
    field.value = who.link;
    field.onclick = () => field.select();
    if (who.copyLink) {
      const also = document.createElement("div");
      also.className = "note";
      also.textContent = "When this server is off: " + who.copyLink;
      el.appendChild(also);
    }
    el.querySelector(".copy").onclick = async () => {
      // navigator.clipboard exists only where the page came over https or from
      // localhost, and this page is mostly opened at 192.168.something - so the
      // button threw and the link never reached the clipboard. The old way still
      // works everywhere: select the field and let the browser copy it.
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(who.link);
        } else {
          field.select();
          field.setSelectionRange(0, field.value.length);
          if (!document.execCommand("copy")) throw new Error("no");
        }
        toast("Link copied - paste it into a message");
      } catch (e) {
        field.select();
        toast("Press Ctrl+C to copy the link");
      }
    };
    el.querySelector(".send").onclick = () => {
      if (navigator.share) {
        navigator.share({ title: "Palladium", text: message(who), url: who.link })
          .catch(() => {});
      } else {
        mail(who, refresh);
      }
    };
    el.querySelector(".mail").onclick = () => mail(who, refresh);
    el.querySelector(".rm").onclick = async () => {
      if (!confirm("Revoke access for " + who.name + "? Their link stops working."))
        return;
      await post("/invites/revoke", { token: who.token });
      refresh();
    };
    return el;
  }

  /* The message itself. It says what the link is for, because a bare address in an
     inbox looks like something to delete. */
  function message(who) {
    const nl = String.fromCharCode(10);
    return "Hello " + who.name + "," + nl + nl +
      "here is your key to my film library - it is yours alone, so keep it to " +
      "yourself:" + nl + nl + who.link + nl + nl +
      "Opens in any browser, and the same page installs the app for a phone or a " +
      "Google TV." + nl +
      // the second machine, named in the message rather than explained later: a
      // guest whose evening stops has nowhere to look otherwise
      (who.copyLink
        ? nl + "If it does not answer - my server sleeps at night - this one holds " +
          "what you were part-way through:" + nl + nl + who.copyLink + nl
        : "");
  }

  /* Hand the message to whatever this machine uses for mail. The address is kept
     with the invitation, so the next time there is nothing to type. */
  async function mail(who, refresh) {
    let to = who.email || "";
    if (!to) {
      to = (prompt("Email address for " + who.name + ":", "") || "").trim();
      if (!to) return;
      await post("/invites/update", { token: who.token, email: to });
      if (refresh) refresh();
    }
    location.href = "mailto:" + encodeURIComponent(to) +
      "?subject=" + encodeURIComponent("A link to my film library") +
      "&body=" + encodeURIComponent(message(who));
  }

  //: which half of the Users tab is showing: the links, or what is kept for whom
  let whoTab = "users";       // the list of people: what this tab is opened for
  //: Remote computer has two directions in it: another machine keeping a copy of
  //: this library, and this machine keeping a copy of another. They are not the
  //: same subject and were read as one long page.
  let remoteTab = "server";
  //: which cache's settings are showing. A house may keep more than one, and
  //: all of them at once was one long page with no seam in it.
  let cacheOn = "";

  /* What this machine is doing that anybody would feel, and the switch that stops
     the parts of it that can wait. */
  let loadTimer = null;
  async function paneLoad(main) {
    clearTimeout(loadTimer);
    const box = block("What this computer is doing");
    const said = document.createElement("div");
    box.appendChild(said);
    main.appendChild(box);

    const game = block("Game mode");
    game.innerHTML +=
      "<div class='note'>For when the graphics card is wanted for something else. " +
      "Tick what it should hold back, then press the button. Encoding is left alone " +
      "unless it is ticked: stopping it turns somebody's film off, which is a heavier " +
      "thing than making them wait for a subtitle.</div>";
    const gameTicks = document.createElement("div");
    gameTicks.className = "addins";
    game.appendChild(gameTicks);
    const gameRow = document.createElement("div");
    gameRow.className = "addrow";
    game.appendChild(gameRow);
    main.appendChild(game);

    const draw = async () => {
      let now = {};
      try {
        now = await get("/performance");
      } catch (e) { return; }
      if (!said.isConnected) return;
      const card = now.card || {};
      const lines = [];
      if (card.name) {
        lines.push(card.name + ": " + card.busy + "% busy, " +
                   Math.round(card.used / 1024 * 10) / 10 + " of " +
                   Math.round(card.total / 1024) + " GB, " + card.hot + "°C");
      }
      const w = now.writing || {};
      lines.push(w.on
        ? "Writing subtitles: " + (w.on.title || "") + " " +
          Math.round((w.on.at || 0) * 100) + "%" +
          (w.queued ? " (" + w.queued + " waiting)" : "")
        : "Writing subtitles: nothing" + (w.queued ? " (" + w.queued + " waiting)" : ""));
      lines.push((now.streams || []).length
        ? "Encoding for " + now.streams.length + " screen" +
          (now.streams.length === 1 ? "" : "s") + ": " +
          now.streams.map((s) => (s.height ? s.height + "p" : "original") +
                                 (s.burn ? ", subtitles burned in" : "")).join("; ")
        : "Encoding: nothing");
      lines.push("Playing now: " + (now.watching || 0));
      const scan = now.scan || {};
      lines.push(scan.running
        ? "Reading the library: " + (scan.phase || "") + " " + (scan.done || 0) +
          " of " + (scan.total || 0)
        : "Reading the library: idle");
      const copy = now.copying || {};
      if (copy.on) {
        lines.push(copy.file
          ? "Copying " + copy.file + " " + Math.round((copy.at || 0) * 100) + "%"
          : "Following another server");
      }
      said.innerHTML = lines.map((l) => "<div class='note'>" + esc(l) + "</div>").join("");

      // what it holds back, each its own answer
      const HOLDS = [
        ["subs", "Writing subtitles",
         "the card's work, and the one worth stopping first"],
        ["scans", "Reading the library", "new films can be noticed later"],
        ["copies", "Copying for another server", "the disk and the line"],
        ["transcode", "Encoding for a screen",
         "a film that needs encoding will not play while this is on"],
      ];
      const game = now.game || {};
      gameTicks.innerHTML = "";
      HOLDS.forEach(([id, name, why]) => {
        const row = document.createElement("div");
        row.className = "addin";
        const tick = document.createElement("button");
        tick.className = "btn ghost kind addintick" + (game[id] ? " on" : "");
        tick.textContent = game[id] ? "✓" : "";
        tick.onclick = async () => {
          tick.disabled = true;
          const does = {}; does[id] = !game[id];
          await post("/performance", { does: does });
          draw();
        };
        const words = document.createElement("div");
        words.className = "addinwords";
        words.innerHTML = "<b>" + esc(name) + "</b><span class='note'>" +
          esc(why) + "</span>";
        row.appendChild(tick);
        row.appendChild(words);
        gameTicks.appendChild(row);
      });

      gameRow.innerHTML = "";
      const b = document.createElement("button");
      b.className = "btn" + (game.on ? "" : " ghost");
      b.textContent = game.on ? "Game mode is on - turn it off"
                              : "Turn game mode on";
      b.onclick = async () => {
        b.disabled = true;
        await post("/performance", { game: !game.on });
        draw();
      };
      gameRow.appendChild(b);
      loadTimer = setTimeout(draw, 3000);
    };
    await draw();
  }

  async function panePeople(main) {
    const data = await get("/invites");
    const bar = document.createElement("div");
    bar.className = "sortbar collbar";
    [["users", "Users"], ["invite", "Invite"],
     ["friends", "Friends"]].forEach(([id, label]) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" + (whoTab === id ? " on" : "");
      b.textContent = label;
      b.onclick = () => { whoTab = id; render(); };
      bar.appendChild(b);
    });
    main.appendChild(bar);
    if (whoTab !== "invite" && whoTab !== "friends") return paneUsers(main, data);
    // libraries of other people's servers: the other direction of the same subject
    if (whoTab === "friends") return paneFriends(main);
    const box = block("Invite someone");
    box.innerHTML +=
      "<div class='note'>Each person gets their own link. It opens this library in " +
      "a browser or sets up the Android app, and can be revoked on its own. The link " +
      "is the only key, so send it only to people you mean to let in.<br>" +
      "Each also has a five-character code, for a television with no keyboard or a " +
      "line read out over the telephone: <code>&lt;address&gt;/i/CODE/open</code> " +
      "opens the library, <code>/i/CODE</code> fetches the app. The code stands " +
      "for the link and is worth as much.</div>";
    const list = document.createElement("div");
    // the owner first, then everybody else by name: a list of a dozen is read by
    // looking for somebody, not by remembering when their key was made
    const invited = (data.people || []).slice().sort((a, b) =>
      ((b.role === "owner") - (a.role === "owner")) ||
      String(a.name || "").localeCompare(String(b.name || "")));
    invited.forEach((w) => list.appendChild(personRow(w, render)));
    if (!(data.people || []).length) {
      const none = document.createElement("div");
      none.className = "note";
      none.textContent = "Nobody yet.";
      list.appendChild(none);
    }
    box.appendChild(list);

    const add = document.createElement("div");
    add.className = "addrow";
    add.innerHTML = '<input type="text" placeholder="Name">' +
      '<input type="email" class="mailto" placeholder="Email (optional)">' +
      '<select class="days"><option value="0">No expiry</option>' +
      '<option value="7">Expires in a week</option>' +
      '<option value="30">Expires in a month</option>' +
      '<option value="365">Expires in a year</option></select>' +
      '<button class="btn add">Create link</button>';
    const nameEl = add.querySelector("input");
    add.querySelector(".add").onclick = async () => {
      const name = nameEl.value.trim();
      if (!name) return toast("Give the person a name first");
      await post("/invites", { name: name,
                               email: add.querySelector(".mailto").value.trim(),
                               days: +add.querySelector(".days").value });
      toast("Link created for " + name);
      render();
    };
    nameEl.onkeydown = (e) => { if (e.key === "Enter") add.querySelector(".add").click(); };
    box.appendChild(add);

    /* Samsung televisions run web apps, so the whole client already works on one -
       what stands in the way is Samsung, which installs nothing that has not been
       signed by a person with a certificate. Written out rather than automated,
       because the last two steps happen on their machine and not on this one. */
    const tizen = document.createElement("div");
    tizen.className = "note reach tizen";
    tizen.innerHTML =
      "<b>On a Samsung TV</b><br>" +
      "The quickest way is no app at all: open the invitation link in the " +
      "television's own web browser. The client is a web page and works as it is." +
      "<br><br>For a proper icon on the home row, the app has to be built and signed " +
      "on your own machine - Samsung will not install one signed by anybody else:" +
      "<ol class='tzsteps'>" +
      "<li>Download <a href='/tizen.zip' download>the app source</a>. This server's " +
      "address is already written into it, so nothing has to be typed on the TV. " +
      "For a guest, use the download link on their row instead - it carries their " +
      "invitation, and the app opens straight into the library as them.</li>" +
      "<li>Install <b>Tizen Studio</b> with the TV extensions, from " +
      "developer.samsung.com. Free, and needs a Samsung account.</li>" +
      "<li>In Tizen Studio open <b>Certificate Manager</b> and make a Samsung " +
      "certificate. It asks for the TV's DUID, which the next step gives you.</li>" +
      "<li>On the television: <b>Apps</b>, then type <b>12345</b> on the remote. " +
      "Turn <b>Developer mode</b> on and enter the IP address of the machine you " +
      "are building on. Restart the television.</li>" +
      "<li>Connect and install:<br>" +
      "<code>sdb connect &lt;tv-ip&gt;</code><br>" +
      "<code>tizen build-web -- .</code><br>" +
      "<code>tizen package -t wgt -s &lt;your-certificate&gt; -- .buildResult</code><br>" +
      "<code>tizen install -n Palladium.wgt -t &lt;device&gt;</code></li>" +
      "<li>It appears on the home row. The certificate lasts a year, and the app " +
      "stops when it expires - reinstall with a fresh one.</li></ol>" +
      "<br><b>Playback on the set.</b> Where the television offers Samsung's AVPlay, " +
      "the client uses it: the panel decodes MKV and HEVC that the browser engine " +
      "refuses, so files play untouched instead of being re-encoded by the server. " +
      "The player's information line says which one has the film. If it says " +
      "\"direct play\" without naming AVPlay, the set did not offer it and " +
      "everything still works, through the server." +
      "<br><br>Published apps go through Samsung's Seller Office, which is weeks of " +
      "review; this is the sideloading route, for your own sets.";
    box.appendChild(tizen);

    /* The links point at the public address. Whether anything answers there is up to
       the router, and saying so here saves a confusing "it works for me" later. */
    const reach = document.createElement("div");
    reach.className = "note reach";
    reach.innerHTML = data.wan
      ? "Links use <b>" + data.wan + ":" + data.port + "</b>, this network as seen " +
        "from outside. For them to work away from home, forward port <b>" + data.port +
        "</b> to <b>" + data.lan + "</b> in the router. On this network the short " +
        "address <b>" + data.lan + ":" + data.port + "</b> works without that."
      : "Could not work out this network's public address, so links fall back to the " +
        "local one. They will work at home but not away from it.";
    box.appendChild(reach);

    /* Whether the outside can actually get in. The router is the part nobody can see
       from here, so this asks it, and says plainly what to do if it will not listen. */
    const net = document.createElement("div");
    net.className = "addrow";
    net.innerHTML = '<button class="btn test">Test from outside</button>' +
      '<button class="btn ghost open">Ask the router to open the port</button>' +
      '<span class="note netstate"></span>';
    const state = net.querySelector(".netstate");
    net.querySelector(".open").onclick = async () => {
      state.textContent = "Asking the router\u2026";
      const r = await post("/network/open", {});
      state.textContent = r.message || "";
      state.className = "note netstate" + (r.ok ? " good" : " bad");
    };
    /* The only test that means anything: machines elsewhere trying to get in. It takes
       a quarter of a minute, so it says so while it waits. */
    net.querySelector(".test").onclick = async () => {
      state.textContent = "Asking four machines abroad to connect\u2026";
      state.className = "note netstate";
      const r = await post("/network/check", {});
      const where = (r.nodes || [])
        .map((n) => n.where + " " + (n.ok ? n.detail : "\u2717")).join("  \u00b7  ");
      state.innerHTML = "";
      state.textContent = (r.message || "") + (where ? "   " + where : "");
      state.className = "note netstate" + (r.ok ? " good" : " bad");
    };
    box.appendChild(net);
    main.appendChild(box);
  }

  /* ---------------- what is going out right now ----------------
     One row per open stream. The rate is what the line is actually carrying over the
     last few seconds, not an average since the film started - a paused film has a
     large total and a rate of zero, and the second number is the useful one. */

  let liveTimer = null;

  async function drawLive(into, totals) {
    clearTimeout(liveTimer);
    if (!document.body.contains(into)) return;     // left the screen
    let data = { live: [] };
    // Both machines, folded into one row per viewing. A film read off two machines is
    // served by both and reported to one, so asking only the machine this page is
    // pointed at showed a row with no position - or nothing at all, on the machine
    // that was only carrying lumps, while somebody was plainly watching.
    let copy = null;
    let copyName = "the other machine";
    let hereName = "this server";
    try {
      const c = await get("/standby");
      copy = c && c.where ? c.where.replace(/\/$/, "") : null;
      if (c && c.name) copyName = c.name;
      if (c && c.mine && c.mine.name) hereName = c.mine.name;
    } catch (e) { copy = null; }
    const asked = [get("/watching").catch(() => ({ live: [] }))];
    if (copy) {
      asked.push(fetch(copy + "/watching" + (CFG && CFG.key ? "?t=" + CFG.key : ""))
                   .then((r) => r.json()).catch(() => ({ live: [] })));
    }
    // which machine each answer came from, in the order they were asked
    const whose = [hereName, copyName];
    try {
      const answers = await Promise.all(asked);
      // what each machine is measuring, if anything: a file read for its loudness is
      // work with no row of its own, and Now playing is where work is shown
      const measuring = answers.map((one, which) =>
        one && one.measuring ? Object.assign({}, one.measuring,
                                             { on: whose[which] || "" }) : null)
        .filter(Boolean);
      const byViewing = new Map();
      answers.forEach((one, which) => {
        ((one && one.live) || []).forEach((w0) => {
          // the machine that answered, carried on the row: a film read off two of
          // them is two rows, and neither said which was which
          const w = Object.assign({}, w0, { from: whose[which] || "" });
          // On the film, not on the name: the row a stream raises is named for the
          // viewer and the row a player's report raises is named for the device, so
          // one person watching one thing arrived as "Olof" with no position and
          // "Streamer" with one, and neither row said what was happening.
          // The key alone, where there is one. It was the key joined to the episode
          // label, and only the machine a player reports to fills that in - so one
          // film read off both was two rows again, keyed "<key>|S02E04" here and
          // "<key>|" there. The title is the fallback for a row with no key at all.
          const name = w.key ? "k:" + w.key
            : "t:" + (w.title || "") + "|" + (w.episode || "");
          const spot = byViewing.get(name) || [];
          if (!spot.length) byViewing.set(name, spot);
          // one screen is one viewing: two addresses on one film are two people, and
          // a report with no address of its own belongs to whichever is open
          const had = spot.find((x) => !x.address || !w.address
                                       || x.address === w.address);
          if (!had) { spot.push(Object.assign({}, w)); return; }
          // both are carrying it, so both rates are real and they add up
          had.mbit = (had.mbit || 0) + (w.mbit || 0);
          had.mb = (had.mb || 0) + (w.mb || 0);
          // both machines, named in the order they were asked
          if (w.from && had.from && had.from.indexOf(w.from) < 0) {
            had.from += " + " + w.from;
          }
          // where the film has got to comes from the machine the player reports to
          if (!had.position && w.position) {
            had.position = w.position;
            had.duration = w.duration || had.duration;
            had.state = w.state || had.state;
            had.client = w.client || had.client;
          }
          // and the name from the row that is a stream rather than a waiting report,
          // because that one is named for the person and this is a list of people
          if (had.how === "waiting" && w.how && w.how !== "waiting") {
            had.who = w.who || had.who;
            had.how = w.how;
          } else if (w.how === "waiting" && had.how && had.how !== "waiting") {
            had.state = had.state || w.state;
          }
        });
      });
      // One film, one name. The machine that keeps copies knows an episode as
      // "Episode 32" until it has been told otherwise, so the same viewing read as
      // two different programmes depending on which machine was reporting it. The
      // fullest name any machine has is the name for all of them.
      const merged = [];
      byViewing.forEach((spot) => spot.forEach((w) => merged.push(w)));
      {
        const best = new Map();
        merged.forEach((w) => {
          const k = w.key || w.title;
          const had = best.get(k) || "";
          const mine = (w.title || "") + (w.episode ? " " + w.episode : "");
          if (mine.length > had.length) best.set(k, mine);
        });
        merged.forEach((w) => {
          const k = w.key || w.title;
          const full = best.get(k);
          if (!full) return;
          const mine = (w.title || "") + (w.episode ? " " + w.episode : "");
          if (full.length > mine.length) {
            const other = merged
              .find((x) => (x.key || x.title) === k &&
                           ((x.title || "") + (x.episode ? " " + x.episode : ""))
                               === full);
            if (other) { w.title = other.title; w.episode = other.episode; }
          }
        });
      }
      data = { live: merged, measuring: measuring };
    } catch (e) { /* server restarting */ }
    into.innerHTML = "";
    if (totals) {
      /* What the main server is using altogether. One viewer's rate says whether that
         viewer will stutter; the total says whether the line is full, which is the
         question when the fourth person complains. */
      const rate = data.live.reduce((n, w) => n + (w.mbit || 0), 0);
      const sent = data.live.reduce((n, w) => n + (w.mb || 0), 0);
      const cooking = data.live.filter(
        (w) => (w.how || "").indexOf("transcode") >= 0).length;
      const hours = data.live.reduce((n, w) => n + (w.seconds || 0), 0) / 3600;
      totals.innerHTML = "";
      [[data.live.length, data.live.length === 1 ? "viewer" : "viewers"],
       [rate.toFixed(1), "Mbit/s altogether"],
       [sent >= 1024 ? (sent / 1024).toFixed(1) + " GB" : Math.round(sent) + " MB",
        "sent since they started"],
       [cooking, cooking === 1 ? "being transcoded" : "being transcoded"],
       [hours.toFixed(1), hours === 1 ? "hour of streaming" : "hours of streaming"]]
        .forEach(([value, label]) => {
          const cell = document.createElement("div");
          cell.className = "total";
          cell.innerHTML = "<b></b><span></span>";
          cell.querySelector("b").textContent = String(value);
          cell.querySelector("span").textContent = label;
          totals.appendChild(cell);
        });
    }
    if (!data.live.length && !(data.measuring || []).length) {
      const none = document.createElement("div");
      none.className = "note";
      none.textContent = "Nobody is watching anything at the moment.";
      into.appendChild(none);
    }
    // A file being listened to for its loudness: work with no viewer behind it, which
    // is exactly why it is worth showing - a disk busy for half a minute at a time all
    // evening should say what it is doing.
    (data.measuring || []).forEach((m) => {
      const el = document.createElement("div");
      el.className = "person live";
      el.innerHTML = '<div class="pmeta"><b></b><span class="note"></span></div>' +
        '<div class="rate"><b></b><span>measuring</span></div>';
      el.querySelector("b").textContent = m.name || "a file";
      const secs = m.since ? Math.max(0, Math.round(Date.now() / 1000 - m.since)) : 0;
      el.querySelector(".pmeta .note").textContent = [
        "loudness, on " + (m.on || "this server"),
        secs ? secs + "s so far" : "",
        (m.done || 0) + " measured",
        m.left ? m.left + " to go" : ""].filter(Boolean).join(" · ");
      el.querySelector(".rate b").textContent = "LUFS";
      into.appendChild(el);
    });
    data.live.forEach((w) => {
      const el = document.createElement("div");
      el.className = "person live";
      el.innerHTML = '<div class="pmeta"><b></b><span class="note"></span></div>' +
        '<div class="rate"><b></b><span>Mbit/s</span></div>';
      // the title is the subject of the sentence, so it goes first - with the
      // episode after it, since the programme's name alone says little
      // the episode, only where the title does not already carry it: "A Series
      // S3E28 - The Episode - A Series S03E28" says it twice over
      const already = w.episode &&
        w.title.toLowerCase().indexOf(w.episode.toLowerCase().slice(0, 12)) >= 0;
      el.querySelector("b").textContent =
        w.title + (w.episode && !already ? "  –  " + w.episode : "");
      // where they are in the film, and whether it is running: "paused 43:12 / 1:40:43"
      const clock = (t) => {
        t = Math.max(0, Math.round(t || 0));
        const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s2 = t % 60;
        const two = (n) => String(n).padStart(2, "0");
        return (h ? h + ":" + two(m) : m) + ":" + two(s2);
      };
      const at = w.duration
        ? clock(w.position) + " / " + clock(w.duration)
        : (w.position ? clock(w.position) : "");
      // when they started, in the clock of whoever is reading
      const began = w.started
        ? new Date(w.started * 1000).toLocaleTimeString([],
            { hour: "2-digit", minute: "2-digit" })
        : "";
      // What is playing it, and which build of it: a fault reported from a
      // three-week-old app is a different conversation from one on today's, and the
      // answer used to mean opening the watch log to find out.
      // "Streamer · Google TV app": what it calls itself, and what sort of thing it is
      // A row relayed from a machine on an older build still files the device under
      // the viewer's id joined to it by a NUL, which renders as the id glued to the
      // device name - and that id is the token their player signs in with.
      const named = (x) => String(x || "").split(" ").pop();
      const on = [named(w.device), w.kind || w.client].filter(Boolean)
        .filter((v, i, all) => all.indexOf(v) === i).join(" · ");
      // "android 0.14.56 tv" is the app's own name for itself; the version is the
      // part worth reading here, since the kind of device is said beside it
      const build = (w.app || "").match(/\d+\.\d+\.\d+/);
      // put on rather than chosen: the shuffle dealt this one
      const dealt = w.casual ? "shuffle" : "";
      el.querySelector(".pmeta .note").textContent = [
        w.who,
        // which machine is sending it: with two of them answering for one library,
        // a row that does not say is a rate with nowhere to put it
        w.from ? "from " + w.from : "",
        [on, build ? "v" + build[0] : ""].filter(Boolean).join(" "),
        dealt,
        began ? "since " + began : "",
        // a file on its way to the machine that keeps copies is not being watched:
        // it has no player, no place in the film and no picture size worth naming
        w.state === "syncing" ? "" :
          w.state === "paused" ? "paused"
            : w.state === "buffering" ? "buffering" : "playing",
        w.state === "syncing" ? "" : at,
        // what that screen says it is holding, and whether it has run out. A number
        // under 20 is a picture about to stop, which is worth seeing from here rather
        // than hearing about afterwards.
        w.state === "syncing" || w.ahead === undefined ? ""
          : w.stalled ? "STALLED"
          : Math.round(w.ahead) + "s buffered",
        w.how,
        w.state === "syncing" ? "" : w.quality,
        Math.max(1, Math.round(w.seconds / 60)) + " min",
        Math.round(w.mb) + " MB sent",
        "peak " + ((w.peak || 0) * 8).toFixed(1) + " Mbit/s",
      ].filter(Boolean).join(" \u00b7 ");
      el.querySelector(".rate b").textContent =
        (w.mbit || w.mbps * 8).toFixed(1);
      into.appendChild(el);
    });
    // films on their way in from a torrent pack: who asked, how far, how fast, how long
    try {
      const book = await get("/torrents/log");
      const coming = (book.downloads || []).filter(
        (d) => d.state === "queued" || d.state === "downloading");
      if (coming.length) {
        const head = document.createElement("div");
        head.className = "note";
        head.style.margin = "18px 0 6px";
        head.textContent = "Downloading";
        into.appendChild(head);
        coming.forEach((d) => {
          const el = document.createElement("div");
          el.className = "person live";
          el.innerHTML = '<div class="pmeta"><b></b><span class="note"></span></div>' +
            '<div class="rate"><b></b><span>Mbit/s</span></div>';
          el.querySelector("b").textContent = downloadName(d, "a film");
          const eta = d.eta != null && d.eta >= 0
            ? (d.eta < 60 ? d.eta + " s left" : d.eta < 3600 ? Math.round(d.eta / 60) + " min left"
               : Math.floor(d.eta / 3600) + " h " + Math.round((d.eta % 3600) / 60) + " min left")
            : "";
          el.querySelector(".pmeta .note").textContent = [
            d.who ? "asked for by " + d.who : "",
            d.state === "queued" ? "queued" : Math.round((d.progress || 0) * 100) + "%",
            ((d.size || 0) / 1e9).toFixed(1) + " GB",
            eta].filter(Boolean).join(" \u00b7 ");
          el.querySelector(".rate b").textContent = (d.mbit || 0).toFixed(1);
          into.appendChild(el);
        });
      }
    } catch (e) { /* a server with no torrents, or not the owner's page */ }
    liveTimer = setTimeout(() => drawLive(into, totals), 2000);
  }

  /* ---------------- friends: whose libraries show up here ---------------- */

  function friendRow(f) {
    const el = document.createElement("div");
    el.className = "person friend";
    el.innerHTML =
      '<label class="onoff"><input type="checkbox"><span></span></label>' +
      '<div class="pmeta"><b></b><span class="note"></span></div>' +
      '<button class="btn ghost open">Open theirs</button>' +
      '<button class="btn ghost rm" title="Forget">&#10005;</button>';
    el.querySelector("b").textContent = f.name;
    el.querySelector(".pmeta .note").textContent =
      f.origin + (f.me ? " \u00b7 you are " + f.me : "");
    const box = el.querySelector("input");
    box.checked = f.on !== false;
    box.onchange = () => {
      saveFriends(friends().map((x) => x.id === f.id ? { ...x, on: box.checked } : x));
      toast(box.checked ? f.name + "'s library now appears in Films and TV"
                        : f.name + "'s library hidden");
    };
    el.querySelector(".open").onclick = () =>
      window.open(f.origin + "/s/" + f.token, "_blank");
    el.querySelector(".rm").onclick = () => {
      if (!confirm("Forget " + f.name + "? Their link is removed from this browser."))
        return;
      saveFriends(friends().filter((x) => x.id !== f.id));
      render();
    };
    return el;
  }

  async function paneFriends(main) {
    const box = block("Friends");
    box.innerHTML +=
      "<div class='note'>Paste the link a friend sent you. Their films and shows " +
      "then appear alongside yours in Films, TV and on the home page, marked with " +
      "their name, and play from their machine. Switch one off to hide it without " +
      "losing the invitation.</div>";
    const list = document.createElement("div");
    const all = friends();
    all.forEach((f) => list.appendChild(friendRow(f)));
    if (!all.length) {
      const none = document.createElement("div");
      none.className = "note";
      none.textContent = "No friends' libraries yet.";
      list.appendChild(none);
    }
    box.appendChild(list);

    const add = document.createElement("div");
    add.className = "addrow";
    add.innerHTML = '<input type="text" placeholder="http://xxx.xxx.xxx:8765/s/...">' +
      '<button class="btn add">Add</button>';
    const input = add.querySelector("input");
    add.querySelector(".add").onclick = async () => {
      const inv = parseInvite(input.value);
      if (!inv) return toast("That does not look like an invitation link");
      if (friends().some((f) => f.token === inv.token)) return toast("Already added");
      let name = inv.origin.replace(/^https?:\/\//, ""), me = "";
      try {
        // the server names itself, and tells us which guest we are on it
        const r = await fetch(inv.origin + "/config?t=" + encodeURIComponent(inv.token));
        if (!r.ok) throw new Error(r.status);
        const c = await r.json();
        name = c.serverName || name;
        me = c.name || "";
      } catch (e) {
        return toast("Cannot reach that server - ask for a fresh link");
      }
      saveFriends(friends().concat([{
        id: "f" + Math.random().toString(36).slice(2, 9),
        name: name, origin: inv.origin, token: inv.token, me: me, on: true,
      }]));
      toast(name + " added");
      render();
    };
    input.onkeydown = (e) => { if (e.key === "Enter") add.querySelector(".add").click(); };
    box.appendChild(add);
    main.appendChild(box);

    /* Who is watching has a page of its own now - it is the thing somebody opens
       when a film stutters, and this screen is about handing out invitations. */
  }

  /* ---------------- reports: what people have written in ---------------- */

  /* Anyone using the server can write in, and read what has been written - except
     what the owner has put out of sight. Somebody else's report saying the same thing
     saves them writing it again. */
  function writeBox() {
    const box = block("Tell me about it");
    box.innerHTML +=
      "<div class='note'>Something not working, or something you would like it to " +
      "do?</div>" +
      '<div class="addrow"><button class="btn ghost kind on" data-k="problem">' +
      "Something is broken</button>" +
      '<button class="btn ghost kind" data-k="request">A request</button></div>' +
      '<div class="addrow"><textarea id="rtext" rows="3" ' +
      'placeholder="Which film, and what happened?"></textarea></div>' +
      '<div class="addrow"><button class="btn send">Send</button></div>';
    let kind = "problem";
    box.querySelectorAll(".kind").forEach((b) => {
      b.onclick = () => {
        kind = b.dataset.k;
        box.querySelectorAll(".kind").forEach((x) => x.classList.toggle("on", x === b));
        box.querySelector("#rtext").placeholder = kind === "problem"
          ? "Which film, and what happened?" : "What should it do?";
      };
    });
    box.querySelector(".send").onclick = async () => {
      const text = box.querySelector("#rtext").value.trim();
      if (!text) return toast("Write something first");
      await post("/feedback", { kind: kind, text: text, app: "web" });
      toast("Sent - thank you");
      // the box lives on the reports page: redrawing with the settings renderer sent
      // people to Subtitles, which is the last place they meant to go
      viewReports();
    };
    return box;
  }

  let reportFilter = "open";
  // which of the three: what is new, what is broken, what somebody would like
  let reportTab = "new";

  /** "3 minutes ago", for a row that says when something last spoke. */
  function agoWords(seconds) {
    if (seconds < 60) return "just now";
    if (seconds < 3600) return Math.round(seconds / 60) + " min ago";
    return Math.round(seconds / 3600) + " h ago";
  }

  /**
   * Every build that is talking to this server.
   *
   * The app names itself in a header and the page does the same, so this is the one
   * place that says whether the television, the phone and the browser are all running
   * what the server is serving - which is the first question when one of them behaves
   * differently from the others.
   */
  function versionsBlock(said) {
    const box = block("Versions");
    const line = (what, version, note) => {
      const row = document.createElement("div");
      row.className = "addrow subrow";
      row.innerHTML = "<span class='sublabel'>" + esc(what) + "</span>" +
        "<span class='kindname'>" + esc(version || "unknown") + "</span>" +
        (note ? "<span class='dim' style='margin-left:10px'>" + esc(note) +
                "</span>" : "");
      box.appendChild(row);
    };
    line("Server", said.server || "", "this computer");
    line("This browser", PAGE_BUILD || "",
         PAGE_BUILD && said.server && PAGE_BUILD !== said.server
           ? "older than the server - reload the page" : "the page you are looking at");
    // Everything else that has spoken today, browsers included: a second browser on
    // another machine is exactly the thing worth seeing here, and one still on an old
    // build is what explains why it behaves differently from this one.
    const seen = said.clients || [];
    if (!seen.length) {
      const none = document.createElement("div");
      none.className = "note";
      none.textContent = "Nothing else has asked for anything since this server started.";
      box.appendChild(none);
      return box;
    }
    seen.forEach((c) => {
      line(c.kind || "app", c.version || "unknown",
           [c.where, agoWords(c.ago || 0)].filter(Boolean).join("  ·  "));
    });
    return box;
  }

  /**
   * What this computer does about Palladium.
   *
   * The installer used to ask both of these before there was a library to serve,
   * which is the wrong moment and the wrong place: they are settings, they change,
   * and one of them needs an administrator that an installer running as an ordinary
   * user never had.
   */
  /* Put a line on the clipboard. The page is usually at 192.168.something, where
   * navigator.clipboard does not exist, so the old selection way is the fallback. */
  async function copyLine(field, said) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(field.value);
      } else {
        field.select();
        field.setSelectionRange(0, field.value.length);
        if (!document.execCommand("copy")) throw new Error("no");
      }
      toast(said || "Copied");
    } catch (e) {
      field.select();
      toast("Press Ctrl+C to copy");
    }
  }


  /* What this machine may say about itself to palladium.video.
   *
   * Its own card rather than a line in another one: it is the only setting on the
   * page that sends anything out of the main server, and a question like that should be
   * asked plainly rather than come across while reading about something else.
   */
  async function drawFaults(box) {
    box.innerHTML = "";
    let cfg = {};
    try {
      cfg = await get("/library/config");
    } catch (e) { /* a guest, or a server too old to answer: leave the card empty */ }
    // Off unless somebody turns it on, and never anything that names a person, a
    // film or a place: titles, paths, addresses, keys and the names of everybody in
    // this house are taken out before a word of it leaves.
    const tellRow = document.createElement("div");
    tellRow.className = "addrow subrow";
    tellRow.innerHTML = "<span class='sublabel'>Report faults</span>";
    [["off", "Nothing"], ["errors", "Faults only"],
     ["more", "Faults and this computer"]].forEach(([id, label]) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" +
        (((cfg && cfg.sendFaults) || "off") === id ? " on" : "");
      b.textContent = label;
      b.onclick = async () => {
        cfg = await post("/library/config", { sendFaults: id });
        toast(id === "off" ? "Nothing is sent"
              : id === "errors" ? "Faults are sent, and nothing else"
              : "Faults are sent, with what kind of computer this is");
        drawFaults(box);
      };
      tellRow.appendChild(b);
    });
    box.appendChild(tellRow);
    const tellNote = document.createElement("div");
    tellNote.className = "note";
    tellNote.textContent = "Titles, file paths, addresses, keys and the names of " +
      "everybody here are taken out before anything is sent. \"This computer\" " +
      "means how many cores, how much memory, which Windows and what the graphics " +
      "card calls itself - a description of a machine, not of a person. Whatever " +
      "this is set to, a single report can still be sent by hand from Reports.";
    box.appendChild(tellNote);
  }

  /* This server's own code, and following another one.
   *
   * Two questions that read as one when they share a box: what a machine needs to
   * follow this server, and what this server needs to follow another. They are drawn
   * into two cards, and both are filled from the one answer.
   */
  async function drawServers(keyBox, followBox, listBox, place) {
    let said = {};
    try {
      said = await get("/follow");
    } catch (e) { return; }
    const mine = said.mine || {};
    // the machine following this one, as it last announced itself: the button above
    // and the note below both read it, so it is settled before either
    const other = said.standby || {};
    const again = () => drawServers(keyBox, followBox, listBox, place);
    keyBox.innerHTML = "";

    // Every key made for a machine, with what that key allows - the way an
    // invitation for a person carries what that person may do. There was one key for
    // the main server and one ceiling for whoever held it, so lending a library to a second
    // machine re-lent the first one's allowance and taking it away took it from all
    // of them.
    const stopped = said.blocked || [];
    const seen = said.followers || [];
    const isOut = (w) => stopped.some((x) => String(x).replace(/\/+$/, "") ===
                                             String(w || "").replace(/\/+$/, ""));
    const keyList = document.createElement("div");

    /* One machine, known by its key, by the fact that it is announcing itself, or by
       both. Two lists meant a machine with a key that was also announcing itself was
       drawn twice, once with what it may do and once with what it was doing. */
    const machine = (k, f) => {
      const row = document.createElement("div");
      row.className = "addrow";
      row.style.alignItems = "center";
      const who = document.createElement("span");
      who.style.flex = "1 1 auto";
      who.style.whiteSpace = "pre-line";
      const out = f ? isOut(f.where) : false;
      const heard = !f || f.ago === undefined ? ""
        : f.ago < 90 ? "just now"
        : f.ago < 3600 ? Math.round(f.ago / 60) + " min ago"
        : Math.round(f.ago / 3600) + " h ago";
      const room = (f && f.room) || {};
      const bits = [];
      bits.push(k && k.cap ? k.cap + " GB at most" : "no limit from here");
      if (k) bits.push(k.mayCopy === false ? "may read, no cache" : "may keep a cache");
      if (f && f.kept) bits.push(f.kept + " files");
      if (room.gb) bits.push(Math.round(room.gb) + " GB used");
      if (room.free) bits.push(Math.round(room.free) + " GB free");
      if (f && f.bad) bits.push(f.bad + " would not come");
      const sent = Object.keys((f && f.sent) || {}).map(
        (n) => n === "tmdb_key" ? "catalogue" : "subtitles");
      if (f) {
        bits.push(sent.length ? "has this house's " + sent.join(" and ") + " key"
                              : "no keys of its own");
      }
      if (f && f.build) bits.push(f.build);
      if (!f && k) {
        bits.push(k.lastSeen
          ? "used " + new Date(k.lastSeen * 1000)
              .toLocaleString([], { dateStyle: "short", timeStyle: "short" })
          : "never used");
      }
      who.textContent = ((k && k.name) || (f && f.name) || "a machine") +
        (f && f.where ? "  \u00b7  " + f.where : "") +
        (f && f.outside ? "  \u00b7  outside " + f.outside : "") +
        (heard ? "  \u00b7  heard " + heard : "") +
        (f && f.managed ? "  \u00b7  managed from here" : "") +
        (f && f.revoked ? "  \u00b7  key revoked" : "") +
        (out ? "  \u00b7  stopped" : "") +
        (bits.length ? "\n" + bits.join("  \u00b7  ") : "");
      row.appendChild(who);
      // the row is how a cache is chosen; the controls on it are not
      if (f && f.where) {
        row.style.cursor = "pointer";
        if (cacheOn === f.where) {
          row.style.borderLeft = "3px solid var(--accent)";
          row.style.paddingLeft = "9px";
        }
        row.onclick = (e) => {
          if (e.target.closest("button, input, select, label")) return;
          cacheOn = f.where;
          render();
        };
      }

      if (k) {
        const gb = document.createElement("input");
        gb.type = "text";
        gb.inputMode = "numeric";
        gb.style.width = "7em";
        gb.value = k.cap ? String(k.cap) : "";
        gb.placeholder = "unlimited";
        gb.onchange = async () => {
          const n = Math.max(0, parseFloat(gb.value.replace(",", ".")) || 0);
          await post("/follow/key", { set: k.token, cap: n });
          toast(n ? (k.name || "That machine") + " may use " + n + " GB"
                  : "No limit for " + (k.name || "that machine"));
          again();
        };
        row.appendChild(gb);

        const may = document.createElement("button");
        may.className = "btn ghost kind" + (k.mayCopy === false ? "" : " on");
        may.textContent = k.mayCopy === false ? "Read only" : "May cache";
        may.onclick = async () => {
          await post("/follow/key", { set: k.token, mayCopy: k.mayCopy === false });
          again();
        };
        row.appendChild(may);
      }

      if (f || out) {
        const act = document.createElement("button");
        act.className = "btn ghost" + (out ? "" : " bad");
        act.textContent = out ? "Let it back in" : "Stop it";
        act.onclick = async () => {
          const called = (k && k.name) || (f && f.name) || "that machine";
          if (!out && !confirm("Stop " + called +
                "? It keeps its key but is refused until you let it back in.")) return;
          act.disabled = true;
          await post("/follow/stop", { where: (f && f.where) || "", allow: !!out });
          toast(out ? "Let back in" : "Stopped");
          again();
        };
        row.appendChild(act);
      }

      if (k) {
        const off = document.createElement("button");
        off.className = "btn ghost bad";
        off.textContent = "Revoke";
        off.onclick = async () => {
          if (!confirm("Revoke the key for " + (k.name || "that machine") +
                       "? It keeps what it has already copied and can take no more.")) {
            return;
          }
          await post("/follow/key", { remove: true, only: k.token });
          again();
        };
        row.appendChild(off);
      }
      if (place) place(row, f);
      else keyList.appendChild(row);
    };

    const paired = [];
    (said.keys || []).forEach((k) => {
      const f = seen.filter((one) => k.token && one.token === k.token)[0] || null;
      if (f) paired.push(f);
      machine(k, f);
    });
    // announcing itself with no key of ours: worth showing, and worth stopping
    seen.filter((f) => paired.indexOf(f) < 0).forEach((f) => machine(null, f));
    // stopped, and given up announcing: still needs a way back in
    stopped.filter((w) => !seen.some((f) => f.where === w))
           .forEach((w) => machine(null, { where: w, name: "" }));
    // the machines themselves belong to the list card, not to the general one
    const into = listBox || keyBox;
    if (keyList.children.length) into.appendChild(keyList);
    const capNote = document.createElement("div");
    capNote.className = "note";
    capNote.textContent = "Gigabytes. The other machine has a limit of its own, and " +
      "the smaller of the two is what it keeps to - so this one can only ever make " +
      "it take less. Empty means it decides for itself.";
    into.appendChild(capNote);

    // Who is following this one, and what has gone to them, are both on Now
    // playing: they are what this server is doing, not how it is set up. What is
    // left here is the setting-up.

    if (followBox) drawFollow(followBox, { get: get, post: post }, { said: said });
  }

  /**
   * One machine copying another: its settings, what it is doing, and its tests.
   * Drawn for this machine, or on the main server for a cache that lets the main server set
   * how it copies (opts.remote), read and written through /follow/managed.
   *
   * Rows tagged local are set on the machine itself only, manage is the permission
   * for the main server, status is what it is doing. Every other row is a setting the
   * house takes over while it manages the machine.
   */
  async function drawFollow(followBox, api, opts) {
    opts = opts || {};
    const get = api.get;
    const post = api.post;
    let said = opts.said;
    if (!said) {
      try {
        said = await get("/follow");
      } catch (e) {
        said = {};
      }
    }
    const again = () => drawFollow(followBox, api,
                                   Object.assign({}, opts, { said: null }));
    const title = opts.remote ? (opts.name || "The cache") : "Follow another server";
    if (!said || !said.follow) {
      followBox.innerHTML = "<h3>" + esc(title) + "</h3>";
      const gone = document.createElement("div");
      gone.className = "note";
      gone.textContent = (said && said.error) || "No answer.";
      followBox.appendChild(gone);
      return;
    }
    const one = said.follow || {};
    const state = said.state || {};
    const put = async (what) => { await post("/follow", what); };
    const tagRow = (el, what) => { el.dataset.side = what; return el; };
    const sleepRow = (el, why) => {
      el.classList.add("asleep");
      if (why) el.title = why;
      el.querySelectorAll("input, button, select").forEach((c) => { c.disabled = true; });
    };
    followBox.innerHTML = "<h3>" + esc(title) + "</h3>";
    tagRow(followBox.firstChild, "status");
    if (opts.remote) {
      const how = document.createElement("div");
      how.className = "note";
      how.textContent = (opts.name || "That machine") + " is managed from here: what " +
        "it copies is set on this page. Its address, its key and that permission are " +
        "set on it.";
      followBox.appendChild(tagRow(how, "status"));

      // and whether that is true this minute: the permission lives on that machine and
      // can be turned off there, which reads here as settings that quietly do nothing
      const askRow = document.createElement("div");
      askRow.className = "addrow subrow";
      askRow.innerHTML = "<span class='sublabel'>Remotely managed</span>";
      const ask = document.createElement("button");
      ask.className = "btn ghost";
      ask.textContent = "Check";
      const heard = document.createElement("span");
      heard.className = "note";
      ask.onclick = async () => {
        ask.disabled = true;
        heard.textContent = "Asking " + (opts.name || "that machine") + "…";
        let back = {};
        try {
          back = await api.get("/follow");
        } catch (e) {
          back = { error: String(e && e.message ? e.message : e) };
        }
        ask.disabled = false;
        const ok = !!(back && back.follow && !back.error);
        heard.textContent = ok
          ? "Yes - it answered, and what is set here reaches it."
          : ((back && back.error) ||
             "No answer. Turn Managed from the main server on over there.");
        heard.className = "note" + (ok ? " good" : " bad");
      };
      askRow.appendChild(ask);
      askRow.appendChild(heard);
      followBox.appendChild(tagRow(askRow, "status"));
    }
    // where Test and Sync now go: at the top, where somebody looks first
    const topSlot = document.createElement("div");
    followBox.appendChild(topSlot);

    const row = (label, value, hint, save) => {
      const line = document.createElement("div");
      line.className = "addrow subrow";
      line.innerHTML = "<span class='sublabel'>" + label + "</span>";
      const input = document.createElement("input");
      input.type = "text";
      input.value = value || "";
      input.placeholder = hint || "";
      input.onchange = async () => { await save(input.value.trim()); again(); };
      line.appendChild(input);
      followBox.appendChild(line);
      return line;
    };

    // An ordinary invitation from the other server, the same one a person is given:
    // its link, or its address and five-character code. Mark that key Cache under
    // Users there and it is a key for a machine rather than a person.
    const paste = document.createElement("div");
    paste.className = "addrow subrow";
    paste.innerHTML = "<span class='sublabel'>Invitation</span>";
    const pbox = document.createElement("input");
    pbox.type = "text";
    pbox.placeholder = "the invite link, or 192.168.1.20:8765 AB12C";
    pbox.onchange = async () => {
      const line = pbox.value.trim();
      if (!line) return;
      const withHttp = (a) => (/^https?:\/\//.test(a) ? a : "http://" + a).replace(/\/$/, "");
      // the link a guest is sent: the token is the last part of it
      const link = line.match(/^(https?:\/\/[^\s/]+)\/s\/([A-Za-z0-9_-]{8,})$/);
      if (link) {
        await put({ master: link[1], key: link[2] });
      } else {
        // an address and the five characters the other server shows, in either order
        const bits = line.replace(/\/i\//g, " ").split(/[\s#]+/).filter(Boolean);
        const code = bits.filter((b) => /^[A-Za-z0-9]{5}$/.test(b))[0] || "";
        const where = bits.filter((b) => b !== code)[0] || "";
        if (!where) return toast("That is not an invitation");
        if (!code) {
          // an address and a whole key, as the setup line used to carry
          const key = bits.filter((b) => b !== where)[0] || "";
          if (!key) return toast("That has no code on it");
          await put({ master: withHttp(where), key: key });
        } else {
          let said = {};
          try {
            said = await (await fetch(withHttp(where) + "/i/" + code + "/setup")).json();
          } catch (e) { said = {}; }
          if (!said.token) return toast("That server did not know that code");
          await put({ master: withHttp(where), key: said.token });
        }
      }
      pbox.value = "";
      again();
    };
    paste.appendChild(pbox);
    followBox.appendChild(tagRow(paste, "local"));

    const onoff = document.createElement("div");
    onoff.className = "addrow subrow";
    onoff.innerHTML = "<span class='sublabel'>Follow</span>";
    const b = document.createElement("button");
    b.className = "btn ghost kind" + (one.on ? " on" : "");
    b.textContent = one.on ? "On" : "Off";
    b.onclick = async () => { await put({ on: !one.on }); again(); };
    onoff.appendChild(b);
    followBox.appendChild(tagRow(onoff, "local"));

    tagRow(row("Its address", one.master, "http://192.168.1.20:8765",
               (v) => put({ master: v })), "local");
    // and whether this machine replaces itself when that one moves on: a copy two
    // months behind the server it copies is a different program
    const grow = document.createElement("div");
    grow.className = "addrow subrow";
    // named, because this card can now be read on the machine it belongs to and
    // "update with it" does not say with what
    grow.innerHTML = "<span class='sublabel'>Update with " +
      esc((one.master || "").replace(/^https?:\/\//, "") || "the server it follows") +
      "</span>";
    const gb = document.createElement("button");
    gb.className = "btn ghost kind" + (one.updateWith ? " on" : "");
    gb.textContent = one.updateWith ? "On" : "Off";
    gb.onclick = async () => { await put({ updateWith: !one.updateWith }); again(); };
    grow.appendChild(gb);
    followBox.appendChild(grow);
    // Either kind of key works. One made by "Make a key" on that server keeps what
    // that whole house watches; an ordinary invitation - your own, on somebody else's
    // server - keeps what you watch there and nothing of anybody else's.
    tagRow(row("Its key", one.key, "a key from that server, or your own invitation",
               (v) => put({ key: v })), "local");
    row("Keep copies in", one.folder, "D:\\Palladium cache",
        (v) => put({ folder: v }));

    // What to keep, before how much of it. Each kind is off, kept only through the
    // hours this machine is the one awake, or kept always. It was all or nothing:
    // a machine wanting one person's watchlist took everybody's half-watched series
    // with it, and the only way to stop that was to stop copying.
    const kindsHead = document.createElement("div");
    kindsHead.className = "sublabel addinhead";
    kindsHead.textContent = "What to keep";
    followBox.appendChild(kindsHead);
    [["partway", "Part-way through",
      "A series or a film somebody stopped in the middle of."],
     ["watchlist", "Watchlists and favourites",
      "What people have marked to watch. Kept whether or not it has been seen."],
     ["lately", "Watched lately",
      "What has been on recently, for carrying on with."],
     ["shuffle", "The shuffle's next",
      "What a collection shuffle would draw next, and the one it is on."],
     ["screen", "On a screen now",
      "Whatever is playing this minute. Kept where it is, never fetched."]
    ].forEach(([kind, label, what]) => {
      const r = document.createElement("div");
      r.className = "addrow subrow";
      r.innerHTML = "<span class='sublabel' title='" + esc(what) + "'>" +
        esc(label) + "</span>";
      const now = String(((one.kinds || {})[kind]) || "always");
      [["off", "Off"], ["night", "Inside hours"], ["always", "Always"]]
        .forEach(([mode, text]) => {
          const b = document.createElement("button");
          b.className = "btn ghost kind" + (now === mode ? " on" : "");
          b.textContent = text;
          b.title = mode === "night"
            ? "Only through the hours below, while the main server is asleep"
            : what;
          b.onclick = async () => {
            const send = {};
            send[kind] = mode;
            await put({ kinds: send });
            again();
          };
          r.appendChild(b);
        });
      followBox.appendChild(r);
    });
    // and the way to make "inside hours" mean now: it belongs with the rows that
    // offer it rather than beside the clock further down, which is where somebody
    // reading those three words would look for it
    if (opts.house) followBox.appendChild(opts.house.tonight(opts.name || "the cache"));

    const much = document.createElement("div");
    much.className = "sublabel addinhead";
    much.textContent = "How much to keep";
    followBox.appendChild(much);
    // A series is copied forward until either of these is reached: half-hour comedies
    // want a count, a drama wants hours, and the first to run out is the honest answer.
    row("Episodes ahead", String(one.episodes), "6", (v) => put({ episodes: v }));
    row("Hours ahead, at most", String(one.hours), "4", (v) => put({ hours: v }));
    // the shuffle is a shelf rather than a series: this is how much of it to keep
    // for the people who asked for it
    // The shuffle is what somebody puts on without choosing; copying what it would
    // have chosen means guessing an evening in advance and spending the night on it.
    // Off unless somebody asks, and then it is hours like everything else.
    const casualRow = document.createElement("div");
    casualRow.className = "addrow subrow";
    casualRow.innerHTML = "<span class='sublabel'>Collection shuffle</span>";
    [[false, "Do not copy"], [true, "Keep ahead"]].forEach(([value, text]) => {
      const b = document.createElement("button");
      const on = Number(one.casualHours || 0) > 0;
      b.className = "btn ghost kind" + (on === value ? " on" : "");
      b.textContent = text;
      b.onclick = () => put({ casualHours: value ? 2 : 0 });
      casualRow.appendChild(b);
    });
    followBox.appendChild(casualRow);
    // drawn either way, asleep while the shuffle is not copied
    const casualHoursRow = row("Hours of shuffle", String(one.casualHours || 0), "2",
                               (v) => put({ casualHours: v }));
    if (!(Number(one.casualHours || 0) > 0)) {
      sleepRow(casualHoursRow, "The shuffle is not copied");
    }
    const capRow = row("Disk to use, GB", String(one.cap), "200", (v) => put({ cap: v }));
    // the key the main server handed over may allow less; the smaller of the two is used
    if (Number(said.houseCap) > 0) {
      capRow.title = (said.houseName || "The main server") + " allows " + said.houseCap +
        " GB on its key; the smaller of the two is used.";
    }

    const dark = document.createElement("div");
    dark.className = "sublabel addinhead";
    dark.textContent = "Hours to sync to cache";
    followBox.appendChild(dark);

    // How much of a programme somebody put on a list: enough to last the night, or
    // all of it. The first is what a night off needs; the second is what somebody
    // means when they put a whole series on a list.
    const wholeRow = document.createElement("div");
    wholeRow.className = "addrow subrow";
    wholeRow.innerHTML = "<span class='sublabel'>Watchlists</span>";
    [[false, "Enough for the night"], [true, "Every unwatched episode"]]
      .forEach(([on, label]) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" + (!!one.wholeList === on ? " on" : "");
        b.textContent = label;
        b.onclick = async () => { await put({ wholeList: on }); again(); };
        wholeRow.appendChild(b);
      });
    followBox.appendChild(wholeRow);

    // Whose evening this disk is for. A machine in one person's room that fills
    // with the rest of the main server's viewing is using their disk for somebody else.
    const forRow = document.createElement("div");
    forRow.className = "addrow subrow";
    forRow.innerHTML = "<span class='sublabel'>What it keeps</span>";
    [["user", "This user"], ["server", "The whole house"]].forEach(([id, label]) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" +
        ((one.cacheFor || "user") === id ? " on" : "");
      b.textContent = label;
      b.onclick = async () => { await put({ cacheFor: id }); again(); };
      forRow.appendChild(b);
    });
    followBox.appendChild(forRow);
    {
      const whoRow = document.createElement("div");
      whoRow.className = "addrow subrow";
      whoRow.innerHTML = "<span class='sublabel'>For</span>";
      const pick = document.createElement("select");
      const names = (state.house || []).slice();
      const now = one.cacheWho || state.forWhom || "";
      if (now && names.indexOf(now) < 0) names.unshift(now);
      if (!names.length) names.push(now || "the owner");
      names.forEach((n) => {
        const o = document.createElement("option");
        o.value = n;
        o.textContent = n;
        o.selected = n === now;
        pick.appendChild(o);
      });
      pick.onchange = async () => { await put({ cacheWho: pick.value }); again(); };
      whoRow.appendChild(pick);
      followBox.appendChild(whoRow);
      if ((one.cacheFor || "user") !== "user") sleepRow(whoRow, "The whole house is kept");
    }
    const forNote = document.createElement("div");
    forNote.className = "note";
    forNote.textContent = "A user cache holds what one person is watching and has " +
      "on a list. The whole house is for a machine standing in for the library " +
      "itself, and fills with everybody's evening.";
    followBox.appendChild(forNote);

    // Whether the cap may delete on its own, and the folder it would be deleting
    // in. Manual unless somebody says otherwise, and never switched on without
    // showing the folder and naming what the first clear would take.
    const asList = (rows, count) => rows.slice(0, 12).map(
      (f) => "  " + f.name + "  (" + (f.bytes / 1e9).toFixed(1) + " GB)").join(BR) +
      (count > 12 ? BR + "  and " + (count - 12) + " more" : "");
    const clearHead = document.createElement("div");
    clearHead.className = "sublabel addinhead";
    clearHead.textContent = "Clearing the cache";
    followBox.appendChild(clearHead);
    const room = await post("/follow/clear", {}).catch(() => null);
    const here = document.createElement("div");
    here.className = "note";
    here.style.wordBreak = "break-all";
    here.textContent = room && room.folder
      ? "Folder: " + room.folder + "  ·  " + (room.used || 0) +
        " GB in it, cap " + (room.cap || 0) + " GB"
      : "No folder set, so nothing is kept and nothing is deleted.";
    followBox.appendChild(here);
    const clearRow = document.createElement("div");
    clearRow.className = "addrow subrow";
    clearRow.innerHTML = "<span class='sublabel'>When it is over the cap</span>";
    [["manual", "I clear it"], ["auto", "Clear it for me"]].forEach(([id, label]) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" +
        ((one.clearBy || "manual") === id ? " on" : "");
      b.textContent = label;
      b.onclick = async () => {
        if (id === "auto" && (one.clearBy || "manual") !== "auto") {
          // what it would take, named, before it is ever allowed to take anything
          const would = await post("/follow/clear", {}).catch(() => null);
          if (!would || !would.sane) {
            return toast("That folder is not one this program will delete in");
          }
          if (!confirm(
            "Palladium will delete files in:" + BR + BR + "  " + would.folder +
            BR + BR + "to keep it under " + would.cap + " GB. Right now that is " +
            would.files + " files, " + would.gb + " GB:" + BR + BR +
            asList(would.rows || [], would.files) + BR + BR +
            "Only files this machine fetched are ever deleted, and never anything " +
            "on the list for tonight. Carry on?")) return;
        }
        await put({ clearBy: id });
        again();
      };
      clearRow.appendChild(b);
    });
    followBox.appendChild(clearRow);
    if (room && room.sane) {
      const now = document.createElement("div");
      now.className = "addrow subrow";
      now.innerHTML = "<span class='sublabel'>Over the cap now</span>";
      const what = document.createElement("span");
      what.className = "note";
      what.textContent = room.files
        ? room.files + " files, " + room.gb + " GB could go"
        : "Nothing to clear";
      now.appendChild(what);
      if (room.files) {
        const go = document.createElement("button");
        go.className = "btn ghost bad";
        go.textContent = "Clear now";
        go.onclick = async () => {
          if (!confirm("Delete " + room.files + " files, " + room.gb + " GB, from:" +
                       BR + BR + "  " + room.folder + BR + BR +
                       asList(room.rows || [], room.files) + BR + BR +
                       "Carry on?")) return;
          const said = await post("/follow/clear", { now: true });
          toast(said && said.cleared ? said.cleared + " GB cleared"
                                     : "Nothing was cleared");
          again();
        };
        now.appendChild(go);
      }
      followBox.appendChild(now);
      if (!room.ready) {
        const wait = document.createElement("div");
        wait.className = "note";
        wait.textContent = "Nothing has been asked for yet since this server " +
          "started, so what is wanted tonight is not known. Clearing waits for " +
          "one pass rather than guessing at it.";
        followBox.appendChild(wait);
      }
    }

    // What is in that folder that this machine did not put there. It cannot be
    // deleted by this program - only what it fetched can be - so this is a heads-up
    // and nothing else: a folder full of files it does not know is a folder that
    // probably belongs to something else.
    const strangers = await post("/follow/extras", {}).catch(() => null);
    if (strangers && (one.folder || "").trim()) {
      const shead = document.createElement("div");
      shead.className = "sublabel addinhead";
      shead.textContent = "Files in that folder this machine did not fetch";
      followBox.appendChild(shead);
      if (!strangers.sane) {
        const bad = document.createElement("div");
        bad.className = "note bad";
        bad.textContent = "That folder is not one this program will keep copies in " +
          "or delete anything in: it is a drive root, a profile, or a folder of " +
          "somebody's own. Give the cache a folder of its own.";
        followBox.appendChild(bad);
      } else if (!strangers.count) {
        const none = document.createElement("div");
        none.className = "note";
        none.textContent = "None. Everything in that folder came from the other " +
          "server.";
        followBox.appendChild(none);
      } else {
        // a folder with a lot in it that this machine did not put there is very
        // likely somebody's own folder, pointed at by a setting typed by hand
        const loud = strangers.count >= 20 || strangers.gb >= 20;
        const note = document.createElement("div");
        note.className = "note" + (loud ? " bad" : "");
        note.textContent = (loud
          ? "Warning: " + strangers.count + " files, " + strangers.gb + " GB in " +
            "that folder did not come from the other server. That is a lot, and it " +
            "usually means the folder belongs to something else - give the cache a " +
            "folder of its own. "
          : strangers.count + " files, " + strangers.gb + " GB. ") +
          "Palladium will not delete any of them: it deletes only files it fetched " +
          "itself. Remove them yourself if they should not be there.";
        followBox.appendChild(note);
        const list = document.createElement("div");
        list.className = "note";
        list.style.cssText = "max-height:160px;overflow:auto;white-space:pre-line";
        list.textContent = strangers.extras.slice(0, 40).map(
          (f) => f.name + "  ·  " + (f.bytes / 1e9 >= 1
                                     ? (f.bytes / 1e9).toFixed(1) + " GB"
                                     : Math.round(f.bytes / 1e6) + " MB")).join(BR);
        followBox.appendChild(list);
        // The one way back for copies made before there was a record of them.
        // Asked for by hand, once, with the folder named - and it takes films and
        // subtitles only, so a folder of archives cannot be claimed at all.
        const films = strangers.extras.filter(
          (f) => /\.(mkv|mp4|m4v|avi|mov|ts|m2ts|webm|mpg|mpeg|wmv|flv|srt|ass|vtt|sub|idx)$/i
            .test(f.name));
        if (films.length) {
          const claimRow = document.createElement("div");
          claimRow.className = "addrow subrow";
          const claim = document.createElement("button");
          claim.className = "btn ghost";
          claim.textContent = "This folder is Palladium's cache";
          claim.onclick = async () => {
            const gb = films.reduce((n, f) => n + f.bytes, 0) / 1e9;
            const rest = strangers.count - films.length;
            if (!confirm(
              "Treat everything already in:" + BR + BR + "  " + strangers.folder +
              BR + BR + "as copies this machine fetched - " + films.length +
              " files, " + gb.toFixed(1) + " GB. The cap may then delete them like " +
              "anything else it copied." + BR + BR +
              (rest ? rest + " other files are not films or subtitles. They are " +
                      "left alone and can never be deleted from here." + BR + BR
                    : "") +
              "Do this only if this folder is Palladium's own cache and nothing " +
              "else. Carry on?")) return;
            const said = await post("/follow/claim", { confirm: true });
            toast(said && said.taken ? said.taken + " files are now the cache's"
                                     : (said && said.why) || "Nothing was taken");
            again();
          };
          claimRow.appendChild(claim);
          followBox.appendChild(claimRow);
        }
      }
    }

    // And what goes when there is no room left under the cap. Never anything on the
    // list; the choice is only about the rest.
    const dropRow = document.createElement("div");
    dropRow.className = "addrow subrow";
    dropRow.innerHTML = "<span class='sublabel'>When it is full</span>";
    [["oldest", "Oldest untouched first"], ["largest", "Largest first"]]
      .forEach(([id, label]) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" +
          ((one.deleteBy || "oldest") === id ? " on" : "");
        b.textContent = label;
        b.onclick = async () => { await put({ deleteBy: id }); again(); };
        dropRow.appendChild(b);
      });
    followBox.appendChild(dropRow);
    const dropNote = document.createElement("div");
    dropNote.className = "note";
    dropNote.textContent = "Nothing on the list is ever deleted - the choice is " +
      "only about what else is on the disk. Oldest untouched is the one nobody " +
      "will miss; largest empties the most room in the fewest deletions.";
    followBox.appendChild(dropNote);

    // "By day" was here: one switch under all of them that could say nothing at all
    // before night whatever the kinds said. Two answers to one question, and the
    // quieter one won without saying so. Each kind says when it may be taken now.

    // And whether the machine this one follows may change these settings from
    // there, so nobody has to walk to a computer in a cupboard.
    const mgmtRow = document.createElement("div");
    mgmtRow.className = "addrow subrow";
    mgmtRow.innerHTML = "<span class='sublabel'>Managed from the main server</span>";
    const mgmt = document.createElement("button");
    mgmt.className = "btn ghost kind" + (one.allowRemote ? " on" : "");
    mgmt.textContent = one.allowRemote ? "Allowed" : "Not allowed";
    mgmt.onclick = async () => {
      await put({ allowRemote: !one.allowRemote });
      again();
    };
    mgmtRow.appendChild(mgmt);
    followBox.appendChild(tagRow(mgmtRow, "manage"));
    const mgmtNote = document.createElement("div");
    mgmtNote.className = "note";
    mgmtNote.textContent = "The server this machine follows may then change these " +
      "settings without anybody walking to it. Only that machine, and only by its " +
      "address.";
    followBox.appendChild(tagRow(mgmtNote, "manage"));
    // Inside these hours it takes copies of everything anybody is in the middle of -
    // not only what is playing.
    row("Night from, hour", String(one.nightFrom), "22",
        (v) => put({ nightFrom: v }));
    row("Night until, hour", String(one.nightTo), "8", (v) => put({ nightTo: v }));
    // What the other server hands to viewers outside the main server. Empty is right when
    // both servers sit behind the one router.
    row("This machine from outside", one.outside, "http://203.0.113.7:8764",
        (v) => put({ outside: v }));

    const note = document.createElement("div");
    note.className = "note";
    const doing = state.copying
      ? "Copying " + state.copying + " - " + Math.round((state.at || 0) * 100) + "%"
      : state.stocking
      ? "Night: keeping everything anybody is half-way through. " +
        (state.kept || 0) + " GB kept"
      : "Following" + (state.whose ? " (" + state.whose + ")" : "") + ". " +
        (state.kept || 0) + " GB kept" +
        (state.why ? " - last trouble: " + state.why : "");
    note.textContent = state.on ? doing
      : "Off. With this on, whatever that server plays is copied here; a series is " +
        "copied ahead; and in the night hours, everything anybody is half-way " +
        "through.";
    followBox.appendChild(tagRow(note, "status"));

    // what the cache holds, against what it was allowed
    const kept = said.kept || {};
    const bar = document.createElement("div");
    bar.className = "keptbar";
    const fill = document.createElement("span");
    fill.style.width = Math.round((kept.share || 0) * 100) + "%";
    bar.appendChild(fill);
    followBox.appendChild(tagRow(bar, "status"));
    const held = document.createElement("div");
    held.className = "note";
    held.textContent = (kept.gb || 0) + " of " + (kept.cap || 0) + " GB kept (" +
      Math.round((kept.share || 0) * 100) + "%)  ·  " + (kept.files || 0) +
      " file" + ((kept.files === 1) ? "" : "s") +
      (kept.free ? "  ·  " + kept.free + " GB free on that disk" : "");
    followBox.appendChild(tagRow(held, "status"));

    // and whether the other server can be reached with the address and key above
    const testRow = document.createElement("div");
    testRow.className = "addrow";
    const test = document.createElement("button");
    // green while the rounds are going through: following, lately, with no trouble
    const going = state.on && state.last &&
      (Date.now() / 1000 - state.last) < 300 && !state.why;
    test.className = "btn ghost" + (going ? " good" : "");
    test.textContent = "Test";
    const result = document.createElement("div");
    result.className = "note";
    test.onclick = async () => {
      test.disabled = true;
      result.textContent = "Asking…";
      let back = {};
      try {
        back = await get("/follow/test");
      } catch (e) {
        back = { master: { ok: false, said: "This server did not answer." } };
      }
      test.disabled = false;
      const how = back.master || {};
      test.className = "btn ghost" + (how.ok ? " good" : " bad");
      result.textContent = how.said || "No address for the other server.";
    };
    testRow.appendChild(test);
    // and building the list again without waiting for the next round: what is worth
    // keeping changes the moment somebody finishes an episode
    const now = document.createElement("button");
    now.className = "btn ghost";
    now.textContent = "Sync now";
    now.onclick = async () => {
      now.disabled = true;
      result.textContent = "Asking…";
      let back = {};
      try {
        back = await get("/follow/now");
      } catch (e) {
        back = { said: "This server did not answer." };
      }
      now.disabled = false;
      result.textContent = back.said || "";
      setTimeout(again, 4000);
    };
    testRow.appendChild(now);

    topSlot.appendChild(tagRow(testRow, "status"));
    topSlot.appendChild(tagRow(result, "status"));

    // Until this machine follows something its copying settings are about a copy
    // nobody keeps: asleep, with what it takes to start following left live. While
    // the main server manages it, what the main server sets is asleep here with where it is set;
    // on the main server, only what the main server may set is drawn.
    if (opts.house) {
      followBox.appendChild(opts.house.share(opts.name || "the cache"));
      followBox.appendChild(opts.house.keys(opts.name || "the cache"));
    }

    const kids = Array.from(followBox.children);
    if (opts.remote) {
      kids.filter((el) => el.dataset.side === "local" || el.dataset.side === "manage")
          .forEach((el) => el.remove());
    } else if (!one.on) {
      kids.filter((el) => !el.dataset.side || el.dataset.side === "manage")
          .forEach((el) => sleepRow(el, "Follow is off"));
    } else if (one.allowRemote && !said.fromHouse) {
      const house = said.houseName ||
        (one.master || "").replace(/^https?:\/\//, "") || "the server it follows";
      kids.filter((el) => !el.dataset.side).forEach((el) => sleepRow(el,
        house + " sets this, under Remote computer there. Turn Managed from the " +
        "house off to set it here."));
    }
  }

  /* What this machine is called, and the port it answers on. */
  async function drawNameAndPort(into) {
    let said = {};
    try {
      said = await get("/follow");
    } catch (e) { return; }
    const again = () => { into.innerHTML = ""; drawNameAndPort(into); };
    // What this machine is called, wherever it is named: the drawing, the server
    // list on a phone, the row a guest sees. The computer's own name unless
    // somebody would rather it were called something else.
    const nameRow = document.createElement("div");
    nameRow.className = "addrow subrow";
    nameRow.innerHTML = "<span class='sublabel'>Name</span>";
    const nameBox = document.createElement("input");
    nameBox.type = "text";
    // The computer's own name stands in the box, so it can be read and edited
    // rather than guessed at; emptying it puts that name back.
    nameBox.value = said.name || "";
    nameBox.placeholder = said.hostname || "this computer's own name";
    nameBox.onchange = async () => {
      await post("/library/config", { serverName: nameBox.value.trim() });
      toast(nameBox.value.trim() ? "Called " + nameBox.value.trim()
                                 : "Back to the computer's own name");
      again();
    };
    nameRow.appendChild(nameBox);
    into.appendChild(nameRow);
    const nameNote = document.createElement("div");
    nameNote.className = "note";
    nameNote.textContent = said.hostname
      ? "Empty means this computer's own name, which is " + said.hostname + "."
      : "Leave it empty to use the computer's own name.";
    into.appendChild(nameNote);

    const portrow = document.createElement("div");
    portrow.className = "addrow subrow";
    portrow.innerHTML = "<span class='sublabel'>Port</span>";
    const portbox = document.createElement("input");
    portbox.type = "text";
    portbox.value = String(said.portWanted || said.port || "");
    portbox.placeholder = "8765";
    portbox.onchange = async () => {
      const back = await post("/machine/port", { port: portbox.value.trim() });
      if (back && back.error) return toast(back.error);
      toast(back && back.restart
        ? "Port " + back.port + " - from the next time this server starts"
        : "Port " + back.port);
      again();
    };
    portrow.appendChild(portbox);
    into.appendChild(portrow);
    if (said.portWanted && said.port && said.portWanted !== said.port) {
      const wait = document.createElement("div");
      wait.className = "note";
      wait.textContent = "Answering on " + said.port +
        " until this server is started again.";
      into.appendChild(wait);
    }

  }

  /* What can be added to this server, and what it costs to have it. */
  async function drawAddins(into) {
    let said = {};
    try {
      said = await get("/machine/addons");
    } catch (e) { return; }
    into.innerHTML = "";                 // the card above it carries the name
    (said.addons || []).forEach((one) => {
      const row = document.createElement("div");
      row.className = "addin";
      const tick = document.createElement("button");
      tick.className = "btn ghost kind addintick" + (one.on ? " on" : "");
      tick.textContent = one.on ? "✓" : "";
      tick.title = one.on ? "On" : "Off";
      tick.onclick = async () => {
        tick.disabled = true;
        await post("/machine/addons", { id: one.id, on: !one.on });
        drawAddins(into);
      };
      const words = document.createElement("div");
      words.className = "addinwords";
      words.innerHTML = "<b>" + esc(one.name) + "</b><span class='note'>" +
        esc(one.what) + "</span>";
      const state = document.createElement("div");
      state.className = "addinstate";
      const act = document.createElement("button");
      act.className = "btn ghost";
      if (one.getting) {
        state.textContent = "fetching…";
        act.textContent = "fetching";
        act.disabled = true;
        setTimeout(() => drawAddins(into), 5000);
      } else if (one.here) {
        state.textContent = one.took + " GB on this computer";
        act.textContent = "Remove";
        act.onclick = async () => {
          if (!confirm("Remove " + one.name + "? It can be fetched again.")) return;
          act.disabled = true;
          await post("/machine/addons", { id: one.id, remove: true });
          drawAddins(into);
        };
      } else {
        state.textContent = "not here · about " + one.size;
        act.textContent = "Fetch";
        act.onclick = async () => {
          act.disabled = true;
          act.textContent = "fetching…";
          const back = await post("/machine/addons", { id: one.id, fetch: true });
          if (back && back.error) toast(back.error);
          drawAddins(into);
        };
      }
      row.appendChild(tick);
      row.appendChild(words);
      row.appendChild(state);
      row.appendChild(act);
      into.appendChild(row);
    });
    // ffmpeg, which is what converts anything a screen cannot play as it stands. It
    // is fetched in the first-run wizard, and a server that got past that screen
    // without it had no way to ask for it afterwards - which is a machine that can
    // only ever hand over files exactly as they are.
    let tools = {};
    try {
      tools = await get("/setup/state");
    } catch (e) { tools = {}; }
    // Always shown, fetched or not. A row that vanished the moment it worked left
    // nowhere to say where it came from, and no way to let go of it again.
    {
      const row = document.createElement("div");
      row.className = "addin";
      const words = document.createElement("div");
      words.className = "addinwords";
      words.innerHTML = "<b>ffmpeg</b><span class='note'>Converts what a screen " +
        "cannot play as it stands, and reads what is inside a file. Without it this " +
        "server can only hand over files exactly as they are. About 80 MB, fetched " +
        "from the people who build it." +
        (tools.ffmpegFrom ? "<br>Source: <code>" + esc(tools.ffmpegFrom) + "</code>"
                          : "") +
        (tools.ffmpegHave ? "<br>On this disk: <code>" + esc(tools.ffmpegHave) +
                            "</code>" : "") +
        "</span>";
      const state = document.createElement("div");
      state.className = "addinstate";
      const act = document.createElement("button");
      act.className = "btn ghost";
      const busy = (tools.fetching || {}).busy;
      const fetchIt = async (overwrite) => {
        const said = await post("/setup/ffmpeg", overwrite ? { overwrite: true } : {});
        if (said && said.ask) {
          if (!confirm("There is already a copy of ffmpeg on this disk:" + BR + BR +
                       "  " + said.have + BR + BR +
                       "Fetching again downloads about 80 MB from" + BR +
                       "  " + (said.where || "the people who build it") + BR +
                       "and writes over it. Carry on?")) {
            return drawAddins(into);
          }
          await post("/setup/ffmpeg", { overwrite: true });
        }
        setTimeout(() => drawAddins(into), 3000);
      };
      if (busy) {
        const f = tools.fetching || {};
        state.textContent = f.said || "fetching…";
        act.textContent = "fetching";
        act.disabled = true;
        setTimeout(() => drawAddins(into), 2000);
      } else if (tools.ffmpeg) {
        state.textContent = "in use";
        act.textContent = "Remove";
        act.onclick = async () => {
          if (!confirm("Stop using ffmpeg. The files stay on the disk and it can be " +
                       "taken up again without downloading anything. Until then " +
                       "this server hands files over exactly as they are. Carry " +
                       "on?")) return;
          await post("/setup/ffmpeg", { off: true });
          drawAddins(into);
        };
      } else if (tools.ffmpegHave) {
        state.textContent = "here, not in use";
        act.textContent = "Use it";
        act.onclick = async () => {
          await post("/setup/ffmpeg", { on: true });
          drawAddins(into);
        };
      } else {
        state.textContent = "not here";
        act.textContent = "Fetch it";
        act.onclick = async () => {
          act.disabled = true;
          act.textContent = "fetching…";
          await fetchIt(false);
        };
      }
      row.appendChild(document.createElement("div"));
      row.appendChild(words);
      row.appendChild(state);
      row.appendChild(act);
      into.appendChild(row);
    }

    // The app itself, for a server that has none beside it. An installed server
    // carries it; one built from source does not, because an APK is not source. It
    // can be fetched - but only because somebody here says so, which is why this is
    // a button and not something that happens when a guest opens the install page.
    let app = {};
    try {
      app = await get("/app/version");
    } catch (e) { app = {}; }
    {
      const row = document.createElement("div");
      row.className = "addin";
      const words = document.createElement("div");
      words.className = "addinwords";
      words.innerHTML = "<b>The Android app</b><span class='note'>The cache this " +
        "server hands to a phone or a television. An installed server carries it; " +
        "one built from source does not, because an APK is not source and does not " +
        "belong in a repository.<br><br>" +
        "<b>Fetch it</b> takes the published build from palladium.video - about 17 " +
        "MB, and nothing about this machine goes with the request. Or put " +
        "<code>palladium.apk</code> beside the program yourself, which is the answer " +
        "if you would rather this machine asked nobody for anything." +
        (app.from ? "<br>Source: <code>" + esc(app.from) + "</code>" : "") +
        (app.have && app.versionName ? "<br>On this disk: " + esc(app.versionName) +
                                       " (" + (app.sizeMb || 0) + " MB)" : "") +
        "</span>";
      const state = document.createElement("div");
      state.className = "addinstate";
      const act = document.createElement("button");
      act.className = "btn ghost";
      if (app.getting) {
        state.textContent = app.size
          ? "fetching — " + Math.round(100 * (app.part || 0)) + "% of " +
            Math.round(app.size / 1e6) + " MB"
          : "fetching…";
        act.textContent = "fetching";
        act.disabled = true;
        setTimeout(() => drawAddins(into), 2000);
      } else if (app.here) {
        state.textContent = "handed out";
        act.textContent = "Remove";
        act.onclick = async () => {
          if (!confirm("Stop handing the app out. The file stays on the disk and " +
                       "can be offered again without downloading anything. Until " +
                       "then a phone asking this server for the app is told there " +
                       "is none. Carry on?")) return;
          await post("/app/fetch", { off: true });
          drawAddins(into);
        };
      } else if (app.have) {
        state.textContent = "here, not handed out";
        act.textContent = "Hand it out";
        act.onclick = async () => {
          await post("/app/fetch", { on: true });
          drawAddins(into);
        };
      } else {
        state.textContent = "not here";
        act.textContent = "Fetch it";
        act.onclick = async () => {
          act.disabled = true;
          act.textContent = "fetching…";
          const said = await post("/app/fetch", {});
          if (said && said.ask) {
            if (!confirm("There is already a copy of the app on this disk" +
                         (said.version ? " (" + said.version + ")" : "") + "." + BR +
                         BR + "Fetching again downloads about 17 MB from" + BR +
                         "  " + (said.where || "palladium.video") + BR +
                         "and writes over it. Carry on?")) {
              return drawAddins(into);
            }
            await post("/app/fetch", { overwrite: true });
          }
          setTimeout(() => drawAddins(into), 2000);
        };
      }
      row.appendChild(document.createElement("div"));
      row.appendChild(words);
      row.appendChild(state);
      row.appendChild(act);
      into.appendChild(row);
    }

    // The scripts that drive the models. They are ours, so they come from our own
    // site - and on their own, so a mended script does not wait for a new server.
    const t = said.tools || {};
    const kit = document.createElement("div");
    kit.className = "addin";
    const kw = document.createElement("div");
    kw.className = "addinwords";
    kw.innerHTML = "<b>Subtitle tools</b><span class='note'>The scripts that run the " +
      "models, fetched from palladium.video. The models themselves come from " +
      "whoever published them.</span>";
    const ks = document.createElement("div");
    ks.className = "addinstate";
    const ka = document.createElement("button");
    ka.className = "btn ghost";
    let letGo = null;
    const newer = t.latest && t.version !== t.latest;
    if (t.getting) {
      ks.textContent = "fetching…";
      ka.textContent = "fetching";
      ka.disabled = true;
      setTimeout(() => drawAddins(into), 4000);
    } else {
      ks.textContent = t.version ? ("version " + t.version + (newer ?
        " · " + t.latest + " is out" : ""))
        : t.here ? "the cache that came with the server" : "not here";
      ka.textContent = t.version ? (newer ? "Update" : "Fetch again") : "Get them";
      ka.onclick = async () => {
        ka.disabled = true;
        ka.textContent = "fetching…";
        let back = await post("/machine/addons", { tools: "fetch" });
        if (back && back.ask) {
          if (!confirm("There is already a copy of the subtitle tools on this disk " +
                       "(" + back.have + ")." + BR + BR + "Fetching again downloads " +
                       "them from " + (back.where || "palladium.video") +
                       " and writes over that copy. Carry on?")) {
            return drawAddins(into);
          }
          back = await post("/machine/addons", { tools: "fetch", overwrite: true });
        }
        if (back && back.error) toast(back.error);
        setTimeout(() => drawAddins(into), 1500);
      };
      // and letting go of them, which is not deleting them: the scripts stay and
      // are taken up again without asking anybody for anything
      if (t.version || t.here) {
        const off = letGo = document.createElement("button");
        off.className = "btn ghost";
        off.textContent = said.toolsOff ? "Use them" : "Remove";
        off.onclick = async () => {
          if (!said.toolsOff &&
              !confirm("Stop using the subtitle tools. The scripts stay on the " +
                       "disk and can be taken up again without downloading " +
                       "anything. Carry on?")) return;
          await post("/machine/addons", { tools: said.toolsOff ? "on" : "off" });
          drawAddins(into);
        };
        if (said.toolsOff) ks.textContent = "here, not in use";
      }
    }
    kit.appendChild(document.createElement("div"));   // where a tick would be
    kit.appendChild(kw);
    kit.appendChild(ks);
    kit.appendChild(ka);
    if (letGo) kit.appendChild(letGo);
    into.appendChild(kit);
    if (t.why) {
      const bad = document.createElement("div");
      bad.className = "note";
      bad.textContent = "Last attempt: " + t.why;
      into.appendChild(bad);
    }

    if (said.can === false) {
      const why = document.createElement("div");
      why.className = "note";
      why.textContent = "No Python on this computer can run them. Install Python from " +
        "python.org, then: pip install faster-whisper transformers sentencepiece";
      into.appendChild(why);
    }
  }

  /* ---- where everything goes ----
   *
   * The same question is asked every evening in three different ways: is the other
   * machine up, is anybody watching, and can the main server be reached from outside. Three
   * lists answered it in words. A drawing answers it at a glance - a box is lit or it
   * is not, and a line carries moving dashes or it is still.
   *
   * Read from /machine, /follow and /watching together, redrawn every five seconds.
   */
  function bracket(x, y, w, h, colour) {
    // corner marks rather than a border: a rectangle round every box turns the
    // drawing into a table, which is the thing it is meant not to be
    const r = 9;
    const d = [
      "M" + x + " " + (y + r) + "V" + y + "H" + (x + r),
      "M" + (x + w - r) + " " + y + "H" + (x + w) + "V" + (y + r),
      "M" + (x + w) + " " + (y + h - r) + "V" + (y + h) + "H" + (x + w - r),
      "M" + (x + r) + " " + (y + h) + "H" + x + "V" + (y + h - r),
    ].join(" ");
    return "<path d='" + d + "' fill='none' stroke='" + colour +
      "' stroke-width='1.4' opacity='.9'/>";
  }

  function node(x, y, w, h, title, lines, live) {
    // The first line sits 38 below the top of the panel and each one after it 15
    // lower, so a box has to be at least that tall or its own words stand outside
    // it - which is what "the text is not in the boxes" was.
    h = Math.max(h, 30 + Math.max(1, lines.length) * 15);
    // And wide enough for the longest thing written in it: an address is as long as
    // it is, and a box sized by guesswork either clipped it or left a gap.
    const widest = Math.max(String(title).length * 8.4 + 40,
                            ...lines.map((l) => String(l == null ? "" : l).length
                                                * 7.05 + 26));
    w = Math.max(w, Math.ceil(widest));
    // A machine that is off is not a fault, and drawing it in the same red as one
    // is why half the boxes could not be read: dim for standing by, amber for
    // doing something, red only for something that ought to answer and does not.
    const colour = live === false ? "#a04a38" : live ? "#ffb000" : "#8a7a55";
    let out = "<g>";
    out += "<rect x='" + x + "' y='" + y + "' width='" + w + "' height='" + h +
      "' rx='2' fill='" + (live ? "rgba(255,176,0,.07)" : "rgba(255,255,255,.03)") +
      "' stroke='" + colour +
      "' stroke-opacity='.3' stroke-width='1'/>";
    out += bracket(x, y, w, h, colour);
    out += "<circle cx='" + (x + 13) + "' cy='" + (y + 15) + "' r='3.5' fill='" +
      colour + "'" + (live ? " class='wpulse'" : "") + "/>";
    const room = Math.max(4, Math.floor((w - 40) / 8.4));
    const named = String(title).length > room
      ? String(title).slice(0, room - 1) + "…" : String(title);
    out += "<text x='" + (x + 25) + "' y='" + (y + 19) + "' class='wtitle' fill='" +
      colour + "'><title>" + esc(title) + "</title>" + esc(named) + "</text>";
    // Cut to the box rather than run out of it. The lines are addresses and builds,
    // and one long enough to leave its panel used to write itself across whatever
    // was beside it - a drawing of the main server with the text loose over the top.
    const fits = Math.max(6, Math.floor((w - 26) / 7.05));
    lines.forEach((l, i) => {
      const one = String(l == null ? "" : l);
      const cut = one.length > fits ? one.slice(0, fits - 1) + "…" : one;
      out += "<text x='" + (x + 13) + "' y='" + (y + 38 + i * 15) +
        "' class='wline'><title>" + esc(one) + "</title>" + esc(cut) + "</text>";
    });
    out += "</g>";
    return out;
  }

  /* A line with dashes running along it while something is actually going that way. */
  function wire(from, to, live, label, back) {
    const mid = (from[0] + to[0]) / 2;
    const d = "M" + from[0] + " " + from[1] + "C" + mid + " " + from[1] + " " +
      mid + " " + to[1] + " " + to[0] + " " + to[1];
    let out = "<path d='" + d + "' fill='none' stroke='#6b5a3a' stroke-width='1' " +
      "opacity='.55'/>";
    if (live) {
      out += "<path d='" + d + "' fill='none' stroke='#ffb000' stroke-width='1.7' " +
        "stroke-dasharray='3 12' class='wflow" + (back ? " back" : "") + "'/>";
    }
    if (label) {
      out += "<text x='" + mid + "' y='" + ((from[1] + to[1]) / 2 - 8) +
        "' class='wlabel' text-anchor='middle'>" + esc(label) + "</text>";
    }
    return out;
  }

  const esc = (t) => String(t == null ? "" : t)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const shortly = (secs) => {
    const ago = Math.max(0, Math.floor(Date.now() / 1000) - (secs || 0));
    return ago < 90 ? "just now" : ago < 3600 ? Math.round(ago / 60) + " min"
      : ago < 86400 ? Math.round(ago / 3600) + " h" : Math.round(ago / 86400) + " d";
  };

  //: Which row each screen sits on, kept between drawings. The list was sorted
  //: freshest first, so every card moved whenever anything else started or stopped
  //: and the drawing could not be read while it was being watched. A screen keeps
  //: the row it was given until it goes quiet; a new one takes the lowest free row.
  const SEATS = new Map();

  /* A film coming in, as lines for a machine's box: what, how far, how fast, how long. */
  function downloadLines(rows) {
    const d = rows[0];
    if (!d) return [];
    const name = String(d.title || "a film").replace(/[<>&]/g, "");
    const short = name.length > 28 ? name.slice(0, 27) + "\u2026" : name;
    const eta = d.eta != null && d.eta >= 0
      ? (d.eta < 60 ? d.eta + " s" : d.eta < 3600 ? Math.round(d.eta / 60) + " min"
         : Math.floor(d.eta / 3600) + " h " + Math.round((d.eta % 3600) / 60) + " min")
      : "";
    return [
      "downloading " + short,
      d.state === "queued" ? "queued"
        : Math.round((d.progress || 0) * 100) + "% - " + Number(d.mbit || 0).toFixed(1) +
          " Mbit/s" + (eta ? " - " + eta + " left" : ""),
      rows.length > 1 ? (rows.length - 1) + " more waiting" : "",
    ];
  }

  async function drawWiring(into) {
    // One answer for the whole drawing. It used to be three, every five seconds,
    // which on a machine at the end of a slow link is three round trips for one
    // picture - and the settings page felt like treacle for want of one endpoint.
    let said = null;
    try {
      said = await get("/wiring");
    } catch (e) {
      said = null;                       // an older server: ask it the old way
    }
    const [machine, follow, watching] = said
      ? [Object.assign({}, said.machine || {}, { name: said.name }),
         { standby: said.standby, state: said.state, follows: said.follows },
         { live: (said.live || []).concat(
             Array((said.syncing || 0)).fill({ how: "syncing" })),
           syncingMbit: Number(said.syncingMbit) || 0 }]
      : await Promise.all([
          get("/machine").catch(() => ({})),
          get("/follow").catch(() => ({})),
          get("/watching").catch(() => ({})),
        ]);
    if (said) machine.clients = said.clients || [];
    const all = (watching && watching.live) || [];
    const live = all.filter((r) => r.how !== "syncing");
    const sync = all.filter((r) => r.how === "syncing");
    // The other machine in the pair. On the house server that is whatever follows
    // it; on the machine that keeps copies it is the main server - which had been drawn as
    // "night server, none, not answering", a box describing a cache it does not
    // have and will never have.
    // ...under the name the answer actually uses. One endpoint replaced three and
    // calls it "standby"; this went on reading "cache", found nothing either way,
    // and drew a live machine as "no second machine - not answering".
    let other = (follow && (follow.standby || follow.cache)) || {};
    const upstream = (follow && follow.follows) || {};
    const followingUp = !other.where && !!(upstream.lan || upstream.outside);
    if (followingUp) {
      other = { where: upstream.lan || upstream.outside,
                outside: upstream.outside || "",
                name: upstream.name || "", alive: true };
    }
    const state = (follow && follow.state) || {};
    const now = Math.floor(Date.now() / 1000);
    // a screen that has said something in the last two minutes is a screen that is on
    // The machine that keeps copies is not a screen - it has a box of its own on
    // the right - and a row of unnamed browsers says nothing. Whoever the server
    // knows is named; the rest carry the address they came from.
    const screens = ((machine && machine.clients) || [])
      .filter((c) => now - (c.when || 0) < 120)
      // A machine that copies from this one is not a screen: it has a box of its
      // own. The test was for "following" and the machine calls itself a "cache",
      // so it slipped through and was drawn twice - once as itself and once as
      // somebody watching. Matched on any of the three words it might use, and on
      // its address, which is the one thing it cannot describe itself out of.
      .filter((c) => !/follow/i.test(String(c.said || "") + " " +
                                    String(c.kind || "") + " " + String(c.name || "")))
      .filter((c) => {
        const at = String(c.where || "");
        return !at || [other.where, other.outside, standbyWhere, standbyOut]
          .filter(Boolean)
          .every((u) => String(u).replace(/^https?:\/\//, "").split(":")[0] !== at);
      })
      .sort((a, b) => (b.when || 0) - (a.when || 0))
      .slice(0, 4)
      .map((c) => Object.assign({}, c, {
        shown: c.name || (c.where === "127.0.0.1" ? "this computer" : c.where),
      }));
    // a screen keeps its row for as long as it is there; one that has gone quiet
    // gives its row up and the next new screen takes it
    {
      const here = new Set(screens.map((c) => String(c.where)));
      Array.from(SEATS.keys()).forEach((k) => {
        if (!here.has(k)) SEATS.delete(k);
      });
      screens.forEach((c) => {
        const key = String(c.where);
        if (SEATS.has(key)) return;
        const taken = new Set(SEATS.values());
        let row = 0;
        while (taken.has(row)) row += 1;
        SEATS.set(key, row);
      });
    }
    const mbit = live.reduce((n, r) => n + (r.mbit || 0), 0);
    const syncMbit = Number(watching && watching.syncingMbit) || 0;
    const door = !!other.outside;

    // The main server as it is actually wired. Everything hangs off the router - both
    // machines and every screen - so it sits in the middle with room around it, and
    // the way in from outside comes down into it. The line between the two servers
    // is the only one that is not the router's doing: it is one machine copying from
    // the other, and it is drawn because that is the thing worth watching.
    // Read left to right, the way the film travels: the way in from outside, the
    // router it arrives at, the machines the films are on, and the screens watching
    // them. It used to be laid out as the main server is wired - the router in the middle
    // with everything hanging off it - which is a true picture of the cables and
    // says nothing about where a film goes.
    const rows = Math.max(1, ...Array.from(SEATS.values()).map((n) => n + 1),
                          screens.length);
    const W = 900;
    const H = Math.max(320, 90 + rows * 72);
    const gate = (machine && machine.gateway) || "";
    const box = [14, 130, 170, 52];          // the router, on the left
    const gx = box[0] + box[2] / 2;
    const gy = box[1] + box[3] / 2;
    const heart = [230, 60, 232, 74];        // this machine
    const copyBox = [230, 196, 232, 74];     // and the one that keeps copies, below it
    const seatX = 560;                       // and the screens they feed
    let g = "";

    // The way in from outside, straight down into the router. Both machines are
    // forwarded and both ports are worth seeing: one of them is what a guest is
    // handed, and the other is what answers when this server is off.
    const mine = (machine && machine.outside) || "";
    const theirs = other.outside || "";
    const outs = [mine, theirs].filter((u, i, all) => u && all.indexOf(u) === i)
      .map((u) => u.replace(/^https?:\/\//, ""));
    // The box decides its own height from what is in it, so the line has to be
    // drawn from the same number - one worked out here and one worked out there is
    // how a line came to start inside a box and end short of the next.
    const outLines = outs.length ? outs : ["no way in"];
    const outY = 14;
    const outH = Math.max(30 + outLines.length * 15, 44);
    g += node(box[0], outY, box[2], outH, "WAN", outLines, !!(mine || theirs));
    g += wire([gx, outY + outH], [gx, box[1]], !!(mine || theirs), "");

    // A real link rather than a box with a handler hung on it. The drawing is made
    // again every few seconds and the handlers went with the old one, so pressing it
    // in the moment between two drawings did nothing at all.
    g += (gate
          ? "<a class='wclick' href='http://" + gate + "' target='_blank' " +
            "rel='noopener'>"
          : "<g>") +
         node(box[0], box[1], box[2], box[3], "ROUTER",
              [gate || "not found", gate ? "press to open" : ""], !!gate) +
         (gate ? "</a>" : "</g>");

    // both machines hang off it
    g += node(heart[0], heart[1], heart[2], heart[3],
              (machine && machine.name) || "THIS SERVER", [].concat([
      ((machine && machine.lan) || "").replace(/^https?:\/\//, "") ||
        ("port " + ((machine && machine.port) || "?")),
      "build " + ((machine && machine.server) || "?") +
        "   " + ((machine && machine.network) || "?"),
      // Watching on one line and copying on the next, and each only while it is
      // happening. They were added together on one line, where a machine sending a
      // film and a machine filling the other one read as the same thing.
      live.length ? live.length + " watching - " + mbit.toFixed(1) + " Mbit" : "",
      sync.length ? sync.length + " copying - " + syncMbit.toFixed(1) + " Mbit" : "",
      // and a film coming in from a torrent pack, with how many wait behind it
      ...downloadLines((said && said.downloads) || []),
      (!live.length && !sync.length && !((said && said.downloads) || []).length)
        ? "nothing going out" : "",
    ].filter(Boolean)), true);
    const copying = !!state.copying || sync.length > 0;
    // which screens are reading a film off the cache this minute, as the cache
    // itself reports - by address, so a line can be drawn to the right screen
    // what the cache is feeding, by screen: address and megabits, as it reports
    // them about itself. This machine cannot see any of it - a player reading part
    // of a film off the cache talks to the cache.
    // On the main server this arrives with the answer: it asks the machine that
    // keeps copies what it is feeding. The machine that keeps copies has no such
    // list about the one it follows - so it drew a screen taking a film off both of
    // them with a line from itself only, and the main server looked idle while it
    // carried half the picture. Asked here instead, the same way the viewer list is.
    let busyRows = Array.isArray(other.busy) ? other.busy : [];
    // on a machine that keeps copies the pair's other half is the one it follows,
    // and its answer carries what that machine is feeding
    if (!busyRows.length && followingUp && Array.isArray(upstream.busy)) {
      busyRows = upstream.busy;
    }
    // and if it did not arrive with the answer - an older server, or one whose
    // wiring call is failing - ask the other machine outright. The rows below this
    // drawing are made that way, and a drawing that disagrees with the list beside it
    // is worse than no drawing.
    if (!busyRows.length && other.where) {
      try {
        const at = other.where.replace(/\/$/, "") +
          "/watching" + (CFG && CFG.key ? "?t=" + CFG.key : "");
        const said2 = await (await fetch(at)).json();
        busyRows = (said2.live || []).filter((r) => r.how !== "syncing");
      } catch (e) { /* the other machine is off, or will not have us: no lines */ }
    }
    const busyFor = (where) => busyRows.find(
      (r) => String((r && r.address) || r) === String(where));
    const busy = busyRows.length;
    const busyMbit = busyRows.reduce((n, r) => n + (Number(r && r.mbit) || 0), 0);
    g += node(copyBox[0], copyBox[1], copyBox[2], copyBox[3],
              other.name || (other.where
                               ? other.where.replace(/^https?:\/\//, "")
                               : (followingUp ? "the main server" : "no second machine")), [
      other.where ? other.where.replace(/^https?:\/\//, "") : "none",
      (other.build ? "build " + other.build + "   " : "") +
        (other.alive ? "seen " + shortly(other.seen) : "not answering"),
      // "Standing by" was said of a machine carrying half of a film. A player
      // reading part of a film off the cache talks to the cache and says nothing here,
      // so this machine has to ask it - and now does.
      copying ? (followingUp ? "taking a copy from it" : "taking a copy")
              : busy ? (busy + " split" +
                        (busyMbit > 0 ? "   " + busyMbit.toFixed(1) + " Mbit" : ""))
              : (followingUp ? "the library is there" : "standing by"),
    ], other.where ? !!other.alive : 0);

    // Out of the router into each machine. Left to right, which is the way a film
    // travels on this drawing.
    g += wire([box[0] + box[2], gy], [heart[0], heart[1] + heart[3] / 2], true, "");
    g += wire([box[0] + box[2], gy], [copyBox[0], copyBox[1] + copyBox[3] / 2],
              !!other.alive, "");

    // one machine filling the other, which is the only line the router did not make
    // and the dashes run the way the film travels: out of the main server into the cache,
    // whichever of the two this drawing was made on
    // The dashes run the way the film travels. Filling the cache runs one way and
    // reading a film off it runs the other, and only the first was ever drawn - so a
    // machine handing out half a film was shown being fed by the machine it was
    // feeding.
    const fromCopy = busy > 0 && !copying;
    // between the two machines, one above the other: filling the cache runs down,
    // and a film being read off it runs back up
    const midX = heart[0] + heart[2] / 2;
    const atHere = [midX, heart[1] + heart[3]];
    const atCopy = [midX, copyBox[1]];
    // No word on the line itself. The line is already dashed and moving while a copy
    // is going, and the box at the end of it says "taking a copy" - so the word sat
    // outside both boxes saying a third time what the drawing had said twice.
    g += followingUp
      ? wire(atCopy, atHere, copying, "", true)
      : wire(atHere, atCopy, copying, "");

    // and the screens on the right, with a line from every machine feeding them.
    // Both machines can be carrying one film at once - the app fetches from each of
    // them - and only one line was ever drawn, so the second machine appeared to be
    // doing nothing while it carried half the picture.
    const fromHere = [heart[0] + heart[2], heart[1] + heart[3] / 2];
    const copyOut = [copyBox[0] + copyBox[2], copyBox[1] + copyBox[3] / 2];
    if (screens.length) {
      screens.forEach((c) => {
        const y = 20 + (SEATS.get(String(c.where)) || 0) * 72;
        const row = live.find((r) => r.address === c.where);
        // what a screen is taking, written in the screen's own box. It sat on the
        // line instead, where with two machines feeding one screen there are two
        // lines and only one of them could carry the figure.
        g += node(seatX, y, 200, 44, c.shown || c.kind || "screen",
                  [(c.kind || "") + (c.version ? "  " + c.version : ""),
                   (function () {
                     const mine = row ? (row.mbit || 0) : 0;
                     const alsoFrom = busyFor(c.where);
                     const theirs = alsoFrom ? (Number(alsoFrom.mbit) || 0) : 0;
                     if (!row && !theirs) return "nothing playing";
                     return (mine + theirs).toFixed(1) + " Mbit   " +
                            ((row && row.how) || "");
                   })()],
                  row ? true : 0);
        // a line from a machine only to the screen it is actually feeding. Every
        // screen used to get a line from every machine, which drew a copy that was
        // carrying one film as though it were carrying all of them.
        if (row) g += wire(fromHere, [seatX, y + 22], true, "");
        // The rate belongs in the screen's box, not on the line into it: figures
        // written along the wires crossed each other and the boxes both.
        if (busyFor(c.where)) g += wire(copyOut, [seatX, y + 22], true, "");
      });
    } else {
      g += node(seatX, 120, 200, 44, "no screens", ["nothing is on"], 0);
      g += wire(fromHere, [seatX, 142], false, "");
    }

    into.innerHTML =
      "<svg viewBox='0 0 " + W + " " + H + "' class='wiring' " +
      "preserveAspectRatio='xMidYMid meet'>" +
      "<rect width='" + W + "' height='" + H + "' fill='#06080b'/>" +
      g + "</svg>";
    // the one box in the drawing that is not this software: pressing it opens the
    // page it serves, which is where a port forward is set and where it is undone.
    // It is an anchor now, so the browser opens it whether or not this has run.
  }

  /* The other machine: the one this server follows, or the one that follows it.
   *
   * Both halves are drawn whether or not they are in use - what this server lends to
   * another machine, and what it copies from one - and what does not apply here is
   * asleep. Each setting is drawn in one place.
   */
  async function paneRemote(main) {
    let said = {};
    try {
      said = await get("/follow");
    } catch (e) {
      said = {};
    }
    // machines heard from in the last day; an address silent longer has gone
    const caches = (said.followers || [])
      .filter((f) => !f.revoked && (f.ago === undefined || f.ago < 86400));
    const heading = (text) => {
      const el = document.createElement("div");
      el.className = "note";
      el.style.cssText = "margin:0 0 8px;letter-spacing:.08em;text-transform:uppercase";
      el.textContent = text;
      main.appendChild(el);
    };

    const bar = document.createElement("div");
    bar.className = "sortbar collbar";
    [["server", "Cache"], ["cache", "Server"],
     ["copying", "Being copied"], ["held", "What is held"]].forEach(([id, label]) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" + (remoteTab === id ? " on" : "");
      b.textContent = label;
      b.onclick = () => { remoteTab = id; render(); };
      bar.appendChild(b);
    });
    main.appendChild(bar);

    /* ---- what the other machine is already holding, and whose it is ---- */
    if (remoteTab === "held") {
      const box = block("What is held");
      const note = document.createElement("div");
      note.className = "note";
      note.textContent = "Every film and episode the other machine already has, and " +
        "why it is kept. This is what would still play if this server went off.";
      box.appendChild(note);

      const pickRow = document.createElement("div");
      pickRow.className = "addrow subrow";
      pickRow.innerHTML = "<span class='sublabel'>Whose</span>";
      const pick = document.createElement("select");
      pick.className = "colldecade";
      pick.add(new Option("Everyone", ""));
      pickRow.appendChild(pick);
      box.appendChild(pickRow);

      const count = document.createElement("div");
      count.className = "note";
      box.appendChild(count);
      const list = document.createElement("div");
      list.className = "heldlist";
      list.textContent = "Reading…";
      box.appendChild(list);

      get("/follow/queue").then((said) => {
        const rows = (said.queue || []).filter((r) => r.here);
        if (!rows.length) { list.textContent = "Nothing held yet."; return; }
        const whoOf = (r) => String(r.who || "").trim() || "nobody in particular";
        const people = {};
        rows.forEach((r) => { people[whoOf(r)] = (people[whoOf(r)] || 0) + 1; });
        Object.keys(people).sort((a, b) => people[b] - people[a])
          .forEach((name) => pick.add(new Option(name + "  (" + people[name] + ")", name)));
        // Every title, not a sentence about them. Sorted by who keeps it and then by
        // why, so one person's whole shelf reads together; the filter narrows it to
        // one person when that is the question.
        const draw = () => {
          const only = pick.value;
          const mine = only ? rows.filter((r) => whoOf(r) === only) : rows;
          const gb = mine.reduce((n, r) => n + (Number(r.gb) || 0), 0);
          count.textContent = mine.length + " titles, " + gb.toFixed(1) + " GB" +
            (only ? " kept for " + only : " in all");
          list.textContent = "";
          const order = mine.slice().sort((a, b) =>
            whoOf(a).localeCompare(whoOf(b)) ||
            String(a.why || "").localeCompare(String(b.why || "")) ||
            String(a.title || "").localeCompare(String(b.title || "")));
          let lastHead = "";
          order.forEach((r) => {
            const head = whoOf(r) + "  ·  " + (r.why || "kept ahead");
            if (head !== lastHead) {
              lastHead = head;
              const h = document.createElement("div");
              h.className = "sublabel heldhead";
              h.textContent = head;
              list.appendChild(h);
            }
            const line = document.createElement("div");
            line.className = "heldone";
            line.innerHTML = "<span class='t'></span><span class='g'></span>";
            line.querySelector(".t").textContent = r.title || r.key || "";
            line.querySelector(".g").textContent =
              (Number(r.gb) || 0).toFixed(2) + " GB";
            list.appendChild(line);
          });
        };
        pick.onchange = draw;
        draw();
      }).catch(() => { list.textContent = "Could not read it."; });
      main.appendChild(box);      // block() builds it; it still has to go on the page
      return;
    }

    /* ---- what the cache is taking, and what is next ---- */
    if (remoteTab === "copying") {
      // Its own half. The queue is watched while something is copying and it sat at
      // the foot of a page of settings, so reading it meant scrolling past every
      // one of them and back again.
      const copying = document.createElement("div");
      main.appendChild(copying);
      const drawCopying = async () => {
        if (!document.body.contains(copying)) return;      // the tab was left
        const next = document.createElement("div");
        await nowCopying(next);
        if (!document.body.contains(copying)) return;
        if (!next.childNodes.length) {
          const none = document.createElement("div");
          none.className = "note";
          none.textContent = "Nothing is queued. The machine that keeps copies has " +
            "everything it is asked to hold, or no machine is following this one.";
          next.appendChild(none);
        }
        copying.replaceChildren(...next.childNodes);
        setTimeout(drawCopying, 5000);
      };
      drawCopying();
      return;
    }

    /* ---- this computer copies from another ---- */
    if (remoteTab === "cache") {
      // the other direction, and just as easy to open by mistake: this machine
      // keeping copies of somebody else's library rather than lending its own
      const why = document.createElement("div");
      why.className = "note";
      why.style.cssText = "margin:2px 0 14px";
      why.textContent =
        "The other way round: this computer keeping copies of another server's " +
        "library, so it can answer when that one is off. Ask whoever runs it for an " +
        "invitation - they make it under Users and set its role to Cache - then " +
        "paste the link, or its address and five-character code, below. What is " +
        "copied and how much of the disk it may use are set here; how much that " +
        "server lets this one take is set there.";
      main.appendChild(why);
      const followBox = block("");
      main.appendChild(followBox);
      drawFollow(followBox, { get: get, post: post }, {});
      return;
    }

    // What the half is for, before any of it: somebody meeting this page has a
    // second computer and no idea what to do with it.
    const what = document.createElement("div");
    what.className = "note";
    what.style.cssText = "margin:2px 0 14px";
    what.textContent =
      "A cache is another computer that keeps copies of this library and answers " +
      "when this one is off - what anybody is part-way through, their watchlist, " +
      "and the next few of a shuffle. Set one up in three steps: invite it under " +
      "Users as though it were a person, set that key's role to Cache, and give the " +
      "invitation to that machine - it asks for one under Remote computer, Server. " +
      "It appears here once it announces itself, with what it holds and what it is " +
      "set to copy.";
    main.appendChild(what);

    /* ---- another computer copies from this one ---- */
    // A box for each machine that keeps a copy. With one, it stands open; with
    // several, the others are a row apiece until one is pressed.
    // What this server does with its copy - how much of the reading it hands over,
    // starting the night early, the keys it sends - sits in that machine's own box,
    // beside its night hours. Built fresh on every draw: the box redraws itself after
    // each change. These talk to this server, not through the cache.
    const mine = await get("/library/config").catch(() => ({}));
    const subrow = (label) => {
      const r = document.createElement("div");
      r.className = "addrow subrow";
      r.innerHTML = "<span class='sublabel'>" + label + "</span>";
      return r;
    };
    const noteOf = (text) => {
      const n = document.createElement("div");
      n.className = "note";
      n.textContent = text;
      return n;
    };
    const house = {
      // going to bed early: the cache's night hours begin now and last as long as a night
      tonight: (name) => {
        const wrap = document.createElement("div");
        const r = subrow("Tonight");
        const early = document.createElement("button");
        early.className = "btn ghost";
        early.textContent = "Start the night now";
        const said = noteOf("");
        early.onclick = async () => {
          early.disabled = true;
          said.textContent = "Telling " + name + " to take tonight's copies\u2026";
          try {
            const back = await post("/follow/tonight", {});
            said.textContent = back && back.said ? back.said : "The night has started early.";
          } catch (e) {
            said.textContent = "Could not reach " + name + ".";
          }
          early.disabled = false;
        };
        r.appendChild(early);
        wrap.append(r, said);
        return wrap;
      },
      // One film read off both machines, for a browser. Off unless somebody sets a
      // share: only whoever knows both machines can say it is worth it.
      share: (name) => {
        const wrap = document.createElement("div");
        const r = subrow("Share reading");
        const pc = document.createElement("input");
        pc.type = "text";
        pc.inputMode = "numeric";
        pc.value = mine.shareWithCopy ? String(mine.shareWithCopy) : "";
        pc.placeholder = "0";
        pc.onchange = async () => {
          const n = Math.max(0, Math.min(90,
            parseInt(pc.value.replace(/[^0-9]/g, ""), 10) || 0));
          await post("/library/config", { shareWithCopy: n });
          mine.shareWithCopy = n;
          toast(n ? n + " parts in every hundred are asked of " + name
                  : "Everything is read from this machine");
        };
        r.appendChild(pc);
        wrap.append(r, noteOf(
          "When a browser plays a film both machines hold, this server can fetch part " +
          "of it from " + name + " so a busy disk here does not stall the picture. The " +
          "number is the most it may take from there, out of every 100 parts; 0 reads " +
          "everything here. The app does not use this: it always reads from every " +
          "machine that has the film."));
        return wrap;
      },
      // the keys it needs of its own: subtitles it fetches itself, and the catalogue it
      // looks titles up in while this server is off. Only keys this server holds.
      keys: (name) => {
        const wrap = document.createElement("div");
        const r = subrow("Send keys");
        const choices = [];
        if (mine.opensubtitles_key && mine.tmdb_key) choices.push(["Both", {}]);
        if (mine.opensubtitles_key) {
          choices.push(["Subtitles only", { only: "opensubtitles_key" }]);
        }
        if (mine.tmdb_key) choices.push(["Catalogue only", { only: "tmdb_key" }]);
        choices.forEach(([label, body]) => {
          const b = document.createElement("button");
          b.className = "btn ghost";
          b.textContent = label;
          b.onclick = async () => {
            b.disabled = true;
            try {
              const said = await post("/follow/keys", body);
              toast(said && said.ok
                    ? "Sent: " + ((said.sent || []).join(", ") || "done")
                    : (said && said.why) || "Could not send");
            } catch (e) {
              toast("Could not send");
            }
            b.disabled = false;
          };
          r.appendChild(b);
        });
        wrap.append(r, noteOf("Without keys of its own, " + name + " cannot fetch " +
          "subtitles for what it copies, or look titles up while this server is off."));
        return wrap;
      },
    };

    const keyBox = document.createElement("div");
    const listBox = document.createElement("div");
    const openWhere = cacheOn ||
      ((caches[0] && caches[0].where) || "");
    const followBox = block("");
    drawServers(keyBox, followBox, listBox, (row, f) => {
      const card = block("");
      card.appendChild(row);
      main.appendChild(card);
      if (!f || !f.where || f.where !== openWhere) return;   // shut: the row, and no more
      const inside = document.createElement("div");
      card.appendChild(inside);
      // this server's own side of caching, beside the machine it is about
      const called = f.name || "the cache";
      inside.appendChild(house.share(called));
      inside.appendChild(house.keys(called));
      if (f.managed) {
        // its own box: what that machine is set to copy is drawn by clearing whatever
        // it is given, and it was given the box this server's own three rows had just
        // been put in - so Share reading, the keys and Tonight were built, added, and
        // wiped off the page before anybody saw them
        const theirs = document.createElement("div");
        inside.appendChild(theirs);
        drawFollow(theirs, remoteApi(f.where),
                   { remote: true, name: called, house: house });
        return;
      }
      const note = document.createElement("div");
      note.className = "note";
      note.textContent = "How it copies is set on " + called +
        ". Turn Managed from the main server on there to set it from here.";
      inside.appendChild(note);
    });

    // a cache that lets this server set how it copies has those settings here,
    // and asleep on that machine
  }

  /* A cache's own copying settings, read and written through this server. */
  function remoteApi(whereTo) {
    return {
      get: (path) => post("/follow/managed", { where: whereTo, path: path, method: "GET" }),
      post: (path, body) => post("/follow/managed",
                                 { where: whereTo, path: path, method: "POST",
                                   body: body || {} }),
    };
  }

  /* What this computer is set to do about Palladium, as several cards rather than
   * one: the machine itself, the code another server needs, the following of one,
   * the add-ins, and a word to the screens in the house. They were one box with five
   * subjects in it, which read as a list of unrelated switches. */
  async function machinePanel(part) {
    const all = document.createDocumentFragment();
    // "machine" is the card about this computer itself; everything else about what
    // this machine does belongs with the rest of the settings
    const chosen = (frag) => {
      if (!part) return frag;                 // all of it, which is the usual answer
      Array.prototype.slice.call(frag.children).forEach((el) => {
        const itself = el.dataset && el.dataset.card === "thisComputer";
        if ((part === "machine") !== itself) el.remove();
      });
      return frag;
    };
    // Everything here is drawn from one answer, so it is asked for once - but the
    // page is put together first. A card that is waiting looks like a card; a tab
    // that is waiting looks broken.
    const said = await get("/machine").catch(() => ({}));
    // what this machine answers about itself is not what it was told to be: the hour
    // it sleeps at and whether it may ask the site for anything live in the library's
    // own settings, and the box for the first of them has been empty all along
    const mine = await get("/library/config").catch(() => ({}));
    // What this machine encodes with. It sat under Quality, among settings about
    // how a film should look; it is a fact about this computer and belongs here.
    const enc = block("Encoding");
    {
      const r = document.createElement("div");
      r.className = "addrow subrow";
      r.innerHTML = "<span class='sublabel'>Decoder</span>";
      ENGINES.forEach(([value, text]) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" + (engine() === value ? " on" : "");
        b.textContent = text;
        b.onclick = () => { setPref("engine", value); render(); };
        r.appendChild(b);
      });
      enc.appendChild(r);
      const n = document.createElement("div");
      n.className = "note";
      n.textContent = "Only used for what a screen cannot play as it stands. The " +
        "card is quicker and is what this uses; the processor is slower and always " +
        "there, which is the answer for a card that is full, busy, or making a mess " +
        "of one particular file.";
      enc.appendChild(n);

      // where encodes are made: here, or on the connected computer while it answers
      const link = await get("/follow").catch(() => ({}));
      const other = (link.follow && link.follow.on && link.follow.master)
        ? (link.follow.master || "") : ((link.cache || {}).where || "");
      const onRow = document.createElement("div");
      onRow.className = "addrow subrow";
      onRow.innerHTML = "<span class='sublabel'>Encode on</span>";
      const encodeOn = (mine && mine.encodeOn) === "connected" ? "connected" : "here";
      [["here", "This computer"], ["connected", "Connected computer"]].forEach(([value, text]) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" + (encodeOn === value ? " on" : "");
        b.textContent = text;
        b.onclick = async () => {
          await post("/library/config", { encodeOn: value });
          render();
        };
        onRow.appendChild(b);
      });
      enc.appendChild(onRow);
      const onNote = document.createElement("div");
      onNote.className = "note";
      onNote.textContent = "Connected computer sends each encode to the computer this " +
        "one copies from, or the one copying from it" +
        (other ? " (" + other.replace(/^https?:\/\//, "") + ")" : "") +
        ". When it does not answer, this computer encodes. Subtitles are cut here.";
      enc.appendChild(onNote);
      if (!other && encodeOn === "here") {
        onRow.classList.add("asleep");
        onRow.title = "No computer is connected";
        onRow.querySelectorAll("button").forEach((c) => { c.disabled = true; });
      }
    }
    all.appendChild(enc);


    const box = block("This computer");
    box.dataset.card = "thisComputer";
    all.appendChild(box);
    const naming = document.createElement("div");
    box.appendChild(naming);
    drawNameAndPort(naming);
    if (!said.windows) {
      const only = document.createElement("div");
      only.className = "note";
      only.textContent = "These settings are for Windows.";
      box.appendChild(only);
      return chosen(all);
    }
    // `good` is the difference between a switch that is on and a switch that is
    // working: a firewall rule for a network Windows calls public is both.
    const line = (label, on, what, why, good) => {
      const row = document.createElement("div");
      row.className = "addrow subrow";
      row.innerHTML = "<span class='sublabel'>" + label + "</span>";
      const b = document.createElement("button");
      b.className = "btn ghost kind" +
        (on ? (good === false ? " bad" : " good") : "");
      b.textContent = on ? "On" : "Off";
      b.onclick = async () => {
        b.disabled = true;
        b.textContent = "…";
        const back = await post("/machine", what(!on));
        b.disabled = false;
        render();
        if (back && back.why) toast(back.why);
      };
      row.appendChild(b);
      box.appendChild(row);
      if (why) {
        const note = document.createElement("div");
        note.className = "note";
        note.style.margin = "2px 0 10px 82px";
        note.textContent = why;
        box.appendChild(note);
      }
    };

    line("Start at sign-in", said.startup, (v) => ({ startup: v }),
         "Puts Palladium in this account's Startup folder. Nothing is installed as a " +
         "service, and no administrator is asked.");
    // the rule covers private and domain networks, so a network Windows calls
    // public is shut whatever the rule says
    const homely = !said.network ||
      said.network === "Private" || said.network === "Domain";
    line("Reach it from the main server", said.firewall,
         (v) => ({ firewall: v }),
         "A firewall rule for port " + said.port + " on private networks, so a phone " +
         "or a television on your own network can reach this server. Windows asks " +
         "for an administrator when the rule is written.",
         homely);
    if (said.firewall && !homely) {
      const warn = document.createElement("div");
      warn.className = "note";
      warn.style.margin = "2px 0 10px 82px";
      warn.textContent = "Windows calls this network " + said.network +
        ", and the rule only covers private ones - so nothing on your network can " +
        "reach this server yet.";
      box.appendChild(warn);
      const fix = document.createElement("div");
      fix.className = "addrow";
      fix.style.margin = "0 0 10px 82px";
      const makeIt = document.createElement("button");
      makeIt.className = "btn ghost";
      makeIt.textContent = "Call this network private";
      makeIt.onclick = async () => {
        makeIt.disabled = true;
        makeIt.textContent = "…";
        const back = await post("/machine", { private: true });
        if (back && back.why) toast(back.why);
        render();
      };
      fix.appendChild(makeIt);
      box.appendChild(fix);
    }


    // Whether this machine may ask palladium.video for anything at all: the server
    // it updates itself with, and the app it hands to a phone. A machine that follows
    // another can take both from that one instead, over the network they share.
    const siteRow = document.createElement("div");
    siteRow.className = "addrow subrow";
    siteRow.innerHTML = "<span class='sublabel'>Fetch from palladium.video</span>";
    [[true, "Allowed"], [false, "Never"]].forEach(([value, text]) => {
      const b = document.createElement("button");
      const now = (mine && mine.fetchFromSite) !== false;
      b.className = "btn ghost kind" + (now === value ? " on" : "");
      b.textContent = text;
      b.onclick = async () => {
        await post("/library/config", { fetchFromSite: value });
        toast(value ? "May fetch from palladium.video"
                    : "Nothing is fetched from palladium.video");
        render();
      };
      siteRow.appendChild(b);
    });
    box.appendChild(siteRow);
    const siteNote = document.createElement("div");
    siteNote.className = "note";
    siteNote.textContent = "The new server and the Android app. With this off, a " +
      "machine that follows another takes both from that machine instead - which is " +
      "faster on one network, and keeps the two on the same build. A machine that " +
      "follows nothing and has this off updates by hand.";
    box.appendChild(siteNote);

    /* The way in from outside. The plain one is the default and needs nothing here:
       the port this server listens on, forwarded in the router. The other fetches
       Caddy, which takes 443, gets a certificate for a name you own, and hands what
       it receives to this server - so a link from away is https and a name rather
       than an address and a port. */
    /* Whether an owner's key still runs the place from outside the house. The key is
       the same one that watches, so away from home it is on phones and in browsers
       that are nowhere near the machine. */
    const admin = block("Running this server from away");
    all.appendChild(admin);
    (async () => {
      let said = {};
      try {
        said = await get("/settings");
      } catch (e) {
        return;                          // an older server, or not the owner
      }
      const draw = (now) => {
        admin.innerHTML = "<h3>Running this server from away</h3>";
        const row = document.createElement("div");
        row.className = "addrow subrow";
        row.innerHTML = "<span class='sublabel'>From away</span>";
        [[true, "The run of the place"], [false, "Watching only"]]
          .forEach(([value, text]) => {
            const b = document.createElement("button");
            b.className = "btn ghost kind" + (now === value ? " on" : "");
            b.textContent = text;
            b.onclick = async () => {
              const back = await post("/settings", { remoteAdmin: value });
              if (back && back.remoteAdmin !== undefined) {
                draw(!!back.remoteAdmin);
                toast(value ? "Settings can be changed from anywhere"
                            : "Settings can be changed at home only");
              } else {
                toast("Only from this network.");
              }
            };
            row.appendChild(b);
          });
        admin.appendChild(row);
        const n = document.createElement("div");
        n.className = "note";
        n.style.margin = "2px 0 6px 82px";
        n.textContent = now
          ? "Your key changes settings wherever you are. Anyone who gets hold of it " +
            "can do the same."
          : "Away from home your key plays films and nothing else; settings, keys " +
            "and the library are refused. On this network and at the machine itself " +
            "nothing changes - which is where this setting can be altered.";
        admin.appendChild(n);
      };
      draw(said.remoteAdmin !== false);
    })();

    const out = block("Reach from outside");
    all.appendChild(out);
    const drawProxy = async () => {
      let p = {};
      try {
        p = await get("/proxy");
      } catch (e) {
        return;                          // an older server, or not the owner
      }
      out.innerHTML = "<h3>Reach from outside</h3>";
      const note = document.createElement("div");
      note.className = "note";
      note.textContent = "Forwarding the port is the plain way and the one in use. " +
        "Caddy is the other: it answers on 443 for a name you own, with a certificate, " +
        "and passes what it gets to this server.";
      out.appendChild(note);

      const howRow = document.createElement("div");
      howRow.className = "addrow subrow";
      howRow.innerHTML = "<span class='sublabel'>How</span>";
      [["port", "Open the port"], ["caddy", "Caddy, with a name"]].forEach(([value, text]) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" + (p.how === value ? " on" : "");
        b.textContent = text;
        b.onclick = async () => {
          await post("/proxy/config", { how: value });
          drawProxy();
        };
        howRow.appendChild(b);
      });
      out.appendChild(howRow);

      if (p.how !== "caddy") {
        const plain = document.createElement("div");
        plain.className = "note";
        plain.style.margin = "2px 0 10px 82px";
        plain.textContent = "Port " + p.port + " forwarded to this machine in the " +
          "router. Nothing else runs.";
        out.appendChild(plain);
        return;
      }

      // Caddy itself: fetched on request, kept beside the library, no installer
      const state = document.createElement("div");
      state.className = "note";
      state.style.margin = "2px 0 10px 82px";
      state.textContent = !p.here
        ? "Caddy is not here yet - about 50 MB, fetched once."
        : p.running ? "Caddy is running." : "Caddy is here but not running.";
      out.appendChild(state);

      const fetching = (p.fetching || {}).busy;
      const actions = document.createElement("div");
      actions.className = "addrow";
      actions.style.margin = "0 0 10px 82px";
      const act = (text, what, ghost) => {
        const b = document.createElement("button");
        b.className = ghost ? "btn ghost" : "btn";
        b.textContent = text;
        b.onclick = async () => {
          b.disabled = true;
          const back = await post(what, {});
          if (back && back.why) toast(back.why);
          drawProxy();
        };
        actions.appendChild(b);
        return b;
      };
      if (!p.here) {
        const b = act(fetching ? (p.fetching.said || "Fetching…") : "Download Caddy",
                      "/proxy/fetch");
        b.disabled = !!fetching;
        if (fetching) setTimeout(drawProxy, 1500);
      } else if (p.running) {
        act("Stop", "/proxy/stop", true);
      } else {
        act("Start", "/proxy/run");
      }
      out.appendChild(actions);

      const field = (label, name, value, hint) => {
        const row = document.createElement("div");
        row.className = "addrow subrow";
        row.innerHTML = "<span class='sublabel'>" + label + "</span>";
        const i = document.createElement("input");
        i.type = "text";
        i.value = value || "";
        i.placeholder = hint || "";
        i.onchange = async () => {
          const body = {};
          body[name] = i.value.trim();
          await post("/proxy/config", body);
          drawProxy();
        };
        row.appendChild(i);
        out.appendChild(row);
      };
      field("Name", "name", p.name, "home.palladium.video");
      const nameNote = document.createElement("div");
      nameNote.className = "note";
      nameNote.style.margin = "2px 0 10px 82px";
      nameNote.textContent = "A certificate is issued for a name, never for an " +
        "address, so this name has to point at this house before it will work.";
      out.appendChild(nameNote);

      const proveRow = document.createElement("div");
      proveRow.className = "addrow subrow";
      proveRow.innerHTML = "<span class='sublabel'>Prove it</span>";
      [["open", "Ports 80 and 443 open"], ["dns", "A DNS token"]].forEach(([value, text]) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" + (p.prove === value ? " on" : "");
        b.textContent = text;
        b.onclick = async () => {
          await post("/proxy/config", { prove: value });
          drawProxy();
        };
        proveRow.appendChild(b);
      });
      out.appendChild(proveRow);
      if (p.prove === "dns") {
        field("DNS at", "dnsProvider", p.dnsProvider, "cloudflare");
        field("Token", "dnsToken", p.dnsSet ? "••••••" : "", "the account's API token");
        const why = document.createElement("div");
        why.className = "note";
        why.style.margin = "2px 0 10px 82px";
        why.textContent = "The certificate is proved through the name's own DNS, so " +
          "nothing needs opening in the router.";
        out.appendChild(why);
      }
      field("The other machine", "follower", p.follower, "miner.palladium.video");
      field("and where it answers", "followerAt", p.followerAt, "192.0.2.25:8764");

      const atLogin = document.createElement("div");
      atLogin.className = "addrow subrow";
      atLogin.innerHTML = "<span class='sublabel'>At sign-in</span>";
      const lb = document.createElement("button");
      lb.className = "btn ghost kind" + (p.startsAtLogin ? " good" : "");
      lb.textContent = p.startsAtLogin ? "On" : "Off";
      lb.onclick = async () => {
        lb.disabled = true;
        const back = await post("/proxy/login", { on: !p.startsAtLogin });
        if (back && back.why) toast(back.why);
        drawProxy();
      };
      atLogin.appendChild(lb);
      out.appendChild(atLogin);
    };
    drawProxy();

    // What this machine is called, where it answers, and the code another computer
    // needs: all three are about an arrangement with another machine, so all three
    // are on Remote computer.


    // The big pieces of machinery. Neither ships with Palladium - together they are
    // eight gigabytes - so each says what it is for, what it costs on disk, and
    // whether it is here at all.
    const faults = block("Report faults");
    const faultBox = document.createElement("div");
    faults.appendChild(faultBox);
    all.appendChild(faults);
    drawFaults(faultBox);

    const addins = block("Add-ins");
    const addinList = document.createElement("div");
    addinList.className = "addins";
    addins.appendChild(addinList);
    all.appendChild(addins);
    drawAddins(addinList);

    // A word to the screens in the house: it appears across the top of the app and
    // stands for ten minutes, or until somebody presses it away.
    const talk = block("A word to the main server");
    const say = document.createElement("div");
    say.className = "addrow subrow";
    say.innerHTML = "<span class='sublabel'>Say something</span>";
    const words = document.createElement("input");
    words.type = "text";
    words.placeholder = "Dinner in ten minutes";
    words.title = "Shown across the top of Palladium on every screen in the house";
    say.appendChild(words);
    // and who to: the main server by default, or one screen by its address, which is how a
    // test reaches the television without landing on everybody's phone
    const whom = document.createElement("select");
    [["", "Everyone in the house"], ["all", "Everyone, here and away"]]
      .forEach(([v, t]) => {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = t;
        whom.appendChild(o);
      });
    (said.clients || []).forEach((c) => {
      if (!c.where) return;
      const o = document.createElement("option");
      o.value = c.where;
      o.textContent = (c.kind || "a screen") + " · " + c.where;
      whom.appendChild(o);
    });
    say.appendChild(whom);

    const send = document.createElement("button");
    send.className = "btn ghost";
    send.textContent = "Send";
    send.onclick = async () => {
      const text = words.value.trim();
      if (!text) return;
      await post("/notice", { text: text, seconds: 600, to: whom.value });
      words.value = "";
      toast("Sent to the screens in the house");
    };
    say.appendChild(send);
    talk.appendChild(say);
    all.appendChild(talk);
    return chosen(all);
  }

  /* Which of two version numbers is the later one. Each place is a number, so
   * 0.17.100 comes after 0.17.99 - as a string it does not. */
  function newerOf(a, b) {
    const bits = (v) => String(v || "").split(".").map((n) => parseInt(n, 10) || 0);
    const x = bits(a), y = bits(b);
    for (let i = 0; i < Math.max(x.length, y.length); i++) {
      if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0) ? a : b;
    }
    return b;
  }

  /**
   * The build this server is, and the one the site is carrying.
   *
   * The app has looked after itself since the beginning; the server has not, and
   * during a beta the server is the half that changes daily. The key is the one the
   * installer was fetched with - updates come through the same gate, so a key that is
   * withdrawn stops both.
   */
  async function serverUpdate() {
    const box = block("This server");
    const note = document.createElement("div");
    note.className = "note";
    note.textContent = "Checking\u2026";
    box.appendChild(note);

    const foot = document.createElement("div");
    foot.className = "addrow subrow";
    foot.innerHTML = "<span class='sublabel'></span>";
    const again = document.createElement("button");
    again.className = "btn ghost";
    again.textContent = "Check again";
    foot.appendChild(again);
    const take = document.createElement("button");
    take.className = "btn";
    take.textContent = "Install it";
    take.style.display = "none";
    foot.appendChild(take);
    box.appendChild(foot);

    const draw = async (force) => {
      note.textContent = "Checking\u2026";
      take.style.display = "none";
      let said = {};
      try {
        said = await get("/update" + (force ? "?force=1" : ""));
      } catch (e) {
        note.textContent = "Could not ask the site.";
        return;
      }
      const here = said.have || "?";
      if (!said.latest) {
        note.textContent = "This server is " + here + ". " +
          (said.why || "The site did not answer.");
        return;
      }
      if (said.lan === false) {
        note.textContent = "This server is " + here +
          (said.latest && said.newer ? ", and " + said.latest + " is out." : ".") +
          " Updating is done from a screen in the same house as it.";
        return;
      }
      if (!said.newer) {
        // a build made here is often ahead of what the site carries, and calling
        // that "the current build" reads as though the two agreed
        const ahead = newerOf(here, said.latest) === here && here !== said.latest;
        note.textContent = ahead
          ? "This server is " + here + ", which is ahead of the site (" +
            said.latest + ", " + (said.when || "") + ")."
          : "This server is " + here + ", which is the current build (" +
            said.latest + ", " + (said.when || "") + ").";
        return;
      }
      note.textContent = "Version " + said.latest + " is out - this is " + here +
        // a build made on this machine needs no key and no download: say so, or
        // "Install it" looks like it is about to go to the site for something that
        // is already here
        (said.from === "this machine" ? ", and the installer is already on this "
                                        + "computer" : "") +
        (said.notes ? ". " + said.notes : "") +
        (said.why ? ". " + said.why : "");
      if (!said.why) take.style.display = "";
      take.onclick = async () => {
        take.disabled = true;
        take.textContent = "Fetching\u2026";
        let answer = {};
        try {
          answer = await post("/update/install", {});
        } catch (e) {
          answer = { ok: false, why: "the server stopped answering" };
        }
        if (!answer.ok) {
          take.disabled = false;
          take.textContent = "Install it";
          note.textContent = answer.why || "It could not be fetched.";
          return;
        }
        take.textContent = "Restarting";
        note.textContent = "Version " + (answer.version || said.latest) +
          " is installing and this server is restarting. Anything playing from it " +
          "stops for a moment; the page will lose the server and both come back on " +
          "their own.";
        // and it is worth watching for: the server is away about ten seconds, and
        // without this the page sat on "installing" until somebody left the tab and
        // came back
        let tries = 0;
        const backAgain = setInterval(async () => {
          tries += 1;
          if (tries > 40) { clearInterval(backAgain); return; }
          try {
            const now = await get("/update");
            clearInterval(backAgain);
            note.textContent = "This server is " + (now.have || "?") + ".";
            take.style.display = "none";
            take.disabled = false;
            take.textContent = "Install it";
          } catch (e) {
            /* still away */
          }
        }, 3000);
      };
    };
    again.onclick = () => draw(true);
    draw(false);
    return box;
  }

  /**
   * What has been added lately.
   *
   * On the reports page because that is where somebody goes to say something is
   * missing, and half of what is missing has just arrived.
   */
  //: minor versions expanded in What is new; null until one is toggled (newest open)
  let newOpen = null;

  async function whatsNew() {
    const box = block("What is new");
    let data = { changes: [] };
    try {
      data = await get("/changes");
    } catch (e) { /* the list is a courtesy */ }
    const list = data.changes || [];
    if (!list.length) {
      box.innerHTML += "<div class='note'>Nothing written down yet.</div>";
      return box;
    }
    box.innerHTML += "<div class='note'>The app is at " + esc(data.app || "?") +
      ". Newest first.</div>";
    // grouped by minor version (0.18, 0.17): one group of hundreds of releases was one long scroll
    const groups = [];
    list.forEach((entry) => {
      const minor = String(entry.version || "").split(".").slice(0, 2).join(".");
      const last = groups[groups.length - 1];
      if (last && last.minor === minor) last.entries.push(entry);
      else groups.push({ minor: minor, entries: [entry] });
    });
    let n = 0;
    groups.forEach((g, gi) => {
      const head = document.createElement("button");
      head.className = "btn ghost kind relgroup";
      const body = document.createElement("div");
      body.hidden = !(newOpen ? newOpen.has(g.minor) : gi === 0);
      const label = () => (body.hidden ? "▸ " : "▾ ") + g.minor + " · " +
        g.entries.length + (g.entries.length === 1 ? " release" : " releases");
      head.textContent = label();
      head.onclick = () => {
        if (!newOpen) newOpen = new Set(groups.length ? [groups[0].minor] : []);
        body.hidden = !body.hidden;
        if (body.hidden) newOpen.delete(g.minor); else newOpen.add(g.minor);
        head.textContent = label();
      };
      g.entries.forEach((entry) => {
        const el = document.createElement("div");
        el.className = "release" + (n++ ? " older" : "");
        el.innerHTML = "<div class='rhead'><b></b><span class='kind'></span>" +
          "<span class='note when'></span></div><ul></ul>";
        el.querySelector("b").textContent = entry.title || entry.version;
        el.querySelector(".kind").textContent = entry.version || "";
        el.querySelector(".when").textContent = entry.when || "";
        const ul = el.querySelector("ul");
        (entry.items || []).forEach((line) => {
          const li = document.createElement("li");
          li.textContent = line;
          ul.appendChild(li);
        });
        body.appendChild(el);
      });
      box.appendChild(head);
      box.appendChild(body);
    });
    return box;
  }

  async function paneReports(main) {
    // opening the page is reading it: the gear stops nagging. The marker is the
    // owner's - a guest has nothing to mark and would only be refused.
    if (!(CFG && CFG.guest)) {
      post("/feedback/seen", {}).then(() => {
        const badge = document.querySelector('#topbar [data-view="reports"]');
        if (badge) badge.classList.remove("hasnews");
      }).catch(() => {});
    }
    // Three questions that share a page and nothing else: what has been added,
    // what is broken, and what somebody would like. One tab each.
    // a heading of their own, so three buttons under the page's tab bar do not read
    // as a second row of page tabs
    const what = document.createElement("h2");
    what.innerHTML = '<span class="ct">Reports</span>';
    main.appendChild(what);
    const tabs = document.createElement("div");
    tabs.className = "addrow subtabs";
    main.appendChild(tabs);
    const data = await get("/feedback");
    const counts = {
      errors: (data.reports || []).filter(
        (r) => !r.done && (r.source || "person") === "auto").length,
      requests: (data.reports || []).filter(
        (r) => !r.done && (r.source || "person") !== "auto").length,
    };
    [["new", "What is new"],
     ["errors", "Errors" + (counts.errors ? " (" + counts.errors + ")" : "")],
     ["requests", "Requests" + (counts.requests ? " (" + counts.requests + ")" : "")]]
      .forEach(([id, label]) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" + (reportTab === id ? " on" : "");
        b.textContent = label;
        b.onclick = () => { reportTab = id; viewReports(); };
        tabs.appendChild(b);
      });

    if (reportTab === "new") {
      main.appendChild(await whatsNew());
      return;
    }
    // the box for writing one belongs with the requests: that is the tab somebody is
    // on when they think of something
    if (reportTab === "requests") main.appendChild(writeBox());
    const box = block(reportTab === "errors" ? "Errors" : "Requests");
    box.innerHTML +=
      "<div class='note'>" +
      (reportTab === "errors"
        ? "Faults the machinery noticed, and crashes sent by the app."
        : "What people have asked for, from the web page or the app.") +
      " Newest first." +
      (data.owner ? "" : " Only the owner can hide or delete anything here.") +
      "</div>";
    /* Within a tab there are only two states worth separating: what is still
       wanted, and what has been dealt with. Sorted includes what was cleared away,
       because the tick can be pressed by mistake and that is the row somebody wants
       back. */
    const filters = document.createElement("div");
    filters.className = "addrow";
    const filed = data.filed || [];
    const everything = (data.reports || []).concat(filed);
    const kind = (r) => ((r.source || "person") === "auto" ? "errors" : "requests");
    const passes = (r, id) =>
      kind(r) === reportTab &&
      (id === "done" ? (!!r.done || !!r.archived) : (!r.done && !r.archived));
    [["open", "Still open"], ["done", "Sorted"]]
      .forEach(([id, label]) => {
        const b = document.createElement("button");
        const n = everything.filter((r) => passes(r, id)).length;
        b.className = "btn ghost kind" + (reportFilter === id ? " on" : "");
        b.textContent = label + " (" + n + ")";
        b.onclick = () => { reportFilter = id; viewReports(); };
        filters.appendChild(b);
      });
    box.appendChild(filters);

    if (data.owner && reportTab === "errors") {
      /* A machine told to send faults that had no way out keeps them, and they are
         worth nothing sitting here. One press hands over everything not yet taken. */
      const waiting = everything.filter(
        (r) => (r.source === "auto" || r.kind === "error" || r.kind === "crash") &&
               !r.sentAway && !r.hidden);
      const going = (data.sending && data.sending.busy) ? data.sending : null;
      if (waiting.length || going) {
        const send = document.createElement("div");
        send.className = "addrow";
        const b = document.createElement("button");
        b.className = "btn ghost";
        b.textContent = going ? "Sending - " + going.left + " left"
                              : "Send the unsent (" + waiting.length + ")";
        b.disabled = !!going;
        b.onclick = async () => {
          if (!confirm("Send " + waiting.length + " fault" +
                       (waiting.length === 1 ? "" : "s") +
                       " to palladium.video?\n\nTitles, file paths, addresses, " +
                       "keys and the names of everybody here are taken out first. " +
                       "They go one a second, so this takes a while.")) return;
          const said = await post("/feedback/sendall", {});
          toast(said && said.already ? "Already going"
                : "Sending " + ((said && said.queued) || 0));
          setTimeout(viewReports, 1500);
        };
        send.appendChild(b);
        const note = document.createElement("span");
        note.className = "note";
        note.textContent = going
          ? going.sent + " sent, " + going.failed + " would not go" +
            (going.why ? " (" + going.why + ")" : "")
          : "Faults this machine has not handed over yet.";
        send.appendChild(note);
        box.appendChild(send);
      }
    }

    const list = document.createElement("div");
    everything
      .filter((r) => passes(r, reportFilter))
      .forEach((r) => {
      const el = document.createElement("div");
      el.className = "report " + (r.kind === "request" ? "req"
                                  : r.source === "auto" ? "auto" : "bug") +
        (r.hidden ? " hidden-row" : "") + (r.done ? " done" : "");
      el.innerHTML = '<div class="rhead">' +
        '<button class="tick" title="Sorted"></button>' +
        '<b></b><span class="kind"></span>' +
        '<span class="note when"></span><span class="spacer"></span>' +
        '<button class="btn ghost hide"></button>' +
        '<button class="btn ghost mine" title="Take back what you wrote">' +
        'Cancel</button>' +
        '<button class="btn ghost del" title="Take this off the board ' +
        '(kept in the archive)">&#10005;</button>' +
        '</div><div class="rtext"></div><div class="rfix"></div>';
      el.querySelector("b").textContent = r.who || "someone";
      el.querySelector(".kind").textContent =
        r.kind === "request" ? "request" : r.kind === "crash" ? "crash"
        : r.source === "auto" ? "fault" : "problem";
      el.querySelector(".when").textContent =
        new Date(r.when * 1000).toLocaleString() + (r.app ? " \u00b7 " + r.app : "") +
        (r.archived ? "  \u00b7  filed away" : "") +
        (r.withdrawn ? "  \u00b7  withdrawn" : "");
      el.querySelector(".rtext").textContent = r.text;
      /* Ticked off: known, understood, and done with. The owner decides that;
         everybody else can see it, which is the point of writing it down. */
      const tick = el.querySelector(".tick");
      tick.textContent = "\u2713";
      tick.classList.toggle("on", !!r.done || !!r.archived);
      if (r.archived) {
        el.classList.add("done");
        tick.title = "Filed away - press to put it back on the board";
        tick.onclick = async () => {
          await post("/feedback/restore", { id: r.id });
          toast("Back on the board");
          viewReports();
        };
      } else if (data.owner) {
        tick.title = r.done ? "Sorted - click to reopen" : "Mark this sorted";
        tick.onclick = async () => {
          await post("/feedback/fix", { id: r.id, done: !r.done });
          viewReports();
        };
      } else {
        tick.disabled = true;
        tick.title = r.done ? "Sorted" : "Not sorted yet";
      }

      /* Hand this one to palladium.video.
       *
       * There whether or not this machine sends anything of its own accord: reading
       * a report, deciding it is worth passing on and pressing send is a different
       * act from a machine reporting on itself, and it is asked for rather than
       * assumed. Everything identifying is taken out on the way, the same as always.
       *
       * On a request as well as on a fault. A request is the one report somebody
       * sat down and wrote, and it was the one kind that could not be passed on.
       */
      if (data.owner) {
        const asking = !(r.kind === "error" || r.kind === "crash");
        const away = document.createElement("button");
        away.className = "btn ghost sendaway" + (r.sentAway ? " on" : "");
        away.textContent = r.sentAway ? "Sent"
          : r.sendWhy ? "Would not send"
          : asking ? "Send this request" : "Send to Palladium";
        away.title = r.sentAway
          ? "Already sent"
          : r.sendWhy ? r.sendWhy + " - click to try again"
          : asking ? "Send this request to palladium.video"
          : "Send this one fault to palladium.video";
        away.onclick = async () => {
          if (r.sentAway) return;
          const yes = window.confirm(
            (asking ? "Send this request to palladium.video?"
                    : "Send this fault to palladium.video?") +
            String.fromCharCode(10, 10) +
            "The text goes with titles, file paths, addresses, keys and the names " +
            "of everybody here taken out, along with what build this is and what " +
            "kind of computer it happened on." +
            (asking ? String.fromCharCode(10, 10) +
                      "It is read as a request for the program, not as a message " +
                      "anybody will answer." : ""));
          if (!yes) return;
          away.disabled = true;
          const said = await post("/feedback/send", { id: r.id });
          away.disabled = false;
          toast(said && said.ok ? "Sent"
                : (said && said.why) || "Could not send it");
          viewReports();
        };
        (el.querySelector(".rfix") || el).appendChild(away);
        if (!r.sentAway && r.sendWhy) {
          const why = document.createElement("div");
          why.className = "note";
          why.textContent = "Not sent: " + r.sendWhy;
          (el.querySelector(".rfix") || el).appendChild(why);
        }
      }

      /* Two lines worth keeping: what caused it, and what was done about it. The
         next person to hit the same fault reads them instead of writing in again. */
      const fix = el.querySelector(".rfix");
      const lines = [["reason", "Why it happened"], ["solution", "What fixed it"]];
      if (data.owner) {
        lines.forEach(([field, label]) => {
          const row = document.createElement("div");
          row.className = "fixrow";
          const cap = document.createElement("span");
          cap.className = "note";
          cap.textContent = label;
          const input = document.createElement("input");
          input.type = "text";
          input.value = r[field] || "";
          input.placeholder = field === "reason"
            ? "the cause, in a sentence" : "what was changed";
          // saved when the cursor leaves it or on Enter: typing a sentence should not
          // mean a request per letter, and the list must not redraw underneath
          const save = async () => {
            const now = input.value.trim();
            if (now === (r[field] || "")) return;
            r[field] = now;
            const body = { id: r.id };
            body[field] = now;
            await post("/feedback/fix", body);
            toast("Saved");
          };
          input.onblur = save;
          input.onkeydown = (e) => { if (e.key === "Enter") input.blur(); };
          row.appendChild(cap);
          row.appendChild(input);
          fix.appendChild(row);
        });
      } else {
        lines.forEach(([field, label]) => {
          if (!r[field]) return;
          const row = document.createElement("div");
          row.className = "fixrow";
          row.innerHTML = "<span class='note'></span><span class='said'></span>";
          row.querySelector(".note").textContent = label;
          row.querySelector(".said").textContent = r[field];
          fix.appendChild(row);
        });
      }

      /* Anybody may take back their own, and nobody else's: a request typed in
         haste, or one that has answered itself. It stays on the board marked
         withdrawn rather than disappearing. */
      const mine = el.querySelector(".mine");
      const meNow = data.owner ? "you" : ((CFG && CFG.name) || "");
      if (r.done || r.archived || (r.who || "") !== meNow) {
        mine.remove();
      } else {
        mine.onclick = async () => {
          const said = await post("/feedback/cancel", { id: r.id });
          if (said && said.error) { toast(said.error); return; }
          toast("Withdrawn");
          viewReports();
        };
      }

      const hide = el.querySelector(".hide");
      if (data.owner) {
        // hidden rows stay visible to the owner, greyed, so they can be brought back
        hide.textContent = r.hidden ? "Hidden - show" : "Hide from others";
        hide.onclick = async () => {
          await post("/feedback/hide", { id: r.id, hidden: !r.hidden });
          viewReports();
        };
      } else {
        hide.remove();
      }
      const del = el.querySelector(".del");
      if (data.owner) {
        del.onclick = async () => {
          if (!confirm("Take this off the board?\n\nIt is kept in " +
                       "feedback-archive.jsonl.")) return;
          await post("/feedback/delete", { id: r.id });
          viewReports();
        };
      } else {
        del.remove();
      }
      list.appendChild(el);
    });
    if (!(data.reports || []).length) {
      const none = document.createElement("div");
      none.className = "note";
      none.textContent = "Nothing reported yet.";
      list.appendChild(none);
    }
    box.appendChild(list);
    // the noticeboard belongs to the server: a guest writes on it and reads it, and
    // the owner is the one who takes things down
    const settled = (data.reports || []).filter((r) => r.done).length;
    if (data.owner && settled) {
      const clear = document.createElement("div");
      clear.className = "addrow";
      clear.innerHTML = '<button class="btn ghost">Clear the sorted ones (' +
        settled + ")</button>" +
        "<span class='note'>They go to feedback-archive.jsonl - what caused a fault " +
        "and what fixed it is worth keeping. Anything still open stays here.</span>";
      clear.querySelector("button").onclick = async () => {
        if (!confirm("File away " + settled + " sorted report" +
                     (settled === 1 ? "" : "s") + "?\n\nThey are kept in " +
                     "feedback-archive.jsonl, not deleted.")) return;
        const back = await post("/feedback/clear", {});
        toast("Filed away " + (back.filed || 0));
        viewReports();
      };
      box.appendChild(clear);
    }
    main.appendChild(box);
  }

  /* ---------------- subtitles ---------------- */

  /* The four decisions every player settles on: size, colour, what sits behind
     the text, and how high it rides. Kept on the server, so the television and the
     phone and this page all draw them the same way. */
  const SUB_SIZES = [["0.8", "80%"], ["1", "100%"], ["1.25", "125%"],
                     ["1.5", "150%"], ["1.8", "200%"]];
  // what a height is measured from: a film wider than the screen leaves black bars
  const SUB_BASES = [["picture", "On screen"], ["screen", "Off screen"]];
  const SUB_COLOURS = [["white", "White"], ["yellow", "Yellow"], ["cyan", "Cyan"],
                       ["green", "Green"], ["grey", "Grey"]];
  const SUB_FONTS = [["sans", "Sans"], ["serif", "Serif"], ["condensed", "Narrow"],
                     ["rounded", "Round"], ["mono", "Mono"]];
  const SUB_BACKS = [["none", "None"], ["shadow", "Drop shadow"],
                     ["dark", "Dark box"], ["black", "Solid black"]];
  const SUB_POS = [["0", "Bottom edge"], ["0.08", "Just up"], ["0.16", "Raised"],
                   ["0.28", "High"], ["0.99", "Very bottom"]];
  // off the picture the same four steps go the other way: the first sits nearest the
  // film and each one after it is a row further down into the black
  const SUB_POS_OFF = [["0", "Nearest"], ["0.08", "One down"], ["0.16", "Two down"],
                       ["0.99", "Very bottom"]];

  function subPreview(box, look) {
    const line = box.querySelector(".subsample b");
    const colours = { white: "#ffffff", yellow: "#ffe94d", cyan: "#6fd3ff",
                      green: "#8dea6a", grey: "#c9d3dc" };
    line.style.color = colours[look.colour] || "#fff";
    line.style.fontSize = (1.6 * look.size).toFixed(2) + "rem";
    line.style.background = look.background === "dark" ? "rgba(0,0,0,.55)"
      : look.background === "black" ? "#000" : "transparent";
    line.style.textShadow = look.background === "shadow"
      ? "0 2px 4px #000, 0 0 6px #000" : "none";
    line.style.padding = look.background === "none" || look.background === "shadow"
      ? "0" : "2px 10px";
    box.querySelector(".subsample").style.paddingBottom =
      (10 + look.position * 150) + "px";
  }

  const SUB_DEVICES = [
    ["tv", "Television", "Seen from across a room \u2014 the 77-inch."],
    ["web", "Computer", "A monitor at desk distance \u2014 this page, and the 27-inch."],
    ["phone", "Phone", "Held at arm's length; needs proportionally larger text."],
  ];

  function subPreviewOf(box, look) {
    const line = box.querySelector(".subsample b");
    line.style.color = { white: "#ffffff", yellow: "#ffe94d", cyan: "#6fd3ff",
                         green: "#8dea6a", grey: "#c9d3dc" }[look.colour] || "#fff";
    line.style.fontSize = (1.5 * look.size).toFixed(2) + "rem";
    line.style.background = look.background === "dark" ? "rgba(0,0,0,.55)"
      : look.background === "black" ? "#000" : "transparent";
    line.style.textShadow = look.background === "shadow"
      ? "0 2px 4px #000, 0 0 6px #000" : "none";
    line.style.padding = look.background === "none" || look.background === "shadow"
      ? "0" : "2px 10px";
    box.querySelector(".subsample").style.paddingBottom = (10 + look.position * 130) + "px";
  }

  async function paneSubs(main) {
    const data = await get("/settings");
    const guest = CFG && CFG.guest;

    // the language first: it is what most people come to this tab to change
    const lang = block("Subtitle language");
    lang.innerHTML +=
      "<div class='note'>Which language to pick automatically when a title has one.</div>" +
      '<div class="addrow"><select id="setlang"></select></div>';
    const sel = lang.querySelector("#setlang");
    LANGS.forEach((l) => sel.add(new Option(l[1], l[0])));
    // the server's answer when this browser has never been asked
    sel.value = "subLang" in prefs() ? prefs().subLang
                                     : (data.language || "");
    sel.onchange = () => {
      setPref("subLang", sel.value);
      // and on the server, so a television and a second browser agree with this one
      if (window.saveLanguage) saveLanguage(sel.value);
      const name = (LANGS.filter((l) => l[0] === sel.value)[0] || ["", "Off"])[1];
      toast(sel.value ? "Default subtitles: " + name : "Subtitles off by default");
    };
    // and what to read when the film has nothing in the first
    const lang2 = document.createElement("div");
    lang2.className = "addrow subrow";
    lang2.innerHTML = "<span class='sublabel'>Second choice</span>";
    const sel2 = document.createElement("select");
    sel2.id = "setlang2";
    LANGS.forEach((l) => sel2.add(new Option(l[0] === "" ? "None" : l[1], l[0])));
    sel2.value = "subLang2" in prefs() ? prefs().subLang2 : (data.language2 || "");
    sel2.onchange = async () => {
      setPref("subLang2", sel2.value);
      try { await post("/settings", { language2: sel2.value }); } catch (e) {}
      const name = (LANGS.filter((l) => l[0] === sel2.value)[0] || ["", "None"])[1];
      toast(sel2.value ? "Second choice: " + name : "No second choice");
    };
    lang2.appendChild(sel2);
    lang.appendChild(lang2);
    const why = document.createElement("div");
    why.className = "note";
    why.textContent = "Read when the film carries nothing in the first language. " +
      "Without one, whatever the film has is used.";
    lang.appendChild(why);
    main.appendChild(lang);

    /* The OpenSubtitles key, where subtitles are set rather than buried in Library.
     *
     * It is also what the machine that keeps copies needs: it fetches subtitles for
     * the films it takes, and without a key of its own it takes films nobody in the
     * house can read. One press sends it there.
     */
    if (!guest) {
      const keyBox = block("OpenSubtitles key");
      keyBox.innerHTML += "<div class='note'>What this server searches with. A " +
        "server that copies from this one needs the same key to fetch subtitles for " +
        "what it takes.</div>";
      const keyRow = document.createElement("div");
      keyRow.className = "addrow subrow";
      keyRow.innerHTML = "<span class='sublabel'>Key</span>";
      const field = document.createElement("input");
      field.type = "text";
      field.className = "plink";
      field.placeholder = "not set";
      // it lives with the library's own settings, not the viewer's
      let lib = {};
      try {
        lib = await get("/library/config");
      } catch (e) {
        lib = {};
      }
      field.value = lib.opensubtitles_key || "";
      field.onchange = async () => {
        await post("/library/config", { opensubtitles_key: field.value.trim() });
        toast("Saved");
      };
      keyRow.appendChild(field);
      // shown, not starred: it is copied to the other machine by hand as often as
      // it is sent, and a key nobody can read is a key nobody can copy
      const grab = document.createElement("button");
      grab.className = "btn ghost";
      grab.textContent = "Copy";
      grab.onclick = async () => {
        field.select();
        try {
          await navigator.clipboard.writeText(field.value);
          toast("Copied");
        } catch (e) {
          document.execCommand("copy");
        }
      };
      keyRow.appendChild(grab);
      keyBox.appendChild(keyRow);
      // and the cache, if there is one
      let copy = {};
      try {
        copy = await get("/standby");
      } catch (e) {
        copy = {};
      }
      if (copy && copy.where) {
        const send = document.createElement("div");
        send.className = "addrow subrow";
        send.innerHTML = "<span class='sublabel'>" + esc(copy.name || "The cache") +
          "</span>";
        const b = document.createElement("button");
        b.className = "btn ghost";
        b.textContent = "Send the key there";
        b.onclick = async () => {
          b.disabled = true;
          try {
            const said = await post("/follow/keys", { only: "opensubtitles_key" });
            toast(said && said.ok ? "Sent" : (said && said.why) || "Could not send");
          } catch (e) {
            toast("Could not send");
          }
          b.disabled = false;
        };
        send.appendChild(b);
        // and the catalogue key, which is what puts posters on its shelves rather
        // than a grid of grey rectangles when the main server cannot be reached
        const both = document.createElement("button");
        both.className = "btn ghost";
        both.textContent = "Send both keys";
        both.title = "The subtitle key and the catalogue key together";
        both.onclick = async () => {
          both.disabled = true;
          try {
            const said = await post("/follow/keys", {});
            toast(said && said.ok
                  ? "Sent: " + (said.sent || []).join(", ")
                  : (said && said.why) || "Could not send");
          } catch (e) {
            toast("Could not send");
          }
          both.disabled = false;
        };
        send.appendChild(both);
        keyBox.appendChild(send);
      }
      main.appendChild(keyBox);
    }


    /* Putting a subtitle in step with the film, for everybody.

       This was a switch inside the player, one answer per viewer, which meant a guest
       who had never opened it got nothing and a guest who turned it off once kept it
       off for good. It belongs to the server: the measurement is the server's work,
       and whether it is done is not a matter of taste. */
    if (!guest) {
      const ahead = block("Fetch subtitles for the next episode");
      ahead.title = "Before the next episode starts, the subtitle this series has "
        + "settled on is fetched for it - five minutes from the end of the one "
        + "playing, so the file is in place before it begins.";
      ahead.innerHTML +=
        "<div class='note'>Five minutes before an episode ends, the subtitle this "
        + "series settled on is fetched for the next one, so it is beside the file "
        + "before it starts. Uses your OpenSubtitles allowance.</div>";
      const aheadRow = document.createElement("div");
      aheadRow.className = "addrow subrow";
      aheadRow.innerHTML = "<span class='sublabel'>Default</span>";
      [[true, "On"], [false, "Off"]].forEach(([value, text]) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" +
                      ((data.autoFetch !== false) === value ? " on" : "");
        b.textContent = text;
        b.onclick = async () => {
          await post("/settings", { autoFetch: value });
          toast(value ? "Fetching ahead on" : "Fetching ahead off");
          render();
        };
        aheadRow.appendChild(b);
      });
      ahead.appendChild(aheadRow);
      main.appendChild(ahead);

      const scan = block("Automatic subtitle sync");
      scan.innerHTML +=
        "<div class='note'>Measures speech in the audio against the subtitle's cue " +
        "times and applies the offset. Runs when a subtitle is fetched, before " +
        "playback. Per-viewer setting in the player overrides this.</div>";
      const row = document.createElement("div");
      row.className = "addrow subrow";
      row.innerHTML = "<span class='sublabel'>Default</span>";
      [[true, "On"], [false, "Off"]].forEach(([value, text]) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" +
                      ((data.autoScan !== false) === value ? " on" : "");
        b.textContent = text;
        b.onclick = async () => {
          await post("/settings", { autoScan: value });
          toast(value ? "Automatic sync on" : "Automatic sync off");
          render();
        };
        row.appendChild(b);
      });
      scan.appendChild(row);
      main.appendChild(scan);
    }

    /* Bitmap subtitles for remote browsers: a full re-encode plus upload, so off
       by default. Owner-only. The Android app decodes them itself. */
    if (!guest) {
      const burn = block("Burn-in transcoding");
      burn.innerHTML +=
        "<div class='note'>PGS and VobSub tracks are bitmaps. A client that " +
        "cannot decode them — any browser, and a Chromecast receiver — " +
        "can only show one if the server re-encodes the video with the subtitle " +
        "drawn into every frame. It is the heaviest job this server runs: the whole " +
        "film re-encoded for as long as it plays, and the upload with it when the " +
        "viewer is remote. This governs every burn-in transcode, browser and cast " +
        "alike; Nobody means none is ever started. The Android app decodes bitmap " +
        "tracks itself and is unaffected.</div>";
      const row = document.createElement("div");
      row.className = "addrow subrow";
      row.innerHTML = "<span class='sublabel'>Burn for</span>";
      [["all", "Everyone"], ["home", "Local network only"], ["none", "Nobody"]]
        .forEach(([value, text]) => {
          const b = document.createElement("button");
          b.className = "btn ghost kind" +
            ((data.burnFor || "home") === value ? " on" : "");
          b.textContent = text;
          b.onclick = async () => {
            await post("/settings", { burnFor: value });
            toast({ all: "Bitmap tracks burned in for any browser",
                    home: "Bitmap tracks burned in on the local network only",
                    none: "Bitmap tracks hidden from every browser" }[value]);
            render();
          };
          row.appendChild(b);
        });
      burn.appendChild(row);
      main.appendChild(burn);
    }

    /* One block per kind of screen. The same text at the same fraction of the picture
       is comfortable on one and wrong on the others, so each keeps its own size. */
    SUB_DEVICES.forEach(([device, title, blurb]) => {
      const look = Object.assign({}, data.devices[device]);
      const box = block(title);
      box.innerHTML += "<div class='note'>" + blurb + "</div>" +
        '<div class="subsample"><b>The quick brown fox jumps over the lazy dog</b></div>';

      const rowOf = (label, options, key) => {
        const row = document.createElement("div");
        row.className = "addrow subrow";
        row.innerHTML = "<span class='sublabel'>" + label + "</span>";
        // a size or a height is a number, and the one stored need not be one of the
        // five offered: mark the nearest rather than none of them
        const bynumber = key === "size" || key === "position";
        const peg = bynumber
          ? nearestOption(options.map((o) => +o[0]), +look[key] || 0) : -1;
        options.forEach(([value, text], i) => {
          const b = document.createElement("button");
          b.className = "btn ghost kind" +
            ((bynumber ? i === peg : String(look[key]) === value) ? " on" : "");
          b.textContent = text;
          // a viewer's own settings: theirs to change, guest or not
          b.onclick = async () => {
            look[key] = key === "size" || key === "position" ? parseFloat(value) : value;
            // Off the picture there are three steps, not four: a height chosen in the
            // picture that is deeper than the last of them was marked as one thing and
            // drawn as another. It comes back to the lowest step there is instead.
            if (key === "base" && value === "screen" &&
                look.position < 0.9 && look.position > 0.16) look.position = 0.16;
            const saved = await post("/settings", { device: device, subtitles: look });
            Object.assign(look, saved.subtitles);
            // the page itself follows the computer settings
            if (device === DEVICE && window.loadSubtitleLook) await loadSubtitleLook();
            render();
          };
          row.appendChild(b);
        });
        box.appendChild(row);
      };

      // a starting point rather than five decisions: whatever the owner uses. It
      // applies to the whole block, so it sits with the block's name.
      const b = document.createElement("button");
      b.className = "btn ghost adopt";
      b.textContent = "Use the server's default";
      b.onclick = async () => {
        const saved = await post("/settings",
                                 { device: device, useServerDefault: true });
        Object.assign(look, saved.subtitles);
        if (device === DEVICE && window.loadSubtitleLook) await loadSubtitleLook();
        toast("Following the server's " + title.toLowerCase() + " settings");
        render();
      };
      (box.querySelector("h3") || box).appendChild(b);

      rowOf("Size", SUB_SIZES, "size");
      // A percentage of what: one hundred per cent is the same share of a 16:9 picture
      // on every screen, so the number is worth stating once rather than leaving each
      // person to work out why their television and their phone disagreed.
      const share = SUB_BASE_VH * (look.size || 1);
      const howBig = document.createElement("div");
      howBig.className = "note";
      howBig.textContent =
        "100% is " + SUB_BASE_VH + "% of the height of a 16:9 picture as wide as the " +
        "screen - the same on a television, a phone held either way, and here. " +
        Math.round((look.size || 1) * 100) + "% is " + share.toFixed(1) + "% of that " +
        "height - about " + Math.round(share / 100 * 1080) +
        " pixels tall on a 1080-line screen, or " +
        Math.round(share / 100 * 2160) + " on a 4K one.";
      box.appendChild(howBig);
      rowOf("Face", SUB_FONTS, "font");
      rowOf("Colour", SUB_COLOURS, "colour");
      rowOf("Behind", SUB_BACKS, "background");
      rowOf("Position", look.base === "screen" ? SUB_POS_OFF : SUB_POS, "position");
      rowOf("Placed", SUB_BASES, "base");
      const fromWhat = document.createElement("div");
      fromWhat.className = "note";
      fromWhat.textContent =
        "A film wider than the screen is drawn with black above and below it. " +
        "On screen, the text sits in the picture and Position lifts it a row at a " +
        "time. Off screen, it sits in the black below the picture, and Position " +
        "takes it a row further down each time.";
      box.appendChild(fromWhat);
      main.appendChild(box);
      subPreviewOf(box, look);
    });

    if (guest) {
      const note = document.createElement("div");
      note.className = "note";
      note.textContent = "These are yours: each person watching keeps their own.";
      main.appendChild(note);
    }
  }

  /* ---------------- frame ---------------- */

  function tabBar() {
    const bar = document.createElement("div");
    bar.className = "subtabs";
    tabs().forEach(([id, label]) => {
      const b = document.createElement("button");
      b.className = "subtab" + (tab === id ? " active" : "");
      b.textContent = label;
      b.onclick = () => { tab = id; render(); };
      bar.appendChild(b);
    });
    return bar;
  }

  async function render() {
    const main = $("#main");
    main.innerHTML = "";
    const h = document.createElement("h2");
    // Whose settings these are. The machine's own - its folders, its port, what it
    // follows - are those of whichever server is open in the bar, and a page that
    // does not say which one is a page somebody edits the wrong machine from.
    // The name the machine gives for itself, which is the only one that is right
    // when the page is pointed somewhere else: guessing from the row it was picked
    // from named this computer while another one's settings were on the screen.
    const on = (cfg && cfg.serverName) ||
      (window.PD_ON
        ? (window.PD_ON.origin || "").replace(/^https?:\/\//, "")
        : ((CFG && CFG.serverName) || "this server"));
    h.innerHTML = '<span class="ct">Settings</span>' +
      '<span class="onwhich">' + esc(on) + '</span>';
    main.appendChild(h);
    main.appendChild(tabBar());
    if (tab === "library") await paneLibrary(main);
    else if (tab === "torrents") main.appendChild(await downloadsBlock());
    else if (tab === "quality") await paneQuality(main);
    else if (tab === "people") await panePeople(main);
    else if (tab === "now") await paneNow(main);
    else if (tab === "log") await paneLog(main);
    else if (tab === "remote") await paneRemote(main);
    else if (tab === "reports") await paneReports(main);
    else paneSubs(main);
    // on Settings and nowhere else: it is about the machine as a whole, and under
    // Library or Users it read as belonging to what was above it
    if (tab === "quality" && !(CFG && CFG.guest)) main.appendChild(advancedBox());
  }

  /* ---------------- what the machine was built with ---------------- */

  /**
   * Every number the server settles on when nobody has said otherwise.
   *
   * Not settings: there is nothing to change here, and that is the point. The numbers
   * are read from the running server rather than written out a second time in this
   * file, so the page cannot come to disagree with the machine it describes.
   */
  function advancedBox() {
    const box = block("");
    box.className = "setblock advanced";
    const open = document.createElement("button");
    open.className = "btn ghost";
    open.textContent = "Advanced \u2013 the numbers this server was built with";
    const holder = document.createElement("div");
    holder.className = "advancedlist";
    let shown = false;
    open.onclick = async () => {
      shown = !shown;
      holder.innerHTML = "";
      if (!shown) return;
      holder.textContent = "Reading\u2026";
      let said = {};
      try {
        said = await get("/advanced");
      } catch (e) {
        holder.textContent = "The server did not answer for these.";
        return;
      }
      holder.innerHTML = "";
      (said.groups || []).forEach((g) => {
        const h = document.createElement("h4");
        h.textContent = g.title;
        holder.appendChild(h);
        if (g.why) {
          const why = document.createElement("div");
          why.className = "note";
          why.textContent = g.why;
          holder.appendChild(why);
        }
        (g.rows || []).forEach((r) => {
          const line = document.createElement("div");
          line.className = "advrow";
          const name = document.createElement("span");
          name.className = "advname";
          name.textContent = r.name;
          const value = document.createElement("span");
          value.className = "advvalue";
          value.textContent = r.value;
          const what = document.createElement("div");
          what.className = "advwhat";
          what.textContent = r.what || "";
          line.append(name, value, what);
          holder.appendChild(line);
        });
      });
    };
    box.append(open, holder);
    return box;
  }

  /* The other machine, for whoever is watching from away.
   *
   * This server sleeps; the cache does not. A guest cannot be expected to know there
   * is a second address, and a page cannot fetch its way out of a server that is
   * off - so the address is given while this one is still answering, with a link
   * that carries their own key. */
  /* The other server, as a box under Settings rather than a tab of its own: what it
     is, whether it is awake, and what it is holding. */

  /* ---------------- quality ---------------- */

  /**
   * What one viewer wants, wherever they watch.
   *
   * Kept with the rest of their settings rather than in the browser, so the answer is
   * the same on the television, the phone and here. It is a preference, not a
   * ceiling: the player can still ask for something else while a film is on, and the
   * server's own limit applies over the top of both.
   */
  function yourQuality(main, s) {
    const mine = s.mine || { height: 0, mbit: 0 };
    const box = block("Yours — the most you want sent to you");
    const note = document.createElement("div");
    note.className = "note";
    note.textContent = "A maximum, for you alone, on every screen you watch on. "
      + "Anything smaller is sent as it is, and the player's gear can ask for less "
      + "while a film is on. It changes nothing for anybody else.";
    box.appendChild(note);
    const save = async (what) => {
      Object.assign(mine, what);
      await post("/settings", { mine: mine });
      toast("Saved");
      render();
    };
    const pick = (label, list, now, key) => {
      const line = document.createElement("div");
      line.className = "addrow subrow";
      line.innerHTML = "<span class='sublabel'>" + label + "</span>";
      list.forEach(([value, text]) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" + (now === value ? " on" : "");
        b.textContent = text;
        b.onclick = () => save({ [key]: value });
        line.appendChild(b);
      });
      box.appendChild(line);
    };
    pick("Picture", HEIGHTS, mine.height || 0, "height");
    pick("Megabits", RATES, mine.mbit || 0, "mbit");
    const cap = (s.quality || {})[s.here || "away"] || {};
    if (cap.height || cap.mbit) {
      const said = document.createElement("div");
      said.className = "note";
      said.textContent = "Whatever you choose here, this server will send you at "
        + "most " + (cap.height ? cap.height + "p" : "the original size")
        + (cap.mbit ? " and " + cap.mbit + " Mbit/s" : "")
        + " from where you are watching.";
      box.appendChild(said);
    }
    main.appendChild(box);
  }

  //: what a player may ask for, and what the ceilings offer
  const HEIGHTS = [[0, "Original"], [1080, "1080p"], [720, "720p"]];
  //: megabits a second. 1080p wants around eight to look like the disc; 720p is
  //: comfortable at four, and two is the figure that survives a bad hotel.
  const RATES = [[0, "Unlimited"], [20, "20"], [12, "12"], [8, "8"], [5, "5"],
                 [4, "4"], [2, "2"]];

  /**
   * The ceiling on what leaves this machine, once for each side of the front door.
   *
   * A house has a gigabit of network in it and an upload of rather less, so the film
   * that plays perfectly in the next room is the one that stutters at a friend's.
   * These are ceilings, not choices: a player still asks for what it wants, and gets
   * whichever is lower.
   */
  /* The look this person's screens wear, offered as the server lists them.
   *
   * Nothing here knows what a skin is called or what colour it is: the server holds
   * the palettes, so adding one is an entry in a file and every screen wears it the
   * next time it asks. A machine that has put a look on by itself - the cache after
   * dark, a card handed to a game - says so, and what is chosen here waits its turn.
   */
  async function skinBlock(main) {
    let said = {};
    try {
      said = await get("/skins");
    } catch (e) {
      return;                            // an older server has no looks to offer
    }
    const box = block("Theme");
    box.innerHTML += "<div class='note'>What every screen of yours is drawn with. " +
      "It follows you from server to server; a machine only overrules it while it " +
      "has a reason to.</div>";
    const row = document.createElement("div");
    row.className = "addrow subrow";
    row.innerHTML = "<span class='sublabel'>Skin</span>";
    (said.skins || []).forEach((one) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" + (one.id === said.chosen ? " on" : "");
      b.textContent = one.name;
      b.title = one.why || "";
      b.onclick = async () => {
        await post("/skins", { skin: one.id });
        await dressUp();
        render();
      };
      row.appendChild(b);
    });
    box.appendChild(row);
    if (said.imposed) {
      const why = document.createElement("div");
      why.className = "note";
      why.textContent = "This machine is wearing " +
        ((said.skins || []).filter((x) => x.id === said.imposed)[0] || {}).name +
        " of its own accord - " + (said.why || "") +
        ". What is chosen here comes back when that passes.";
      box.appendChild(why);
    }
    main.appendChild(box);
  }

  /* How a film is fetched for this viewer: off one machine or several, and
     whether it may carry on from another when one stops. */
  /* What to call you, shown as it stands. A name was something only whoever wrote
     the invitation could give, so everybody wore whatever they were christened at
     the moment they were invited - the owner included, as "me". */
  function yourName(main, data) {
    // What to call you. A name was something only whoever wrote the invitation could
    // give, so everybody wore whatever they were christened at the moment they were
    // invited - and the owner wore "me", which is what a server calls whoever
    // installed it before anybody has said who that is.
    const who = block("Your name");
    const row = document.createElement("div");
    row.className = "addrow subrow";
    row.innerHTML = "<span class='sublabel'>Called</span>";
    const box = document.createElement("input");
    box.type = "text";
    box.style.flex = "1 1 auto";
    box.value = (data.myName && data.myName !== "me") ? data.myName : "";
    box.placeholder = "what to call you";
    box.onchange = async () => {
      const called = box.value.trim();
      if (!called) return;
      await post("/settings", { myName: called });
      toast("You are " + called + " from now on");
      render();
    };
    row.appendChild(box);
    who.appendChild(row);
    who.innerHTML += "<div class='note'>What shows against whatever you are " +
      "watching. Yours to change; the name on the key you were given stays as it " +
      "was written, so whoever gave it to you still knows whose it is.</div>";
    main.appendChild(who);
  }

  function howFetched(main, data) {
    // Both belong to the person watching rather than to the machine: one house may
    // want a film read off every machine that has it, and somebody on a thin line
    // would rather it came off one and stayed there.
    const how = block("How films are fetched");
    [["splitPlay", "Read off several machines",
      "A film held on more than one machine is read from all of them at once, so " +
      "losing one of them does not stop the picture."],
     ["failover", "Move to another machine",
      "If the machine serving a film stops answering, the film carries on from " +
      "another that holds it, at the same moment."]]
      .forEach(([name, label, note]) => {
        const row = document.createElement("div");
        row.className = "addrow subrow";
        row.innerHTML = "<span class='sublabel'>" + label + "</span>";
        [[true, "On"], [false, "Off"]].forEach(([val, text]) => {
          const b = document.createElement("button");
          b.className = "btn ghost kind" +
            ((data[name] !== false) === val ? " on" : "");
          b.textContent = text;
          b.onclick = async () => {
            await post("/settings", { [name]: val });
            render();
          };
          row.appendChild(b);
        });
        how.appendChild(row);
        const n = document.createElement("div");
        n.className = "note";
        n.textContent = note;
        how.appendChild(n);
      });
    main.appendChild(how);
  }

  /* What the Films tab stands: three kinds, each turned on or off by whoever is
     looking. Theirs, so it holds on the phone and the television as well. */
  function filmsShelf(main, data) {
    const box = block("What Films shows");
    const said = (data.filmsShow && typeof data.filmsShow === "object")
      ? data.filmsShow : {};
    // the same defaults the server answers with, so a page read before anything was
    // ever chosen lights the same buttons the shelf is actually standing
    const fallback = { disk: true, download: true, request: true };
    [["disk", "On disk",
      "Films this house holds a file for. These play."],
     ["download", "Download",
      "Films one of the packs carries. Not here yet, but fetching one is a button."],
     ["request", "Request",
      "Films nowhere in the house and on no pack: new on streaming, and all anybody " +
      "can do is ask."]]
      .forEach(([name, label, note]) => {
        const on = said[name] === undefined ? fallback[name] : !!said[name];
        const row = document.createElement("div");
        row.className = "addrow subrow";
        row.innerHTML = "<span class='sublabel'>" + label + "</span>";
        [[true, "Show"], [false, "Hide"]].forEach(([val, text]) => {
          const b = document.createElement("button");
          b.className = "btn ghost kind" + (on === val ? " on" : "");
          b.textContent = text;
          b.onclick = async () => {
            await post("/settings", { filmsShow: { [name]: val } });
            render();
          };
          row.appendChild(b);
        });
        box.appendChild(row);
        const n = document.createElement("div");
        n.className = "note";
        n.textContent = note;
        box.appendChild(n);
      });
    main.appendChild(box);
  }

  /* A poster behind the shelves: the title last opened, or the one somebody
     stopped. Theirs rather than the machine's, so it follows them to the phone and
     the television. */
  function backdropBlock(main, s) {
    const box = block("Backdrop");
    box.innerHTML += "<div class='note'>Artwork behind what is on screen. Always " +
      "puts it behind the shelves too - whatever was opened last, or the newest thing " +
      "in Continue watching. Title page only keeps it to a film or series' own page. " +
      "Yours, and set for each kind of screen: a television across the room is not a " +
      "phone held at arm's length.</div>";
    const said = (s.backdrop && typeof s.backdrop === "object") ? s.backdrop : {};
    const word = (v) => v === true || v === undefined || v === null ? "on"
      : v === false ? "off"
      : ["on", "poster", "off"].indexOf(String(v)) >= 0 ? String(v) : "on";
    SUB_DEVICES.forEach(([device, title]) => {
      const row = document.createElement("div");
      row.className = "addrow subrow";
      row.innerHTML = "<span class='sublabel'>" + title + "</span>";
      const on = word(said[device]);
      [["on", "Always"], ["poster", "Title page only"], ["off", "Off"]].forEach(([value, text]) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" + (on === value ? " on" : "");
        b.textContent = text;
        b.onclick = async () => {
          await post("/settings", { backdrop: value, device: device });
          if (device === "web" && window.backdropWanted) window.backdropWanted(value);
          render();
        };
        row.appendChild(b);
      });
      box.appendChild(row);
    });
    main.appendChild(box);
  }

  async function paneQuality(main) {
    const s = await get("/settings");
    await skinBlock(main);
    backdropBlock(main, s);
    yourName(main, s);
    yourQuality(main, s);
    howFetched(main, s);
    filmsShelf(main, s);
    if (CFG && CFG.guest) return;      // the ceilings below are the owner's business
    // what this machine is set to do about itself, and the build it is running: the
    // machine tab keeps the card about the computer, the rest of it reads as settings
    main.appendChild(await serverUpdate());
    const rest = document.createElement("div");
    main.appendChild(rest);
    machinePanel().then((box) => rest.replaceWith(box)).catch(() => {});
    // what the computer is doing now, and the switch that holds back the parts of it
    // that can wait: one machine's load, beside the rest of what that machine is set to
    await paneLoad(main);
    const caps = s.quality || { home: { height: 0, mbit: 0 },
                                away: { height: 0, mbit: 0 } };
    const save = async (where, what) => {
      caps[where] = Object.assign({}, caps[where], what);
      await post("/settings", { quality: { [where]: caps[where] } });
      toast("Saved");
      render();
    };
    const heading = document.createElement("h3");
    heading.className = "subhead pane";
    heading.textContent = "THE SERVER'S OWN LIMITS — EVERYBODY";
    main.appendChild(heading);
    [["home", "Everyone, on this network",
      "The most this server will send to anything in the house, itself included. " +
      "There is usually no reason to cap it — the network is faster than the " +
      "film."],
     ["away", "Everyone, from outside",
      "The most this server will send to anybody reaching it from elsewhere, guests " +
      "included. This is the one worth setting: it is bounded by what this line can " +
      "upload, not by what they can download. Whichever is lower — this, or what " +
      "the viewer asked for — is what they get."]].forEach(([where, title, why]) => {
      const box = block(title);
      const note = document.createElement("div");
      note.className = "note";
      note.innerHTML = why;
      box.appendChild(note);
      const pick = (label, list, now, key) => {
        const line = document.createElement("div");
        line.className = "addrow subrow";
        line.innerHTML = "<span class='sublabel'>" + label + "</span>";
        list.forEach(([value, text]) => {
          const b = document.createElement("button");
          b.className = "btn ghost kind" + (now === value ? " on" : "");
          b.textContent = text;
          b.onclick = () => save(where, { [key]: value });
          line.appendChild(b);
        });
        box.appendChild(line);
      };
      pick("Picture", HEIGHTS, caps[where].height || 0, "height");
      pick("Megabits", RATES, caps[where].mbit || 0, "mbit");
      const said = document.createElement("div");
      said.className = "note";
      const h = (HEIGHTS.find((x) => x[0] === (caps[where].height || 0)) || [])[1];
      const m = caps[where].mbit;
      said.textContent = !caps[where].height && !m
        ? "No ceiling: whatever the viewer asks for."
        : "At most " + (caps[where].height ? h : "the original size") +
          (m ? ", at most " + m + " Mbit/s" : "") +
          ". A film already smaller than this is sent as it is.";
      box.appendChild(said);
      main.appendChild(box);
    });
  }

  async function startScan(probe) {
    const r = await post("/library/scan", { probe: probe, identify: true });
    if (r.error) toast(r.error);
    else toast("Scanning" + (probe ? " and reading media info" : ""));
    poll();
  }

  let timer = null;
  async function poll() {
    clearTimeout(timer);
    const el = document.getElementById("scanstate");
    if (!el) return;                       // left the settings view
    let s = null;
    try {
      s = await get("/library/status");
    } catch (e) {
      // the server is being replaced, or the line went: ask again rather than
      // throwing where nobody can catch it
      timer = setTimeout(poll, 8000);
      return;
    }
    const sc = s.scan || {};
    el.textContent = sc.running
      ? `${sc.phase}: ${sc.done} of ${sc.total}`
      : `${s.movies} films, ${s.shows} shows, ${s.episodes} episodes, ` +
        `${s.identified} identified`;
    timer = setTimeout(poll, sc.running ? 1500 : 8000);
  }

  /** Open the settings on one particular tab - "subs" for how subtitles look. */
  window.settingsTab = function (which) {
    if (tabs().some((t) => t[0] === which)) tab = which;
  };

  /**
   * Reports, as a page of its own.
   *
   * It stopped belonging in Settings the moment it grew three tabs: what is new and
   * what people are asking for are not settings, and nobody looks for them there.
   * The pane is the same one; only where it hangs has changed.
   */
  /**
   * Who is watching, right now, and what the main server is using altogether.
   *
   * Its own page rather than the tail of the invitations screen: this is the thing
   * somebody opens when a film stutters, and nobody looks for that under People.
   */

  // which viewer the log is filtered to; empty for everyone
  let logWho = "";

  /* What the main server has watched, over three windows.

     Above the log rather than inside it: the log answers "what did somebody put on
     last Tuesday", and this answers "how much is this thing used", which is the
     question somebody actually opens the page with. */
  async function statsBlock(main) {
    let data;
    try {
      data = await get("/stats");
    } catch (e) {
      return;                            // a guest, or a server without the endpoint
    }
    const box = block("Watched");
    box.innerHTML += "<div class='note'>Each viewing counts from its first "
      + "progress report to its last, capped at the runtime of the title.</div>";
    const row = document.createElement("div");
    row.className = "statrow";
    [["week", "This week"], ["month", "This month"], ["total", "Altogether"]]
      .forEach(([key, label]) => {
        const d = data[key] || {};
        const cell = document.createElement("div");
        cell.className = "statcell";
        const top = (d.top || [])[0];
        cell.innerHTML =
          "<span class='statlabel'>" + label + "</span>" +
          "<b>" + (d.hours === undefined ? "-" : d.hours) + " h</b>" +
          "<span class='statunder'>" + (d.viewings || 0) + " viewings &middot; " +
          (d.titles || 0) + " titles</span>" +
          (top ? "<span class='statunder'>most: " + esc(top.title) + "</span>" : "") +
          ((d.people || []).length > 1
            ? "<span class='statunder'>" + (d.people || []).slice(0, 3)
                .map((p) => esc(p.who) + " " + p.hours + "h").join(" &middot; ")
              + "</span>"
            : "");
        row.appendChild(cell);
      });
    box.appendChild(row);
    main.appendChild(box);
    await copiedBlock(main);
  }

  /* The same three questions about what went to the machine that keeps copies.
   *
   * Watching and copying are the two things this server does with a disk and a
   * network, and the figures for one mean nothing without the other - a heavy week
   * of copying and a quiet week of watching is a different machine from the reverse.
   */
  async function copiedBlock(main) {
    let data;
    try {
      data = await get("/copied");
    } catch (e) {
      return;                            // nothing follows this server
    }
    if (!((data.total || {}).files)) return;
    const box = block("Copied");
    box.innerHTML += "<div class='note'>What has gone to the machine that keeps "
      + "copies, counted as each file finished.</div>";
    const row = document.createElement("div");
    row.className = "statrow";
    [["week", "This week"], ["month", "This month"], ["total", "Altogether"]]
      .forEach(([key, label]) => {
        const d = data[key] || {};
        const cell = document.createElement("div");
        cell.className = "statcell";
        cell.innerHTML =
          "<span class='statlabel'>" + label + "</span>" +
          "<b>" + (d.gb === undefined ? "-" : d.gb) + " GB</b>" +
          "<span class='statunder'>" + (d.files || 0) + " files &middot; " +
          (d.titles || 0) + " titles</span>" +
          (d.top ? "<span class='statunder'>largest: " + esc(d.top.title) +
                   "</span>" : "") +
          ((d.where || []).length
            ? "<span class='statunder'>" + (d.where || [])
                .map((p) => esc(p.who) + " " + p.gb + " GB").join(" &middot; ")
              + "</span>"
            : "");
        row.appendChild(cell);
      });
    box.appendChild(row);
    main.appendChild(box);
  }


  /** Who watched what, and when. */
  //: which of the two screens under Watch log is showing
  let logTab = "log";
  /* whose downloads the Downloads log shows; empty for everyone's */
  let downloadsWho = "";
  //: whether the whole book was asked for rather than the newest few hundred
  let logAll = false;
  //: showing only what the machine that keeps copies is holding
  let logCopy = false;

  /* Every download from a torrent pack: who asked for which film, and when. */
  async function paneDownloads(main) {
    const box = block("Downloads");
    let said = {};
    try {
      said = await get("/torrents/log");
    } catch (e) {
      said = {};
    }
    const rows = said.downloads || [];
    const people = Array.from(new Set(rows.map((r) => r.who || "someone"))).sort();
    const bar = document.createElement("div");
    bar.className = "addrow subrow";
    bar.innerHTML = "<span class='sublabel'>Who</span>";
    const pick = document.createElement("select");
    [["", "Everyone"]].concat(people.map((p) => [p, p])).forEach(([v, t]) => {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = t;
      o.selected = v === downloadsWho;
      pick.appendChild(o);
    });
    pick.onchange = () => { downloadsWho = pick.value; render(); };
    bar.appendChild(pick);
    box.appendChild(bar);
    const list = document.createElement("div");
    list.className = "traffic";
    const head = document.createElement("div");
    head.className = "trow thead";
    head.innerHTML = "<span>Film</span><b>Who</b><b>GB</b><b>State</b>";
    list.appendChild(head);
    const shown = rows.filter((r) => !downloadsWho || (r.who || "someone") === downloadsWho);
    // this week, this month and altogether, as the Watch log has for viewings: the last
    // seven and thirty days, for everyone or for whoever is picked above the list
    const sum = block("Downloaded");
    sum.innerHTML += "<div class='note'>Counted when each film finished; a film still " +
      "coming in counts as far as it has got.</div>";
    const statrow = document.createElement("div");
    statrow.className = "statrow";
    const now = Date.now() / 1000;
    const had = (r) => (r.size || 0) * (r.state === "done" ? 1 : (r.progress || 0)) / 1e9;
    [["This week", now - 7 * 86400], ["This month", now - 30 * 86400], ["Altogether", 0]]
      .forEach(([label, since]) => {
        const got = shown.filter((r) => (r.state === "done" || r.state === "downloading") &&
          ((r.state === "done" ? (r.done || r.when) : r.when) || 0) >= since);
        const films = got.filter((r) => r.state === "done");
        const coming = got.length - films.length;
        const asked = shown.filter((r) => (r.when || 0) >= since).length;
        const people = {};
        got.forEach((r) => {
          const who = r.who || "someone";
          people[who] = (people[who] || 0) + had(r);
        });
        const largest = films.slice().sort((a, b) => (b.size || 0) - (a.size || 0))[0];
        const cell = document.createElement("div");
        cell.className = "statcell";
        cell.innerHTML =
          "<span class='statlabel'>" + label + "</span>" +
          "<b>" + got.reduce((n, r) => n + had(r), 0).toFixed(1) + " GB</b>" +
          "<span class='statunder'>" + films.length + (films.length === 1 ? " film" : " films") +
            (coming ? " &middot; " + coming + " coming in" : "") +
            " &middot; " + asked + " asked for</span>" +
          (largest ? "<span class='statunder'>largest: " + esc(largest.title || "") + "</span>" : "") +
          (Object.keys(people).length > 1
            ? "<span class='statunder'>" + Object.keys(people)
                .sort((a, b) => people[b] - people[a]).slice(0, 3)
                .map((p) => esc(p) + " " + people[p].toFixed(1) + " GB").join(" &middot; ") +
              "</span>"
            : "");
        statrow.appendChild(cell);
      });
    sum.appendChild(statrow);
    main.appendChild(sum);
    shown.forEach((r) => {
      const el = document.createElement("div");
      el.className = "trow";
      const name = document.createElement("span");
      name.style.whiteSpace = "pre-line";
      name.textContent = downloadName(r, r.key) + "\n" +
        new Date((r.when || 0) * 1000).toLocaleString();
      const who = document.createElement("b");
      who.textContent = r.who || "someone";
      const gb = document.createElement("b");
      gb.textContent = ((r.size || 0) / 1e9).toFixed(1);
      const st = document.createElement("b");
      st.textContent = r.state === "downloading" ? Math.round((r.progress || 0) * 100) + "%"
        : r.state === "failed" ? "failed" + (r.why ? ": " + r.why : "") : (r.state || "");
      el.append(name, who, gb, st);
      list.appendChild(el);
    });
    if (!shown.length) {
      const none = document.createElement("div");
      none.className = "note";
      none.textContent = rows.length ? "Nothing downloaded by " + downloadsWho + "."
                                     : "Nothing has been downloaded yet.";
      list.appendChild(none);
    }
    box.appendChild(list);
    main.appendChild(box);
  }

  async function paneLog(main) {
    // What has been watched, what has been copied, and what did the watching.
    // Three screens of one question, none of them a tab's worth on its own.
    const tabs = document.createElement("div");
    tabs.className = "addrow subrow";
    [["log", "Watch log"], ["sent", "Transferred"], ["downloads", "Downloads"],
     ["versions", "Versions"]].forEach(([id, label]) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" + (logTab === id ? " on" : "");
      b.textContent = label;
      b.onclick = () => { logTab = id; render(); };
      tabs.appendChild(b);
    });
    main.appendChild(tabs);
    if (logTab === "versions") {
      main.appendChild(versionsBlock(await get("/machine").catch(() => ({}))));
      return;
    }
    if (logTab === "downloads") {
      await paneDownloads(main);
      return;
    }
    if (logTab === "sent") {
      await paneTransferred(main);
      return;
    }
    await statsBlock(main);
    const box = block("Watch log");
    box.innerHTML += "<div class='note'>Every viewing and every download, newest first. " +
      "Viewings are written from the "
      + "progress each client reports, so it covers the browser, the phone and the "
      + "television alike.</div>";
    let data = { watched: [] };
    try {
      data = await get("/watchlog" + (logAll ? "?limit=20000" : ""));
    } catch (e) {
      box.innerHTML += "<div class='note'>Could not read it.</div>";
      main.appendChild(box);
      return;
    }
    // downloads among the viewings: who asked for which film, when, and how far it got
    let fetched = [];
    try {
      fetched = ((await get("/torrents/log")).downloads || []).map((d) => ({
        download: d, who: d.who || "someone", key: d.key, started: d.when || 0,
        title: downloadName(d, d.key) }));
    } catch (e) {
      fetched = [];                    // a server with no torrents
    }
    const all = (data.watched || []).concat(fetched)
      .sort((a, b) => (b.started || 0) - (a.started || 0));
    // the newest few hundred, unless the whole book has been asked for
    if ((data.held || 0) > (data.watched || []).length) {
      const more = document.createElement("div");
      more.className = "addrow";
      const b = document.createElement("button");
      b.className = "btn ghost";
      b.textContent = "Show all " + data.held + " viewings";
      b.onclick = () => { logAll = true; render(); };
      more.appendChild(b);
      box.appendChild(more);
    }
    if (!all.length) {
      box.innerHTML += "<div class='note'>Nothing watched yet.</div>";
      main.appendChild(box);
      return;
    }
    // one entry per person, with how many viewings each
    const counts = {};
    all.forEach((w) => { counts[w.who] = (counts[w.who] || 0) + 1; });
    const who = document.createElement("div");
    who.className = "addrow";
    who.innerHTML = "<span class='lbl'>Viewer:</span>";
    const pick = document.createElement("select");
    pick.className = "genrepick";
    pick.add(new Option("Everyone (" + all.length + ")", ""));
    Object.keys(counts).sort().forEach((name) => {
      pick.add(new Option(name + " (" + counts[name] + ")", name));
    });
    pick.value = logWho;
    pick.onchange = () => { logWho = pick.value; render(); };
    who.appendChild(pick);
    // and what of it the other machine is holding. "Which of these could I still
    // watch tonight" is the question this page is opened with once the main server server
    // is off, and until now it could only be answered by going through the shelves.
    let copies = null;
    try {
      copies = new Set(((await get("/copies")) || {}).keys || []);
    } catch (e) {
      copies = null;                   // nothing follows this server
    }
    if (copies && copies.size) {
      // Which machine, by name. It was a switch called "On the cache" - a job rather
      // than a machine, and unanswerable on a screen with two servers on it. The
      // names are this server's own and whichever one keeps copies of it.
      const where = document.createElement("select");
      where.className = "btn ghost";
      const mine = (CFG && CFG.serverName) || "this server";
      const theirs = standbyName || "the other server";
      [["", "Anywhere"], ["here", "On " + mine], ["copy", "On " + theirs]]
        .forEach(([id, label]) => {
          const o = document.createElement("option");
          o.value = id;
          o.textContent = label;
          if ((logCopy ? "copy" : "") === id) o.selected = true;
          where.appendChild(o);
        });
      where.onchange = () => { logCopy = where.value === "copy"; render(); };
      who.appendChild(where);
    }
    box.appendChild(who);

    let rows = logWho ? all.filter((w) => w.who === logWho) : all;
    if (logCopy && copies) rows = rows.filter((w) => copies.has(String(w.key)));
    const table = document.createElement("div");
    table.className = "watchlog";
    rows.forEach((w) => {
      const when = new Date(w.started * 1000);
      // a date anybody can sort by eye, and the clock this viewer reads
      const stamp = when.getFullYear() + "-" +
        String(when.getMonth() + 1).padStart(2, "0") + "-" +
        String(when.getDate()).padStart(2, "0") + " " +
        when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit",
                                      hour12: CLOCK === "12" });
      const mins = Math.round((w.seconds || 0) / 60);
      const pos = w.duration ? Math.round(100 * (w.position || 0) / w.duration) : 0;
      const el = document.createElement("div");
      el.className = "logrow" + (w.download ? " download" : "");
      el.innerHTML = "<span class='who'></span><span class='what'></span>" +
        "<span class='note when'></span><span class='note how'></span>";
      el.querySelector(".who").textContent = w.who;
      // the title is the way back to the thing itself: a film to its own page, an
      // episode to its programme at the right season
      const what = el.querySelector(".what");
      what.textContent = w.title || w.key;
      if (w.key && window.openKey) {
        what.className = "what goes";
        what.title = "Open this in the library";
        what.onclick = () => window.openKey(w.key);
      }
      el.querySelector(".when").textContent = stamp;
      el.querySelector(".how").textContent = w.download ? [
        "\u2913 " + (w.download.state === "downloading"
          ? "downloading " + Math.round((w.download.progress || 0) * 100) + "%"
          : w.download.state === "done" ? "downloaded" : (w.download.state || "asked for")),
        ((w.download.size || 0) / 1e9).toFixed(1) + " GB",
        w.download.why || "",
      ].filter(Boolean).join("  \u00b7  ") : [
        mins ? mins + " min" : "under a minute",
        pos ? pos + "% in" : "",
        // what it was, then what it calls itself: "Google TV app - Streamer"
        [w.client, String(w.device || "").split(" ").pop()]
          .filter(Boolean).join(" - "),
        w.app,
        w.casual ? "casual" : "",
      ].filter(Boolean).join("  \u00b7  ");
      table.appendChild(el);
    });
    box.appendChild(table);
    main.appendChild(box);
  }

  /* 24 hours or 12, for every time this page prints. */
  let CLOCK = "24";
  (async () => {
    try {
      CLOCK = (await get("/settings?device=web")).clock || "24";
    } catch (e) { /* 24 is the answer for most of the world */ }
  })();

  /* The drawing of what is talking to what. It opens Now playing, where the
   * question is what is happening right now, and nothing else on the page
   * answers that in one picture. */
  function wiringBlock(into) {
    // the drawing goes first: what is on, and what is talking to what
    const map = block("Where it all goes");
    const canvas = document.createElement("div");
    canvas.className = "wirebox";
    // Drawn again on request. It redraws itself every few seconds, which is right
    // for watching something happen and no use at all when something has just
    // changed and you want to see it now.
    const again = document.createElement("button");
    again.className = "btn ghost";
    again.textContent = "Refresh";
    again.onclick = () => {
      again.disabled = true;
      drawWiring(canvas).catch(() => {}).finally(() => { again.disabled = false; });
    };
    map.appendChild(again);
    map.appendChild(canvas);
    into.appendChild(map);
    drawWiring(canvas);
    // the same two seconds the rows below it use: a drawing that is ten seconds
    // behind the numbers beside it is worse than no drawing, because it is read as
    // now. One answer from each machine, which is what the rows cost anyway.
    const beat = setInterval(() => {
      if (!document.body.contains(canvas)) return clearInterval(beat);
      drawWiring(canvas);
    }, 2000);
  }

  async function paneNow(main) {
    // what is talking to what, before anything else on the page
    wiringBlock(main);
    const sum = document.createElement("div");
    sum.className = "livetotals";
    main.appendChild(sum);
    const live = document.createElement("div");
    // the same column as the drawing above, so the rate on the right of a row lines
    // up with the right edge of the box rather than running out to the window
    live.className = "liverows";
    main.appendChild(live);
    drawLive(live, sum);
  }

  /* Who is following this server, and what has already gone to them. */
  /* What the machine that keeps copies has fetched, newest first.
   *
   * Filed with the watch log rather than with the machine that fetched it: what
   * was watched and what was copied are one question asked of two books, and
   * reading either one alone is how a queue looks unaccountable.
   */
  async function paneTransferred(main) {
    const box = block("Transferred");
    box.innerHTML += "<div class='note'>Every file copied to the machine that keeps copies, newest first: why it was wanted, how big it was and how fast it went.</div>";
    const sent = document.createElement("div");
    sent.className = "traffic";
    box.appendChild(sent);
    const when = (secs) => {
      const ago = Math.max(0, Math.floor(Date.now() / 1000) - secs);
      return ago < 90 ? "just now"
        : ago < 3600 ? Math.round(ago / 60) + " min ago"
        : ago < 86400 ? Math.round(ago / 3600) + " h ago"
        : new Date(secs * 1000).toLocaleDateString();
    };
    const drawSent = async (many) => {
      let log = {};
      try {
        log = await get("/follow/log?limit=" + (many || 10));
      } catch (e) {
        return;
      }
      sent.innerHTML = "";
      const h = document.createElement("div");
      h.className = "trow thead";
      h.innerHTML = "<span>Transferred</span><b>Why</b><b>GB</b><b>Where</b>";
      sent.appendChild(h);
      (log.copies || []).slice(0, many || 10).forEach((row) => {
        const el = document.createElement("div");
        el.className = "trow";
        const name = document.createElement("span");
        // The file and when it went; how fast it managed sits under it, since a
        // number without the file it belongs to says nothing.
        const rate = row.mbit ? row.mbit.toFixed(0) + " Mbit" +
                     (row.peak && row.peak > row.mbit * 1.3
                      ? " (peak " + row.peak.toFixed(0) + ")" : "") : "";
        name.style.whiteSpace = "pre-line";
        name.textContent = (row.title || row.key || "a file") + "\n" +
          [when(row.when), rate].filter(Boolean).join("  ·  ");
        // and why it went, in a column of its own: it is the question anybody
        // reading this list is actually asking, and it was crowded onto the end of
        // the file's own line where it read as part of the name.
        const why = document.createElement("b");
        why.textContent = [row.why || "", row["for"] || ""]
          .filter(Boolean).join(" · ");
        const gb = document.createElement("b");
        gb.textContent = (row.gb || 0).toFixed(2);
        const to = document.createElement("b");
        to.textContent = row.to || row.address || "";
        el.appendChild(name);
        el.appendChild(why);
        el.appendChild(gb);
        el.appendChild(to);
        sent.appendChild(el);
      });
      const foot = document.createElement("div");
      foot.className = "addrow";
      const all = document.createElement("button");
      all.className = "btn ghost";
      all.textContent = (many || 10) > 10 ? "Show the last ten" : "Check log";
      all.onclick = () => drawSent((many || 10) > 10 ? 10 : 500);
      foot.appendChild(all);
      sent.appendChild(foot);
    };
    drawSent(10);
    main.appendChild(box);
  }

  /* What the machine that keeps copies is taking, and what is next. */
  async function nowCopying(into) {
    let said = {};
    try {
      said = await get("/follow/queue");
    } catch (e) {
      return;                            // nothing follows this server
    }
    // block() appends to the page; here it belongs in the holder that is refilled
    const page = into;
    // A subtitle is a few kilobytes travelling with its film, not a place in the
    // queue: three of them under one title made a numbered list read like a mess.
    const rows = (said.queue || []).filter((r) => r.now || (!r.here && !r.side));
    if (!rows.length) return;
    const sides = (said.queue || []).filter((r) => !r.here && r.side).length;
    // whose queue this is, by name - and whether this machine has any business
    // showing one at all. A server that follows another was answering the same
    // question about itself: what it would hand to a machine copying from it, out of
    // its own library. That is a real answer to a question nobody asked, and it reads
    // as a second queue that disagrees with the first.
    let takenBy = "";
    let followsOne = null;
    try {
      const who = await get("/follow");
      takenBy = ((who.followers || [])[0] || {}).name ||
                ((who.cache || {}).name || "");
      const mine = who.follow || {};
      if (!(who.followers || []).length && mine.on && mine.master) {
        followsOne = mine.master;
      }
    } catch (e) { takenBy = ""; }
    if (followsOne) {
      const note = block("Being copied");
      note.innerHTML += "<div class='note'>This machine keeps copies for " +
        esc(followsOne.replace(/^https?:\/\//, "")) + ". The queue is that " +
        "server's - it decides the order, and this one works through it. Nothing " +
        "copies from here.</div>";
      into.appendChild(note);
      return;
    }
    // How much the whole queue comes to, not just the row being fetched: the column
    // gives each file's size and nothing added them up, so an hour's work and a
    // night's read the same.
    const whole = rows.reduce((sum, r) => sum + (Number(r.gb) || 0), 0);
    const box = block(takenBy ? "Being copied to " + takenBy : "Being copied");
    box.innerHTML += "<div class='note'>What " + (takenBy || "the machine that keeps " +
      "copies") + " is taking, in the order it will take it. Whoever is watching " +
      "leads it; then anything moved up by hand." +
      (sides ? "  " + sides + " subtitle" + (sides > 1 ? "s" : "") +
               " travel with them." : "") +
      "<br><b>" + rows.length + (rows.length === 1 ? " title" : " titles") + ", " +
      whole.toFixed(1) + " GB in all.</b></div>";
    // Full to the cap is not the same as broken, and it looks the same from here:
    // a queue that does not move. The disk usually has room; the cap is a number
    // somebody chose, and this says so rather than leaving it to be worked out.
    let room = {};
    let trouble = [];
    try {
      const following = await get("/follow");
      room = (following.followers || [])[0] || {};
      trouble = following.trouble || [];
    } catch (e) {
      room = {};
    }
    // What would not come, and how many times it has not come. A row that sits at
    // the top of the queue and never moves is either a file that keeps failing or a
    // disk with no room, and both look identical from here.
    if (trouble.length) {
      const bad = document.createElement("div");
      bad.className = "traffic";
      const head = document.createElement("div");
      head.className = "trow thead";
      head.innerHTML = "<span>Would not come</span><b>Tries</b><b>When</b>";
      bad.appendChild(head);
      trouble.slice(0, 6).forEach((t) => {
        const el = document.createElement("div");
        el.className = "trow";
        const name = document.createElement("span");
        name.textContent = (t.name || "?") + "  ·  " + (t.why || "");
        const times = document.createElement("b");
        times.textContent = t.times || 1;
        const when = document.createElement("b");
        const ago = Math.max(0, Math.floor(Date.now() / 1000) - (t.when || 0));
        when.textContent = ago < 90 ? "just now"
          : ago < 3600 ? Math.round(ago / 60) + " min ago"
          : Math.round(ago / 3600) + " h ago";
        el.appendChild(name);
        el.appendChild(times);
        el.appendChild(when);
        bad.appendChild(el);
      });
      box.appendChild(bad);
    }
    const held = (room.room || {});
    if (held.cap && held.gb && held.gb / held.cap > 0.97) {
      const full = document.createElement("div");
      full.className = "note";
      full.innerHTML = "<b>It is full to its cap</b> - " + held.gb + " GB of the " +
        held.cap + " GB it is allowed" +
        (held.free ? ", on a disk with " + Math.round(held.free) + " GB free" : "") +
        ". Everything it takes from here deletes something older. Raise the cap on " +
        "Remote computer if you want it to hold more.";
      box.appendChild(full);
    }
    const list = document.createElement("div");
    list.className = "traffic queue";
    const head = document.createElement("div");
    head.className = "trow thead";
    head.innerHTML = "<span>Next</span><b>GB</b><b>Why</b>";
    list.appendChild(head);
    // All of it. A queue shown ten rows deep is a queue nobody can check, and the far
    // end is where a file sits for a day without anybody being able to see it there.
    rows.forEach((row, i) => {
      const el = document.createElement("div");
      el.className = "trow";
      const name = document.createElement("span");
      name.textContent = (i + 1) + ".  " + (row.title || row.key);
      const gb = document.createElement("b");
      gb.textContent = row.gb ? row.gb.toFixed(2) : "";
      const why = document.createElement("b");
      // Why it is here, and whose viewing put it there. A list of titles and sizes
      // answers what and nothing else; when the order looks wrong it is the reason
      // that is wrong, and it cannot be argued with unless it is on the screen.
      // The one being fetched said only that it was being fetched, and dropped the
      // reason it is wanted at all - which is the column's whole job, and the one
      // row where somebody is most likely to be asking.
      const reason = row.why || (row.hot ? "on a screen now" : "");
      why.textContent = [row.now ? "being fetched" : "", reason, row.who]
        .filter(Boolean).join("  ·  ");
      el.appendChild(name);
      el.appendChild(gb);
      el.appendChild(why);
      list.appendChild(el);
    });
    box.appendChild(list);
    into.appendChild(box);
  }

  async function viewReports() {
    // opened from a notice or from the count beside the gear: the same page as the
    // gear opens, standing on its Reports tab
    tab = "reports";
    await window.viewSettings();
  }
  window.viewReports = viewReports;

  window.viewSettings = async function () {
    if (CFG && CFG.guest) {
      // the library settings are not readable by a guest, and asking would only 403
      cfg = {};
      if (tabs().every((t) => t[0] !== tab)) tab = "subs";
      return render();
    }
    cfg = await get("/library/config");
    cfg = await post("/library/config", {});   // comes back with folder existence checks
    render();
  };
})();
