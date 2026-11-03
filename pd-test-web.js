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

  // ---- 11. the server picker's light and name: what is known, of the machine answering
  {
    const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
    const w = dom.window;
    w.eval(take("pickLight") + take("pickName") + "window.pickLight = pickLight; window.pickName = pickName;");
    check("11 no colour until there has been a look", w.pickLight(false, null, null) === "waiting"
          && w.pickLight(true, true, null) === "waiting");
    check("11 green when this page's server answers, red when it does not",
          w.pickLight(false, true, null) === "up" && w.pickLight(false, false, true) === "down");
    check("11 carried by the copy, the light is the copy's",
          w.pickLight(true, false, true) === "up" && w.pickLight(true, true, false) === "down");
    check("11 and so is the name", w.pickName(true, "Copy", "Main") === "Copy" && w.pickName(false, "Copy", "Main") === "Main"
          && w.pickName(true, "", "Main") === "The other server" && w.pickName(false, "", "") === "This server");
    const css = fs.readFileSync(path.join(__dirname, "static", "style.css"), "utf8");
    const base = (css.match(/\.srvpick \.dot\{[^}]*\}/) || [""])[0];
    check("11 the light has no colour of its own in the stylesheet", base.length > 0 && !/#42c96a/i.test(base)
          && /\.srvpick \.dot\.up\{background:#42c96a\}/.test(css), base);
    check("11 the picker is drawn again when a look changes what is known",
          /if \(\(home !== was \|\| there !== wasCopy\) && typeof drawServerPick === "function"\) drawServerPick\(\);/.test(SOURCE));
  }

  // ---- 12. the soundtrack the server marks is the one a browser plays
  {
    const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
    const w = dom.window;
    w.eval(take("soundNeedsPicking") + "\nwindow.soundNeedsPicking = soundNeedsPicking;");
    const part = (marks) => ({ Stream: [{ streamType: 1 }].concat(marks.map((m, n) =>
      ({ streamType: 2, index: n + 1, selected: m })), [{ streamType: 3, index: 9, selected: true }]) });
    check("12 the second track marked: picked out by ffmpeg", w.soundNeedsPicking(part([false, true])) === 2);
    check("12 the first marked, or none, or one track: played as it is",
          w.soundNeedsPicking(part([true, false])) === null && w.soundNeedsPicking(part([false, false])) === null
          && w.soundNeedsPicking(part([true])) === null && w.soundNeedsPicking(null) === null);
    check("12 direct play gives way to it", /if \(soundNeedsPicking\(part\) !== null\) return null;/.test(SOURCE));
  }

  // ---- 13. the monitor: workers on a tab of their own, and a phone layout that reads
  {
    const css = fs.readFileSync(path.join(__dirname, "static", "style.css"), "utf8").replace(/\r\n/g, "\n");
    const settings = fs.readFileSync(path.join(__dirname, "static", "settings.js"), "utf8");
    check("13 a Workers tab", /\["workers", "Workers"\]/.test(settings));
    check("13 the overview leaves the workers out",
          css.includes('body.monitor:is([data-design="overview"],[data-design="drawing"]) .nowtop>.workers{display:none}'));
    const phone = css.indexOf("/* A phone: one column that scrolls");
    const compact = css.indexOf('body.monitor[data-design="overview"] .person.live .pmeta .note{font-size:10px');
    check("13 the phone's rows come after the television's compact ones", phone > compact && compact > 0);
    check("13 and are as specific, so they win",
          css.slice(phone, phone + 4000).includes("body.monitor[data-design] .person.live .pmeta b{font-size:17px"));
  }

  // ---- 14. the subtitle a title opens with: the app's rule
  {
    const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
    const w = dom.window;
    w.eval("let LIST = [], PREFS = {}, MEDIA = null; let SERVERLANG = '', SERVERLANG2 = '';\n" +
           "const subOptions = () => LIST; const prefs = () => PREFS; const mediaOf = () => MEDIA;\n" +
           SOURCE.slice(SOURCE.indexOf("const LANG_SAME = "), SOURCE.indexOf("};", SOURCE.indexOf("const LANG_SAME = ")) + 2) + "\n" +
           take("sameLang") + take("preferredSub") + take("nextWantsSubs") +
           "\nwindow.set = (l, p, m) => { LIST = l; PREFS = p; MEDIA = m || null; }; window.preferredSub = preferredSub;" +
           " window.nextWantsSubs = nextWantsSubs;");
    const t = (id, code, more) => Object.assign({ id: id, code: code, external: false, picked: false,
                                                  forced: false, sdh: false }, more || {});
    const pick = (list, prefs) => { w.set(list, prefs); const got = w.preferredSub({}, 0); return got ? got.id : null; };
    check("14 a Swedish file chosen once does not keep an English reader on Swedish",
          pick([t(1, "swe", { external: true, picked: true }), t(2, "eng")], { subLang: "en" }) === 2);
    check("14 chosen last time and in the first language: that one",
          pick([t(1, "eng"), t(2, "eng", { external: true, picked: true })], { subLang: "en" }) === 2);
    check("14 a file beside the video before the film's own",
          pick([t(1, "eng"), t(2, "eng", { external: true })], { subLang: "en" }) === 2);
    check("14 a full track before a forced one or one describing sounds",
          pick([t(1, "eng", { forced: true }), t(2, "eng", { sdh: true }), t(3, "eng")], { subLang: "en" }) === 3);
    check("14 nothing in the first language: the second",
          pick([t(1, "fre"), t(2, "swe")], { subLang: "en", subLang2: "sv" }) === 2);
    check("14 the second set to off: nothing", pick([t(1, "fre")], { subLang: "en", subLang2: "off" }) === null);
    check("14 off: nothing, unless one was chosen", pick([t(1, "eng")], { subLang: "" }) === null
          && pick([t(1, "eng", { picked: true })], { subLang: "" }) === 1);
    w.set([t(1, "eng"), t(2, "swe", { start: true })], { subLang: "en" }, { subsDecided: true });
    check("14 the server's choice is taken over the web's own rule", w.preferredSub({}, 0).id === 2);
    w.set([t(1, "eng")], { subLang: "en" }, { subsDecided: true });
    check("14 decided, none marked: none", w.preferredSub({}, 0) === null);
    w.set([t(1, "eng")], {});
    check("14 the next episode with subtitles only when they were on, the series wants them, or a file is beside it",
          w.nextWantsSubs(false, { subsWanted: false }) === false && w.nextWantsSubs(true, {}) === true
          && w.nextWantsSubs(false, { subsWanted: true }) === true);
    w.set([t(1, "eng", { external: true })], {});
    check("14 a file beside it counts", w.nextWantsSubs(false, {}) === true);
    check("14 the next episode asks it", /play\(meta, 0, nextWantsSubs\(showing, meta\) \? \{\} : \{ subId: 0 \}\);/.test(SOURCE));
  }

  // ---- 15. the popular page's filters, and a pack's episode opening its releases
  {
    const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
    const w = dom.window;
    w.eval(take("popularFilter") + "\nwindow.popularFilter = popularFilter;");
    const list = [{ title: "A", year: 2019, genres: ["Drama", "Crime"] },
                  { title: "B", year: 2024, genres: ["Drama"] },
                  { title: "C", year: 1998, genres: ["Comedy"] },
                  { title: "D", year: 0, genres: [] }];
    const t = (g, d) => Array.from(w.popularFilter(list, g, d)).map((x) => x.title).join("");
    check("15 nothing marked: all of them", t("", "") === "ABCD");
    check("15 every marked genre", t("Drama", "") === "AB" && t("Drama,Crime", "") === "A");
    check("15 any marked decade", t("", "2010") === "A" && t("", "2010,1990") === "AC");
    check("15 both at once", t("Drama", "2020") === "B");
    check("15 a pack's episode opens the release list, its page only for one who may not fetch",
          /row\.onclick = \(\) => \(ep\.offered\s*\? \(\(!CFG\.guest \|\| CFG\.mayFetch\) \? episodeDownloads\(row, ep\) : viewOffer\(ep\)\)/.test(SOURCE));
  }

  // ---- 16. Popular is an order of the tab: the same page, the same controls
  {
    const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
    const w = dom.window;
    w.eval("let sortState = { key: 'popular', dir: 'desc' };\n" + take("sortLike") +
           "\nwindow.sortLike = sortLike;");
    const list = [{ title: "Beta", popularity: 5 }, { title: "alpha", popularity: 9 },
                  { title: "Gamma", popularity: 7 }, { title: "Delta", popularity: 0 }];
    check("16 Popular: most watched first, the rest after",
          Array.from(w.sortLike(list.slice())).map((x) => x.title[0].toUpperCase()).join("") === "AGBD");
    const body = SOURCE.slice(SOURCE.indexOf("async function viewSection(type)"),
                              SOURCE.indexOf("async function viewSection(type)") + 14000);
    check("16 the tab draws Popular itself, with its own sort, service, genre and decade",
          body.includes('if (sortState.key === "popular") {') &&
          body.includes("h.appendChild(withPopular(sortBar(() => viewSection(type)), type));\n    h.appendChild(serviceBar(type));") &&
          !SOURCE.includes("function popularSortBar(") && !SOURCE.includes("const POPULAR_WANTED"));
  }

  // ---- 17. the popular page by where a show was made
  {
    const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
    const w = dom.window;
    w.eval(take("popularFilter") + take("countryName") +
           "\nwindow.popularFilter = popularFilter; window.countryName = countryName;");
    const list = [{ title: "A", countries: ["US"] }, { title: "B", countries: ["KR"] },
                  { title: "C", countries: ["GB", "US"] }, { title: "D", countries: [] }];
    const t = (c) => Array.from(w.popularFilter(list, "", "", c)).map((x) => x.title).join("");
    check("17 nothing marked: every country", t("") === "ABCD");
    check("17 any marked country", t("US") === "AC" && t("KR,GB") === "BC" && t("kr") === "B");
    check("17 the country by its name", w.countryName("KR") === "South Korea" && w.countryName("US") === "United States");
  }

  // ---- 18. a download's corner: how far first, the version on its own line
  {
    const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
    const w = dom.window;
    w.eval("const etaShort = (s) => s + ' s';\n" + take("offerWord") + take("offerVersion") +
           "\nwindow.offerWord = offerWord; window.offerVersion = offerVersion;");
    const it = { offer: { state: "downloading", progress: 0.57, eta: 120, version: "1080p WEB-DL - THEGAME" } };
    check("18 the percentage first, with no version in it", w.offerWord(it) === "57% \u00b7 120 s");
    check("18 the version alone, for its own line", w.offerVersion(it) === "1080p WEB-DL - THEGAME"
          && w.offerVersion({ offer: { state: "queued", version: "x" } }) === "");
    check("18 the badge draws it under", /'<small class="ver">' \+ esc\(offerVersion\(it\)\)/.test(SOURCE));
  }

  // ---- 19. Popular in the TV page's order menu
  {
    const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
    const w = dom.window;
    w.eval("let ordered = 0; const sortState = { key: 'popular' };\n" +
           take("withPopular") +
           "\nwindow.make = (type) => { const bar = document.createElement('div');" +
           " const sel = document.createElement('select'); sel.add(new Option('Name', 'name'));" +
           " sel.onchange = () => { ordered++; }; bar.appendChild(sel); return withPopular(bar, type); };" +
           " window.counts = () => ordered;");
    const tv = w.make("show"), films = w.make("movie");
    const sel = tv.querySelector("select");
    check("19 TV and Films both offer Popular, first, and show it chosen",
          sel.options[0].value === "popular" && sel.value === "popular"
          && films.querySelector("select").options[0].value === "popular");
    sel.value = "popular"; sel.onchange();
    sel.value = "name"; sel.onchange();
    check("19 Popular is chosen like any other order: no page of its own", w.counts() === 2);
  }

  // ---- 20c. a film on SVT Play plays from there, from its own page
  {
    check("20c SVT Play: a Play button on the page of a film SVT shows, its stream asked of the server",
          SOURCE.includes(`(m.svt ? '<button class="btn" id="svtplay">`) &&
          SOURCE.includes('api("/library/svt/" + encodeURIComponent(m.svt), {}, null)') &&
          fs.existsSync(path.join(__dirname, "static", "hls.min.js")));
  }

  // ---- 20l. every way into Films or TV lands on the tab; the door sets order and marks
  {
    check("20l the tab's Popular order reads the films' or the series' popular list",
          /if \(type !== "show" && type !== "movie"\) return bar;/.test(SOURCE) &&
          SOURCE.includes('type === "movie" ? "/library/popularFilms" : "/library/popularShows"'));
    check("20l a shelf's name and a genre on a title open the tab under its own name",
          SOURCE.includes('const tabState = SORT_BY_TAB[toTab[0] === "movie" ? "movies" : "shows"];') &&
          SOURCE.includes('go(kind === "show" ? "shows" : "movies");') &&
          !SOURCE.includes('go(kind === "show" ? "tv" : "movies");'));
  }

  // ---- 20k. a search can be spoken: what was heard goes in the box as typing
  {
    check("20k the mic button puts what was said in the search box and searches",
          /box\.value = said;\s*box\.dispatchEvent\(new Event\("input"\)\)/.test(SOURCE) &&
          /window\.isSecureContext/.test(SOURCE));
  }

  // ---- 20j. a series not held says where it streams
  {
    check("20j the add page of a series carries Region and its streaming services",
          /'<div class="sub">' \+ facts \+ "<\/div>" \+\s*\/\/[^\n]*\n\s*badgesRow\(\[\], m\) \+/.test(SOURCE));
  }

  // ---- 20i. a series' single episodes in the one download menu, after the packs
  {
    check("20i one download menu on a series' page: packs, then its single episodes, each fetched alone",
          !SOURCE.includes("function trackerEpisodesInto(") &&
          SOURCE.includes('group.label = "Single episodes";') &&
          SOURCE.indexOf('pick.add(new Option("Let it find packs by itself') <
            SOURCE.indexOf('group.label = "Single episodes";') &&
          SOURCE.includes("body: JSON.stringify({ id: ep.id, title: m.title, year: m.year || 0 }) })).json();"));
  }

  // ---- 20h. a series nobody has: Checking while asked, Request where nothing carries it
  {
    check("20h series bands: Checking, Request, Add; the setting to show series nobody has",
          SOURCE.includes(`: it.addable && it.onTracker === false ? '<div class="ribbon"><span>Request</span></div>'`) &&
          fs.readFileSync(path.join(__dirname, "static", "settings.js"), "utf8").includes('post("/settings", { tvRequest: val })'));
  }

  // ---- 20g. what was played from SVT Play keeps its place and comes back on at once
  {
    check("20g SVT Play: the place told every ten seconds and on closing; a played card plays at once",
          SOURCE.includes('const key = "sv" + m.svt;') && SOURCE.includes('tell("stopped");') &&
          SOURCE.includes('if (it && it.svt && String(it.ratingKey || "").startsWith("sv")) return playSvt(it);'));
  }

  // ---- 20f. a stream's page: seasons in a menu, the cast under Show more
  {
    check("20f SVT Play: seasons in a menu; on a stream's page the cast is under Show more",
          SOURCE.includes("pick.onchange = () => show(+pick.value);") &&
          SOURCE.includes('if (m.svt) wrap.querySelector(".cast").remove();') &&
          SOURCE.includes('if (it.svt) { pressableText(wrap, m); wrap.querySelector(".cast").remove(); }'));
  }

  // ---- 20e. a poster SVT Play can stream wears the Stream band
  {
    check("20e the Stream band on what SVT Play has, before Request, Download and Add",
          SOURCE.includes(`: (it.askable || it.addable) && it.svt ? '<div class="ribbon stream"><span>Stream</span></div>'`));
  }

  // ---- 20d. a series on SVT Play: its seasons and episodes on its add page
  {
    check("20d SVT Play series: seasons and episodes on the page, each episode played from SVT",
          SOURCE.includes("if (it.svt) svtSeriesInto(wrap.querySelector(\".svtseries\"), it.svt, m.title);") &&
          SOURCE.includes('b.onclick = () => playSvt({ svt: e.id, title: title + " \\u00b7 " + e.name });'));
  }

  // ---- 20p. a title page still waiting on the tracker says Checking and asks again
  {
    // 20p, the title page saying Checking until the tracker has answered, is drawn and read in 45
  }

  // ---- 20o. a tracker that did not answer leaves nothing to add, rather than no pack
  {
    check("20o no answer from the tracker: the add button stays off, and says so",
          SOURCE.includes("} catch (e) { found = []; unanswered = true; }") &&
          /if \(unanswered\) \{[^}]*return;\s*\}/.test(SOURCE));
  }

  // ---- 20n. a Popular films row under Recently released films, opening the whole list
  {
    const ids = [...SOURCE.matchAll(/\{ id: "([a-z]+)", title:/g)].map((x) => x[1]);
    check("20n the Popular films row follows Recently released films and opens its page",
          ids.indexOf("popularfilms") === ids.indexOf("released") + 1 &&
          SOURCE.includes('if (cat.id === "popularfilms") return viewPopularShows(false, "movie");') &&
          SOURCE.includes("if (before >= 0) out.splice(before + 1, 0, c);"));
  }

  // ---- 20q. My library and In packs: on disk, in a pack; both marked keep everything
  {
    const w = page("", ["ofHouse"]);
    const list = [{ ratingKey: "a" }, { ratingKey: "b", offered: true },
                  { ratingKey: "c", askable: true }, { ratingKey: "d", addable: true }];
    const keys = (h) => w.ofHouse(list, h).map((x) => x.ratingKey).join("");
    check("20q My library keeps what is on disk, In packs what a pack carries, both all of it",
          keys({ mine: true, packs: false }) === "a" && keys({ mine: false, packs: true }) === "b" &&
          keys({ mine: true, packs: true }) === "abcd", keys({ mine: true, packs: false }));
  }

  // ---- 20r. a catalogue read a service at a time goes back into the chosen order
  {
    const w = page("", ["inServiceOrder"], { serviceSort: { key: "popular", dir: "desc" } });
    const got = w.inServiceOrder([{ ratingKey: "a", popularity: 3 }, { ratingKey: "b", popularity: 90 },
                                  { ratingKey: "c", popularity: 12 }]).map((x) => x.ratingKey).join("");
    w.serviceSort = { key: "name", dir: "asc" };
    const named = w.inServiceOrder([{ ratingKey: "x", title: "Zed" }, { ratingKey: "y", title: "abba" }])
      .map((x) => x.ratingKey).join("");
    check("20r services read one at a time are sorted back by popularity, or by name", got === "bca" && named === "yx",
          got + " " + named);
  }

  // ---- 20s. Clear in a filter menu says All and unticks at once, before the list comes
  {
    const w = page("<div id='host'></div>", ["genreMenu"], { genreKeep: null });
    let applied = null;
    const bar = w.genreMenu("8,1899", Promise.resolve([["8", "Netflix"], ["1899", "HBO Max"]]),
                            (v) => { applied = v; }, -1,
                            { label: "Service:", joins: "x", names: [["8", "Netflix"], ["1899", "HBO Max"]] });
    w.document.getElementById("host").appendChild(bar);
    await tick();
    const was = bar.querySelector(".genrepick").textContent;
    bar.querySelector(".genreclear").click();
    const now = bar.querySelector(".genrepick").textContent;
    check("20s Clear says All and unticks before the list is read again",
          was.startsWith("Netflix + HBO Max") && now.startsWith("All") && applied === "" &&
          !bar.querySelector(".genreopt.on") && !bar.querySelector(".genreopt input:checked"), was + " / " + now);
  }

  // ---- 20t. everything read a service at a time: a pause keeps what came, Continue
  //            carries on from the next page rather than from the start
  {
    const asked = [];
    const pages = { "8,1899": 1, "8": 3, "1899": 2 };
    const w = page("", ["catalogueKey", "serviceCatalogue", "readCatalogue", "inServiceOrder"], {
      servicePages: { movie: "all" }, serviceSort: { key: "popular", dir: "desc" },
      SERVICE_SORTS: [["popular", "Popular", "popularity"]], genreWanted: {}, decadeWanted: {},
      allRead: null, allDone: null,
      api: async (path, q) => {
        asked.push(q.provider + ":" + q.page);
        const n = pages[q.provider];
        return { totalSize: 100, more: q.page < n,
                 Metadata: [{ ratingKey: q.provider + "-" + q.page, popularity: 100 - asked.length }] };
      } });
    let stops = 0;
    const first = await w.serviceCatalogue("movie", "8,1899", () => (++stops < 3 ? true : "pause"));
    // another order while paused: the same titles sorted, nothing asked, still paused
    const asks = asked.length;
    w.serviceSort = { key: "popular", dir: "asc" };
    const resorted = await w.serviceCatalogue("movie", "8,1899", () => true);
    const stillPaused = resorted.paused === true && asked.length === asks &&
      resorted.list[0].popularity <= resorted.list[resorted.list.length - 1].popularity;
    w.allRead.paused = false;
    const second = await w.serviceCatalogue("movie", "8,1899", () => true);
    check("20t a pause keeps what came and Continue asks from the next page on",
          first.paused === true && first.list.length === 3 && stillPaused && second.paused === false &&
          asked.join(" ") === "8:1 8:2 8:3 1899:1 1899:2" && second.list.length === 5 &&
          second.total === 5,
          asked.join(" ") + " | " + first.list.length + "/" + second.list.length);
  }

  // ---- 20u. everything read once: a new order sorts it, nothing is asked again
  {
    const asked = [];
    const w = page("", ["catalogueKey", "serviceCatalogue", "readCatalogue", "inServiceOrder"], {
      servicePages: { movie: "all" }, serviceSort: { key: "popular", dir: "desc" },
      SERVICE_SORTS: [["popular", "Popular", "popularity"], ["name", "Name", "titleSort"]],
      genreWanted: {}, decadeWanted: {}, allRead: null, allDone: null,
      api: async (path, q) => {
        asked.push(q.provider + ":" + q.page);
        return { totalSize: 2, more: false,
                 Metadata: [{ ratingKey: "a", title: "Zed", popularity: 9 },
                            { ratingKey: "b", title: "Abba", popularity: 1 }] };
      } });
    const first = await w.serviceCatalogue("movie", "8", () => true);
    w.serviceSort = { key: "name", dir: "asc" };
    const again = await w.serviceCatalogue("movie", "8", () => true);
    check("20u a new order of what was read whole is a sort, not a reload",
          asked.length === 1 && first.list.map((x) => x.ratingKey).join("") === "ab" &&
          again.list.map((x) => x.ratingKey).join("") === "ba", asked.join(" "));
  }

  // ---- 20v. a new order while everything is loading carries on, it does not start over
  {
    const asked = [];
    const w = page("", ["catalogueKey", "serviceCatalogue", "readCatalogue", "inServiceOrder"], {
      servicePages: { movie: "all" }, serviceSort: { key: "popular", dir: "desc" },
      SERVICE_SORTS: [["popular", "Popular", "popularity"], ["name", "Name", "titleSort"]],
      genreWanted: {}, decadeWanted: {}, allRead: null, allDone: null,
      api: async (path, q) => {
        asked.push(q.provider + ":" + q.page);
        return { totalSize: 50, more: q.page < 4,
                 Metadata: [{ ratingKey: q.provider + "-" + q.page, title: "T" + q.page,
                              popularity: q.page }] };
      } });
    // the first reading stops after two pages, as a reading overtaken does
    let n = 0;
    await w.serviceCatalogue("movie", "8", () => ++n < 2);
    w.serviceSort = { key: "name", dir: "asc" };
    const done = await w.serviceCatalogue("movie", "8", () => true);
    check("20v a new order mid-load carries on from the page it got to",
          asked.join(" ") === "8:1 8:2 8:3 8:4" && done.list.length === 4 && done.paused === false &&
          done.list.map((x) => x.title).join("") === "T1T2T3T4", asked.join(" "));
  }

  // ---- 20x. a filter menu closes on a press outside, also while its list is read again
  {
    const w = page("<div id='host'></div><div id='elsewhere'>x</div>", ["genreMenu"], { genreKeep: null });
    const bar = w.genreMenu("", Promise.resolve([["a", "A"], ["b", "B"]]), () => {}, -1, null);
    w.document.getElementById("host").appendChild(bar);
    await tick();
    const menu = bar.querySelector(".genremenu");
    bar.querySelector(".genrepick").click();
    // a mark: the list is asked for again, and the menu stands until it is redrawn
    const box = bar.querySelector(".genreopt input");
    box.checked = true;
    box.dispatchEvent(new w.Event("change"));
    w.document.getElementById("elsewhere").dispatchEvent(new w.MouseEvent("mousedown", { bubbles: true }));
    check("20x a press outside closes the menu while the list is read again, and it stays shut",
          menu.classList.contains("hidden") && w.genreKeep === null);
  }

  // ---- 20aa. Popular with this house's titles in it: no popularity is a tie, broken by
  //             the score and then the name - not the order they came in
  {
    const w = page("", ["inServiceOrder"], { serviceSort: { key: "popular", dir: "desc" } });
    const got = w.inServiceOrder([{ ratingKey: "z", title: "Zulu", rating: 9 },
                                  { ratingKey: "a", title: "Alien", rating: 8.2 },
                                  { ratingKey: "p", title: "Pop", popularity: 40, rating: 5 },
                                  { ratingKey: "b", title: "Brazil", rating: 8.2 }])
      .map((x) => x.ratingKey).join("");
    check("20aa titles without popularity follow by score, then name", got === "pzab", got);
  }

  // ---- 20ab. a mood's row: one line of wide cards, paged with arrows; Don't recommend
  //             takes a card off and the next comes in
  {
    const told = [];
    const w = page("", ["startShelf", "startCard"], {
      main: { clientWidth: 1300 }, esc: (t) => String(t), img: (a) => a,
      open: () => {}, play: () => {},
      startTell: async (it, what) => { told.push(it.ratingKey + ":" + what); return true; } });
    const list = Array.from({ length: 10 }, (_, i) => ({ ratingKey: "k" + i, title: "T" + i, type: "movie" }));
    const row = w.startShelf({ id: "laugh", name: "Make me laugh", tagline: "x", Metadata: list });
    // one strip that slides: every card is on it, and the place is how far along it is
    const keys = () => Array.from(row.querySelectorAll(".spcard .spctitle")).map((c) => c.textContent).join(",");
    const at = () => row.querySelector(".spcards").dataset.at;
    const first = keys() + "@" + at();
    row.querySelector(".sparrow.next").click();
    const second = at();
    const slid = row.querySelector(".spcards").style.transform;
    row.querySelector(".sparrow.prev").click();
    row.querySelectorAll(".spcard")[1].querySelectorAll(".spcbtn")[2].click();
    await tick();
    const after = keys() + "@" + at();
    check("20ab a mood's row slides four along at a time, and Don't recommend takes a card off",
          first === "T0,T1,T2,T3,T4,T5,T6,T7,T8,T9@0" && second === "4" && /translateX/.test(slid) &&
          told.join() === "k1:not" && after === "T0,T2,T3,T4,T5,T6,T7,T8,T9@0" &&
          row.querySelector(".sparrow.prev").hidden,
          first + " | " + second + " | " + after);
    // and a row that says where more is ends in Show more, its name a link to the same
    const opened = [];
    const w2 = page("", ["startShelf", "startCard"], {
      main: { clientWidth: 1300 }, esc: (t) => String(t), img: (a) => a,
      open: () => {}, play: () => {}, startTell: async () => true,
      startMore: (m) => opened.push(m.genre) });
    const row2 = w2.startShelf({ id: "laugh-movie", name: "Make me laugh", tagline: "x",
                                 Metadata: list, more: { tab: "films", sort: "popular", genre: "Comedy" } });
    row2.querySelector(".spmore").click();
    row2.querySelector(".sphead").click();
    check("20ab2 a Start row ends in Show more and its name opens the same",
          opened.join() === "Comedy,Comedy", opened.join());
  }

  // ---- 20m. Films opens on its popular page too, unless a service is marked
  {
    check("20m Films and TV open on Popular from their button unless a service is marked",
          SOURCE.includes('if (kind && serviceWanted[kind] === SRC_HOUSE) {\n      SORT_BY_TAB[t.dataset.view].key = "popular";') &&
          SOURCE.includes('const SORT_BY_TAB = { movies: { key: "popular", dir: "desc" },'));
  }

  // ---- 20b. TV opens on the popular shows, the library's series under My shows
  {
    check("20b a Popular row's See all is the tab on Popular, nothing marked",
          SOURCE.includes('SORT_BY_TAB[view].key = "popular";\n  SORT_BY_TAB[view].dir = "desc";\n  if (asTab) return viewSection(type);'));
  }

  // ---- 20. the TV page's country menu is gone; the Service filter stands in its place
  {
    check("20 no country menu on TV; a Service menu on Films and TV",
          !/h\.appendChild\(countryBar\(type\)\)/.test(SOURCE)
          && /h\.appendChild\(serviceBar\(type\)\);/.test(SOURCE));
  }

  // ---- 21. a sport's card opens its events
  {
    check("21 a sport on the Sports row opens its races or games",
          /if \(it && it\.sportRow\) return viewSport\(it\);/.test(SOURCE)
          && /api\("\/library\/sports", \{ sport: it\.sport \}/.test(SOURCE));
  }

  // ---- 22. a live channel plays when pressed
  {
    check("22 a channel on the Live TV row plays, through the server's own pieces",
          /if \(it && it\.live\) return playLive\(it\);/.test(SOURCE)
          && /url\("\/live\/" \+ ch\.ratingKey \+ "\/index\.m3u8"/.test(SOURCE)
          && SOURCE.includes("tune(it);"));
  }

  // ---- 23. a live channel starts at half volume, then at the last one set
  {
    const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only", url: "http://x/" });
    const w = dom.window;
    w.eval(take("liveVolume") + "\nwindow.liveVolume = liveVolume;");
    check("23 half the first time", w.liveVolume() === 0.5);
    w.localStorage.setItem("liveVolume", "0.3");
    check("23 the last one set after that", w.liveVolume() === 0.3);
    w.localStorage.setItem("liveVolume", "7");
    check("23 nonsense is half", w.liveVolume() === 0.5);
  }

  // ---- 23. a download written over the subtitle in use is read again
  {
    check("23 a subtitle replaced in place is reloaded, not left showing the old one",
          /\} else if \(picker && S && S\.subId\) \{\s*\/\/[^\n]*\n\s*picker\.value = String\(S\.subId\);\s*picker\.dispatchEvent\(new Event\("change"\)\);/.test(SOURCE));
  }

  // ---- 24. a film from the tracker says so on its poster, as a pack's does not
  {
    const css = fs.readFileSync(path.join(__dirname, "static", "style.css"), "utf8");
    check("24 no On tracker label: the download list says where each version comes from",
          !SOURCE.includes("srctag") && !css.includes("srctag"));
  }

  // ---- 25. the Monitor opens the Log tab's watch log, a week of both servers
  {
    const settings = fs.readFileSync(path.join(__dirname, "static", "settings.js"), "utf8");
    check("25 no Watch log button on the Monitor; the log opened on its own scrolls, list first",
          !settings.includes('log.textContent = "Watch log";')
          && settings.includes('if (document.body.classList.contains("monitor")) document.body.dataset.design = "log";')
          && fs.readFileSync(path.join(__dirname, "static", "style.css"), "utf8")
               .includes('body.monitor[data-design="log"] main>.logbox{order:-1}'));
    check("25 a phone held sideways gets the phone's Monitor",
          SOURCE.includes('document.body.classList.toggle("phonelike",')
          && fs.readFileSync(path.join(__dirname, "static", "style.css"), "utf8")
               .includes("body.monitor.phonelike:is("));
    check("25 the last week by default, filtered by viewer and by the server it played on",
          settings.includes('data = await get("/watchlog" + (logWeek ? "?days=7"')
          && settings.includes("if (logOn) rows = rows.filter((w) => machinesOf(w).includes(logOn));"));
    check("25 #monitor/log opens on it, for the app's Log", SOURCE.includes(
          'settingsTab(which === "log" ? "log" : "now")'));
  }

  // ---- 26. a search from Home: films first, then programmes
  {
    check("26 Home orders the rows films, programmes, episodes",
          SOURCE.includes('|| ["movie", "show", "episode"];'));
  }

  // ---- 27. a film not asked of the tracker yet says Checking
  {
    check("27 Checking… rather than Request until the tracker has answered",
          SOURCE.includes(`'<div class="ribbon checking"><span>Checking…</span></div>'`));
  }

  // ---- 28. the own playlist: its row opens its page, its settings are the owner's
  {
    const iptv = fs.readFileSync(path.join(__dirname, "static", "iptv.js"), "utf8");
    const settings = fs.readFileSync(path.join(__dirname, "static", "settings.js"), "utf8");
    check("28 a plugin's row opens its own page, and a refused channel says why",
          SOURCE.includes("if (cat.open) return cat.open();")
          && iptv.includes("open: () => viewMyChannels() }]);")
          && SOURCE.includes("if (code !== 429 && code !== 504 && code !== 404) return;") && !SOURCE.includes("mylive"));
    check("28 My channels stands at the foot of Start, not on Home",
          iptv.includes("window.PD_START_ROWS = (window.PD_START_ROWS || []).concat([")
          && !iptv.includes("CATS.push(")
          && SOURCE.includes("for (const cat of (window.PD_START_ROWS || [])) {"));
    const css28 = fs.readFileSync(path.join(__dirname, "static", "style.css"), "utf8");
    check("28 Start and Home scroll with no scrollbar, other pages keep theirs",
          css28.includes("html:has(#main > .sp),html:has(#main > .homemark){scrollbar-width:none}")
          && SOURCE.includes('here.className = "homemark";'));
    check("28 a music round on Continue watching wears Music, any other shuffle Shuffle",
          SOURCE.includes(`(/^music(:|$)/.test(it.shuffleId || "") ? "Music" : "Shuffle")`));
    check("28 its settings are buttons in a tab of their own, each press redrawing its row alone",
          iptv.includes("if (data.liveList === undefined) {")
          && iptv.includes('b.className = "btn ghost kind" + (isOn(one) ? " on" : "");')
          && iptv.includes("await onPress(one);\n          draw();")
          && !iptv.includes("liveGroups: next });\n          render();")
          && iptv.includes('window.PD_TABS.push(["iptv", "IP-TV", iptvTab]);')
          && settings.includes("if (plugged) await plugged[2](main, { get, post, block, render });")
          && iptv.includes("await save({ liveGroups: next });") && !iptv.includes("liveScreen")
          && SOURCE.includes("if (it && it.liveGroup && window.PD_OPEN_GROUP) return window.PD_OPEN_GROUP(it);")
          && iptv.includes("window.PD_OPEN_GROUP = (it) => viewMyChannels(it.liveGroup);")
          && iptv.includes("...(groupsOnly ? { more: 1 } : {}) }, null);"));
  }

  // ---- 29. a search shows the library at once and fills in the catalogue's films
  {
    check("29 a search still filling in is asked again in a moment",
          SOURCE.includes("if (c.fillingIn) filling = true;")
          && SOURCE.includes("if (filling && !asking && again < SEARCH_AGAIN_TIMES) {"));
  }

  // ---- 30. a search asked again redraws only what changed; the closest title first
  {
    check("30 the same answer again is not drawn again, and no Searching on a second ask",
          SOURCE.includes("const same = again > 0 && SEARCH_SIG.q === q && SEARCH_SIG.sig === sig;")
          && SOURCE.includes("if (!genreKeep && !again) main.innerHTML"));
    check("30 search rows by closeness, not by the shelf's order",
          SOURCE.includes("h.Metadata.sort((a, b) => (a.match ?? 9) - (b.match ?? 9));"));
    const ctl30 = fs.readFileSync(path.join(__dirname, "static", "controls.js"), "utf8");
    check("30 a still over the bar while it is moved or pointed at",
          ctl30.includes("peekAt(bar.scrub.value / 1000);") && SOURCE.includes("window.framePreview = (sec, w) =>"));
  }

  // ---- 31. live channels stepped through in the playlist's order; the monitor alone
  {
    const w = page("", ["liveStep"]);
    const L = [{ ratingKey: "a" }, { ratingKey: "b" }, { ratingKey: "c" }];
    check("31 next and previous channel, round at the ends",
          w.liveStep(L, "a", 1).ratingKey === "b" && w.liveStep(L, "c", 1).ratingKey === "a"
          && w.liveStep(L, "a", -1).ratingKey === "c" && w.liveStep(L, "x", 1).ratingKey === "a"
          && w.liveStep(L, "x", -1).ratingKey === "c" && w.liveStep([], "a", 1) === null);
    check("31 the monitor opens without the front page under it, once",
          SOURCE.includes('if (where !== "monitor" && SCREEN === FIRST_SCREEN) go(firstTab());')
          && SOURCE.includes('} else if (where === "settings" || where === "reports") {'));
    const set31 = fs.readFileSync(path.join(__dirname, "static", "settings.js"), "utf8");
    check("31 the queue moves one place up or down, and a series can be removed",
          set31.includes("keys[at] = keys[to];") && set31.includes('post("/shows/remove", { show: name })'));
  }

  // ---- 32. where a title streams, on every title's page
  {
    const w = page("", ["providersLine"], { esc: (t) => String(t), img: (u) => u });
    const line = w.providersLine({ providers: [{ name: "A", logo: "/art/provider/a.png", kind: "stream" },
                                               { name: "B", logo: "/art/provider/b.png", kind: "store" }] });
    check("32 the services' marks, a shop said as rent or buy, nothing for none",
          line.includes('src="/art/provider/a.png"') && line.includes("B - rent or buy")
          && w.providersLine({}) === "");
    check("32 on a film here, a programme, an offer and a series not held alike",
          (SOURCE.match(/badgesRow\([^;]*?, (m|s)\) \+/g) || []).length === 4);
    check("32 a service's catalogue is read from what api() hands back, the container itself",
          /const mc = d \|\| \{\};/.test(SOURCE) && !/d\.MediaContainer \|\| \{\}/.test(SOURCE));
    const c32 = page("", ["channelsWord"]);
    check("32 the channels as said: Stereo, 5.1, 7.1, nothing unknown",
          c32.channelsWord(2) === "Stereo" && c32.channelsWord(6) === "5.1"
          && c32.channelsWord(8) === "7.1" && c32.channelsWord(undefined) === "");
    const b32 = page("", ["badgesRow", "providersLine", "flagOf"], { esc: (t) => String(t), img: (u) => u });
    const row32 = b32.badgesRow(["1080p", "", "HEVC"], { providers: [{ name: "A", logo: "/a.png", country: "SE" }] });
    check("32 each fact a badge of its own, then Region and the services; nothing for nothing",
          (row32.match(/class="badge"/g) || []).length === 2 && row32.includes(">Region<")
          && row32.indexOf("HEVC") < row32.indexOf("Region") && b32.badgesRow([], {}) === "");
  }

  // ---- 33. the timeline's still box only with a still in it
  {
    const ctl33 = fs.readFileSync(path.join(__dirname, "static", "controls.js"), "utf8");
    const pa33 = fs.readFileSync(path.join(__dirname, "android", "app", "src", "main", "java",
                                           "se", "palladium", "tv", "PlayerActivity.kt"), "utf8");
    check("33 the box shows when a still has loaded, never empty",
          ctl33.includes('if (peekOn) { peek.src = img.src; peekShown = sec; peek.classList.add("on"); }')
          && ctl33.includes('if (peekShown >= 0) peek.classList.add("on");')
          && !/^\s*peek\.classList\.add\("on"\);\n\s*peekWant =/m.test(ctl33.replace(/\r\n/g, "\n"))
          && pa33.includes("if (previewShown >= 0) img.visibility = android.view.View.VISIBLE")
          && pa33.includes("if (got != null && previewOn) {"));
  }

  // ---- 34. the studios in Show more, not on the page
  {
    check("34 studios only in the Show more sheet",
          // the function itself, and the sheet
          (SOURCE.match(/studiosLine\(m\)/g) || []).length === 2
          && SOURCE.includes('insertAdjacentHTML("afterend", studiosLine(m))'));
    const ma34 = fs.readFileSync(path.join(__dirname, "android", "app", "src", "main", "java",
                                           "se", "palladium", "tv", "MainActivity.kt"), "utf8");
    check("34 and in the app: studios() drawn only in the Show more box, wrapping",
          (ma34.match(/\bstudios\(\)/g) || []).length === 1
          && ma34.includes("Box(Modifier.padding(bottom = 14.dp)) { studios() }"));
  }

  // ---- 35. the favorite and collection marks next to Play, then the rest
  {
    const ma35 = fs.readFileSync(path.join(__dirname, "android", "app", "src", "main", "java",
                                           "se", "palladium", "tv", "MainActivity.kt"), "utf8")
      .replace(/\r\n/g, "\n");
    const play35 = ma35.indexOf("startIt(resume)\n                }");
    check("35 app: marks straight after Play, before From start and Mark watched; alone on a series' row",
          play35 > 0 && ma35.indexOf("if (!full.offered && !full.askable) marks()", play35) > play35
          && ma35.indexOf("if (!full.offered && !full.askable) marks()", play35)
             < ma35.indexOf('Pill("From start"', play35)
          && ma35.indexOf("marks()\n                        Pill(\"Mark all watched\")") < 0
          && ma35.indexOf("verticalAlignment = Alignment.CenterVertically) {\n                marks()\n            }") > 0);
    check("35 web: the mark control before Mark watched",
          SOURCE.indexOf("slot.appendChild(markControl(m));")
          < SOURCE.indexOf("slot.appendChild(watchedButton(m, () => playFromStartOnly(m)));"));
  }

  // ---- 36. every passing bar over the shelves is counted out of the posters' height
  {
    const ma36 = fs.readFileSync(path.join(__dirname, "android", "app", "src", "main", "java",
                                           "se", "palladium", "tv", "MainActivity.kt"), "utf8");
    check("36 the notice, server and update bars all left out when posters are sized",
          ["notice", "server", "update"].every((n) => ma36.includes('browse.bars["' + n + '"] = it.size.height / dens')));
  }

  // ---- 37. the app's film badges: one row, the streaming marks in it; no year, no play mode
  {
    const ma37 = fs.readFileSync(path.join(__dirname, "android", "app", "src", "main", "java",
                                           "se", "palladium", "tv", "MainActivity.kt"), "utf8");
    check("37 film badges: no year chip, no Transcoded, streaming marks inside the badge row",
          !ma37.includes("full.year?.let { Chip(it.toString()) }") && !ma37.includes('else -> "Transcoded"')
          && ma37.indexOf("full.providers.forEach") > ma37.indexOf("// The badges in one row"));
  }

  // ---- 38. Start's Edit rows: a row hidden and one moved up are saved as shown
  {
    const sent = [];
    let redrawn = 0;
    const w = page('<div id="where"></div>', ["startForm"], {
      CFG: { guest: true }, toast: () => {}, viewStart: () => { redrawn++; },
      fetch: async (u, o) => { sent.push(JSON.parse(o.body)); return {}; } });
    const d = w.document;
    const front = { rowsMine: false, rowList: [
      { id: "shuffle", name: "Shuffle", on: true }, { id: "new-movie", name: "New films", on: true },
      { id: "new-show", name: "New series", on: true }, { id: "classic-movie", name: "Classics", on: false }] };
    w.startForm(d.querySelector("#where"), front);
    const rows = () => [...d.querySelectorAll(".homerow")];
    check("38 every row listed, the hidden one struck", rows().length === 4
          && rows()[3].querySelector(".homename").classList.contains("off"));
    rows()[0].querySelector("button").click();                // hide Shuffle
    rows()[2].querySelectorAll("button")[1].click();          // New series up
    const save = [...d.querySelectorAll("button")].filter((b) => b.textContent === "Save")[0];
    save.click();
    await tick();
    check("38 saved as shown: the kept in order, the hidden apart",
          sent.length === 1 && JSON.stringify(sent[0].startRows) === '["new-show","new-movie"]'
          && JSON.stringify(sent[0].startRowsOff) === '["shuffle","classic-movie"]' && redrawn === 1,
          JSON.stringify(sent));
  }

  // ---- 39. More like this: nothing carries every genre, so the set that finds most
  {
    const asked = [];
    const lib = [
      { ratingKey: "a", genres: ["Thriller", "Action", "Science Fiction", "Adventure"] },
      { ratingKey: "b", genres: ["Thriller", "Action", "Science Fiction", "Adventure"] },
      { ratingKey: "c", genres: ["Thriller", "Romance", "Science Fiction", "Adventure"] }];
    const w = page('<div id="main"></div>', ["genreFewer", "genreSets", "moreLikeThis"], {
      esc: (x) => x, sectionsOf: async () => ({ movie: 1 }), fits: () => 10,
      fromAll: async (f) => f(null),
      api: async (u, q) => { asked.push(q.genre);
        const ws = q.genre.split(",");
        return lib.filter((x) => ws.every((g) => x.genres.indexOf(g) >= 0)); } });
    w.grid = () => w.document.createElement("div");
    const box = await w.moreLikeThis({ ratingKey: "s", type: "movie", year: 1997,
      genres: ["Thriller", "Action", "Romance", "Science Fiction", "Adventure"] });
    const tags = box ? [...box.querySelectorAll(".tag")].map((t) => t.textContent) : [];
    check("39 widened to the four that find most, the tags saying so",
          !!box && JSON.stringify(tags) === '["#thriller","#action","#science fiction","#adventure"]',
          JSON.stringify(tags) + " " + asked.length);
  }

  // ---- 40. a film not out yet says its date where Play would be
  {
    const w = page("", ["upcomingLine"]);
    check("40 the date, said short", w.upcomingLine("2026-12-15") === "Out 15 Dec 2026",
          w.upcomingLine("2026-12-15"));
    check("40 no date, no made-up one", w.upcomingLine(null) === "Not out yet" &&
          w.upcomingLine("2026-13-01") === "Not out yet");
    check("40 viewMovie draws no Play for it",
          /m\.upcoming\s*\?[^:]*upcomingLine\(m\.originallyAvailableAt\)[\s\S]{0,120}:\s*playerBar\(m, resume\)/
            .test(take("viewMovie")));
  }

  // ---- 41. a poster's download badge moves with each poll, and says Arriving once gone
  {
    const w = page(
      '<div class="card" data-key="k1"><div class="offerbadge"><span>3%</span></div></div>' +
      '<div class="card" data-key="k2"><div class="offerbadge"><span>Queued</span></div></div>',
      ["offerWord", "offerVersion", "offerBadgeText", "moveOfferBadges"],
      { esc: (s) => String(s == null ? "" : s), etaShort: (s) => Math.round(s / 60) + " min",
        badgeKeys: new Set(), CSS: { escape: (s) => s } });
    const d = w.document;
    const kept = d.querySelector('[data-key="k1"] .offerbadge');
    w.moveOfferBadges([{ ratingKey: "k1", offer: { state: "downloading", progress: 0.42, eta: 360,
                                                   version: "1080p WEB-DL" } },
                       { ratingKey: "k2", offer: { state: "queued" } }]);
    const one = d.querySelector('[data-key="k1"] .offerbadge span');
    check("41 the per cent moved on", /^42% · 6 min/.test(one.textContent), one.textContent);
    check("41 with the version under it", !!one.querySelector("small.ver"));
    check("41 the badge is the element it was", d.querySelector('[data-key="k1"] .offerbadge') === kept);
    w.moveOfferBadges([{ ratingKey: "k2", offer: { state: "downloading", progress: 0.01 } }]);
    check("41 one gone from the list says Arriving",
          d.querySelector('[data-key="k1"] .offerbadge span').textContent === "Arriving");
    check("41 the next one started",
          /^1%/.test(d.querySelector('[data-key="k2"] .offerbadge span').textContent));
  }

  // ---- 42. a film not out yet opens its own page; a coming episode its series
  {
    const html = fs.readFileSync(path.join(__dirname, "static", "index.html"), "utf8")
      .replace(/<script[^>]*src=[^>]*><\/script>/g, "");
    const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://x/", pretendToBeVisual: true });
    const w = dom.window;
    w.fetch = () => new Promise(() => {});
    w.eval(SOURCE);
    const went = [];
    w.open = (it) => went.push(["open", it.ratingKey]);
    w.viewShow = (show) => went.push(["show", show]);
    w.card({ ratingKey: "f1", type: "movie", title: "A Film", upcoming: true,
             originallyAvailableAt: "2099-12-15" }).click();
    w.card({ ratingKey: "e1", type: "episode", title: "An Episode", upcoming: true,
             grandparentRatingKey: "s1", parentIndex: 2, airs: "Fri" }).click();
    check("42 the film opens its page", JSON.stringify(went[0]) === '["open","f1"]', JSON.stringify(went));
    check("42 the episode opens its series", JSON.stringify(went[1]) === '["show","s1"]', JSON.stringify(went));
  }

  // ---- 43. a film page whose release is coming in keeps its progress button
  {
    const html = fs.readFileSync(path.join(__dirname, "static", "index.html"), "utf8")
      .replace(/<script[^>]*src=[^>]*><\/script>/g, "");
    const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://x/", pretendToBeVisual: true });
    const w = dom.window;
    const answers = { "/tracker/list": { free: 100, versions: [{ id: "9", name: "F.2023.2160p", size: 8e9, seeds: 5 }] },
                      "/tracker/active": { taking: [] } };
    w.fetch = (u) => Promise.resolve({ json: () => Promise.resolve(answers[String(u)] || {}) });
    w.eval(SOURCE);
    w.moreLikeThis = () => Promise.resolve(null);
    w.castRow = () => {};
    w.api = () => new Promise(() => {});
    w.document.body.appendChild(w.document.createElement("main"));
    w.eval("main = document.querySelector('main')");
    w.viewOffer({ ratingKey: "oA", type: "movie", title: "F", year: 2023, offered: true,
                  offer: { size: 6e9, state: "downloading", progress: 0.4, file: "F.2023.WEB.mkv" } });
    await new Promise((r) => setTimeout(r, 50));
    const d = w.document;
    check("43 the progress button shows", d.getElementById("offerget").style.display !== "none" &&
          /40%/.test(d.getElementById("offerget").textContent), d.getElementById("offerget").textContent);
    check("43 no second Download beside it", d.getElementById("offertracker").style.display === "none");
    // the server's words: looking for seeders is not "Downloading 0%"
    w.viewOffer({ ratingKey: "oB", type: "movie", title: "F", year: 2023, offered: true,
                  offer: { size: 6e9, state: "downloading", progress: 0, file: "F.mkv",
                           stage: "finding", words: "Looking for seeders" } });
    await new Promise((r) => setTimeout(r, 50));
    check("43 the button says what it is doing, and is its Cancel", /Cancel.*Looking for seeders/.test(d.getElementById("offerget").textContent),
          d.getElementById("offerget").textContent);
  }

  // ---- 44. a series or film without a key is not asked for: it was a 404 on every restore
  {
    const html = fs.readFileSync(path.join(__dirname, "static", "index.html"), "utf8")
      .replace(/<script[^>]*src=[^>]*><\/script>/g, "");
    const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://x/", pretendToBeVisual: true });
    const w = dom.window;
    const asked = [];
    w.fetch = (u) => { asked.push(String(u)); return new Promise(() => {}); };
    w.eval(SOURCE);
    w.viewShow(undefined); w.viewShow("undefined"); w.viewMovie(null);
    w.sessionStorage.setItem("pd-screen", JSON.stringify({ kind: "show", key: "undefined" }));
    await w.restoreScreen();
    check("44 nothing asked for a missing key", !asked.some((u) => /undefined|null/.test(u)), JSON.stringify(asked));
    check("44 the word for it", w.keyed("e12") && !w.keyed(undefined) && !w.keyed("undefined") && !w.keyed(""));
  }

  // ---- 45. a title the tracker has not been asked about says Checking, not Request
  {
    const html = fs.readFileSync(path.join(__dirname, "static", "index.html"), "utf8")
      .replace(/<script[^>]*src=[^>]*><\/script>/g, "");
    const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://x/", pretendToBeVisual: true });
    const w = dom.window;
    w.fetch = (u) => Promise.resolve({ json: () => Promise.resolve(String(u) === "/tracker/list" ? { versions: [] }
                                                                   : { taking: [] }) });
    w.eval(SOURCE);
    w.moreLikeThis = () => Promise.resolve(null);
    w.castRow = () => {};
    w.api = () => new Promise(() => {});
    w.document.body.appendChild(w.document.createElement("main"));
    w.eval("main = document.querySelector('main')");
    w.viewOffer({ ratingKey: "t1", type: "movie", title: "New", year: 2026, askable: true, checking: true });
    await new Promise((r) => setTimeout(r, 50));
    const d = w.document;
    check("45 the button says Checking while the tracker is asked", /Checking/.test(d.getElementById("offerget").textContent)
          && d.getElementById("offerget").disabled, d.getElementById("offerget").textContent);
    check("45 and the ribbon with it", /Checking/.test(d.querySelector(".poster .ribbon span").textContent));
    w.viewOffer({ ratingKey: "t2", type: "movie", title: "Old", year: 2020, askable: true, checking: false });
    await new Promise((r) => setTimeout(r, 50));
    check("45 asked about and nothing found: Request", /^Request/.test(d.getElementById("offerget").textContent),
          d.getElementById("offerget").textContent);
  }

  // ---- 46. a film coming in from a pack: its Download is its Cancel - once to arm, again to stop
  {
    const html = fs.readFileSync(path.join(__dirname, "static", "index.html"), "utf8")
      .replace(/<script[^>]*src=[^>]*><\/script>/g, "");
    const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://x/", pretendToBeVisual: true });
    const w = dom.window;
    const posted = [];
    w.fetch = (u, o) => { if (o && o.method === "POST") posted.push(String(u));
      return Promise.resolve({ json: () => Promise.resolve(String(u) === "/torrents/cancel" ? { ok: true }
        : String(u) === "/tracker/list" ? { versions: [] } : { taking: [] }) }); };
    w.eval(SOURCE);
    w.moreLikeThis = () => Promise.resolve(null);
    w.castRow = () => {};
    w.api = () => new Promise(() => {});
    w.toast = () => {};
    w.drawDownloadBanner = () => {};
    w.document.body.appendChild(w.document.createElement("main"));
    w.eval("main = document.querySelector('main')");
    w.viewOffer({ ratingKey: "oP", type: "movie", title: "P", year: 2020, offered: true,
                  offer: { size: 5e9, state: "downloading", progress: 0.5, file: "P.mkv" } });
    await new Promise((r) => setTimeout(r, 50));
    const b = w.document.getElementById("offerget");
    check("46 coming in: the button is its Cancel", /^✕ Cancel/.test(b.textContent), b.textContent);
    b.click();
    check("46 one press arms it, nothing stopped", /Press again/.test(b.textContent) && !posted.includes("/torrents/cancel"));
    b.click();
    await new Promise((r) => setTimeout(r, 30));
    check("46 the second stops it", posted.includes("/torrents/cancel"), JSON.stringify(posted));
  }

  // ---- 47. Settings, Health: the problems first, or All good; then the machines and the rest
  {
    const SET = fs.readFileSync(path.join(__dirname, "static", "settings.js"), "utf8").replace(/\r\n/g, "\n");
    const at = SET.indexOf("\n  function healthBlocks(");
    const fn = SET.slice(at + 1, SET.indexOf("\n  }\n", at) + 4);
    const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
    const w = dom.window;
    w.eval("const block = (t) => { const e = document.createElement('div'); e.className = 'setblock';" +
           " e.innerHTML = '<h3>' + t + '</h3>'; return e; };" +
           "const esc = (t) => String(t == null ? '' : t);\n" + fn + "\nwindow.healthBlocks = healthBlocks;");
    const ok = w.healthBlocks({ machines: [{ name: "Main", build: "1.2", up: true }], problems: [],
                                facts: { disks: [{ drive: "D:", freeGB: 100, downloads: true }],
                                         backup: { at: 1000, ok: true, parts: {} }, torrents: [], workers: [] } }, 2000);
    check("47 nothing wrong: All good, in green", ok.querySelector("h3").textContent === "All good"
          && ok.querySelector(".setblock").classList.contains("good"));
    check("47 the machines and the disks under it", /Main/.test(ok.textContent) && /1\.2/.test(ok.textContent)
          && /100 GB free/.test(ok.textContent));
    const bad = w.healthBlocks({ machines: [{ name: "Copy", up: false }],
                                 problems: ["Copy is not answering", "D: has 5 GB free for downloads"],
                                 facts: { torrents: [{ name: "T", state: "checkingDL", since: 1000 }] } }, 2200);
    check("47 trouble: how many, each said, the machine down, the hung check",
          bad.querySelector("h3").textContent === "2 things need you" && /Copy is not answering/.test(bad.textContent)
          && /not answering/.test(bad.textContent) && /checkingDL/.test(bad.textContent) && /20 min/.test(bad.textContent)
          && /no backup has run/.test(bad.textContent));
    check("47 a server is a dot - green when it answers, red when not - and no word up",
          ok.querySelector(".updot.up") !== null && bad.querySelector(".updot.down") !== null
          && !/up/.test(ok.textContent));
  }

  // ---- 48. a pack copy of a film here already: Play plays the film here while it comes in
  {
    const html = fs.readFileSync(path.join(__dirname, "static", "index.html"), "utf8")
      .replace(/<script[^>]*src=[^>]*><\/script>/g, "");
    const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://x/", pretendToBeVisual: true });
    const w = dom.window;
    w.fetch = (u) => Promise.resolve({ json: () => Promise.resolve(String(u) === "/tracker/list"
                                                                   ? { versions: [] } : { taking: [] }) });
    w.eval(SOURCE);
    w.moreLikeThis = () => Promise.resolve(null);
    w.castRow = () => {};
    const played = [];
    w.api = (p) => Promise.resolve(p === "/library/metadata/m1"
      ? { Metadata: [{ ratingKey: "m1", type: "movie", title: "Held", viewOffset: 61000 }] } : {});
    w.play = (it, at) => played.push([it.ratingKey, at]);
    w.document.body.appendChild(w.document.createElement("main"));
    w.eval("main = document.querySelector('main')");
    w.viewOffer({ ratingKey: "oH", type: "movie", title: "Held", year: 2014, offered: true,
                  offer: { size: 5e9, state: "downloading", progress: 0.5, file: "H.mkv", heldKey: "m1" } });
    await new Promise((r) => setTimeout(r, 30));
    const b = w.document.getElementById("offerheld");
    check("48 a Play beside the download", !!b && /Play/.test(b.textContent));
    if (b) b.click();
    await new Promise((r) => setTimeout(r, 30));
    check("48 it plays the copy here, from where it was left", JSON.stringify(played) === '[["m1",61]]',
          JSON.stringify(played));
    w.viewOffer({ ratingKey: "oN", type: "movie", title: "New", year: 2020, offered: true,
                  offer: { size: 5e9, state: "", file: "N.mkv" } });
    await new Promise((r) => setTimeout(r, 30));
    check("48 nothing here: no Play", !w.document.getElementById("offerheld"));
  }
  // ---- 50. a version this house holds says On disk, from a pack or the tracker alike
  {
    const w = page("", ["versionTag"]);
    const said = [{ disk: true, have: true }, { pack: true, have: true }, { have: true },
                  { pack: true }, { box: true, seasonPack: true }, {}].map((v) => w.versionTag(v));
    check("50 held versions say On disk, the rest where they come from",
          JSON.stringify(said) === '["On disk","On disk","On disk","In a pack here","Season pack","Tracker"]',
          JSON.stringify(said));
    check("50 no list says it twice", !/on disk already/i.test(SOURCE), "");
  }

  // ---- 51. an episode's other versions: the one here says On disk, its button too
  {
    const html = fs.readFileSync(path.join(__dirname, "static", "index.html"), "utf8")
      .replace(/<script[^>]*src=[^>]*><\/script>/g, "");
    const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://x/", pretendToBeVisual: true });
    const w = dom.window;
    w.fetch = async () => ({ json: async () => ({ free: 50, versions: [
      { id: "1", name: "Show S01E01 1080p WEB H264-A", size: 9e8, seeds: 30, have: true },
      { id: "2", name: "Show S01E01 1080p x265-B", size: 4e8, seeds: 12 }] }) });
    w.eval(SOURCE);
    const row = w.document.createElement("div");
    row.innerHTML = '<div class="info"></div><button class="eptick">x</button>';
    await w.episodeDownloads(row, { ratingKey: "e1", grandparentTitle: "Show" });
    const tick = row.querySelector(".eptick");
    const held = tick.style.alignSelf === "flex-start" && tick.style.marginTop !== "";
    const lines = [...row.querySelectorAll(".epdlrow")];
    const btn = (i) => lines[i] && lines[i].querySelector("button");
    check("51 the one here: On disk, nothing to press; the other: Download",
          lines.length === 2 && /^On disk/.test(lines[0].textContent) && btn(0).textContent === "On disk"
          && btn(0).disabled && btn(1).textContent === "⤓ Download" && !btn(1).disabled,
          lines.map((l) => l.textContent).join(" | "));
    await w.episodeDownloads(row, { ratingKey: "e1", grandparentTitle: "Show" });
    check("51 the row's marks held at the top while the list is open, let go when it closes",
          held && tick.style.alignSelf === "" && tick.style.marginTop === "" && !row.querySelector(".epdl"),
          tick.style.cssText);
  }

  // ---- 52. the film page's version menu: a version here already is chosen as the copy it is
  {
    const w = page('<div class="verline"><select id="s"><option value="0">F.2000.1080p.x264-A.mkv · 1080p</option>' +
                   '<option value="1">F.2000.2160p-B.mkv · 4K</option><option value="dl:1">a</option>' +
                   '<option value="dl:2">b</option></select></div>', ["offerDownload"]);
    w.Event = w.Event || Event;
    const sel = w.document.getElementById("s");
    let changed = "";
    sel.addEventListener("change", () => { changed = sel.value; });
    sel._downloads = { free: 40, rows: [{ id: "1", have: true, size: 5e9, name: "F.2000.2160p-B" },
                                        { id: "2", size: 6e9, name: "F.2000.720p-C" }] };
    sel.value = "dl:1";
    w.offerDownload({ title: "F", year: 2000 }, sel);
    const none = !w.document.querySelector(".dlhere");
    sel.value = "dl:2";
    w.offerDownload({ title: "F", year: 2000 }, sel);
    const two = w.document.querySelector(".dlhere .btn");
    check("52 the one here becomes the chosen copy, no button; another gets Download",
          none && changed === "1" && /^⤓ Download/.test(two.textContent) && !two.disabled,
          changed + " | " + (two && two.textContent));
  }

  // ---- 53. a download just started says so, not Arriving, until its progress comes
  check("53 a started download is not said to be arriving",
        !SOURCE.includes('"Downloading - arriving"') && /: "Starting the download\u2026";/.test(SOURCE), "");

  // ---- 54. a Start slide plays only a film that is here; one not out yet says when
  {
    const w = page("", ["upcomingLine", "heroAct"]);
    const said = [{ type: "movie", upcoming: true, originallyAvailableAt: "2026-12-15" },
                  { type: "movie", askable: true }, { type: "movie", offered: true },
                  { type: "movie" }, { type: "show" }]
      .map((it) => { const a = w.heroAct(it); return a.word + (a.plays ? "+" : ""); });
    check("54 not out: its date; not here: Request; a pack's: Get it; here: Play",
          JSON.stringify(said) === JSON.stringify(["Out 15 Dec 2026", "Request", "\u2913 Get it", "\u25b6 Play+", "\u25b6 Watch"]),
          JSON.stringify(said));
  }

  // ---- 55. a film not here can go on the watchlist and in a collection from its page
  check("55 the offer page carries the watchlist and collection marks",
        /async function viewOffer\(m\) \{[\s\S]*?marks\.insertBefore\(markControl\(m\), wrap\.querySelector\("#offersaid"\)\)[\s\S]*?main\.appendChild\(wrap\);/
          .test(SOURCE), "");

  // ---- 56. Settings: every section's tab is a tab there is, and every old tab name opens one
  {
    const SET = fs.readFileSync(path.join(__dirname, "static", "settings.js"), "utf8");
    const owner = SET.match(/const OWNER_TABS = \[([\s\S]*?)\];/)[1];
    const ids = [...owner.matchAll(/\["([a-z]+)",/g)].map((m) => m[1]);
    const table = SET.match(/const SECTION_TAB = \{([\s\S]*?)\};/)[1];
    const goes = [...table.matchAll(/": "([a-z]+)"/g)].map((m) => m[1]);
    const old = SET.match(/const OLD_TAB = \{([\s\S]*?)\};/)[1];
    const oldGoes = [...old.matchAll(/: \["([a-z]+)"/g)].map((m) => m[1]);
    check("56 ten tabs, every section and every old name lands on one of them",
          ids.length === 10 && goes.length >= 25 && goes.every((g) => ids.includes(g))
          && oldGoes.every((g) => ids.includes(g)),
          JSON.stringify({ ids, bad: goes.concat(oldGoes).filter((g) => !ids.includes(g)) }));
  }

  // ---- 57. Settings: a section put on a page puts it on one its tab has
  {
    const SET = fs.readFileSync(path.join(__dirname, "static", "settings.js"), "utf8");
    const pagesSrc = SET.match(/const PAGES = \{([\s\S]*?)\};/)[1];
    const named = new Set([...pagesSrc.matchAll(/\["([a-z]+)", "/g)].map((m) => m[1]).concat(["shelves", "library"]));
    const placed = [...SET.match(/const SECTION_PAGE = \{([\s\S]*?)\};/)[1].matchAll(/": "([a-z]+)"/g)].map((m) => m[1]);
    check("57 every page a section is put on exists", placed.length >= 10 && placed.every((p) => named.has(p)),
          JSON.stringify(placed.filter((p) => !named.has(p))));
  }

  // ---- 58. a film's series on its poster: pressed it steps, the place and the name under it
  {
    const w = page('<div class="detail"><div class="poster"><img src="own.jpg"></div><div class="meta"><h1>B</h1></div></div>',
                   ["sagaRow", "openKey"],
                   { api: async () => ({ Metadata: [{ ratingKey: "a", title: "A", year: 1999, thumb: "/a" },
                                                    { ratingKey: "b", title: "B", year: 2005, at: true },
                                                    { ratingKey: "c", title: "C", year: 2020, thumb: "/c" }] }),
                     items: (c) => c.Metadata, img: (p) => "img:" + p });
    const wrap = w.document.querySelector(".detail");
    await w.sagaRow(wrap, "b");
    const P = wrap.querySelector(".poster");
    const went = [];
    w.openKey = (k) => went.push(k);
    const foot = () => wrap.querySelector(".sagacol > .sagaat").textContent;
    const quiet = foot() === "2 of 3" && P.querySelector(".sagastep.next").style.display === "none";
    w.SAGA_KEYS.step(1);                                   // not pressed yet: no stepping
    const still = P.querySelector("img").getAttribute("src") === "own.jpg";
    P.click();                                             // pressed: it starts
    const going = /^B \(2005\)/.test(foot()) && P.querySelector(".sagastep.next").style.display === "";
    P.querySelector(".sagastep.next").click();             // stepped on: C on show, named under it
    const onC = /^C \(2020\)/.test(foot()) && P.querySelector("img").getAttribute("src") === "img:/c";
    P.click();                                             // chosen: C opens
    check("58 the poster starts stepping only when pressed; the place and the name under it; another one opens",
          quiet && still && going && onC && JSON.stringify(went) === '["c"]', foot() + " " + JSON.stringify(went));
    // with a mouse: over it the arrows come up, off it the poster is its own again
    P.dispatchEvent(new w.MouseEvent("mouseleave"));
    const back = foot() === "2 of 3" && P.querySelector(".sagastep.next").style.display === "none";
    P.dispatchEvent(new w.MouseEvent("mouseenter"));
    const up = /^B \(2005\)/.test(foot()) && P.querySelector(".sagastep.next").style.display === "";
    check("58 a mouse over the poster brings the arrows up, off it the poster is its own again", back && up, foot());
  }

  // ---- 49. a card not out yet wears its date on the ribbon, Upcoming without one
  {
    const w = page("", ["upcomingBand"]);
    const today = new w.Date(2026, 9, 29);
    const said = [{ originallyAvailableAt: "2026-12-15" }, { airDate: "2027-01-03" },
                  { airDate: "2027-10-01" }, {}, { airDate: "2026-10-29" }, { airDate: "2026-13-01" }]
      .map((it) => w.upcomingBand(it, today));
    check("49 the date, short; month and year far off; Upcoming without a date to come",
          JSON.stringify(said) === '["15 Dec","3 Jan","Oct 2027","Upcoming","Upcoming","Upcoming"]',
          JSON.stringify(said));
    check("49 the card's ribbon says it", /it\.upcoming \? '<div class="ribbon soon"><span>' \+ upcomingBand\(it\)/
          .test(SOURCE), "");
  }

  // ---- 51. the Add page's packs are a list on show, a doubtful one saying so first
  check("51 the list is open, not a menu", /pick\.size = Math\.min\(12, pick\.options\.length\);/.test(SOURCE) &&
        /pick\.classList\.toggle\("openlist", pick\.size > 1\)/.test(SOURCE));
  check("51 a pack of either series says so before its name",
        /new Option\(\[p\.unsure \? "Either series" : "", p\.name,/.test(SOURCE));

  // ---- 52. a film taken from the tracker: finished, it opens its own page, not Download again
  {
    const html = fs.readFileSync(path.join(__dirname, "static", "index.html"), "utf8")
      .replace(/<script[^>]*src=[^>]*><\/script>/g, "");
    const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://x/", pretendToBeVisual: true });
    const w = dom.window;
    let polls = 0;
    const row = { hash: "h1", key: "rA", title: "New", year: 2026, progress: 0.5 };
    w.fetch = (u) => Promise.resolve({ json: () => Promise.resolve(
      String(u) === "/tracker/list" ? { free: 100, versions: [{ id: "9", name: "New.2026.1080p", size: 5e9, seeds: 5 }] }
      : String(u) === "/tracker/active" ? { taking: [polls++ ? Object.assign({}, row, { done: true, progress: 1 }) : row] }
      : {}) });
    w.eval(SOURCE);
    w.moreLikeThis = () => Promise.resolve(null);
    w.castRow = () => {};
    const opened = [];
    w.api = () => Promise.resolve({ Metadata: [{ ratingKey: "lib7", type: "movie", title: "New" }] });
    w.eval("viewMovie = (k) => { window._opened = k; }");
    w.document.body.appendChild(w.document.createElement("main"));
    w.eval("main = document.querySelector('main')");
    w.viewOffer({ ratingKey: "rA", type: "movie", title: "New", year: 2026, askable: true, onTracker: true });
    await new Promise((r) => setTimeout(r, 400));
    const g = w.document.getElementById("offertracker");
    check("52 coming in: the button is its Cancel, the list drawn after it or not", g && /Cancel/.test(g.textContent), g && g.textContent);
    await new Promise((r) => setTimeout(r, 3400));
    check("52 finished: the film's own page opens", w._opened === "lib7", String(w._opened));
    check("52 and the button never says Download again", g && !/Download/.test(g.textContent.replace("Downloaded", "")), g && g.textContent);
  }

  // ---- 50. an episode asked for: the season is drawn again once it can be played
  {
    let answer = [{ index: 3, offered: true, ratingKey: "p3" }], drawn = [], again = 0, said = [];
    const w = page('<select id="season"><option value="k1" selected>1</option><option value="k2">2</option></select>' +
                   '<div id="eplist"></div>', ["reloadWhenIn"], {
      api: async () => answer, items: (c) => c, toast: (t) => said.push(t),
      loadEpisodes: (k, key) => drawn.push([k, key]), EPISODE_LANDS_EVERY: 1,
      setTimeout: () => { again += 1; } });
    await w.reloadWhenIn("k1", 3);
    check("50 still only on offer: nothing drawn, looked at again", !drawn.length && again === 1);
    answer = [{ index: 3, ratingKey: "e3" }];
    w.document.getElementById("eplist").innerHTML = '<div class="epdl"></div>';
    await w.reloadWhenIn("k1", 3);
    check("50 a release list open is not closed under the reader", !drawn.length && again === 2);
    w.document.getElementById("eplist").innerHTML = "";
    await w.reloadWhenIn("k1", 3);
    check("50 in: the season drawn on it, and said", JSON.stringify(drawn) === '[["k1","e3"]]' && said.length === 1 && again === 2);
    w.document.getElementById("season").value = "k2";
    await w.reloadWhenIn("k1", 3);
    check("50 another season open: it stops", drawn.length === 1 && again === 2);
  }

  console.log(FAILS.length ? "WEB FAILED: " + FAILS.length : "WEB PASSED");
  FAILS.forEach((f) => console.log("  - " + f));
  process.exit(FAILS.length ? 1 : 0);
})().catch((e) => { console.log("WEB FAILED: the test itself stopped"); console.log(e); process.exit(1); });
