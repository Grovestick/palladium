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
  let cfg = null;
  let picking = null;          // which list the folder picker is adding to
  let tab = "library";

  const OWNER_TABS = [["library", "Library"], ["subs", "Subtitles"],
                      ["quality", "Quality"], ["people", "Users"],
                      ["now", "Now playing"], ["load", "Performance"],
                      ["log", "Watch log"], ["reports", "Reports"],
                      ["machine", "This computer"],
                      ["remote", "Remote computer"]];
  // a guest is a visitor, not an administrator: no folders, nobody to invite, and no
  // friends of ours to browse - only the two screens that are theirs
  const GUEST_TABS = [["subs", "Subtitles"], ["quality", "Quality"],
                      ["reports", "Reports"], ["copy", "Night server"]];
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

  /* the picker walks the server's drives, because a browser file input hands back a
     sandboxed name rather than a usable path */
  async function openPicker(list, path) {
    picking = list;
    const data = await post("/library/browse", { path: path || "" });
    const wrap = $("#picker");
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
  /**
   * Whether this viewer takes part in watch parties.
   *
   * Their own answer: a box in the corner while they are watching is a thing to be
   * able to say no to, and no means nothing arrives rather than nothing shown. The
   * server can still put a notice on the screen - that is the house speaking, not a
   * room of people.
   */
  function partyBlock(main, data) {
    const box = block("Watch parties");
    box.innerHTML +=
      "<div class='note'>The lobby, the party and what is said in them. With this " +
      "off nothing from anybody else reaches this screen; the server can still show " +
      "a notice.</div>";
    const row = document.createElement("div");
    row.className = "addrow subrow";
    row.innerHTML = "<span class='sublabel'>For me</span>";
    [[true, "On"], [false, "Off"]].forEach(([value, text]) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" +
                    ((data.watchParty !== false) === value ? " on" : "");
      b.textContent = text;
      b.onclick = async () => {
        await post("/settings", { watchParty: value });
        toast(value ? "Watch parties on" : "Watch parties off");
        render();
      };
      row.appendChild(b);
    });
    box.appendChild(row);
    main.appendChild(box);
  }

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
    row("Decoder", ENGINES, engine(),
        "ffmpeg on this machine - on the graphics card where there is one, on the "
        + "processor where there is not. Only used for what a screen cannot play "
        + "as it stands.",
        (v) => setPref("engine", v));

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

  async function paneLibrary(main) {
    await panePlayback(main);
    partyBlock(main, await get("/settings").catch(() => ({})));
    await numberingBlock(main);
    accentBlock(main, await get("/settings"));
    const stats = await get("/library/status");
    const bar = block("Index");
    bar.classList.add("stats");
    bar.innerHTML += "<div class='statgrid'>" +
      [["Films", stats.movies], ["Shows", stats.shows], ["Episodes", stats.episodes],
       ["Files", stats.files], ["Probed", stats.probed],
       ["Identified", stats.identified]]
        .map(([k, v]) => "<div><b>" + v + "</b><span>" + k + "</span></div>").join("") +
      "</div>";
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
      "half-watched films it keeps a copy of, whose watchlist, and whose shuffle - " +
      "the next few things casual play would put on. Nothing is kept for a person " +
      "with all three off.<br>Owner marks whoever sits at this machine: their " +
      "viewing is what its own screens file, so one person is not a guest in a " +
      "browser and the machine itself on their own network.</div>";
    const list = document.createElement("div");
    // whoever owns the machine is a viewer too, and has the same answers to give -
    // unless they watch under a key of their own, in which case that row is them
    // and a second one called "You" would be the same person twice
    if (!(data.me || {}).token) {
      list.appendChild(cacheRow(Object.assign(
        { name: "You", token: "me" }, data.me || {})));
    }
    (data.people || []).forEach((w) => list.appendChild(cacheRow(w)));
    box.appendChild(list);
    main.appendChild(box);
  }

  function cacheRow(who) {
    const el = document.createElement("div");
    el.className = "person";
    el.innerHTML = '<div class="pmeta"><b></b><span class="note"></span></div>' +
      // "kind" is what carries the on colour: without it the two buttons saved the
      // setting and looked exactly the same afterwards
      '<button class="btn ghost kind cdeck">Continue watching</button>' +
      '<button class="btn ghost kind clist">Watchlist</button>' +
      '<button class="btn ghost kind ccas">Casual</button>' +
      // whether this person is handed the address this machine answers to on its
      // own network. They always have the way in from outside, which is the one
      // that works from where they are; the other is inside somebody's house.
      '<button class="btn ghost kind clan" title="Tell them the address this ' +
      'server answers to on the home network">Home address</button>' +
      // who the person at this machine is. Not what they may change - that is still
      // a matter of which network a request comes from - but whose viewing the
      // machine's own screens are filing.
      (who.token === "me" ? ""
        : '<button class="btn ghost kind cown">Owner</button>');
    const cost = who.cost || {};
    // what this person costs the other server, so the dear ones can be turned off
    const bill = cost.files
      ? cost.files + " files, " + cost.gb + " GB (" + cost.deck +
        " half-watched, " + cost.list + " on the list" +
        (cost.casual ? ", " + cost.casual + " in the shuffle" : "") + ")"
      : "nothing to keep";
    el.querySelector("b").textContent = who.name + (who.you ? "  (you)" : "");
    el.querySelector(".note").textContent = (who.token === "me" || who.you
      ? "this machine's owner"
      : (who.lastSeen
          ? "last watched " + new Date(who.lastSeen * 1000).toLocaleDateString()
          : "not used yet")) + " · " + bill;
    const own = el.querySelector(".cown");
    if (own) {
      if (who.you) own.classList.add("good");
      own.onclick = async () => {
        if (who.you) {
          if (!confirm("Stop treating " + who.name + " as the person at this " +
                       "machine? What they have watched stays theirs.")) return;
        } else if (!confirm("Treat " + who.name + " as the person at this machine? " +
                            "Anything watched here under no name at all moves to " +
                            "them, and from now on this machine's screens are them.")) {
          return;
        }
        own.disabled = true;
        const back = await post("/invites/owner",
                                { token: who.you ? "" : who.token });
        if (back && back.error) toast(back.error);
        else if (back && back.moved) {
          toast(back.moved.rows + " viewings and " + back.moved.places +
                " places moved");
        }
        render();
      };
    }
    [["cdeck", "cacheDeck"], ["clist", "cacheList"],
     ["ccas", "cacheCasual"], ["clan", "shareLan"]].forEach(([css, name]) => {
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
    return el;
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
    el.querySelector("b").textContent = who.name + (who.you ? "  (you)" : "");
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
  let whoTab = "invite";

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
    [["invite", "Invite"], ["users", "Users"],
     ["friends", "Friends"]].forEach(([id, label]) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" + (whoTab === id ? " on" : "");
      b.textContent = label;
      b.onclick = () => { whoTab = id; render(); };
      bar.appendChild(b);
    });
    main.appendChild(bar);
    if (whoTab === "users") return paneUsers(main, data);
    // libraries of other people's servers: the other direction of the same subject
    if (whoTab === "friends") return paneFriends(main);
    const box = block("Invite someone");
    box.innerHTML +=
      "<div class='note'>Each person gets their own link. It opens this library in " +
      "a browser or sets up the Android app, and can be revoked on its own. The link " +
      "is the only key, so send it only to people you mean to let in.<br>" +
      "Each also has a five-character code, for a television with no keyboard or a " +
      "line read out over the telephone: <code>&lt;address&gt;/i/CODE/open</code> " +
      "opens the library, <code>/i/CODE.apk</code> fetches the app. The code stands " +
      "for the link and is worth as much.</div>";
    const list = document.createElement("div");
    (data.people || []).forEach((w) => list.appendChild(personRow(w, render)));
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
    try { data = await get("/watching"); } catch (e) { /* server restarting */ }
    into.innerHTML = "";
    if (totals) {
      /* What the house is using altogether. One viewer's rate says whether that
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
    if (!data.live.length) {
      const none = document.createElement("div");
      none.className = "note";
      none.textContent = "Nobody is watching anything at the moment.";
      into.appendChild(none);
    }
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
      const on = [w.device, w.kind || w.client].filter(Boolean)
        .filter((v, i, all) => all.indexOf(v) === i).join(" · ");
      // "android 0.14.56 tv" is the app's own name for itself; the version is the
      // part worth reading here, since the kind of device is said beside it
      const build = (w.app || "").match(/\d+\.\d+\.\d+/);
      el.querySelector(".pmeta .note").textContent = [
        w.who,
        [on, build ? "v" + build[0] : ""].filter(Boolean).join(" "),
        began ? "since " + began : "",
        // a file on its way to the machine that keeps copies is not being watched:
        // it has no player, no place in the film and no picture size worth naming
        w.state === "syncing" ? "" :
          w.state === "paused" ? "paused"
            : w.state === "buffering" ? "buffering" : "playing",
        w.state === "syncing" ? "" : at,
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
   * page that sends anything out of the house, and a question like that should be
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
  async function drawServers(codeBox, followBox, keyBox) {
    let said = {};
    try {
      said = await get("/follow");
    } catch (e) { return; }
    const one = said.follow || {};
    const state = said.state || {};
    const mine = said.mine || {};
    // the machine following this one, as it last announced itself: the button above
    // and the note below both read it, so it is settled before either
    const other = said.standby || {};
    const again = () => drawServers(codeBox, followBox, keyBox);
    const put = async (what) => { await post("/follow", what); };

    /* ---- what this machine is called, and where it answers ---- */
    // The code another server needs is on Remote computer now, with the rest of what
    // is about another machine. What stays here is what this one is: its name and the
    // port it answers on.
    codeBox.innerHTML = "<h3>Name and port</h3>";
    keyBox.innerHTML = "";

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
    codeBox.appendChild(nameRow);
    const nameNote = document.createElement("div");
    nameNote.className = "note";
    nameNote.textContent = said.hostname
      ? "Empty means this computer's own name, which is " + said.hostname + "."
      : "Leave it empty to use the computer's own name.";
    codeBox.appendChild(nameNote);

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
    codeBox.appendChild(portrow);
    if (said.portWanted && said.port && said.portWanted !== said.port) {
      const wait = document.createElement("div");
      wait.className = "note";
      wait.textContent = "Answering on " + said.port +
        " until this server is started again.";
      codeBox.appendChild(wait);
    }

    // The line another machine is given: this address and this key together. Shown
    // rather than hidden behind a button - somebody setting a second server up wants
    // to read it off the screen, not go looking for it.
    const codeRow = document.createElement("div");
    codeRow.className = "addrow subrow";
    codeRow.innerHTML = "<span class='sublabel'>Setup line</span>";
    const codeField = document.createElement("input");
    codeField.type = "text";
    codeField.readOnly = true;
    codeField.value = mine.key ? (mine.where + "#" + mine.key) : "";
    codeField.placeholder = "no key made yet";
    codeField.onclick = () => codeField.select();
    codeRow.appendChild(codeField);
    keyBox.appendChild(codeRow);

    const codeButtons = document.createElement("div");
    codeButtons.className = "addrow";
    const copy = document.createElement("button");
    copy.className = "btn ghost";
    copy.textContent = "Copy";
    copy.onclick = () => {
      if (!codeField.value) return toast("There is no key yet");
      copyLine(codeField, "Copied - paste it into the other server");
    };
    codeButtons.appendChild(copy);
    const make = document.createElement("button");
    make.className = "btn ghost";
    make.textContent = mine.key ? "New key" : "Make a key";
    make.onclick = async () => {
      if (mine.key &&
          !confirm("A new key stops the machine using the old one. Carry on?")) return;
      const back = await post("/follow/key", { again: !!mine.key });
      if (!back || !back.key) return toast("Could not make a key");
      again();
    };
    codeButtons.appendChild(make);
    // and a knock on the machine that follows this one, which is the only way to
    // find out that it is there before somebody's evening depends on it
    const tryIt = document.createElement("button");
    // green when the other machine has been heard from lately: a state of the world
    // shown without anybody having to press anything
    const heard = other.seen && (Date.now() / 1000 - other.seen) < 300;
    tryIt.className = "btn ghost" + (heard ? " good" : "");
    tryIt.textContent = "Test";
    const tried = document.createElement("div");
    tried.className = "note";
    tryIt.onclick = async () => {
      tryIt.disabled = true;
      tried.textContent = "Knocking…";
      let back = {};
      try {
        back = await get("/follow/test");
      } catch (e) {
        back = { follower: { ok: false, said: "This server did not answer." } };
      }
      tryIt.disabled = false;
      const how = back.follower || {};
      tryIt.className = "btn ghost" + (how.ok ? " good" : " bad");
      tried.textContent = how.said || "Nothing to test.";
    };
    codeButtons.appendChild(tryIt);
    keyBox.appendChild(codeButtons);
    keyBox.appendChild(tried);

    const codeNote = document.createElement("div");
    codeNote.className = "note";
    codeNote.textContent = mine.key
      ? "Paste it into the other computer under This computer, Follow another server. "
        + "That machine may then read this library and keep copies of it, so it can "
        + "answer while this one is off. Anybody holding this line can do that, so "
        + "hand it over the way you would a key - and New key stops the old one."
      : "Another computer can keep copies of this library and answer while this one "
        + "is off. It needs a key of yours to do it: make one, and paste the line "
        + "into that machine.";
    keyBox.appendChild(codeNote);

    // How much of this library that machine may hold. Lending somebody a copy is not
    // lending them the whole disk, and the machine doing the copying should not be
    // the only one with a say in how much it takes.
    const capRow = document.createElement("div");
    capRow.className = "addrow subrow";
    capRow.innerHTML = "<span class='sublabel'>Most it may use</span>";
    const capBox = document.createElement("input");
    capBox.type = "text";
    capBox.inputMode = "numeric";
    capBox.value = mine.cap ? String(mine.cap) : "";
    capBox.placeholder = "no limit from here";
    capBox.onchange = async () => {
      const gb = Math.max(0, parseFloat(capBox.value.replace(",", ".")) || 0);
      await post("/library/config", { followerCap: gb });
      toast(gb ? "That machine may use " + gb + " GB"
               : "No limit from this end");
      again();
    };
    capRow.appendChild(capBox);
    keyBox.appendChild(capRow);
    const capNote = document.createElement("div");
    capNote.className = "note";
    capNote.textContent = "Gigabytes. The other machine has a limit of its own, and " +
      "the smaller of the two is what it keeps to - so this one can only ever make " +
      "it take less. Empty means it decides for itself.";
    keyBox.appendChild(capNote);

    // Which machines are actually following this one, and the way to stop one of
    // them. Stopping is about a machine, not about the key: rotating the key would
    // stop every follower at once, and this stops the one named.
    const stopped = said.blocked || [];
    const seen = said.followers || [];
    if (seen.length || stopped.length) {
      const head = document.createElement("div");
      head.className = "note";
      head.style.margin = "16px 0 4px";
      head.textContent = "Machines following this one";
      keyBox.appendChild(head);
    }
    const draw = (f, isStopped) => {
      const row = document.createElement("div");
      row.className = "addrow";
      row.style.alignItems = "center";
      const who = document.createElement("span");
      who.style.flex = "1 1 auto";
      const heard = f.ago === undefined ? ""
        : f.ago < 90 ? "just now"
        : f.ago < 3600 ? Math.round(f.ago / 60) + " min ago"
        : Math.round(f.ago / 3600) + " h ago";
      who.textContent = (f.name || "a server") + "  ·  " + (f.where || "") +
        (heard ? "  ·  heard " + heard : "") +
        (isStopped ? "  ·  stopped" : "");
      row.appendChild(who);
      const act = document.createElement("button");
      act.className = "btn ghost" + (isStopped ? "" : " bad");
      act.textContent = isStopped ? "Let it back in" : "Stop it";
      act.onclick = async () => {
        if (!isStopped &&
            !confirm("Stop " + (f.name || "that machine") +
                     "? It keeps its key but is refused until you let it back in.")) {
          return;
        }
        act.disabled = true;
        await post("/follow/stop", { where: f.where, allow: !!isStopped });
        toast(isStopped ? "Let back in" : "Stopped");
        again();
      };
      row.appendChild(act);
      keyBox.appendChild(row);
    };
    const isOut = (w) => stopped.some((b) => String(b).replace(/\/+$/, "") ===
                                             String(w || "").replace(/\/+$/, ""));
    seen.forEach((f) => draw(f, isOut(f.where)));
    // one that has been stopped and has given up announcing itself still needs a way
    // back in, so it is listed from the block list alone
    stopped.filter((w) => !seen.some((f) => isOut(f.where) && f.where === w))
           .forEach((w) => draw({ where: w, name: "" }, true));

    // Who is following this one, and what has gone to them, are both on Now
    // playing: they are what this server is doing, not how it is set up. What is
    // left here is the setting-up.

    /* ---- and this server following another ---- */
    followBox.innerHTML = "<h3>Follow another server</h3>";

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
    };

    // One line carries both halves: the other server's address and its key.
    const paste = document.createElement("div");
    paste.className = "addrow subrow";
    paste.innerHTML = "<span class='sublabel'>Paste setup line</span>";
    const pbox = document.createElement("input");
    pbox.type = "text";
    pbox.placeholder = "http://192.168.1.20:8765#key";
    pbox.onchange = async () => {
      const line = pbox.value.trim();
      if (!line) return;
      const cut = line.indexOf("#") >= 0 ? line.indexOf("#") : line.indexOf(" ");
      if (cut < 0) return toast("That line has no key on it");
      await put({ master: line.slice(0, cut).trim().replace(/\/$/, ""),
                  key: line.slice(cut + 1).trim() });
      pbox.value = "";
      again();
    };
    paste.appendChild(pbox);
    followBox.appendChild(paste);

    const onoff = document.createElement("div");
    onoff.className = "addrow subrow";
    onoff.innerHTML = "<span class='sublabel'>Follow</span>";
    const b = document.createElement("button");
    b.className = "btn ghost kind" + (one.on ? " on" : "");
    b.textContent = one.on ? "On" : "Off";
    b.onclick = async () => { await put({ on: !one.on }); again(); };
    onoff.appendChild(b);
    followBox.appendChild(onoff);

    row("Its address", one.master, "http://192.168.1.20:8765",
        (v) => put({ master: v }));
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
    row("Its key", one.key, "a key from that server, or your own invitation",
        (v) => put({ key: v }));
    row("Keep copies in", one.folder, "D:\\Palladium cache",
        (v) => put({ folder: v }));

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
    row("Hours of casual play", String(one.casualHours), "2",
        (v) => put({ casualHours: v }));
    row("Disk to use, GB", String(one.cap), "200", (v) => put({ cap: v }));

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

    // Whether watchlists may fill while people are up. What somebody is watching
    // this minute always waits for the night; this is about the rest.
    const dayRow = document.createElement("div");
    dayRow.className = "addrow subrow";
    dayRow.innerHTML = "<span class='sublabel'>By day</span>";
    [[true, "Watchlists may fill"], [false, "Nothing until night"]]
      .forEach(([on, label]) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" +
          ((one.listByDay !== false) === on ? " on" : "");
        b.textContent = label;
        b.onclick = async () => { await put({ listByDay: on }); again(); };
        dayRow.appendChild(b);
      });
    followBox.appendChild(dayRow);
    const dayNote = document.createElement("div");
    dayNote.className = "note";
    dayNote.textContent = "What somebody starts watching now is fetched at night " +
      "either way - by then this machine is the one that will be awake.";
    followBox.appendChild(dayNote);

    // And whether the machine this one follows may change these settings from
    // there, so nobody has to walk to a computer in a cupboard.
    const mgmtRow = document.createElement("div");
    mgmtRow.className = "addrow subrow";
    mgmtRow.innerHTML = "<span class='sublabel'>Managed from the house</span>";
    const mgmt = document.createElement("button");
    mgmt.className = "btn ghost kind" + (one.allowRemote ? " on" : "");
    mgmt.textContent = one.allowRemote ? "Allowed" : "Not allowed";
    mgmt.onclick = async () => {
      await put({ allowRemote: !one.allowRemote });
      again();
    };
    mgmtRow.appendChild(mgmt);
    followBox.appendChild(mgmtRow);
    const mgmtNote = document.createElement("div");
    mgmtNote.className = "note";
    mgmtNote.textContent = "The server this machine follows may then change these " +
      "settings without anybody walking to it. Only that machine, and only by its " +
      "address.";
    followBox.appendChild(mgmtNote);
    // An hour before the other machine sleeps, and through the night, it takes copies
    // of everything anybody is in the middle of - not only what is playing.
    row("Night from, hour", String(one.nightFrom), "22",
        (v) => put({ nightFrom: v }));
    row("Night until, hour", String(one.nightTo), "8", (v) => put({ nightTo: v }));
    // What the other server hands to viewers outside the house. Empty is right when
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
        "copied ahead; and from an hour before it sleeps, everything anybody is " +
        "half-way through.";
    followBox.appendChild(note);

    // what the cache holds, against what it was allowed
    const kept = said.kept || {};
    const bar = document.createElement("div");
    bar.className = "keptbar";
    const fill = document.createElement("span");
    fill.style.width = Math.round((kept.share || 0) * 100) + "%";
    bar.appendChild(fill);
    followBox.appendChild(bar);
    const held = document.createElement("div");
    held.className = "note";
    held.textContent = (kept.gb || 0) + " of " + (kept.cap || 0) + " GB kept (" +
      Math.round((kept.share || 0) * 100) + "%)  ·  " + (kept.files || 0) +
      " file" + ((kept.files === 1) ? "" : "s") +
      (kept.free ? "  ·  " + kept.free + " GB free on that disk" : "");
    followBox.appendChild(held);

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
      setTimeout(() => drawServers(codeBox, followBox, keyBox), 4000);
    };
    testRow.appendChild(now);
    followBox.appendChild(testRow);
    followBox.appendChild(result);
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
    // The app itself, for a server that has none beside it. An installed server
    // carries it; one built from source does not, because an APK is not source. It
    // can be fetched - but only because somebody here says so, which is why this is
    // a button and not something that happens when a guest opens the install page.
    let app = {};
    try {
      app = await get("/app/version");
    } catch (e) { app = {}; }
    if (!app.here) {
      const row = document.createElement("div");
      row.className = "addin";
      const words = document.createElement("div");
      words.className = "addinwords";
      words.innerHTML = "<b>The Android app</b><span class='note'>This server has no " +
        "copy of the app, so it cannot hand one to a phone or a television. An " +
        "installed server carries it; one built from source does not, because an APK " +
        "is not source and does not belong in a repository.<br><br>" +
        "Two ways to give it one. <b>Fetch it</b> takes the published build from " +
        "palladium.video - about 17 MB, and nothing about this machine goes with the " +
        "request. Or build it yourself from the source at " +
        "<a href='https://github.com/grovestick/palladium' target='_blank' " +
        "rel='noopener'>github.com/grovestick/palladium</a> and put " +
        "<code>palladium.apk</code> beside the program, which is the answer if you " +
        "would rather this machine asked nobody for anything.</span>";
      const state = document.createElement("div");
      state.className = "addinstate";
      const act = document.createElement("button");
      act.className = "btn ghost";
      if (app.getting) {
        state.textContent = "fetching…";
        act.textContent = "fetching";
        act.disabled = true;
        setTimeout(() => drawAddins(into), 5000);
      } else {
        state.textContent = "not here";
        act.textContent = "Fetch it";
        act.onclick = async () => {
          act.disabled = true;
          act.textContent = "fetching…";
          await post("/app/fetch", {});
          setTimeout(() => drawAddins(into), 3000);
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
    const newer = t.latest && t.version !== t.latest;
    if (t.getting) {
      ks.textContent = "fetching…";
      ka.textContent = "fetching";
      ka.disabled = true;
      setTimeout(() => drawAddins(into), 4000);
    } else {
      ks.textContent = t.version ? ("version " + t.version + (newer ?
        " · " + t.latest + " is out" : ""))
        : t.here ? "the copy that came with the server" : "not here";
      ka.textContent = t.version ? (newer ? "Update" : "Fetch again") : "Get them";
      ka.onclick = async () => {
        ka.disabled = true;
        ka.textContent = "fetching…";
        const back = await post("/machine/addons", { tools: "fetch" });
        if (back && back.error) toast(back.error);
        setTimeout(() => drawAddins(into), 1500);
      };
    }
    kit.appendChild(document.createElement("div"));   // where a tick would be
    kit.appendChild(kw);
    kit.appendChild(ks);
    kit.appendChild(ka);
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
   * machine up, is anybody watching, and can the house be reached from outside. Three
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
    // was beside it - a drawing of the house with the text loose over the top.
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
             Array((said.syncing || 0)).fill({ how: "syncing" })) }]
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
    // it; on the machine that keeps copies it is the house - which had been drawn as
    // "night server, none, not answering", a box describing a follower it does not
    // have and will never have.
    let other = (follow && follow.standby) || {};
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
      .filter((c) => !/following/i.test(String(c.said || c.kind || "")))
      .sort((a, b) => (b.when || 0) - (a.when || 0))
      .slice(0, 4)
      .map((c) => Object.assign({}, c, {
        shown: c.name || (c.where === "127.0.0.1" ? "this computer" : c.where),
      }));
    const mbit = live.reduce((n, r) => n + (r.mbit || 0), 0);
    const door = !!other.outside;

    // The house as it is actually wired. Everything hangs off the router - both
    // machines and every screen - so it sits in the middle with room around it, and
    // the way in from outside comes down into it. The line between the two servers
    // is the only one that is not the router's doing: it is one machine copying from
    // the other, and it is drawn because that is the thing worth watching.
    const rows = Math.max(screens.length, 1);
    const W = 820;
    const H = Math.max(300, 150 + rows * 58);
    const gate = (machine && machine.gateway) || "";
    const box = [318, 100, 194, 52];         // the router, in the middle of it all
    const gx = box[0] + box[2] / 2;
    const gy = box[1] + box[3] / 2;
    const heart = [286, 214, 232, 74];       // this machine, below the router
    const copyBox = [592, 214, 214, 74];     // and the one that keeps copies
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
    const outY = 4;
    const outH = Math.max(30 + outLines.length * 15, 44);
    g += node(316, outY, 198, outH, "OUTSIDE", outLines, !!(mine || theirs));
    g += wire([gx, outY + outH], [gx, box[1]], !!(mine || theirs), "");

    g += "<g class='wclick' data-open='" + (gate ? "http://" + gate : "") + "'>" +
         node(box[0], box[1], box[2], box[3], "ROUTER",
              [gate || "not found", "press to open"], !!gate) +
         "</g>";

    // both machines hang off it
    g += node(heart[0], heart[1], heart[2], heart[3],
              (machine && machine.name) || "THIS SERVER", [
      ((machine && machine.lan) || "").replace(/^https?:\/\//, "") ||
        ("port " + ((machine && machine.port) || "?")),
      "build " + ((machine && machine.server) || "?") +
        "   " + ((machine && machine.network) || "?"),
      live.length ? live.length + " watching - " + mbit.toFixed(1) + " Mbit"
                  : "nobody watching",
    ], true);
    const copying = !!state.copying || sync.length > 0;
    g += node(copyBox[0], copyBox[1], copyBox[2], copyBox[3],
              other.name || (followingUp ? "the house" : "night server"), [
      other.where ? other.where.replace(/^https?:\/\//, "") : "none",
      (other.build ? "build " + other.build + "   " : "") +
        (other.alive ? "seen " + shortly(other.seen) : "not answering"),
      copying ? (followingUp ? "taking a copy from it" : "taking a copy")
              : (followingUp ? "the library is there" : "standing by"),
    ], other.where ? !!other.alive : 0);

    // Down into this machine, and across into the one that keeps copies - to the
    // short side of it, which is the edge facing the router.
    // Drawn from this machine up to the router, because that is the way the film
    // travels: the house sends, the router passes it on. The dashes ran the other
    // way, which read as the router feeding the server.
    g += wire([heart[0] + heart[2] / 2, heart[1]], [gx - 30, box[1] + box[3]],
              live.length > 0, "", true);
    g += wire([box[0] + box[2], gy], [copyBox[0], copyBox[1] + 22],
              !!other.alive, "");

    // one machine filling the other, which is the only line the router did not make
    // and the dashes run the way the film travels: out of the house into the copy,
    // whichever of the two this drawing was made on
    g += followingUp
      ? wire([copyBox[0], copyBox[1] + copyBox[3] - 18],
             [heart[0] + heart[2], heart[1] + heart[3] - 18], copying,
             copying ? "copying" : "", true)
      : wire([heart[0] + heart[2], heart[1] + heart[3] - 18],
             [copyBox[0], copyBox[1] + copyBox[3] - 18], copying,
             copying ? "copying" : "");

    // and the screens, which reach the house the same way everything else does
    if (screens.length) {
      screens.forEach((c, i) => {
        const y = 26 + i * 58;
        const row = live.find((r) => r.address === c.where);
        g += node(14, y, 190, 44, c.shown || c.kind || "screen",
                  [(c.kind || "") + (c.version ? "  " + c.version : "")],
                  row ? true : 0);
        g += wire([204, y + 22], [box[0], gy], !!row,
                  row ? (row.mbit || 0).toFixed(1) + " Mbit" : "", true);
      });
    } else {
      g += node(14, 100, 190, 44, "no screens", ["nothing is on"], 0);
      g += wire([204, 122], [box[0], gy], false, "", true);
    }

    into.innerHTML =
      "<svg viewBox='0 0 " + W + " " + H + "' class='wiring' " +
      "preserveAspectRatio='xMidYMid meet'>" +
      "<rect width='" + W + "' height='" + H + "' fill='#06080b'/>" +
      g + "</svg>";
    // the one box in the drawing that is not this software: pressing it opens the
    // page it serves, which is where a port forward is set and where it is undone
    into.querySelectorAll(".wclick[data-open]").forEach((el) => {
      const to = el.getAttribute("data-open");
      if (!to) return;
      el.style.cursor = "pointer";
      el.onclick = () => window.open(to, "_blank", "noopener");
    });
  }

  /* The other machine: the one this server follows, or the one that follows it.
   *
   * Everything here is about a computer that is not this one - the address it is
   * reached at, what it is asked to keep, the keys it needs to do that, and the
   * build it is running. They were mixed in with this machine's own settings, where
   * the two kinds of thing read as one long list of switches.
   */
  async function paneRemote(main) {
    // Two halves of one subject, and they are opposites: what this server lends to
    // another machine, and what it borrows from one. A rule between them, so nobody
    // reads a setting from the wrong side of the arrangement.
    // What this machine is called and where it answers: it is the half of the
    // arrangement the other computer has to be told, so it reads here rather than
    // among this machine's own switches.
    const codeCard = block("");
    main.appendChild(codeCard);

    const lend = document.createElement("div");
    lend.className = "note";
    lend.style.cssText = "margin:0 0 8px;letter-spacing:.08em;text-transform:uppercase";
    lend.textContent = "Another computer copies from this one";
    main.appendChild(lend);

    const keyCard = block("Let another computer keep copies");
    const keyBox = document.createElement("div");
    keyCard.appendChild(keyBox);
    main.appendChild(keyCard);

    const rule = document.createElement("div");
    rule.style.cssText =
      "border-top:1px solid var(--line);margin:26px 0 14px";
    main.appendChild(rule);
    const borrow = document.createElement("div");
    borrow.className = "note";
    borrow.style.cssText = "margin:0 0 8px;letter-spacing:.08em;text-transform:uppercase";
    borrow.textContent = "This computer copies from another";
    main.appendChild(borrow);

    const followBox = block("");
    main.appendChild(followBox);
    drawServers(codeCard, followBox, keyBox);

    // the keys it needs of its own: subtitles it fetches itself, and the catalogue
    // it looks posters up in when this machine cannot be reached
    let copy = {};
    try {
      copy = await get("/standby");
    } catch (e) {
      copy = {};
    }
    if (copy && copy.where) {
      const keys = block("What it needs of its own");
      keys.innerHTML += "<div class='note'>It fetches subtitles for what it takes " +
        "and looks titles up for itself when this machine cannot be reached. " +
        "Without keys of its own it holds films half the house cannot read, on a " +
        "shelf of grey rectangles.</div>";
      const row = document.createElement("div");
      row.className = "addrow subrow";
      row.innerHTML = "<span class='sublabel'>" + esc(copy.name || "The copy") +
        "</span>";
      [["Send both keys", {}],
       ["Subtitles only", { only: "opensubtitles_key" }],
       ["Catalogue only", { only: "tmdb_key" }]].forEach(([label, body]) => {
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
        row.appendChild(b);
      });
      keys.appendChild(row);
      main.appendChild(keys);
    }
  }

  /* What this computer is set to do about Palladium, as several cards rather than
   * one: the machine itself, the code another server needs, the following of one,
   * the add-ins, and a word to the screens in the house. They were one box with five
   * subjects in it, which read as a list of unrelated switches. */
  async function machinePanel() {
    const all = document.createDocumentFragment();
    // Everything here is drawn from one answer, so it is asked for once - but the
    // page is put together first. A card that is waiting looks like a card; a tab
    // that is waiting looks broken.
    const said = await get("/machine").catch(() => ({}));
    // the drawing goes first: what is on, and what is talking to what
    const map = block("Where it all goes");
    const canvas = document.createElement("div");
    canvas.className = "wirebox";
    map.appendChild(canvas);
    all.appendChild(map);
    drawWiring(canvas);
    const beat = setInterval(() => {
      if (!document.body.contains(canvas)) return clearInterval(beat);
      drawWiring(canvas);
    }, 12000);
    const box = block("This computer");
    all.appendChild(box);
    if (!said.windows) {
      box.innerHTML += "<div class='note'>These settings are for Windows.</div>";
      return all;
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
    line("Reach it from the house", said.firewall,
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

    // When this machine goes off at night. A follower asks for it and starts an hour
    // before, so that what anybody is half-way through is on the other server before
    // this one stops answering.
    const sleeps = document.createElement("div");
    sleeps.className = "addrow subrow";
    sleeps.innerHTML = "<span class='sublabel'>Goes to sleep at</span>";
    const when = document.createElement("input");
    when.type = "text";
    when.placeholder = "23:00";
    when.value = (said && said.sleepAt) || "";
    when.onchange = async () => {
      await post("/library/config", { sleepAt: when.value.trim() });
    };
    sleeps.appendChild(when);
    box.appendChild(sleeps);

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
    const talk = block("A word to the house");
    const say = document.createElement("div");
    say.className = "addrow subrow";
    say.innerHTML = "<span class='sublabel'>Say something</span>";
    const words = document.createElement("input");
    words.type = "text";
    words.placeholder = "Dinner in ten minutes";
    words.title = "Shown across the top of Palladium on every screen in the house";
    say.appendChild(words);
    // and who to: the house by default, or one screen by its address, which is how a
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
    return all;
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
        take.textContent = "Installing";
        note.textContent = "Version " + (answer.version || said.latest) +
          " is installing. This page will lose the server for a moment and the " +
          "program will come back on its own.";
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
    list.forEach((entry, n) => {
      const el = document.createElement("div");
      el.className = "release" + (n ? " older" : "");
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
      box.appendChild(el);
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
       * a fault, deciding it is worth passing on and pressing send is a different
       * act from a machine reporting on itself, and it is asked for rather than
       * assumed. Everything identifying is taken out on the way, the same as always.
       */
      if (data.owner && (r.kind === "error" || r.kind === "crash")) {
        const away = document.createElement("button");
        away.className = "btn ghost sendaway" + (r.sentAway ? " on" : "");
        away.textContent = r.sentAway ? "Sent" : "Send to Palladium";
        away.title = r.sentAway
          ? "Already sent"
          : "Send this one fault to palladium.video";
        away.onclick = async () => {
          if (r.sentAway) return;
          const yes = window.confirm(
            "Send this fault to palladium.video?" + String.fromCharCode(10, 10) +

            "The text goes with titles, file paths, addresses, keys and the names " +
            "of everybody here taken out, along with what build this is and what " +
            "kind of computer it happened on.");
          if (!yes) return;
          away.disabled = true;
          const said = await post("/feedback/send", { id: r.id });
          away.disabled = false;
          toast(said && said.ok ? "Sent"
                : (said && said.why) || "Could not send it");
          if (said && said.ok) viewReports();
        };
        (el.querySelector(".rfix") || el).appendChild(away);
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

    /* The OpenSubtitles key, where subtitles are set rather than buried in Library.
     *
     * It is also what the machine that keeps copies needs: it fetches subtitles for
     * the films it takes, and without a key of its own it takes films nobody in the
     * house can read. One press sends it there.
     */
    if (!guest) {
      const keyBox = block("OpenSubtitles key");
      keyBox.innerHTML += "<div class='note'>What this server searches with. The " +
        "machine that keeps copies needs the same key to fetch subtitles for what " +
        "it takes.</div>";
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
      // and the copy, if there is one
      let copy = {};
      try {
        copy = await get("/standby");
      } catch (e) {
        copy = {};
      }
      if (copy && copy.where) {
        const send = document.createElement("div");
        send.className = "addrow subrow";
        send.innerHTML = "<span class='sublabel'>" + esc(copy.name || "The copy") +
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
        // than a grid of grey rectangles when the house cannot be reached
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
    main.appendChild(lang);

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
    else if (tab === "quality") await paneQuality(main);
    else if (tab === "people") await panePeople(main);
    else if (tab === "now") await paneNow(main);
    else if (tab === "load") await paneLoad(main);
    else if (tab === "log") await paneLog(main);
    else if (tab === "machine") {
      // the build this machine is running and how to change it, then what it is set
      // to do about itself. Neither waits for the other: asking the site how old
      // this build is takes a second, and there is no reason for the rest of the
      // tab to sit blank while it happens
      main.appendChild(await serverUpdate());
      const spot = document.createElement("div");
      main.appendChild(spot);
      machinePanel().then((box) => spot.replaceWith(box)).catch(() => {});
    }
    else if (tab === "remote") await paneRemote(main);
    else if (tab === "reports") await paneReports(main);
    else if (tab === "copy") await paneCopy(main);
    else paneSubs(main);
  }

  /* The other machine, for whoever is watching from away.
   *
   * This server sleeps; the copy does not. A guest cannot be expected to know there
   * is a second address, and a page cannot fetch its way out of a server that is
   * off - so the address is given while this one is still answering, with a link
   * that carries their own key. */
  async function paneCopy(main) {
    const box = block("Night server");
    let said = {};
    try {
      said = await get("/standby");
    } catch (e) {
      said = {};
    }
    const where = said.link || said.outside || said.where || "";
    if (!where) {
      box.innerHTML += "<div class='note'>This server keeps no copy of itself. When " +
        "it is off, it is off.</div>";
      main.appendChild(box);
      return;
    }
    box.innerHTML += "<div class='note'>" + esc(said.name || "Another machine") +
      " holds what you were part-way through and your watchlist, and answers when " +
      "this server does not. It is " + (said.alive ? "awake now" : "not answering " +
      "at the moment") + ".<br>Keep this address - a page cannot find it once this " +
      "server is off.</div>";
    const row = document.createElement("div");
    row.className = "addrow subrow";
    const field = document.createElement("input");
    field.type = "text";
    field.readOnly = true;
    field.className = "plink";
    field.value = where;
    field.onclick = () => field.select();
    row.appendChild(field);
    const copy = document.createElement("button");
    copy.className = "btn ghost";
    copy.textContent = "Copy";
    copy.onclick = () => copyLine(field, "Copied - keep it somewhere");
    row.appendChild(copy);
    const open = document.createElement("button");
    open.className = "btn";
    open.textContent = "Open it";
    open.onclick = () => window.open(where, "_blank", "noopener");
    row.appendChild(open);
    box.appendChild(row);
    main.appendChild(box);
  }

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
   * next time it asks. A machine that has put a look on by itself - the copy after
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

  async function paneQuality(main) {
    const s = await get("/settings");
    await skinBlock(main);
    yourQuality(main, s);
    if (CFG && CFG.guest) return;      // the ceilings below are the owner's business
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
   * Who is watching, right now, and what the house is using altogether.
   *
   * Its own page rather than the tail of the invitations screen: this is the thing
   * somebody opens when a film stutters, and nobody looks for that under People.
   */

  // which viewer the log is filtered to; empty for everyone
  let logWho = "";

  /* What the house has watched, over three windows.

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
  //: whether the whole book was asked for rather than the newest few hundred
  let logAll = false;
  //: showing only what the machine that keeps copies is holding
  let logCopy = false;

  async function paneLog(main) {
    // The log is what has been watched; Versions is what did the watching. Two
    // screens of one question, and neither is big enough to be a tab of its own.
    const tabs = document.createElement("div");
    tabs.className = "addrow subrow";
    [["log", "Watch log"], ["versions", "Versions"]].forEach(([id, label]) => {
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
    await statsBlock(main);
    const box = block("Watch log");
    box.innerHTML += "<div class='note'>Every viewing, newest first. Written from the "
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
    const all = data.watched || [];
    // the newest few hundred, unless the whole book has been asked for
    if ((data.held || 0) > all.length) {
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
    // watch tonight" is the question this page is opened with once the house server
    // is off, and until now it could only be answered by going through the shelves.
    let copies = null;
    try {
      copies = new Set(((await get("/copies")) || {}).keys || []);
    } catch (e) {
      copies = null;                   // nothing follows this server
    }
    if (copies && copies.size) {
      const onlyCopy = document.createElement("button");
      onlyCopy.className = "btn ghost kind" + (logCopy ? " on" : "");
      onlyCopy.textContent = "On the copy";
      onlyCopy.title = "Only what the machine that keeps copies is holding";
      onlyCopy.onclick = () => { logCopy = !logCopy; render(); };
      who.appendChild(onlyCopy);
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
      el.className = "logrow";
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
      el.querySelector(".how").textContent = [
        mins ? mins + " min" : "under a minute",
        pos ? pos + "% in" : "",
        // what it was, then what it calls itself: "Google TV app - Streamer"
        [w.client, w.device].filter(Boolean).join(" - "),
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

  async function paneNow(main) {
    const sum = document.createElement("div");
    sum.className = "livetotals";
    main.appendChild(sum);
    const live = document.createElement("div");
    main.appendChild(live);
    drawLive(live, sum);
    // What the other machine is fetching belongs on this page too - it is the other
    // half of "what is this server doing right now" - but under its own heading,
    // because nobody is watching it.
    await nowCopying(main);
    await nowFollowers(main);
  }

  /* Who is following this server, and what has already gone to them. */
  async function nowFollowers(into) {
    let said = {};
    try {
      said = await get("/follow");
    } catch (e) {
      return;
    }
    const following = said.followers || [];
    if (!following.length) return;
    const box = block("The machine that keeps copies");
    const list = document.createElement("div");
    list.className = "traffic";
    const head = document.createElement("div");
    head.className = "trow thead";
    head.innerHTML = "<span>Following this server</span><b>Heard</b><b>Build</b>";
    list.appendChild(head);
    const gb = (n) => (n || 0).toFixed(n && n < 10 ? 1 : 0) + " GB";
    following.forEach((f) => {
      const el = document.createElement("div");
      el.className = "trow";
      const name = document.createElement("span");
      const dot = document.createElement("i");
      dot.className = "onslave";
      dot.style.position = "static";
      dot.style.display = "inline-block";
      dot.style.marginRight = "8px";
      if (!f.alive) dot.style.background = "#7d2e2e";
      name.appendChild(dot);
      name.appendChild(document.createTextNode(
        (f.name || "a server") + "  ·  lan " + f.where +
        (f.outside ? "  ·  wan " + f.outside : "")));
      // How full that machine is. It is the only one that can measure its own disk,
      // and "is there room for tonight" is the question anybody looking at this row
      // is actually asking.
      const room = f.room || {};
      if (room.cap || room.gb || room.free) {
        const disk = document.createElement("div");
        disk.className = "note";
        disk.style.margin = "3px 0 0 17px";
        disk.textContent =
          gb(room.gb) + " kept" +
          (room.files ? " in " + room.files + " files" : "") +
          (room.cap ? "  ·  " + gb(room.cap) + " allowed" +
                      (room.gb ? " (" + Math.round(100 * room.gb / room.cap) +
                                 "% of it)" : "") : "") +
          (room.free ? "  ·  " + gb(room.free) + " free on the disk" : "");
        name.appendChild(disk);
      }
      const heard = document.createElement("b");
      heard.textContent = f.ago < 90 ? "just now"
        : f.ago < 3600 ? Math.round(f.ago / 60) + " min ago"
        : Math.round(f.ago / 3600) + " h ago";
      const build = document.createElement("b");
      build.textContent = f.build || "";
      el.appendChild(name);
      el.appendChild(heard);
      el.appendChild(build);
      list.appendChild(el);
    });
    box.appendChild(list);

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
      h.innerHTML = "<span>Transferred</span><b>GB</b><b>Where</b>";
      sent.appendChild(h);
      (log.copies || []).slice(0, many || 10).forEach((row) => {
        const el = document.createElement("div");
        el.className = "trow";
        const name = document.createElement("span");
        name.textContent = (row.title || row.key || "a file") + "  ·  " +
          when(row.when);
        const gb = document.createElement("b");
        gb.textContent = (row.gb || 0).toFixed(2);
        const to = document.createElement("b");
        to.textContent = row.to || row.address || "";
        el.appendChild(name);
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
    into.appendChild(box);
  }

  /* What the machine that keeps copies is taking, and what is next. */
  async function nowCopying(into) {
    let said = {};
    try {
      said = await get("/follow/queue");
    } catch (e) {
      return;                            // nothing follows this server
    }
    // A subtitle is a few kilobytes travelling with its film, not a place in the
    // queue: three of them under one title made a numbered list read like a mess.
    const rows = (said.queue || []).filter((r) => !r.here && !r.side);
    if (!rows.length) return;
    const sides = (said.queue || []).filter((r) => !r.here && r.side).length;
    const box = block("Being copied");
    box.innerHTML += "<div class='note'>What the machine that keeps copies is " +
      "taking, in the order it will take it. Whoever is watching leads it; then " +
      "anything moved up by hand." +
      (sides ? "  " + sides + " subtitle" + (sides > 1 ? "s" : "") +
               " travel with them." : "") + "</div>";
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
    list.className = "traffic";
    const head = document.createElement("div");
    head.className = "trow thead";
    head.innerHTML = "<span>Next</span><b>GB</b><b>Why</b>";
    list.appendChild(head);
    rows.slice(0, 10).forEach((row, i) => {
      const el = document.createElement("div");
      el.className = "trow";
      const name = document.createElement("span");
      name.textContent = (i + 1) + ".  " + (row.title || row.key);
      const gb = document.createElement("b");
      gb.textContent = row.gb ? row.gb.toFixed(2) : "";
      const why = document.createElement("b");
      why.textContent = row.hot ? "watching" : row.pinned ? "moved up" : "";
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
