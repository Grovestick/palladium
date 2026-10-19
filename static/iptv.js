/* Live TV from the owner's own IPTV playlist: a plugin, with pd_iptv.py on the server.
   Delete both files and it is gone; index.html's tag for this file then finds nothing,
   which is harmless. It adds the My channels row, the row's page, and its settings. */
(function () {
  if (typeof CATS === "undefined") return;
  CATS.push({ id: "mylive", title: "My channels", by: "popularity",
              path: (sec) => ["/library/live/own", {}],
              open: () => viewMyChannels() });

  /* The owner's own playlist, whole: a group to narrow it to and words to look for,
     both asked of the server - a provider's list runs to thousands of channels. */
  async function viewMyChannels(group = "", words = "") {
    pushView(() => viewMyChannels(group, words));
    $("#back").classList.add("on");
    CTX = null;
    onResize = null;
    main.innerHTML = '<div class="empty">Loading&hellip;</div>';
    let groups = [];
    try { groups = items(await api("/library/live/own/groups", {}, null)); } catch (e) { groups = []; }
    main.innerHTML = "";
    const h = document.createElement("h2");
    h.innerHTML = '<span class="ct">My channels</span><span class="count"></span>';
    main.appendChild(h);
    const bar = document.createElement("div");
    bar.className = "sortbar";
    const pick = document.createElement("select");
    pick.className = "sortpick";
    pick.add(new Option("All groups", ""));
    groups.forEach((g) => pick.add(new Option(g.title + "  (" + g.count + ")", g.title)));
    pick.value = group;
    const find = document.createElement("input");
    find.type = "search";
    find.className = "sortpick";
    find.placeholder = "Channel name";
    find.value = words;
    bar.append(pick, find);
    main.appendChild(bar);
    let g = document.createElement("div");
    main.appendChild(g);
    let seq = 0;
    const load = async () => {
      const mine = ++seq;
      // no group and no words: the groups, each opening to its channels
      const groupsOnly = !pick.value && !find.value.trim();
      let c = {};
      try {
        c = await api("/library/live/own", { group: pick.value, words: find.value.trim(),
                                             start: 0, count: 300,
                                             ...(groupsOnly ? { more: 1 } : {}) }, null);
      } catch (e) { c = {}; }
      if (mine !== seq) return;
      const list = items(c);
      const total = c.totalSize || list.length;
      const what = groupsOnly ? " groups" : " channels";
      h.querySelector(".count").textContent = list.length < total
        ? list.length + " of " + total + what : total + what;
      const fresh = list.length ? grid(list, 0)
        : Object.assign(document.createElement("div"), { className: "empty", textContent: "No channels" });
      main.replaceChild(fresh, g);
      g = fresh;
    };
    pick.onchange = load;
    let wait = 0;
    find.oninput = () => { clearTimeout(wait); wait = setTimeout(load, 250); };
    await load();
  }

  // a group circle on the row opens that group's channels
  window.PD_OPEN_GROUP = (it) => viewMyChannels(it.liveGroup);

  // its own tab in Settings, with every setting it has
  window.PD_TABS = window.PD_TABS || [];
  window.PD_TABS.push(["iptv", "IP-TV", iptvTab]);

  /* A row of buttons, each lit or not, that redraws itself and nothing else: a press
     saves and lights the button where it is. Redrawing the whole page for each one
     asked the server for the settings once per block on it - seconds a press. */
  function chipRow(label, items, isOn, onPress) {
    const row = document.createElement("div");
    row.className = "addrow subrow";
    row.style.flexWrap = "wrap";
    const draw = () => {
      row.innerHTML = "";
      const lbl = document.createElement("span");
      lbl.className = "sublabel";
      lbl.textContent = label;
      row.appendChild(lbl);
      items().forEach((one) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" + (isOn(one) ? " on" : "");
        b.textContent = one.text;
        b.onclick = async () => {
          b.disabled = true;
          await onPress(one);
          draw();
        };
        row.appendChild(b);
      });
    };
    draw();
    row.redraw = draw;
    return row;
  }

  function note(text) {
    const n = document.createElement("div");
    n.className = "note";
    n.textContent = text;
    return n;
  }

  /* Settings, IP-TV: the playlist, how many play at once, who sees them, the groups
     standing first on the row, and the groups each kind of screen shows. */
  async function iptvTab(main, { get, post, block, render }) {
    let data = {};
    try { data = await get("/settings"); } catch (e) { return; }
    if (data.liveList === undefined) {
      main.appendChild(note("IP-TV is set by the owner."));
      return;
    }
    const save = async (body) => {
      const back = await post("/settings", body);
      if (back && !back.error) Object.assign(data, back);
      else if (back && back.error) toast(back.error);
      return back;
    };

    // ---- the playlist
    const list = block("Playlist");
    const addr = document.createElement("div");
    addr.className = "addrow";
    addr.style.alignItems = "center";
    const field = document.createElement("input");
    field.type = "text";
    field.placeholder = "http://…/playlist.m3u";
    field.style.flex = "1 1 auto";
    field.value = data.liveList === "file" ? "" : data.liveList;
    const said = note(!data.liveList
      ? "An M3U playlist: its address, or the file. Its channels stand in the Live TV row."
      : (data.liveList === "file" ? "A playlist sent as a file: " : "The playlist at this address: ") +
        data.liveCount + " channels, read again every six hours. The address stays on this server." +
        (data.liveTried ? " Of the groups shown, " + data.liveTried + " tried so far; " +
          (data.liveDead || 0) + " did not play and are left out until they do." : ""));
    const answered = (back) => {
      if (back && back.error) return;
      toast(back && back.read ? back.read + " channels read"
        : back && back.liveList ? "The playlist could not be read" : "Playlist removed");
      render();
    };
    const use = document.createElement("button");
    use.className = "btn ghost";
    use.textContent = "Use";
    use.onclick = async () => {
      use.disabled = true;
      said.textContent = "Reading the playlist…";
      answered(await save({ liveList: field.value.trim() }));
    };
    const file = document.createElement("input");
    file.type = "file";
    file.accept = ".m3u,.m3u8,audio/x-mpegurl,application/vnd.apple.mpegurl";
    file.style.display = "none";
    file.onchange = async () => {
      const f = file.files && file.files[0];
      if (!f) return;
      said.textContent = "Reading the playlist…";
      answered(await save({ liveListText: await f.text() }));
    };
    const pick = document.createElement("button");
    pick.className = "btn ghost";
    pick.textContent = "File…";
    pick.onclick = () => file.click();
    addr.append(field, use, pick, file);
    if (data.liveList) {
      const off = document.createElement("button");
      off.className = "btn ghost";
      off.textContent = "Remove";
      off.onclick = async () => answered(await save({ liveList: "" }));
      addr.appendChild(off);
    }
    list.appendChild(addr);
    list.appendChild(said);
    main.appendChild(list);

    // ---- YouTube channels: what is live on them stands first on the row
    const tube = block("YouTube channels");
    const tubeList = document.createElement("div");
    const drawTube = () => {
      tubeList.innerHTML = "";
      (data.liveYouTube || []).forEach((u) => {
        const row = document.createElement("div");
        row.className = "addrow";
        row.style.alignItems = "center";
        const name = document.createElement("span");
        name.textContent = u;
        name.style.flex = "1 1 auto";
        const rm = document.createElement("button");
        rm.className = "btn ghost";
        rm.textContent = "Remove";
        rm.onclick = async () => {
          await save({ liveYouTube: (data.liveYouTube || []).filter((x) => x !== u) });
          drawTube();
        };
        row.append(name, rm);
        tubeList.appendChild(row);
      });
    };
    drawTube();
    const addTube = document.createElement("div");
    addTube.className = "addrow";
    addTube.style.alignItems = "center";
    const tubeField = document.createElement("input");
    tubeField.type = "text";
    tubeField.placeholder = "https://www.youtube.com/@channel";
    tubeField.style.flex = "1 1 auto";
    const tubeAdd = document.createElement("button");
    tubeAdd.className = "btn ghost";
    tubeAdd.textContent = "Add";
    tubeAdd.onclick = async () => {
      const u = tubeField.value.trim();
      if (!u) return;
      const back = await save({ liveYouTube: (data.liveYouTube || []).concat([u]) });
      if (back && !back.error) { tubeField.value = ""; drawTube(); }
    };
    addTube.append(tubeField, tubeAdd);
    const now = data.liveYouTubeNow || [];
    tube.append(tubeList, addTube, note(!data.liveYouTubeReady
      ? "yt-dlp is not on this server, so YouTube streams cannot be played."
      : "Every stream live on these channels stands first in the Live TV row while it is live." +
        (now.length ? " Live now: " + now.map((c) => c.title).join(", ") + "."
                    : " Nothing live on them right now; checked every three minutes.")));
    main.appendChild(tube);
    if (!data.liveList) return;

    // ---- how many at once, and who sees them
    const use2 = block("Connections and viewers");
    const mostNote = note("");
    const sayMost = () => {
      mostNote.textContent = data.liveMost
        ? "At most " + data.liveMost + " channel" + (data.liveMost === 1 ? "" : "s") +
          " play at once; people watching the same channel share one. For a subscription that allows so many."
        : "No limit on how many channels play at once.";
    };
    use2.appendChild(chipRow("At once",
      () => [[0, "No limit"], [1, "1"], [2, "2"], [3, "3"], [4, "4"], [5, "5"]]
        .map(([v, text]) => ({ v, text })),
      (one) => data.liveMost === one.v,
      async (one) => { await save({ liveMost: one.v }); sayMost(); }));
    sayMost();
    use2.appendChild(mostNote);
    const whoNote = note("");
    const sayWho = () => {
      whoNote.textContent = data.liveWho === "all"
        ? "Guests see the channels too, and play through this server."
        : "Only you see the channels; guests do not.";
    };
    use2.appendChild(chipRow("Shown to",
      () => [{ v: "me", text: "Only me" }, { v: "all", text: "Everyone" }],
      (one) => data.liveWho === one.v,
      async (one) => { await save({ liveWho: one.v }); sayWho(); }));
    sayWho();
    use2.appendChild(whoNote);
    main.appendChild(use2);

    const groups = () => (data.liveAllGroups || []).map((g) => ({ v: g.title, text: g.title + " " + g.count }));

    // ---- the groups standing first on the row
    const first = block("First on the row");
    const firstNote = note("");
    const sayFirst = () => {
      const f = data.liveFolders || [];
      firstNote.textContent = f.length
        ? "The row begins with " + f.map((g, n) => (n + 1) + ". " + g).join(", ") +
          ", each a circle that opens its channels; the channels follow, most watched first."
        : "No groups first: the row is the channels, most watched first.";
    };
    first.appendChild(chipRow("Groups",
      () => groups().map((g) => {
        const at = (data.liveFolders || []).indexOf(g.v);
        return { v: g.v, text: (at >= 0 ? (at + 1) + ". " : "") + g.v };
      }),
      (one) => (data.liveFolders || []).indexOf(one.v) >= 0,
      async (one) => {
        const f = data.liveFolders || [];
        await save({ liveFolders: f.indexOf(one.v) >= 0 ? f.filter((x) => x !== one.v) : f.concat([one.v]) });
        sayFirst();
      }));
    sayFirst();
    first.appendChild(firstNote);
    main.appendChild(first);

    // ---- the groups shown: one list, on every screen
    const shown = block("Groups shown");
    const keptFor = () => data.liveGroups || [];
    const shownNote = note("");
    const sayShown = () => {
      const k = keptFor();
      shownNote.textContent = "Every screen - the TV, a phone, this page - shows " +
        (k.length ? k.join(", ") : "every group") + ". The player's own group button changes the same list.";
    };
    const chips = chipRow("Groups",
      () => [{ v: "", text: "All" }].concat(groups()),
      (one) => one.v === "" ? keptFor().length === 0 : keptFor().indexOf(one.v) >= 0,
      async (one) => {
        const k = keptFor();
        const next = one.v === "" ? [] : (k.indexOf(one.v) >= 0 ? k.filter((x) => x !== one.v) : k.concat([one.v]));
        await save({ liveGroups: next });
        sayShown();
      });
    shown.appendChild(chips);
    sayShown();
    shown.appendChild(shownNote);
    main.appendChild(shown);
  }
})();
