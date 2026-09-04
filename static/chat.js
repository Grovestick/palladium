/* The watch party: a page of its own, reached from the tab of that name.
 *
 * Two rooms and a way in. The lobby is open to everyone on the server - it lists who
 * is about and what each has playing, and it is where an evening gets arranged. The
 * party is the evening itself: who is watching along, what they are watching, and
 * what is said about it while it plays.
 *
 * Nothing here polls. Every read is a request the server holds open until there is
 * something to answer with, so a line appears as it is sent.
 */
(function party() {
  let room = "lobby";
  const since = { lobby: 0, party: 0 };
  const held = { lobby: [], party: [] };
  const listening = {};
  let chosen = null;                       // the title an invitation is about
  const asking = new Set();                // and who is being asked
  let state = {};                          // the last answer from /party
  let showing = false;                     // whether this page is the one on screen

  function line(m) {
    const el = document.createElement("div");
    el.className = "chatline";
    const when = new Date((m.when || 0) * 1000);
    el.innerHTML = "<b>" + esc(m.from || m.who || "someone") + "</b>" +
      "<span class='chatwhen'>" +
      String(when.getHours()).padStart(2, "0") + ":" +
      String(when.getMinutes()).padStart(2, "0") + "</span>" +
      "<div>" + esc(m.text || "") + "</div>";
    return el;
  }

  function add(m) {
    const list = document.getElementById("partysaid");
    if (!list) return;
    const wasDown = list.scrollTop + list.clientHeight >= list.scrollHeight - 20;
    list.appendChild(line(m));
    if (wasDown) list.scrollTop = list.scrollHeight;
  }

  function rooms() {
    const bar = document.createElement("div");
    bar.className = "sortbar collbar";
    [["lobby", "Lobby"], ["party", state.on ? "Party" : "No party on"],
     ["ask", "Invite"]].forEach(([id, label]) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" + (room === id ? " on" : "");
      b.textContent = label;
      b.onclick = () => {
        if (id === "party" && !state.on) return;
        room = id;
        draw();
      };
      bar.appendChild(b);
    });
    if (state.on) {
      const end = document.createElement("button");
      end.className = "btn collact";
      end.style.marginLeft = "auto";
      end.textContent = "End the party";
      end.onclick = async () => {
        await post("/party", { on: false });
        room = "lobby";
        look();
      };
      bar.appendChild(end);
    }
    return bar;
  }

  function invited() {
    if (!state.invited) return null;
    const box = document.createElement("div");
    box.className = "partyinvite";
    box.innerHTML = "<span>" + esc(state.invited.from || "somebody") +
      " asks you to watch " + esc(state.invited.title || "something") + "</span>";
    const yes = document.createElement("button");
    yes.className = "btn";
    yes.textContent = "Join";
    yes.onclick = async () => {
      const key = state.invited && state.invited.key;
      await post("/party", { accept: true });
      room = "party";
      await look();
      if (key) openKey(key);
    };
    const no = document.createElement("button");
    no.className = "btn ghost";
    no.textContent = "No";
    no.onclick = async () => { await post("/party", { decline: true }); look(); };
    box.appendChild(yes);
    box.appendChild(no);
    return box;
  }

  /* Who is about, with what they have on. One line per screen. */
  function here() {
    const rows = state.here || [];
    if (!rows.length) return null;
    const box = document.createElement("div");
    box.className = "partyhere";
    box.innerHTML = "<div class='chatlabel'>Here now</div>";
    rows.forEach((p) => {
      const one = document.createElement("div");
      one.className = "partyone";
      one.innerHTML = "<b>" + esc(p.name) + "</b>" +
        (p.watching ? "<span class='chatdoing'>watching " + esc(p.watching) + "</span>"
                    : "<span class='chatidle'>in the library</span>");
      box.appendChild(one);
    });
    return box;
  }

  /* Choosing what to watch and who to ask. */
  function ask() {
    const box = document.createElement("div");
    box.className = "partyask";
    box.innerHTML = "<div class='chatlabel'>What to watch</div>";
    const find = document.createElement("input");
    find.type = "text";
    find.placeholder = "A film or a programme";
    box.appendChild(find);
    const found = document.createElement("div");
    found.className = "partyfound";
    box.appendChild(found);
    const label = document.createElement("div");
    label.className = "chatlabel";
    label.textContent = "Who to ask";
    box.appendChild(label);
    const people = document.createElement("div");
    people.className = "partyfound";
    box.appendChild(people);
    const go = document.createElement("button");
    go.className = "btn";
    go.textContent = "Start it and invite";
    go.disabled = true;
    box.appendChild(go);

    const ready = () => { go.disabled = !chosen || !asking.size; };
    const search = async () => {
      found.innerHTML = "";
      if (find.value.trim().length < 2) return;
      let rows = [];
      try {
        const c = await api("/hubs/search", { query: find.value, limit: 8 });
        rows = (c.Hub || []).reduce((all, h) => all.concat(h.Metadata || []), items(c));
      } catch (e) { rows = []; }
      rows.slice(0, 6).forEach((m) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" +
          (chosen && chosen.ratingKey === m.ratingKey ? " on" : "");
        b.textContent = m.title + (m.year ? " (" + m.year + ")" : "");
        b.onclick = () => { chosen = m; search(); ready(); };
        found.appendChild(b);
      });
    };
    find.oninput = search;

    (state.guests || []).concat(state.screens || []).forEach((p) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" + (asking.has(p.key) ? " on" : "");
      // a guest is their name; anybody in the house is the address of their screen,
      // and printing the address twice said nothing twice
      b.textContent = p.name;
      b.onclick = () => {
        if (asking.has(p.key)) asking.delete(p.key);
        else asking.add(p.key);
        b.classList.toggle("on");
        ready();
      };
      people.appendChild(b);
    });

    go.onclick = async () => {
      if (!chosen || !asking.size) return;
      await post("/party", { on: true, key: chosen.ratingKey, title: chosen.title });
      await post("/party", { invite: Array.from(asking),
                             key: chosen.ratingKey, title: chosen.title });
      toast("Asked " + asking.size + (asking.size === 1 ? " screen" : " screens"));
      asking.clear();
      room = "party";
      look();
    };
    return box;
  }

  function draw() {
    if (!showing) return;
    main.innerHTML = "";
    const head = document.createElement("h2");
    head.innerHTML = '<span class="ct">Watch party</span>' +
      "<span class='count'>" +
      (state.on ? (state.title ? "watching " + esc(state.title) : "on")
                : "nothing on") + "</span>";
    main.appendChild(head);

    if (state.off) {
      const off = document.createElement("div");
      off.className = "empty";
      off.textContent = "Watch parties are off for you. Settings, Library, " +
        "Watch parties turns them back on.";
      main.appendChild(off);
      return;
    }

    const asked = invited();
    if (asked) main.appendChild(asked);
    main.appendChild(rooms());

    if (room === "ask") {
      main.appendChild(ask());
      return;
    }
    if (room === "lobby") {
      const who = here();
      if (who) main.appendChild(who);
    }

    const said = document.createElement("div");
    said.className = "partysaid";
    said.id = "partysaid";
    main.appendChild(said);

    const write = document.createElement("form");
    write.className = "partywrite";
    write.innerHTML = '<input id="partyline" type="text" maxlength="300" ' +
      'autocomplete="off" placeholder="Say something">' +
      '<button class="btn" type="submit">Send</button>';
    write.onsubmit = async (e) => {
      e.preventDefault();
      const field = write.querySelector("#partyline");
      const text = field.value.trim();
      if (!text) return;
      field.value = "";
      try {
        await fetch("/chat", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: text, room: room }) });
      } catch (err) {
        field.value = text;               // it did not go: leave it to be sent again
      }
    };
    main.appendChild(write);
    held[room].forEach(add);
    said.scrollTop = said.scrollHeight;
  }

  /* One room, listened to for as long as the page is loaded. */
  async function listen(which) {
    if (listening[which]) return;
    listening[which] = true;
    let waited = false;
    for (;;) {
      try {
        const r = await fetch("/chat?room=" + which + "&since=" + since[which] +
                              "&wait=" + (waited ? 45 : 0));
        waited = true;
        const back = await r.json();
        const rows = back.messages || [];
        if (rows.length) {
          since[which] = rows[rows.length - 1].id;
          held[which] = held[which].concat(rows).slice(-200);
          if (showing && room === which) rows.forEach(add);
        } else if (back.id) {
          since[which] = Math.max(since[which], back.id);
        }
        if (which === "party" && back.party === false) {
          await new Promise((go) => setTimeout(go, 5000));
        }
      } catch (e) {
        await new Promise((go) => setTimeout(go, 10000));
      }
    }
  }

  async function look() {
    try {
      state = await (await fetch("/party")).json();
    } catch (e) { state = {}; }
    if (room === "party" && !state.on) room = "lobby";
    draw();
  }

  // opening what the party is watching, in the page that is already loaded
  async function openKey(key) {
    try {
      const found = items(await api("/library/metadata/" + key))[0];
      if (found && window.open) open(found, true);
    } catch (e) { /* the shelf is one press away in any case */ }
  }

  /* The page itself, from the tab. */
  window.viewParty = async function () {
    showing = true;
    onResize = null;
    CTX = null;
    setBackdrop(null);
    main.innerHTML = '<div class="empty">Loading&hellip;</div>';
    await look();
    listen("lobby");
    listen("party");
    if (!window.__partyWatch) {
      // who is about changes when a person does something, so it is asked slowly
      window.__partyWatch = setInterval(() => { if (showing) look(); }, 8000);
    }
  };

  // any other tab: the listeners stay, the drawing stops
  document.addEventListener("click", (e) => {
    const tab = e.target.closest && e.target.closest(".tab");
    if (tab && tab.dataset.view !== "party") showing = false;
  }, true);
})();
