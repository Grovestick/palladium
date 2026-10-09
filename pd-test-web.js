#!/usr/bin/env node
/* The web page's own pieces, run against a page that is not a browser.

     node pd-test-web.js

   Each function is taken out of static/app.js as it stands and run in a made-up page
   with stand-ins for the server. What is tested is what a press changes on the page:
   that it changes that, and that everything else on the page is still the same
   elements it was - a page drawn again is a page that blinked.

   Needs jsdom, which lives in .webtest beside this file (npm install jsdom there).
   Exit code 0 when every check held, 1 otherwise. Run by the build. */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require(path.join(__dirname, ".webtest", "node_modules", "jsdom"));

// another copy of the page script may be named, to see the checks fail on a broken one
const SOURCE = fs.readFileSync(process.env.PD_APPJS || path.join(__dirname, "static", "app.js"), "utf8")
  .replace(/\r\n/g, "\n");
const FAILS = [];
const check = (what, ok, detail) => { if (!ok) FAILS.push(what + (detail ? " " + detail : "")); };

/* One top-level function of app.js, as text: from its first line to the brace that
   closes it at the margin. */
function take(name) {
  const at = SOURCE.search(new RegExp("^(async )?function " + name + "\\(", "m"));
  if (at < 0) throw new Error("no function " + name + " in app.js");
  const end = SOURCE.indexOf("\n}\n", at);
  return SOURCE.slice(at, end + 2);
}

/* A page with those functions in it, and whatever else it is given. */
function page(html, names, extra) {
  const dom = new JSDOM("<!doctype html><body>" + html + "</body>",
                        { runScripts: "outside-only" });
  const w = dom.window;
  Object.assign(w, extra || {});
  w.eval("const $ = (q) => document.querySelector(q);\n" +
         "const isWatched = (it) => !!(it && it.viewCount);\n" +
         names.map(take).join("\n") + "\n" +
         names.map((n) => "window." + n + " = " + n + ";").join("\n"));
  return w;
}
const tick = () => new Promise((done) => setTimeout(done, 0));

(async () => {
  // ---- 1. a film's page: marking it watched changes the play button and nothing else
  {
    const played = [];
    const w = page(
      '<div id="main"><div class="detail"><div class="poster"><img id="art"></div>' +
      '<div class="meta"><h1>A Film</h1><div class="actions">' +
      '<span class="split"><span class="splitrow"><button class="btn" data-off="754">Resume 12:34</button>' +
      '<button class="btn caret" id="pmenu-toggle">v</button></span>' +
      '<span class="menu hidden" id="pmenu"><button data-off="0">Play from start</button></span></span>' +
      '<span id="watchedbtn"></span></div><p class="summary">About it.</p></div></div></div>',
      ["playFromStartOnly", "watchedButton"],
      { play: (m, off, o) => played.push([m.ratingKey, off]), opts: () => ({}),
        setWatched: async (item, on) => { item.viewCount = on ? 1 : 0; item.viewOffset = 0; } });
    const d = w.document;
    const kept = ["#main", ".detail", ".poster", "#art", "h1", ".summary", "#watchedbtn"]
      .map((q) => d.querySelector(q));
    const m = { ratingKey: "f1", viewCount: 0, viewOffset: 754000 };
    const b = w.watchedButton(m, () => w.playFromStartOnly(m));
    d.querySelector("#watchedbtn").appendChild(b);
    check("1 the button says it is not watched yet", /Mark watched/.test(b.textContent));
    b.click();
    await tick();
    check("1 marked: the button says so", /Watched/.test(b.textContent) && b.classList.contains("on"));
    check("1 Resume and its menu are gone", !d.querySelector(".split") && !d.querySelector("#pmenu"));
    const playBtn = d.querySelector(".actions > button.btn");
    check("1 one Play in their place", !!playBtn && playBtn.textContent === "Play"
          && playBtn.dataset.off === "0");
    if (playBtn) playBtn.click();
    check("1 which plays from the start", played.length === 1 && played[0][1] === 0, JSON.stringify(played));
    check("1 everything else on the page is the element it was",
          ["#main", ".detail", ".poster", "#art", "h1", ".summary", "#watchedbtn"]
            .every((q, i) => d.querySelector(q) === kept[i]));
    b.click();
    await tick();
    check("1 unmarked again: still one Play, nothing drawn twice",
          d.querySelectorAll(".actions > button.btn").length === 1 && /Mark watched/.test(b.textContent));
    w.playFromStartOnly(m);
    check("1 asked again with nothing to change, nothing changes",
          d.querySelectorAll(".actions > button.btn").length === 1);
  }

  // ---- 2. a season: a tick, a whole season marked, a star - the rows stay the rows
  {
    let season = [
      { ratingKey: "e1", index: 1, title: "One", summary: "", duration: 1500000, viewCount: 0, viewOffset: 300000 },
      { ratingKey: "e2", index: 2, title: "Two", summary: "", duration: 1500000, viewCount: 1, viewOffset: 0 },
      { ratingKey: "e3", index: 3, title: "Three", summary: "", duration: 1500000, viewCount: 0, viewOffset: 0 },
    ];
    let asked = 0;
    const marked = new Set();
    const fetched = [];
    const w = page(
      '<h1 id="title">A Series</h1><span id="showmark"></span>' +
      '<select id="season"><option value="s1">Season 1</option></select>' +
      '<div id="seasonbar"></div><div id="eplist"></div>',
      ["loadEpisodes", "repaintEpisodes", "refreshEpisodeMarks"],
      { CFG: { guest: true, mayFetch: false }, collEditing: "", collEditName: "", showNow: null,
        standbyName: "", DOWNLOAD_MARK: "", S: null,
        api: async () => { asked += 1; return { Metadata: season.map((e) => Object.assign({}, e)) }; },
        items: (c) => c.Metadata || [],
        img: (t) => t, esc: (s) => String(s == null ? "" : s),
        mins: (ms) => Math.round(ms / 60000) + "m",
        clock: (s) => Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"),
        isCopied: () => false, isMarked: (it) => marked.has(String(it.ratingKey)),
        setMarked: async (it, on) => { on ? marked.add(String(it.ratingKey)) : marked.delete(String(it.ratingKey)); },
        setWatched: async (item, on) => { item.viewCount = on ? 1 : 0; item.viewOffset = 0; },
        markButtons: (item, label, after) => {
          const b = new JSDOM("").window.document.createElement("span"); return b; },
        url: (p, q) => p + "?key=" + (q && q.key), confirm: () => true,
        fetch: async (u) => { fetched.push(u); return { ok: true }; },
        episodeDownloads: () => {}, pickCollection: () => {}, viewOffer: () => {},
        play: () => {}, collectionHolds: async () => new Set(), noServer: () => {},
        requestAnimationFrame: (f) => f() });
    const d = w.document;
    // markButtons has to make elements of this page, not of another
    w.markButtons = (item, label, after) => {
      const box = d.createElement("span");
      box.className = "markgroup";
      box.textContent = label;
      box._after = after;
      return box;
    };
    await w.loadEpisodes("s1");
    const rows = [...d.querySelectorAll("#eplist .ep")];
    check("2 three rows drawn", rows.length === 3 && asked === 1);
    const gone = [];
    new w.MutationObserver((list) => list.forEach((mu) =>
      mu.removedNodes.forEach((n) => { if (n.classList && n.classList.contains("ep")) gone.push(n); })))
      .observe(d.querySelector("#eplist"), { childList: true });
    check("2 the first says where it was left", /resume 5:00/.test(rows[0].querySelector(".when").textContent));
    // one tick
    rows[0].querySelector(".eptick").click();
    await tick();
    check("2 ticked: lit, the row greyed, and no place left to resume",
          rows[0].querySelector(".eptick").classList.contains("on")
          && rows[0].classList.contains("seen-row")
          && !/resume/.test(rows[0].querySelector(".when").textContent));
    check("2 a tick asks the server for nothing more", asked === 1);
    // a star
    rows[2].querySelector(".eptick.star").click();
    await tick();
    check("2 starred: that row's star is lit, the others' are not",
          rows[2].querySelector(".eptick.star").classList.contains("on")
          && !rows[1].querySelector(".eptick.star").classList.contains("on"));
    // the whole season marked watched: the server says what each now is
    season = season.map((e) => Object.assign({}, e, { viewCount: 1, viewOffset: 0 }));
    [...d.querySelectorAll("#seasonbar button")].find((b) => /Mark season watched/.test(b.textContent)).click();
    await tick(); await tick(); await tick();
    check("2 the season was marked on the server", fetched.some((u) => /scrobble\?key=s1/.test(u)), fetched.join(","));
    check("2 every row is ticked", rows.every((r) => r.querySelector(".eptick").classList.contains("on")
                                                 && r.classList.contains("seen-row")));
    // unwatched again
    season = season.map((e) => Object.assign({}, e, { viewCount: 0, viewOffset: 0 }));
    [...d.querySelectorAll("#seasonbar button")].find((b) => /Mark season unwatched/.test(b.textContent)).click();
    await tick(); await tick(); await tick();
    check("2 and none is, after the season is unmarked",
          rows.every((r) => !r.querySelector(".eptick").classList.contains("on")
                            && !r.classList.contains("seen-row")));
    const now = [...d.querySelectorAll("#eplist .ep")];
    check("2 through all of it the rows are the same elements, none taken off the page",
          now.length === 3 && now.every((r, i) => r === rows[i]) && gone.length === 0,
          gone.length + " removed");
    check("2 the list never said Loading", !/Loading/.test(d.querySelector("#eplist").textContent));
    // a mark above the rows changing: the rows say again what is true, and stay
    marked.add("e1");
    d.querySelector("#seasonbar .markgroup")._after();
    check("2 a season's mark repaints the rows in place",
          rows[0].querySelector(".eptick.star").classList.contains("on")
          && [...d.querySelectorAll("#eplist .ep")].every((r, i) => r === rows[i]) && gone.length === 0);
    check("2 and asks the server for no list", asked === 3, "asked " + asked);
  }

  // ---- 3. a favourite on the watchlist: the card's heart, not the page
  {
    let fav = false, redrawn = 0;
    const w = page(
      '<div id="grid"><div class="card" id="a"><div class="poster"><img></div></div>' +
      '<div class="card" id="b"><div class="poster"><div class="onslave"></div><div class="bar"></div></div></div>' +
      '<div class="card" id="c"><div class="poster"><div class="seen">v</div><div class="favheart">h</div></div></div></div>',
      ["favHeartOn"], { isFav: () => fav, viewWatchlist: () => { redrawn += 1; } });
    const d = w.document;
    const cards = ["#a", "#b", "#c"].map((q) => d.querySelector(q));
    fav = true;
    w.favHeartOn(cards[0], { ratingKey: "1" });
    w.favHeartOn(cards[0], { ratingKey: "1" });
    check("3 made a favorite: one heart on that card", cards[0].querySelectorAll(".favheart").length === 1);
    w.favHeartOn(cards[1], { ratingKey: "2" });
    const h = cards[1].querySelector(".favheart");
    check("3 the heart sits clear of the dot and the bar", !!h && h.classList.contains("stack") && h.classList.contains("over"));
    fav = false;
    w.favHeartOn(cards[0], { ratingKey: "1" });
    check("3 no longer one: the heart is gone", !cards[0].querySelector(".favheart"));
    check("3 none of that drew the page again", redrawn === 0
          && ["#a", "#b", "#c"].every((q, i) => d.querySelector(q) === cards[i]));
    w.favHeartOn(cards[2], { ratingKey: "3" });
    check("3 a watched title that stops being a favorite may leave the list: drawn again",
          redrawn === 1 && !cards[2].querySelector(".favheart"));
    cards[0].remove();
    w.favHeartOn(cards[0], { ratingKey: "1" });
    check("3 a card no longer on the page: drawn again", redrawn === 2);
  }

  // ---- 4. a subtitle fetched for a film: its menu filled again, the page left alone
  {
    let redrawn = 0, heard = 0;
    const film = { ratingKey: "f1", Media: [{ subs: [{ id: 3, name: "English" }] }] };
    const w = page(
      '<div id="main"><div class="detail"><h1>A Film</h1><div class="actions verline">' +
      '<select id="subpick"><option value="">Off</option><option value="3">English</option>' +
      '<option value="__get">Download</option></select><span class="note">none in this file</span>' +
      '<div id="subpanel">the list of ones to fetch</div></div></div></div>',
      ["fillSubtitleMenu", "subtitlesFetched"],
      { api: async () => ({ Metadata: [{ ratingKey: "f1", Media: [{ subs: [
          { id: 3, name: "English" }, { id: 9, name: "Swedish", match: true }] }] }] }),
        items: (c) => c.Metadata || [],
        subOptions: (m, mi) => m.Media[mi].subs, subsInOrder: (l) => l,
        preferredSub: (m, mi) => m.Media[mi].subs[m.Media[mi].subs.length - 1],
        subLabel: (t) => t.name, castSession: () => null, tint: () => {},
        preferredCopy: () => 0, viewMovie: () => { redrawn += 1; } });
    const d = w.document;
    const kept = ["#main", ".detail", "h1", "#subpick"].map((q) => d.querySelector(q));
    d.querySelector("#subpick").addEventListener("change", () => { heard += 1; });
    await w.subtitlesFetched(film);
    const sel = d.querySelector("#subpick");
    check("4 the fetched subtitle is in the menu, between Off and Download",
          [...sel.options].map((o) => o.value).join(",") === ",3,9,__get");
    check("4 and is the one chosen", sel.value === "9");
    check("4 the menu's listeners heard it change", heard === 1);
    check("4 it no longer says there are none", !d.querySelector(".note"));
    check("4 the list of ones to fetch closed", !d.querySelector("#subpanel"));
    check("4 the page was not drawn again, and is the elements it was",
          redrawn === 0 && ["#main", ".detail", "h1", "#subpick"].every((q, i) => d.querySelector(q) === kept[i]));
    d.querySelector("#subpick").remove();
    await w.subtitlesFetched(film);
    check("4 with no menu on the page to fill, the page is drawn", redrawn === 1);
  }

  // ---- 5. Settings: a press redraws the pane out of sight and swaps it in whole
  {
    const settings = fs.readFileSync(path.join(__dirname, "static", "settings.js"), "utf8").replace(/\r\n/g, "\n");
    const at = settings.indexOf("  //: counts the drawings begun, so one overtaken knows it was");
    const end = settings.indexOf("\n  }\n", settings.indexOf("  async function render() {", at));
    const dom = new JSDOM('<!doctype html><body><div id="main"><h2 id="old">Settings</h2><p id="was">old pane</p></div></body>',
                          { runScripts: "outside-only" });
    const w = dom.window;
    let release = null, drawn = 0;
    w.scrollTo = () => {};
    w.drawPage = (main) => new Promise((done) => {
      const n = ++drawn;
      // looked up by name while it is built: must be the new page's, not the old one's
      main.innerHTML = '<h2 id="old">Settings ' + n + '</h2><p id="now">new pane ' + n + "</p>";
      w.foundWhileDrawing = w.document.querySelector("#old").textContent;
      release = done;
    });
    w.eval("const $ = (q) => document.querySelector(q);\n" + settings.slice(at, end + 4) + "\nwindow.render = render;");
    const d = w.document;
    const first = w.render();
    check("5 while the new pane is built the old one is still on show",
          !!d.querySelector("#was") && d.querySelector("#was").parentNode === d.querySelector("#main"));
    check("5 the pane being built is out of sight",
          d.querySelector(".redrawing").style.visibility === "hidden");
    check("5 a name looked up while it is built finds the new pane", w.foundWhileDrawing === "Settings 1");
    // a second press before the first has finished: the first is overtaken
    const firstRelease = release;
    const second = w.render();
    check("5 a drawing overtaken is thrown away, and the old pane still stands",
          d.querySelectorAll(".redrawing").length === 1 && !!d.querySelector("#was"));
    firstRelease();
    await first;
    check("5 the overtaken drawing finishing changes nothing", !!d.querySelector("#was") && !d.querySelector("#main > #now"));
    release();
    await second;
    check("5 then the new pane is the page, whole, and the old is gone",
          !d.querySelector("#was") && !d.querySelector(".redrawing")
          && d.querySelector("#main > #now").textContent === "new pane 2"
          && d.querySelectorAll("#main > *").length === 2);
  }

  // ---- 6. this page's own server not answering: a restart is waited out
  {
    const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
    const w = dom.window;
    w.eval("const HOME_GRACE_MS = 30000;\n" + take("homeVerdict") + "\nwindow.homeVerdict = homeVerdict;");
    const run = (looks) => {             // [seconds, answered] pairs, in order
      const st = { misses: 0, since: 0 };
      let was = null;
      return looks.map(([sec, ok]) => {
        const v = w.homeVerdict(st, ok, was, 1000000 + sec * 1000);
        was = v.home;
        return v.home === true ? "up" : v.home === false ? "down" : "unknown";
      }).join(" ");
    };
    check("6 a restart of twenty seconds never calls the server down",
          run([[0, true], [10, false], [11.7, false], [13.4, false], [20, false], [28, false], [30, true]])
          === "up up up up up up up");
    check("6 gone for half a minute, it is down - and up again at its first answer",
          run([[0, true], [10, false], [20, false], [39.9, false], [40, false], [50, false], [60, true]])
          === "up up up up down down up");
    check("6 one miss half a minute after another that was answered between is one miss",
          run([[0, true], [10, false], [20, true], [45, false]]) === "up up up up");
    check("6 never seen up, a miss is not known rather than down",
          run([[0, false], [1.7, false], [10, false]]) === "unknown unknown unknown");
    check("6 and that too is down after half a minute",
          run([[0, false], [15, false], [30, false]]) === "unknown unknown down");
    const st = { misses: 0, since: 0 };
    check("6 while it waits it asks again soon, and stops asking soon once it knows",
          w.homeVerdict(st, false, true, 1000).again === true
          && w.homeVerdict(st, false, true, 40000).again === false
          && w.homeVerdict(st, true, false, 41000).again === false);
  }

  // ---- 7. where a title starts: past its skip rule on a fresh start from 0 only
  {
    const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
    const w = dom.window;
    w.eval(take("leadStart") + "\nwindow.leadStart = leadStart;");
    const ep = { ratingKey: "e1", skipStart: 12.5 };
    check("7 a fresh start from 0 begins past the rule", w.leadStart(ep, 0, "") === 12.5);
    check("7 another title on screen before it is still a fresh start", w.leadStart(ep, 0, "e0") === 12.5);
    check("7 a resume keeps its place", w.leadStart(ep, 300, "") === 0 && w.leadStart(ep, 5, "") === 0);
    check("7 the title already on screen asked for at 0 is a seek to 0", w.leadStart(ep, 0, "e1") === 0);
    check("7 no rule, no skip", w.leadStart({ ratingKey: "e2" }, 0, "") === 0
          && w.leadStart({ ratingKey: "e2", skipStart: 0 }, 0, "") === 0);
    // play() and stop() are too wide to run here: that they use the rule is read
    const body = take("play");
    check("7 play() asks the rule with the title last started, then remembers this one",
          /const lead = leadStart\(meta, offset, LEAD_SEEN\);\s+LEAD_SEEN = String\(meta\.ratingKey\);/.test(body));
    check("7 closing the player forgets it, a restart of the stream does not",
          /^function stop\(keepOpen\) \{\s+if \(!keepOpen\) LEAD_SEEN = "";/m.test(SOURCE));
  }

  // ---- 8. the monitor's worker boxes: made once, lit while working, updated in place
  {
    const SET = fs.readFileSync(process.env.PD_SETTINGSJS || path.join(__dirname, "static", "settings.js"), "utf8")
      .replace(/\r\n/g, "\n");
    const inner = (name) => {            // a function inside the page's closure
      const at = SET.indexOf("\n  function " + name + "(");
      if (at < 0) throw new Error("no function " + name + " in settings.js");
      return SET.slice(at + 1, SET.indexOf("\n  }\n", at) + 4);
    };
    const dom = new JSDOM('<!doctype html><body><div id="w"></div></body>', { runScripts: "outside-only" });
    const w = dom.window;
    w.eval(inner("workerLine") + "\n" + inner("drawWorkers") +
           "\nwindow.workerLine = workerLine; window.drawWorkers = drawWorkers;");
    const box = w.document.getElementById("w");
    const rows = (state, extra) => [
      Object.assign({ name: "Loudness", state: state, step: "a file", for: 99, left: 60, next: null }, extra || {}),
      { name: "Subtitle check", state: "waiting", step: "", for: 0, next: 40, left: 12 },
      { name: "Tracker search", state: "off", step: "", for: 0, next: null }];
    w.drawWorkers(box, [{ on: "main", rows: rows("working") }]);
    const first = Array.from(box.querySelectorAll(".wbox"));
    const named = (n) => first.filter((e) => e.querySelector("b").textContent === n)[0];
    check("8 a box a worker", first.length === 3 && !!named("Loudness") && !!named("Subtitle check"));
    check("8 the one working is lit, the others are not",
          named("Loudness").classList.contains("on") && !named("Subtitle check").classList.contains("on")
          && !named("Tracker search").classList.contains("on"));
    check("8 it says what it is on, for how long and what is left",
          named("Loudness").querySelector("span").textContent === "a file \u00b7 2 min \u00b7 60 to go",
          named("Loudness").querySelector("span").textContent);
    check("8 one asleep says when it wakes, one never started says so",
          named("Subtitle check").querySelector("span").textContent === "next in 40 s \u00b7 12 to go"
          && named("Tracker search").querySelector("span").textContent === "not running");
    check("8 one machine has no heading", box.querySelector(".wgroup").hidden === true);
    w.drawWorkers(box, [{ on: "main", rows: rows("waiting", { step: "", for: 0 }) }]);
    const second = Array.from(box.querySelectorAll(".wbox"));
    check("8 drawn again: the same boxes, the light out",
          second.length === 3 && second.every((e, i) => e === first[i]) && !named("Loudness").classList.contains("on")
          && named("Loudness").querySelector("span").textContent === "waiting \u00b7 60 to go");
    w.drawWorkers(box, [{ on: "main", rows: rows("stalled") }, { on: "copy", rows: rows("working").slice(0, 1) }]);
    check("8 stalled is marked and said, and is not lit",
          named("Loudness").classList.contains("stalled") && !named("Loudness").classList.contains("on")
          && named("Loudness").querySelector("span").textContent.indexOf("stalled") === 0);
    check("8 a second machine: its own boxes under its own heading, both headings shown",
          box.querySelectorAll(".wbox").length === 4 && box.querySelectorAll(".wgroup").length === 2
          && Array.from(box.querySelectorAll(".wgroup")).every((h) => !h.hidden));
    w.drawWorkers(box, [{ on: "main", rows: rows("working").slice(0, 2) }]);
    check("8 a machine and a worker no longer reported leave",
          box.querySelectorAll(".wbox").length === 2 && box.querySelectorAll(".wgroup").length === 1
          && box.querySelector(".wgroup").hidden === true);
    check("8 the list under the drawing no longer carries worker rows",
          SET.indexOf('<span>measuring</span>') < 0 && SET.indexOf('<span>analysing</span>') < 0);
  }

  // ---- 9. Back out of a tab that a front-page row opened returns to the front page
  {
    const dom = new JSDOM('<!doctype html><body><button id="back" class="on"></button>' +
                          '<a class="tab" data-view="home"></a><a class="tab active" data-view="movies"></a></body>',
                          { runScripts: "outside-only" });
    const w = dom.window;
    w.eval("const $ = (q) => document.querySelector(q);\nlet navStack = [], returning = false, tabBelow = '';\n" +
           "const went = [], drew = [];\nconst backToFilm = () => false;\n" +
           "const go = (v) => { went.push(v); tabBelow = ''; };\n" + take("goBack") +
           "\nwindow.run = (stack, below) => { went.length = 0; drew.length = 0; tabBelow = below;" +
           " navStack = stack.map((n) => () => drew.push(n)); goBack();" +
           " return { went: went.slice(), drew: drew.slice(), left: navStack.length }; };");
    let r = w.run(["films"], "home");
    check("9 the tab a row opened, Back: the front page", r.went.join() === "home" && !r.drew.length, JSON.stringify(r));
    r = w.run(["films", "a film"], "home");
    check("9 a film opened from that tab, Back: the tab again, not the front page yet",
          r.drew.join() === "films" && !r.went.length && r.left === 1, JSON.stringify(r));
    r = w.run(["a list"], "");
    check("9 any other view, Back: the tab it was on, as before", r.went.join() === "movies", JSON.stringify(r));
    const row = take("rowSection");
    check("9 the rows that open a tab leave a history step and say where they came from",
          /go\(toTab\[0\] === "movie" \? "movies" : "shows"\);[\s\S]{0,160}tabBelow = "home";\s+pushView\(\(\) => viewSection\(toTab\[0\]\)\);/.test(row));
    check("9 any other way of changing tab forgets it",
          /^function go\(view, keep\) \{\s+tabBelow = "";/m.test(SOURCE));
  }

  // ---- 10. the Cache tab: a cache's card lands on the page, though it is drawn after
  //          the pane has returned and the page built out of sight has been swapped in
  {
    const settings = fs.readFileSync(process.env.PD_SETTINGSJS || path.join(__dirname, "static", "settings.js"), "utf8")
      .replace(/\r\n/g, "\n");
    const at = settings.indexOf("  async function paneRemote(main) {");
    const end = settings.indexOf("  /* A cache's own copying settings, read and written through this server. */", at);
    const dom = new JSDOM('<!doctype html><body><div id="main"></div></body>', { runScripts: "outside-only" });
    const w = dom.window;
    w.eval(
      "let remoteTab = 'server', cacheOn = '';\n" +
      "const esc = (s) => String(s);\n" +
      "const toast = () => {};\n" +
      "const render = () => {};\n" +
      "const block = (t) => { const el = document.createElement('div'); el.className = 'setblock'; return el; };\n" +
      "const post = async () => ({});\n" +
      "const get = async (p) => p === '/follow'\n" +
      "  ? { followers: [{ where: 'http://c:1', name: 'C', ago: 5, managed: false }] } : {};\n" +
      "const drawFollow = async () => {};\n" +
      "const remoteApi = () => ({});\n" +
      // the real one asks the server and then places a row for each machine; this one
      // waits to be let go, so the page can be swapped in before any row is placed
      "const drawServers = async (keyBox, followBox, listBox, place, begin) => {\n" +
      "  await new Promise((r) => { window.letGo = r; });\n" +
      "  if (begin) begin();\n" +
      "  const row = document.createElement('div'); row.textContent = 'the row of machine C';\n" +
      "  place(row, { where: 'http://c:1', name: 'C', managed: false });\n" +
      "  window.again = () => { if (begin) begin(); const r2 = document.createElement('div');\n" +
      "    r2.textContent = 'the row of machine C'; place(r2, { where: 'http://c:1', name: 'C' }); };\n" +
      "};\n" +
      settings.slice(at, end) + "\nwindow.paneRemote = paneRemote;");
    const d = w.document;
    const outer = d.querySelector("#main");
    // as render() does it: a page of its own, out of sight, swapped in when the pane returns
    const page = d.createElement("div");
    outer.appendChild(page);
    await w.paneRemote(page);
    page.replaceWith(...page.childNodes);
    check("10 the pane has returned before the machines were drawn", typeof w.letGo === "function"
          && !outer.textContent.includes("the row of machine C"));
    w.letGo();
    await new Promise((r) => setTimeout(r, 20));
    const count = () => outer.textContent.split("the row of machine C").length - 1;
    check("10 the cache's card is on the page all the same", count() === 1, "found " + count());
    check("10 with what this server does for it beside the row",
          outer.textContent.includes("Share reading") && outer.textContent.includes("How it copies is set on C"));
    w.again();
    check("10 drawn again after a change, there is still one card", count() === 1, "found " + count());
  }

  console.log(FAILS.length ? "WEB FAILED: " + FAILS.length : "WEB PASSED");
  FAILS.forEach((f) => console.log("  - " + f));
  process.exit(FAILS.length ? 1 : 0);
})().catch((e) => { console.log("WEB FAILED: the test itself stopped"); console.log(e); process.exit(1); });
