/* Palladium - browse the library, play in the browser or cast to a Chromecast.

   What costs the server:
     * subtitles default to OFF. A picture-based track has to be drawn into the
       image, which forces a full re-encode even where the video could have been
       copied. Burning is opt-in per title.
     * the cast profile advertises AC3/EAC3 so multichannel audio is copied
       instead of re-encoded to AAC.
     * "Original" quality sets no bitrate cap, so a high-bitrate source is not
       re-encoded just to fit under one.
   With subtitles off, an h264 source is video:copy - ffmpeg only remuxes. */

/* Faults, reported the moment they happen - installed before anything else runs.
   This used to sit at the end of the file, so anything throwing while the file was
   being read took the listener down with it: the page half worked, whatever was
   wired up after the fault did nothing, and nothing anywhere said why. */
(function reportOwnFaults() {
  const seen = {};
  const send = (text) => {
    const key = text.slice(0, 120);
    if (seen[key] || Object.keys(seen).length > 12) return;
    seen[key] = 1;
    fetch("/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "error", text: text.slice(0, 1500), app: "web" }),
    }).catch(() => {});
  };
  window.addEventListener("error", (e) => {
    if (!e || !e.message) return;
    send(e.message + " at " + (e.filename || "?").split("/").pop() + ":" + e.lineno);
  });
  window.addEventListener("unhandledrejection", (e) => {
    const r = e && e.reason;
    send("unhandled: " + ((r && (r.stack || r.message)) || String(r)));
  });
})();

let CFG = null;
const CLIENT_ID = (() => {
  let id = localStorage.getItem("palladium-client-id");
  if (!id) {
    id = "palladium-" + Math.random().toString(36).slice(2, 12);
    localStorage.setItem("palladium-client-id", id);
  }
  return id;
})();
/* The build this page was served from, asked for once and then sent with every
   request - which is how a tab left open on last week's build can be told apart from
   one that has been reloaded since. Empty until the answer comes back. */
let PAGE_BUILD = "";
fetch("/build").then((r) => r.json()).then((said) => {
  PAGE_BUILD = said.version || "";
}).catch(() => { /* an older server, or a friend's: the page still works */ });

const $ = (s) => document.querySelector(s);
const main = $("#main");
const SECTIONS = { movie: null, show: null };
/* Navigation. Every step deeper also pushes a browser history entry, so the
   mouse's back button (and Alt+Left, and the on-screen arrow) all come back
   through popstate and walk the same stack one step at a time. */
let navStack = [];
let onResize = null;
let playerEntry = false;   // the open player owns a history entry of its own
let suppressPop = false;   // set when we consume an entry ourselves

/* Where a list was when a title was opened from it, so back lands on that title rather
   than at the top of a library of a thousand films. */
let returnTo = null;
let returning = false;

function restoreListPlace() {
  const want = returning ? returnTo : null;
  returning = false;
  if (!want) return;
  returnTo = null;
  requestAnimationFrame(() => {
    const card = want.key &&
      main.querySelector('.card[data-key="' + CSS.escape(String(want.key)) + '"]');
    if (card) card.scrollIntoView({ block: "center" });
    else window.scrollTo(0, want.scroll || 0);
  });
}

function pushView(fn) {
  navStack.push(fn);
  window.history.pushState({ depth: navStack.length }, "");
}

function goBack() {
  if (backToFilm()) return;          // came here from a film: go back to it
  returning = true;                  // the list drawn next scrolls to the title left
  navStack.pop();                                   // leave the current view
  const prev = navStack[navStack.length - 1];       // and re-render the one below
  if (prev) prev();
  else {
    $("#back").classList.remove("on");
    go(document.querySelector(".tab.active").dataset.view);
  }
}

/* Chrome draws its own control overlay whenever a <video> element is itself the
   fullscreen element, and that overlay ignores the controls attribute - including the
   "press Esc to exit" toast, which wakes it up again. Fullscreening the container
   instead keeps the video an ordinary child, so our own idle logic still governs. */
/* Full screen, in whatever spelling this browser has. Safari has no
   requestFullscreen on an element at all, and calling it threw a TypeError where a
   rejected promise was expected - which took the whole page down rather than leaving
   the film playing in a window. */
function askFullscreen(box) {
  const go = box && (box.requestFullscreen || box.webkitRequestFullscreen ||
                     box.webkitRequestFullScreen);
  if (go) {
    const r = go.call(box);
    return r && r.catch ? r : { catch: () => {} };
  }
  const v = $("#video");                     // an iPhone: the system player, or nothing
  if (v && v.webkitEnterFullscreen) v.webkitEnterFullscreen();
  return { catch: () => {} };
}

function leaveFullscreen() {
  const shut = document.exitFullscreen || document.webkitExitFullscreen;
  if (!shut) return;
  const r = shut.call(document);
  if (r && r.catch) r.catch(() => {});
}

document.addEventListener("fullscreenchange", () => {
  const fs = document.fullscreenElement;
  if (fs && fs.id === "video") {
    // The video alone renders when it is the full-screen element, and everything the
    // page draws over it - subtitles, the transport bar, the notices - is simply not
    // there. Hand full screen to the player, which contains all of it.
    askFullscreen($("#player")).catch(() => {
      // if the swap is refused, come out rather than watch without any of the chrome
      leaveFullscreen();
      toast("Press f for full screen");
    });
  }
});

/* Chrome puts a <video> into full screen on a double click, before anything of ours
   runs. Taking the double click is the only way to keep that from happening. */
if ($("#video")) {
  $("#video").addEventListener("dblclick", (e) => {
    e.preventDefault();
    const player = $("#player");
    if (document.fullscreenElement) leaveFullscreen();
    else askFullscreen(player);
  });
}

window.addEventListener("popstate", () => {
  if (suppressPop) { suppressPop = false; return; }
  // a film left open behind the settings page is the first thing back should return
  // to; the player's own entry is still below it, and stops the film when reached
  if (leftPlayerOpen && backToFilm()) return;
  if (playerEntry) {
    playerEntry = false;
    stop();
    // and out onto the homepage, drawn again, as closing the player does. This
    // branch stopped the film and left whatever was behind it on screen - a page
    // from before the film, with Continue watching still at the place it started
    // from. closePlayer is not called: the history step it would take is this one.
    go("home");
    return;
  }
  goBack();
});

/* ---------------- friends' servers ----------------
   A friend is another Palladium, reached by the link they sent: an origin and a
   token. Their libraries can be shown alongside yours, and each can be switched
   off without forgetting the invitation. */

const FRIENDS_KEY = "palladium-friends";
const SECCACHE = {};          // section numbers differ per server; asked once each

function friends() {
  try { return JSON.parse(localStorage.getItem(FRIENDS_KEY)) || []; }
  catch (e) { return []; }
}

/**
 * A row, put right by what its address says it is.
 *
 * A row keeps the name it was added under, and an address can come to belong to
 * another machine - a link handed on, a port moved, a row written while the cache was
 * answering for the main server. The name then belongs to one machine and the address to
 * another, and the list says you are already on a machine you have never opened,
 * because by its address the row is right and only its name is wrong.
 */
function putRight(f, said) {
  if (!f || !said || !said.id) return f;
  const fixed = Object.assign({}, f, { machine: said.id });
  if (said.name && said.name !== f.name) fixed.name = said.name;
  return fixed;
}

function saveFriends(list) {
  localStorage.setItem(FRIENDS_KEY, JSON.stringify(list));
}

function friendById(id) {
  return friends().filter((f) => f.id === id)[0] || null;
}

/* null stands for "this server", so a merge can treat it like any other */
function shownServers() {
  // A server picked by hand is the one being looked at, on its own. The shelves
  // merge everything that is shown, which is right when nobody has chosen - and
  // wrong the moment somebody does: picking the machine that keeps copies and
  // getting the same merged shelf back is picking nothing.
  if (CTX) return [CTX];
  return [null].concat(friends().filter((f) => f.on !== false));
}

let CTX = null;               // whose server the open title belongs to

/* Said out loud on the window, because the settings page is another script.
 *
 * One script's top-level binding is readable from another only by grace and
 * ordering, and a page that quietly reads nothing looks exactly like a page that
 * read the wrong thing - which is how Settings came to name this computer while
 * another machine's settings were on the screen. */
function nowOn(s) {
  CTX = s;
  window.PD_ON = s;
}
const srvOf = (it) => (it && it.__s ? friendById(it.__s) : null);

/* Parse an invitation. Accepts the whole link, which is what people paste. */
function parseInvite(text) {
  const m = String(text).trim().match(/^(https?:\/\/[^\/]+)\/s\/([A-Za-z0-9_-]{8,})/);
  if (!m) return null;
  return { origin: m[1], token: m[2] };
}

async function sectionsOf(srv) {
  if (!srv) return SECTIONS;
  if (!SECCACHE[srv.id]) {
    const sec = { movie: null, show: null };
    const c = await api("/library/sections", {}, srv);
    for (const d of items(c)) if (sec[d.type] === null) sec[d.type] = d.key;
    SECCACHE[srv.id] = sec;
  }
  return SECCACHE[srv.id];
}

/* Ask every shown server the same question and stitch the answers together. One
   server being down must not empty the shelf, so a failure contributes nothing and
   is otherwise ignored. */
async function fromAll(fn) {
  const lists = await Promise.all(shownServers().map(async (srv) => {
    try {
      const got = await fn(srv);
      const list = Array.isArray(got) ? got : items(got);
      list.forEach((it) => { it.__s = srv ? srv.id : ""; });
      return list;
    } catch (e) { return []; }
  }));
  return [].concat.apply([], lists);
}

const byNum = (key) => (a, b) => (b[key] || 0) - (a[key] || 0);

/* ---------------- the library ---------------- */

function url(path, params = {}, srv) {
  const s = srv !== undefined ? srv : CTX;
  const u = new URL(base(s) + path);      // our own library, or a friend's
  // a friend knows us by the invitation; on our own server the cookie speaks for us
  const auth = s ? { t: s.token } : {};
  for (const [k, v] of Object.entries({ ...params, ...auth }))
    if (v !== undefined && v !== null) u.searchParams.set(k, v);
  return u.toString();
}

/* Whether a failure is the server being gone rather than the answer being no. */
const serverGone = (e) =>
  e instanceof TypeError || /Failed to fetch|NetworkError|-> 5\d\d/.test(String(e));

async function api(path, params, srv) {
  const s = srv !== undefined ? srv : CTX;
  try {
    return await ask(path, params, s);
  } catch (e) {
    // This server has gone and there is a machine holding copies of it. The page
    // cannot fetch its way out of a server that is off, so the address was handed
    // over while it still answered - this is where that is spent, once, and
    // everything after it comes from there.
    const where = standbyAddress();
    if (!where || s || !serverGone(e)) throw e;
    const there = { id: "__copy", origin: where.replace(/\/$/, ""),
                    name: standbyName || "",
                    token: (CFG && CFG.key) || "" };
    const said = await ask(path, params, there);
    nowOn(there);
    if (typeof drawServerPick === "function") drawServerPick();
    // by name. "The machine that keeps copies" is what it does, not what it is
    // called, and the name is the thing on the bar a moment later.
    toast(standbyName ? "Carrying on from " + standbyName
                      : "Carrying on from " + (standbyName || "the other server"),
                      true);
    return said;
  }
}

async function ask(path, params, s) {
  const r = await fetch(url(path, params, s), {
    // a friend's server is another origin, and a header of ours would make every
    // read a preflighted one for nothing
    headers: s ? { Accept: "application/json" }
               : { Accept: "application/json",
                   "X-Palladium-App": "web " + PAGE_BUILD },
  });
  if (!r.ok) throw new Error(path + " -> " + r.status);
  const j = await r.json();
  return j.MediaContainer || {};
}

const items = (c) => c.Metadata || c.Directory || [];

function img(thumb, w, h, srv) {
  if (!thumb) return "";
  const s = srv !== undefined ? srv : CTX;
  // artwork is served straight from the library's own cache, at the size it was
  // fetched at: there is no picture transcoder in front of it
  return url(thumb, {}, s);
}

function mins(ms) {
  if (!ms) return "";
  const m = Math.round(ms / 60000);
  return m >= 60 ? Math.floor(m / 60) + "h " + (m % 60) + "m" : m + "m";
}

function clock(s) {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
}

/**
 * The artwork behind a title's page, and behind the lists afterwards.
 *
 * One element on the page rather than a background on the panel: it stays clear of
 * every stacking context, and fades between titles instead of flashing. The last
 * title looked at stays behind the shelves, fainter, until another one is opened.
 */
let lastBackdrop = "";
/* Whether a title has been opened since the page loaded. Until one is, what stands
   behind the shelves is the newest thing in Continue watching. */
let visitedBackdrop = false;
/* Whether this viewer wants one at all, as the server keeps it for them. Asked once;
   the settings page says so straight away when it is changed. */
let backdropMode = "poster";
/* on behind everything, poster on a title's own page and nowhere else, off. True and
   false are what an older server answers. */
const backdropWord = (said) =>
  said === true || said === undefined || said === null ? "on"
  : said === false ? "off"
  : ["on", "poster", "off"].indexOf(String(said)) >= 0 ? String(said) : "on";
window.backdropWanted = (mode) => {
  backdropMode = backdropWord(mode);
  setBackdrop(null);
};
fetch("/settings").then((r) => r.json()).then((s) => {
  // the answer for this kind of screen; an older server sends one for all of them
  backdropMode = backdropWord(
    s.backdropHere !== undefined ? s.backdropHere
      : (s.backdrop && typeof s.backdrop === "object" ? s.backdrop.web : s.backdrop));
  setBackdrop(null);
}).catch(() => {});

function setBackdrop(url, soft) {
  const el = $("#backdrop");
  if (!el) return;
  if (url) {
    lastBackdrop = url;
    if (!soft) visitedBackdrop = true;
  }
  // poster: only while a title's own page is open, never the one left behind after it
  const show = backdropMode === "off" ? ""
    : backdropMode === "poster" ? (soft ? "" : url || "")
    : (url || lastBackdrop);
  if (!show) {
    el.classList.remove("on", "kept");
    return;
  }
  el.style.backgroundImage = "url(" + JSON.stringify(show).slice(1, -1) + ")";
  el.classList.add("on");
  // only what is left over from the last title is dimmed; its own page keeps it full
  el.classList.toggle("kept", !url || !!soft);
}

function esc(s) {
  return String(s === undefined || s === null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/* The other machine that keeps copies of this library.
 *
 * A browser cannot look for it by itself: when this server is off the page does not
 * load at all. What it can do is remember the address while the server is on, and
 * show it the moment a request finds nothing there - so the evening moves to the
 * other machine rather than stopping. */
let standbyWhere = "";
let standbyOut = "";
//: the guest's own way in to the cache, as the server words it, and whether it was
//: awake when the server last heard from it
let standbyLink = "";
/* This viewer's own key, so an address picked here can be turned into a way in. A
   cookie belongs to the machine that set it: arriving at the cache with one for the
   house is arriving with nothing. */
let standbyKey = "";
//: what the cache calls itself, for the row that offers it
let standbyName = "";
//: and this machine's own two addresses, as it names them. The row for the machine
//: you are on was built from the address you arrived at and no other, so having
//: opened it from outside there was no way back to the address on this network.
let myLan = "";
let myOut = "";
try {
  myLan = localStorage.getItem("pd-my-lan") || "";
  myOut = localStorage.getItem("pd-my-out") || "";
} catch (e) { myLan = ""; }
//: and, when this page is open on a machine that copies from another, where that
//: other one is. A browser's list of servers belongs to the origin it is on, so a
//: page opened on the cache starts with an empty list and no way back to the main server.
let houseWhere = "";
let houseOut = "";
let houseName = "";
try {
  houseWhere = localStorage.getItem("pd-house") || "";
  houseOut = localStorage.getItem("pd-house-out") || "";
  houseName = localStorage.getItem("pd-house-name") || "";
} catch (e) { houseWhere = ""; }
let standbyAlive = false;
try {
  standbyWhere = localStorage.getItem("pd-standby") || "";
  standbyOut = localStorage.getItem("pd-standby-out") || "";
  standbyLink = localStorage.getItem("pd-standby-link") || "";
  standbyKey = localStorage.getItem("pd-standby-key") || "";
} catch (e) {
  standbyWhere = "";
}

async function learnStandby() {
  try {
    const said = await (await fetch("/standby")).json();
    const me = (said && said.mine) || {};
    myLan = String(me.lan || "").replace(/\/$/, "");
    myOut = String(me.outside || "").replace(/\/$/, "");
    try {
      localStorage.setItem("pd-my-lan", myLan);
      localStorage.setItem("pd-my-out", myOut);
    } catch (e) { /* a private window keeps nothing */ }
    const home = (said && said.follows) || {};
    houseWhere = String(home.lan || "").replace(/\/$/, "");
    houseOut = String(home.outside || "").replace(/\/$/, "");
    houseName = String(home.name || "");
    try {
      localStorage.setItem("pd-house", houseWhere);
      localStorage.setItem("pd-house-out", houseOut);
      localStorage.setItem("pd-house-name", houseName);
    } catch (e) { /* a private window keeps nothing */ }
    const where = (said && said.where ? String(said.where) : "").replace(/\/$/, "");
    const out = (said && said.outside ? String(said.outside) : "").replace(/\/$/, "");
    if (where && where !== location.origin) {
      standbyWhere = where;
      localStorage.setItem("pd-standby", where);
    }
    if (out && out !== location.origin) {
      standbyOut = out;
      localStorage.setItem("pd-standby-out", out);
    }
    standbyAlive = !!(said && said.alive);
    if (said && said.name) standbyName = String(said.name);
    if (said && said.link) {
      standbyLink = said.link;
      try { localStorage.setItem("pd-standby-link", said.link); } catch (e) {}
    }
    if (said && said.key) {
      standbyKey = String(said.key);
      try { localStorage.setItem("pd-standby-key", standbyKey); } catch (e) {}
    }
  } catch (e) {
    /* an older server, or one that has none */
  }
}

/* Which of the two addresses to name: the one on this network for a browser that is
 * on it, the forwarded one for a browser that is not. The page cannot try them, so
 * it goes by the address it was opened at. */
function standbyAddress() {
  const home = /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|127\.|localhost)/
    .test(location.hostname);
  const pick = (home ? standbyWhere || standbyOut : standbyOut || standbyWhere) || "";
  return thisMachine(pick) ? "" : pick;
}

/* Whether an address is the machine this page is already being served by.
 *
 * By every address it answers to, not only the one in the bar. The address of the
 * copy is remembered between visits, so a browser opened on the cache itself held its
 * own address as somewhere to go - and ran the whole moving-house routine to arrive
 * where it already was, saying so each time the buffer dipped. */
function thisMachine(where) {
  const at = String(where || "").replace(/\/$/, "");
  if (!at) return true;
  return [location.origin, (CTX && CTX.origin) || "", myLan, myOut]
    .some((u) => u && String(u).replace(/\/$/, "") === at);
}

/** Said when the server did not answer at all, with somewhere to go if there is. */
function noServer(what) {
  const said = what || "Could not reach the server";
  const where = standbyAddress();
  if (!where) return toast(said);
  offerTheCopy(said);
  return undefined;
}

/* Hand the viewer the other machine, once, with a way to open it.
 *
 * A page cannot fetch its way out of a server that is off - the page itself came
 * from there - so this is a line across the top with a button that opens the cache in
 * a tab of its own. The guest's own link where the server gave one, so nobody has to
 * be told an address or find their invitation again. */
let offeredTheCopy = false;

/* The other machine, with the key on the end where there is one: an address alone
   lands on the page that asks for an invitation, and somebody who is already on this
   server has one. The address is picked here - the home network one indoors, the
   outside one away - so the key is put on it here as well. */
function copyWayIn() {
  const at = standbyAddress();
  if (at && standbyKey) return at.replace(/\/$/, "") + "/s/" + standbyKey;
  return standbyLink || at;
}

async function offerTheCopy(said) {
  const where = copyWayIn();
  if (!where) return toast(said || "Could not reach the server");
  if (offeredTheCopy) return;
  offeredTheCopy = true;
  // By name. The line appears at the one moment somebody is anxious, and "the
  // machine that keeps copies" is what it does rather than what it is called.
  const name = standbyName || "the other server";
  const note = document.getElementById("copynote");
  if (!note) {
    toast((said || "") + " - " + name + " is at " +
          where.replace(/^https?:\/\//, ""));
    return;
  }
  // and only if it is there. Both machines restart during an update, so this used to
  // come up saying the cache was still there at the one moment it was not, with a
  // button that opened a page which could not load.
  let up = false;
  try {
    await fetch(where.replace(/\/$/, "") + "/app/version",
                { cache: "no-store", mode: "no-cors" });
    up = true;
  } catch (e) {
    up = false;
  }
  note.innerHTML = up
    ? "<b>This server is not answering</b> - " + esc(name) + " is still there. "
    : "<b>Neither server is answering</b> - " + esc(name) +
      " is not there either. They may both be restarting; it takes a few seconds. ";
  const go = document.createElement("button");
  go.textContent = up ? "Open " + name : "Try " + name;
  go.onclick = () => {
    // A blocked pop-up is silence, and silence reads as a broken button. If the tab
    // does not open, this window goes there instead.
    const tab = window.open(where, "_blank", "noopener");
    if (!tab) location.href = where;
  };
  note.appendChild(go);
  const not = document.createElement("button");
  not.textContent = "Not now";
  not.onclick = () => {
    note.classList.add("hidden");
    document.body.classList.remove("has-copynote");
    offeredTheCopy = false;
  };
  note.appendChild(not);
  note.classList.remove("hidden");
  document.body.classList.add("has-copynote");
}

function toast(msg, mark) {
  const t = $("#toast");
  // with the mark, the library's own P in its own yellow leads the line: a wait of a
  // few seconds plainly belongs to Palladium rather than to the film
  if (mark) {
    t.innerHTML = "<b class='pmark'>P</b>";
    t.appendChild(document.createTextNode(msg));
  } else {
    t.textContent = msg;
  }
  t.classList.add("on");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("on"), 3200);
}

/**
 * Mark something watched, or not: a film, an episode, a season, or a whole series.
 *
 * The server keeps this as progress at full duration, so the tick, the resume point
 * and Continue watching cannot disagree with each other.
 */
async function setWatched(item, watched) {
  const key = item.ratingKey;
  await fetch(url("/:/" + (watched ? "scrobble" : "unscrobble"), { key: key }));
  // Watched means finished: there is nothing left to resume, and the server has
  // already dropped the resume point - marking writes progress at the full duration,
  // unmarking deletes the row. Say so here too, or the page keeps offering Resume
  // at the minute it was left, and Continue watching keeps the card.
  item.viewCount = watched ? 1 : 0;
  item.viewOffset = 0;
}

const isWatched = (it) => !!(it && it.viewCount);

/* What this viewer has marked to watch, by rating key. Held here so a grid can show
   the mark without asking the server about every card; the server is the record. */
let MARKED = new Set();
/* And what those keys add up to above the episodes: a season or a programme carries
   no key of its own on this shelf, so the server says which of them are covered
   whole and which only in part. */
let MARK_ALL = new Set();
let MARK_SOME = new Set();

/** Take a shelf's answer apart: the keys themselves, and what they cover. */
function keepMarks(d) {
  MARKED = new Set((d.watchlist || []).map(String));
  MARK_ALL = new Set((d.covers || []).map(String));
  MARK_SOME = new Set((d.part || []).map(String));
}

/** Read the marks back from the server. */
async function loadMarks() {
  try {
    const r = await fetch("/watchlist", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    if (!r.ok) return;
    keepMarks(await r.json());
  } catch (e) { /* no marks is a fine answer */ }
}

const isMarked = (it) => !!(it && (MARKED.has(String(it.ratingKey)) ||
                                   MARK_ALL.has(String(it.ratingKey))));
/** Marked, but not all the way through: some of the episodes under it. */
const partMarked = (it) => !!(it && MARK_SOME.has(String(it.ratingKey)));

/**
 * Read both shelves back before drawing a screen.
 *
 * Marks are changed in a lot of places - a season inside a programme, an episode row,
 * a card on the shelf, the app on the television - and a screen arrived at afterwards
 * has to show what is true now rather than what was true when the page was opened.
 * Two small POSTs, and only ever on the way into a view.
 */
async function refreshMarks() {
  await Promise.all([loadMarks(), loadFavs()]);
}

/**
 * Mark this for later, or take the mark off.
 *
 * Only things on this server can be marked: the list is a list of keys, and another
 * server's keys mean nothing here.
 */
async function setMarked(item, on) {
  const r = await fetch("/watchlist", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: String(item.ratingKey), on: !!on }),
  });
  if (!r.ok) { toast("Could not change the watchlist"); return; }
  keepMarks(await r.json());
}

/* What this viewer keeps on both machines, and what those keys add up to. */
let FAVS = new Set();
let FAV_ALL = new Set();
let FAV_SOME = new Set();

function keepFavs(d) {
  FAVS = new Set((d.favorites || []).map(String));
  FAV_ALL = new Set((d.covers || []).map(String));
  FAV_SOME = new Set((d.part || []).map(String));
}

async function loadFavs() {
  try {
    const r = await fetch("/favorites", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    if (r.ok) keepFavs(await r.json());
  } catch (e) { /* none is a fine answer */ }
}

const isFav = (it) => !!(it && (FAVS.has(String(it.ratingKey)) ||
                                FAV_ALL.has(String(it.ratingKey))));
const partFav = (it) => !!(it && FAV_SOME.has(String(it.ratingKey)));

/** A favourite, or not: kept on both machines whatever else is copied. */
async function setFav(item, on) {
  const r = await fetch("/favorites", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: String(item.ratingKey), on: !!on }),
  });
  if (!r.ok) { toast("Could not change favorites"); return; }
  keepFavs(await r.json());
}

/** The button that does it, beside the one about having watched the thing. */
function markButton(item, after) {
  const b = document.createElement("button");
  const draw = () => {
    b.className = "btn ghost markbtn" + (isMarked(item) ? " on" : "");
    // a filled star for marked, a hollow one for not: the same shape either way, so
    // the button does not change size as it changes meaning
    b.innerHTML = isMarked(item) ? "\u2605 On the watchlist" : "\u2606 Watchlist";
  };
  b.onclick = async () => {
    await setMarked(item, !isMarked(item));
    draw();
    if (after) after();
  };
  draw();
  return b;
}

/* ---------------- cards & rows ---------------- */

/**
 * Is this Safari - or anything on an iPhone or iPad, which is Safari underneath?
 *
 * iOS requires every browser to use WebKit, so Chrome on an iPad has exactly the same
 * appetite as Safari does. A Mac running Chrome does not, hence the second test rather
 * than a check for "Apple".
 */
/**
 * Show the right casting control for the browser in hand.
 *
 * Google Cast only exists in Chromium, so in Safari its button is a control that does
 * nothing - hidden. Safari has AirPlay instead, and only says so when a route exists,
 * which is why the button appears when a television is on and not before.
 */
function castControls() {
  const v = $("#video");
  // The Cast framework is fetched from Google and arrives when it arrives - never
  // at page load, which is when this used to decide. Deciding then hid the cast
  // button on every browser that has one. So the button is left alone until the
  // framework has had its say: it announces itself through __onGCastApiAvailable,
  // and only silence there means there is nothing to cast with.
  const chromecast = !!(window.chrome && window.chrome.cast);
  if (!chromecast) {
    // give it the time a script fetch takes, then decide once
    setTimeout(() => {
      if (window.chrome && window.chrome.cast) return;
      document.querySelectorAll("#castbtn, #c-castbtn").forEach((b) => {
        b.style.display = "none";
      });
    }, 8000);
  }
  if (!v || !window.WebKitPlaybackTargetAvailabilityEvent) return;
  const air = document.createElement("button");
  air.id = "c-airplay";
  air.title = "Play on an Apple TV";
  air.textContent = "\u25B6\u25AB";        // the AirPlay mark, near enough
  air.style.display = "none";
  air.onclick = (e) => {
    e.stopPropagation();
    v.webkitShowPlaybackTargetPicker();
  };
  const bar = document.getElementById("ctl");
  if (bar) bar.appendChild(air);
  v.addEventListener("webkitplaybacktargetavailabilitychanged", (e) => {
    air.style.display = e.availability === "available" ? "" : "none";
  });
}

/**
 * This viewer's own invitation, as the landing page left it.
 *
 * A guest's browser is recognised by a cookie, which a Chromecast does not have - so
 * anything handed to a receiver has to carry the token in the address instead. The
 * cookie is readable here on purpose: it is this viewer's own key, not a secret kept
 * from them.
 */
function myToken() {
  const m = document.cookie.match(/(?:^|;\s*)pal=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

/**
 * The address a television should fetch from.
 *
 * Not localhost, which means the receiver itself, and not this server's address on our
 * network when the viewer is somewhere else entirely - the address they are using is
 * the one that works from where they are.
 */
function castOrigin() {
  if (CTX) return CTX.origin;                 // a friend's server: their address
  if (CFG.guest) return location.origin;      // a guest: the way they reached us
  return CFG.lan || location.origin;          // at home: the LAN address, not localhost
}

/**
 * A television's own browser: LG's webOS, Samsung's Tizen.
 *
 * Both are Chromium underneath, but their media stacks are built around HLS the way a
 * television is - a playlist and segments play, and a long single response of
 * fragmented MP4 frequently does not. They are sent the same stream Safari gets.
 */
function isTvBrowser() {
  return /web0?s|webappmanager|tizen|smarttv|smart-tv|netcast/i
    .test(navigator.userAgent);
}

function isApple() {
  const ua = navigator.userAgent;
  const ios = /iPad|iPhone|iPod/.test(ua) ||
    // an iPad calls itself a Mac; only the touch points give it away
    (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  const safari = /^((?!chrome|android|crios|fxios).)*safari/i.test(ua);
  return ios || safari;
}


/**
 * "This is the wrong programme": choose the right one.
 *
 * The list is what TMDB answers for the title, or for whatever is typed instead.
 * Choosing rewrites the item - name, year, poster, plot, and for a series its episode
 * titles and air dates - from that entry.
 */
async function fixMatch(item, after) {
  const old = document.getElementById("matchbox");
  if (old) old.remove();
  const box = document.createElement("div");
  box.id = "matchbox";
  box.className = "subpanel wide";
  box.innerHTML = "<div class='subhead'><h4>What is this?</h4>"
    + "<button class='xclose' id='mx' title='Close'>✕</button></div>" +
    "<div class='addrow'><input id='mq' type='text' placeholder='search by another name'>" +
    "<button class='btn ghost' id='mgo'>Search</button></div>" +
    "<div class='sublist' id='mlist'><div class='note'>Looking&hellip;</div></div>";
  document.body.appendChild(box);
  const list = () => box.querySelector("#mlist");
  // three ways out, because a panel that has to be answered is a trap: the cross,
  // Escape, and a click on the page behind it
  const shut = () => {
    box.remove();
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("mousedown", onAway, true);
  };
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); shut(); } };
  const onAway = (e) => { if (!box.contains(e.target)) shut(); };
  box.querySelector("#mx").onclick = shut;
  document.addEventListener("keydown", onKey, true);
  // not on this click, the one that opened it
  setTimeout(() => document.addEventListener("mousedown", onAway, true), 0);

  const show = async (term) => {
    list().innerHTML = "<div class='note'>Looking&hellip;</div>";
    let found = [];
    try {
      const c = await api("/library/matches/" + item.ratingKey,
                          term ? { q: term } : {});
      found = c.matches || [];
    } catch (e) { /* nothing found is a fine answer */ }
    list().innerHTML = "";
    if (!found.length) {
      list().innerHTML = "<div class='note'>Nothing found. Try another name.</div>";
      return;
    }
    found.forEach((m) => {
      const row = document.createElement("button");
      row.className = "subrow track" + (m.current ? " on" : "");
      row.innerHTML = "<span class='name'></span><span class='note'></span>";
      row.querySelector(".name").textContent =
        m.title + (m.year ? "  (" + m.year + ")" : "");
      row.querySelector(".note").textContent = m.current ? "matched now"
        : (m.overview || "").slice(0, 90);
      row.onclick = async () => {
        if (m.current) { shut(); return; }
        row.querySelector(".note").textContent = "matching\u2026";
        const r = await fetch("/library/rematch", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key: item.ratingKey, tmdbId: m.id }),
        });
        shut();
        if (!r.ok) { toast("Could not change the match"); return; }
        toast("Now " + ((await r.json()).title || m.title));
        if (after) after();
      };
      list().appendChild(row);
    });
  };
  box.querySelector("#mgo").onclick = () => show(box.querySelector("#mq").value.trim());
  box.querySelector("#mq").onkeydown = (e) => {
    if (e.key === "Enter") show(box.querySelector("#mq").value.trim());
  };
  show("");
}

/** The button that opens it - the owner's, since it rewrites the library. */
function fixMatchButton(item, after) {
  const b = document.createElement("button");
  b.className = "btn ghost";
  b.textContent = "Fix match";
  b.title = "This is the wrong film or programme";
  b.onclick = () => fixMatch(item, after);
  return b;
}


/** Each collection, with whether it holds all, some or none of a title. */
async function shelvesHolding(key) {
  try {
    const r = await fetch("/collections/for", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: String(key) }),
    });
    return (await r.json()).collections || [];
  } catch (e) { return []; }
}

/** One title on or off one collection; the collections as they now stand, or null. */
async function markShelf(id, key, on) {
  try {
    const r = await fetch("/collections/mark", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: id, key: String(key), on: !!on }),
    });
    if (!r.ok) return null;
    return (await r.json()).collections || [];
  } catch (e) { return null; }
}

/**
 * The collections menu for one title, under the button that opened it: a tick for a
 * collection holding all of it, a half for some, and a line that makes a new one.
 */
async function pickCollection(item, anchor, after) {
  document.querySelectorAll(".collpick").forEach((m) => m.remove());
  const menu = document.createElement("div");
  menu.className = "collpick";
  const at = anchor.getBoundingClientRect();
  menu.style.left = Math.max(8, Math.min(at.left, window.innerWidth - 290)) + "px";
  menu.style.top = (at.bottom + window.scrollY + 4) + "px";
  menu.textContent = "Loading\u2026";
  document.body.appendChild(menu);
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const close = (e) => {
    if (e && menu.contains(e.target)) return;
    menu.remove();
    document.removeEventListener("mousedown", close);
    document.removeEventListener("keydown", onKey);
  };
  setTimeout(() => {
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
  }, 0);
  const took = (now) => {
    if (!now) return toast("Could not change that");
    draw(now);
    if (after) after();
  };
  const draw = (shelves) => {
    menu.innerHTML = "";
    const head = document.createElement("div");
    head.className = "collpick-head";
    head.textContent = shelves.length ? "Collections" : "No collections yet";
    menu.appendChild(head);
    shelves.forEach((s) => {
      const row = document.createElement("button");
      row.className = "collpick-row" + (s.state === "all" ? " on" : s.state === "some" ? " some" : "");
      row.innerHTML = '<span class="tick">' +
        (s.state === "all" ? "\u2713" : s.state === "some" ? "\u25D0" : "") + "</span>" + esc(s.name);
      row.title = s.state === "all" ? "In this collection - press to take it out"
        : s.state === "some" ? "Partly in this collection - press to add the rest"
        : "Add to this collection";
      row.onclick = async (e) => {
        e.stopPropagation();
        took(await markShelf(s.id, item.ratingKey, s.state !== "all"));
      };
      menu.appendChild(row);
    });
    const make = document.createElement("button");
    make.className = "collpick-row make";
    make.innerHTML = '<span class="tick">+</span>New collection';
    make.onclick = async (e) => {
      e.stopPropagation();
      const name = (prompt("Name the new collection") || "").trim();
      if (!name) return;
      const made = await saveCollection({ name: name, mode: "manual" });
      const shelf = (made || []).find((c) => (c.name || "").toLowerCase() === name.toLowerCase());
      if (shelf) took(await markShelf(shelf.id, item.ratingKey, true));
    };
    menu.appendChild(make);
  };
  draw(await shelvesHolding(item.ratingKey));
}

/* Right-click on a poster: which collections it is on. */
document.addEventListener("contextmenu", (e) => {
  const el = e.target.closest && e.target.closest(".card[data-key]");
  const key = el && el.dataset.key;
  if (!key || key.startsWith("coll:") || el.classList.contains("shuffled")) return;
  e.preventDefault();
  // on the watchlist: favorite it, or its collections
  if (el.dataset.watch === "1") return favMenu({ ratingKey: key }, el);
  pickCollection({ ratingKey: key }, el);
});

/* Held on the watchlist: make it a favorite or not, or pick its collections. */
function favMenu(item, anchor) {
  document.querySelectorAll(".favmenu").forEach((m) => m.remove());
  const menu = document.createElement("div");
  menu.className = "favmenu";
  const fav = document.createElement("button");
  fav.textContent = isFav(item) ? "\u2661 Remove favorite" : "\u2665 Favorite";
  fav.onclick = async () => {
    menu.remove();
    await setFav(item, !isFav(item));
    viewWatchlist();
  };
  const coll = document.createElement("button");
  coll.textContent = "\u21BB Collections\u2026";
  coll.onclick = () => {
    menu.remove();
    pickCollection(item, anchor);
  };
  menu.append(fav, coll);
  const r = anchor.getBoundingClientRect();
  menu.style.left = Math.max(8, Math.min(window.innerWidth - 230, r.left + window.scrollX)) + "px";
  menu.style.top = (r.top + window.scrollY + 36) + "px";
  document.body.appendChild(menu);
  setTimeout(() => {
    const away = (ev) => {
      if (menu.contains(ev.target)) return;
      menu.remove();
      document.removeEventListener("click", away);
    };
    document.addEventListener("click", away);
  }, 0);
}

/* Which shelf is being shuffled, if one is: what Next and Previous act on. */
let shuffleOn = "";

/**
 * Draw from one shelf and play what comes up.
 *
 * The draw is the server's, because a round belongs to the person rather than to
 * whichever browser they happen to be at - and the main server keeps it, so the same
 * evening carries on from any machine.
 */
async function shuffleDraw(cid, resume, back) {
  let d;
  try {
    d = await (await fetch("/collections/shuffle", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: cid, resume: !!resume, back: !!back }),
    })).json();
  } catch (e) {
    return noServer();
  }
  if (!d || d.error || !d.item) {
    return toast(d && d.error ? d.error : "That shelf is empty");
  }
  CTX = null;
  casualOn = true;
  shuffleOn = cid;
  // Asked for again by key, the way the collection queue asks. What the draw hands
  // back describes the title; what the player wants is the library's own answer with
  // its files and its tracks on it, and handing it the first where it wanted the
  // second is why pressing this did nothing anybody could see.
  let meta = null;
  try {
    meta = items(await api("/library/metadata/" + d.key))[0] || null;
  } catch (e) {
    meta = null;
  }
  if (!meta) return toast("Drew something the library could not open");
  // Said out loud when it will not start. A button that draws a film and then fails
  // silently on the way to playing it is a button that does nothing, and there is no
  // way to tell those two apart from the outside.
  try {
    await play(meta, Math.floor(d.resumeAt || 0), {});
  } catch (e) {
    dbg("shuffle-play-failed", { shelf: cid, key: d.key, why: String(e) });
    return toast("Drew " + (meta.grandparentTitle || meta.title || "something") +
                 " but could not start it: " + String(e && e.message ? e.message : e));
  }
  toast(d.left > 0 ? d.left + " left in this round"
                   : "last of this round - it starts again after this");
}

/* Whether what is playing came out of the casual shuffle - which decides what the
   next and previous buttons do, and what happens when it ends. */
let casualOn = false;

/**
 * Show the two stepping buttons, or hide them, for whatever is playing.
 *
 * An episode always has somewhere to go; casual watching always has another draw; a
 * film on its own has neither, and gets no buttons rather than dead ones.
 */
function showStepping() {
  const prev = $("#c-prev"), next = $("#c-next");
  if (!prev || !next) return;
  const ep = S && S.meta && S.meta.type === "episode";
  const on = !!(casualOn || ep);
  prev.classList.toggle("hidden", !on);
  next.classList.toggle("hidden", !on);
  next.title = casualOn ? "Next in the shuffle" : "Next episode";
  prev.title = casualOn ? "The one before" : "Previous episode";
}

/** The next thing: another draw when watching casually, the next episode otherwise. */
async function stepNext() {
  if (!S || !S.meta) return;
  if (casualOn && shuffleOn) return shuffleDraw(shuffleOn, false);
  const was = S.meta;
  let next = null;
  try {
    next = items(await api("/next", { key: was.ratingKey }))[0] || null;
  } catch (e) { next = null; }
  if (!next) return toast("That was the last episode");
  stop(true);
  play(next, 0, {});
}

/** And the one before: the previous draw, or the previous episode. */
async function stepPrev() {
  if (!S || !S.meta) return;
  // The first press starts this one again, as it does on every disc player ever
  // made; only within the opening ten seconds does it mean the one before.
  const into = ($("#video").currentTime || 0) + (S.timeBase || 0) - (S.start || 0);
  if (into > 10) {
    if (S.gpu) return play(S.meta, 0, opts(), false, S.mi || 0);
    $("#video").currentTime = 0;
    return;
  }
  // a shelf steps back through its own round
  if (casualOn && shuffleOn) return shuffleDraw(shuffleOn, false, true);
  const was = S.meta;
  let before = null;
  try {
    before = items(await api("/prev", { key: was.ratingKey }))[0] || null;
  } catch (e) { before = null; }
  if (!before) return toast("That was the first episode");
  stop(true);
  play(before, 0, {});
}

if ($("#c-next")) {
  $("#c-next").addEventListener("click", (e) => { e.stopPropagation(); stepNext(); });
}
if ($("#c-prev")) {
  $("#c-prev").addEventListener("click", (e) => { e.stopPropagation(); stepPrev(); });
}



/** The watchlist star, the favourite heart and the collections, as one control. */
function markControl(item, after) {
  const box = document.createElement("span");
  box.className = "splitmark";
  const left = document.createElement("button");
  left.className = "half left";
  const coll = document.createElement("button");
  coll.className = "half coll";
  let inShelf = false;
  const draw = () => {
    // one button stepping through three: off, on the watchlist, a favorite
    const fav = isFav(item), marked = isMarked(item);
    left.classList.toggle("on", marked || fav);
    left.classList.toggle("fav", fav);
    left.innerHTML = fav ? "♥" : marked ? "★" : "☆";
    left.title = fav ? "A favorite: on your watchlist and kept when watched - press to take it off"
      : marked ? "On your watchlist - press to make it a favorite"
      : "Add to your watchlist";
    coll.classList.toggle("on", inShelf);
    coll.innerHTML = "\u21BB";
    coll.title = "Collections: add it to one, or take it out";
  };
  left.onclick = async (e) => {
    e.stopPropagation();
    if (isFav(item)) {
      await setFav(item, false);
      await setMarked(item, false);
    } else if (isMarked(item)) {
      await setFav(item, true);
    } else {
      await setMarked(item, true);
    }
    draw();
    if (after) after();
  };
  const learn = () => shelvesHolding(item.ratingKey).then((shelves) => {
    inShelf = shelves.some((s) => s.state !== "none");
    draw();
  });
  coll.onclick = (e) => {
    e.stopPropagation();
    pickCollection(item, coll, () => {
      learn();
      if (after) after();
    });
  };
  draw();
  learn();
  box.append(left, coll);
  return box;
}

/**
 * "Whole programme" or "This season", each with the two marks written out.
 *
 * Two unlabelled split controls on one page - one by the title, one by the season -
 * say nothing about which is which. These say what they will mark and what the mark
 * means, and show which of them are already set.
 */
/** All, some or none of what this key stands for, per shelf. */
async function marksState(key) {
  try {
    return await (await fetch("/marks?key=" + encodeURIComponent(key))).json();
  } catch (e) { return { watchlist: "none", favorites: "none" }; }
}

function markButtons(item, label, after, whole) {
  const box = document.createElement("span");
  box.className = "markgroup";
  const name = document.createElement("span");
  name.className = "lbl";
  name.textContent = label;
  box.appendChild(name);
  const pair = [["☆", "★", "Watchlist", isMarked, setMarked, "star"]];
  // A programme and a season are not shelves: marking one marks its episodes, so
  // these buttons say how many of them are on that shelf - all, some or none - and
  // set or clear the lot.
  const spread = !String(item.ratingKey).startsWith("e") &&
    (item.type === "show" || item.type === "season");
  let held = { watchlist: "none" };
  const buttons = [];
  pair.forEach(([off, on, word, is, set, kind]) => {
    const b = document.createElement("button");
    const shelf = "watchlist";
    const where = "your watchlist";
    const how = () => (spread ? held[shelf] : (is(item) ? "all" : "none"));
    const draw = () => {
      const state = how();
      b.className = "btn ghost mark" + kind +
        (state === "all" ? " on" : state === "some" ? " some" : "");
      b.innerHTML = (state === "none" ? off : on) + " " + word +
        (state === "some" ? " (some)" : "");
      b.title = state === "all"
        ? "All of " + label.toLowerCase() + " is in " + where + " - press to take it off"
        : state === "some"
          ? "Some of " + label.toLowerCase() + " is in " + where +
            " - press to put the rest in"
          : "Put " + label.toLowerCase() + " in " + where;
    };
    b.onclick = async (e) => {
      e.stopPropagation();
      // some means "not all": the press finishes the job rather than undoing it
      await set(item, how() !== "all");
      if (spread) held = await marksState(item.ratingKey);
      draw();
      if (after) after();
    };
    buttons.push(draw);
    draw();
    box.appendChild(b);
  });
  if (spread) {
    marksState(item.ratingKey).then((said) => {
      held = said;
      buttons.forEach((draw) => draw());
    });
  }
  return box;
}

/* Which titles the machine that keeps copies is holding, by this library's keys.
 *
 * Asked for once and then every few minutes: it changes as a copy finishes, and a
 * shelf drawn a moment before should not have to be right for ever. */
let copiesHere = new Set();

async function learnCopies() {
  try {
    const said = await (await fetch("/copies")).json();
    copiesHere = new Set((said && said.keys) || []);
    markTheCopies();
  } catch (e) {
    /* an older server, or nothing following it */
  }
}

/* Put the dot on cards that are already on screen.
 *
 * The shelves are drawn the moment the page opens, and the answer about the other
 * machine arrives after that - so the first screen of every session wore no dots at
 * all until something happened to redraw it. */
function markTheCopies() {
  if (!copiesHere.size) return;
  document.querySelectorAll(".card").forEach((el) => {
    const key = el.dataset.key || "";
    const poster = el.querySelector(".poster");
    if (!poster || !copiesHere.has(key)) return;
    if (poster.querySelector(".onslave")) return;
    const dot = document.createElement("div");
    dot.className = "onslave" + (poster.querySelector(".bar") ? " over" : "");
    dot.title = "Also on " + (standbyName || "the other server");
    poster.appendChild(dot);
  });
}

/** Whether the other machine holds this one - an episode, or a film. */
function isCopied(it) {
  if (!copiesHere.size || !it) return false;
  // a season or a programme is not copied as such; its episodes are
  return copiesHere.has(String(it.ratingKey || ""));
}

/* This server keeps copies of another one: say so, and say what of yours is here.
 *
 * A shelf holding nine films where the main server has nine hundred reads as a library
 * that has broken, unless the page says plainly which machine this is and what it
 * was asked to keep. */
function sayItIsTheCopy() {
  const copy = CFG && CFG.copyOf;
  const note = document.getElementById("copynote");
  if (!note || !copy) return;
  const kept = [copy.deck ? "what you were part-way through" : "",
                copy.list ? "your watchlist" : "",
                copy.casual ? "the next few of your casual shuffle" : ""]
    .filter(Boolean);
  const what = kept.length
    ? kept.join(kept.length === 2 ? " and " : ", ").replace(/, ([^,]*)$/, " and $1")
    : "nothing yet - nobody has asked for anything to be kept for you";
  try {
    if (localStorage.getItem("pd-copynote") === copy.name) return;   // said already
  } catch (e) { /* a browser with no memory says it every time */ }
  // What is on this machine is on it for everybody who may watch here - it was
  // copied for one person and it is on the shelf for all of them.
  const much = (copy.films || copy.episodes)
    ? (copy.films || 0) + " films and " + (copy.episodes || 0) + " episodes, all of "
      + "them playable by anyone here"
    : "what has been copied so far";
  note.innerHTML = "Copies from <b>" + esc(copy.name) + "</b>: " +
    much + ". Kept for you: " + what + ". ";
  const right = document.createElement("button");
  right.textContent = "Right";
  right.onclick = () => {
    note.classList.add("hidden");
    document.body.classList.remove("has-copynote");
    try { localStorage.setItem("pd-copynote", copy.name); } catch (e) {}
  };
  note.appendChild(right);
  note.classList.remove("hidden");
  document.body.classList.add("has-copynote");
}

function card(it, onDeck, coll) {
  const el = document.createElement("div");
  el.className = "card";
  // A shelf being shuffled reads as one of its episodes, and would be taken for one
  // if it were not said across the poster: pressing it plays whatever the hat has
  // next, not the programme this happens to be an episode of.
  if (it.shuffle) el.classList.add("shuffled");
  // a film on offer from a torrent pack: not here yet, greyed until it is
  if (it.offered) el.classList.add("offered");
  el.dataset.key = String(it.ratingKey || "");
  const srv = srvOf(it);
  const isEp = it.type === "episode";
  // always the official vertical cover: an episode shows its series poster,
  // never the 16:9 episode still
  const thumb = isEp ? (it.grandparentThumb || it.parentThumb || it.thumb)
    : it.type === "season" ? (it.thumb || it.parentThumb)
    : (it.thumb || it.parentThumb);
  // a season card names the programme and says which season underneath: that is the
  // way round anybody reads it, and it is what "a series was added" means
  const title = isEp ? it.grandparentTitle
    : it.type === "season" ? (it.grandparentTitle || it.parentTitle || it.title)
    : it.title;
  const sub = isEp ? "S" + it.parentIndex + " E" + it.index + " - " + it.title
    : it.type === "season"
      // on a shelf that holds part of a season, say which part: "6 of 10" is the
      // difference between a season that is marked and one that is half marked
      ? [it.title, it.leafCount
           ? (it.shelfCount && it.shelfCount < it.leafCount
                ? it.shelfCount + " of " + it.leafCount + " episodes"
                : it.leafCount + (it.leafCount === 1 ? " episode" : " episodes"))
           : ""]
        .filter(Boolean).join(" · ")
    // Sorted by release, a series is placed by its newest episode - so that is what
    // the card says, rather than the year the programme began. a 1999 series above a 2024 one
    // reads as a mistake until it says "last aired 14 Sep 2026".
    : (it.type === "show" && sortState.key === "released" && it.originallyAvailableAt
       ? "last aired " + new Date(it.originallyAvailableAt).toLocaleDateString(
             undefined, { day: "numeric", month: "short", year: "numeric" })
       : (it.year || ""));
  const pct = it.viewOffset && it.duration ? (it.viewOffset / it.duration) * 100 : 0;
  el.innerHTML =
    // a missing poster shows the title instead: a broken-image glyph in the
    // corner of an empty box looks like a fault, which it is not
    '<div class="poster"><span class="ph"></span>' +
    (thumb ? '<img loading="lazy" src="' + img(thumb, 300, 450, srv) +
      '" alt="" onerror="this.remove()">' : "") +
    // whose shelf this came from, so a merged list is still legible
    (srv ? '<div class="from">' + esc(srv.name) + "</div>" : "") +
    // held in 2160 lines somewhere: on a series that means one season of it may be,
    // which is still the reason to pick it off a shelf
    ((it.maxHeight || 0) >= 1700
      ? '<div class="uhd" title="Held in 4K">4K</div>' : "") +
    // a tick for something finished, or how far through a series you are
    // a programme with only some of its episodes marked wears a hollow star, so
    // "all of it" and "three of them" do not look like the same thing
    (isMarked(it) ? '<div class="marked" title="On your watchlist">&#9733;</div>'
      : partMarked(it) ? '<div class="marked part" title="Some of it is on your ' +
        'watchlist">&#9734;</div>' : "") +
    // a dot for a title the other machine is holding: it plays when this
    // server is off, and that is worth knowing before the evening it matters
    (isCopied(it) ? '<div class="onslave' + (pct ? " over" : "") +
      '" title="Also on ' + esc(standbyName || "the other server") +
       '"></div>' : "") +
    // a favorite: a red heart in the corner, over the dot when there is one
    (isFav(it) ? '<div class="favheart' + (isCopied(it) ? " stack" : "") +
      (pct ? " over" : "") + '" title="Favorite">&#9829;</div>' : "") +
    (it.offered ? '<div class="offerbadge">' + esc(offerWord(it)) + "</div>" : "") +
    (isWatched(it) ? '<div class="seen">&#10003;</div>'
      : it.viewedLeafCount ? '<div class="seen part">' + it.viewedLeafCount + "/" +
        (it.leafCount || "?") + "</div>" : "") +
    (pct ? '<div class="plabel">' + clock(it.viewOffset / 1000) + " / " +
             clock(it.duration / 1000) + '</div>' +
           '<div class="bar"><i style="width:' + pct + '%"></i></div>' : "") + "</div>" +
    '<div class="t">' + esc(title) + '</div><div class="s">' + esc(sub) + "</div>";
  // Only on Continue watching: two plain buttons, because there are two honest
  // reasons for something to leave this shelf. Watched means "I finished it" and it
  // counts as seen; unwatched forgets the place, for something started by mistake.
  // Either way the shelf is built from where somebody has got to, so either way it
  // goes.
  if (onDeck) {
    const pair = document.createElement("div");
    pair.className = "deckmarks";
    [["✓", true, "Watched - take it off this shelf"],
     ["✗", false, "Not watched - forget where I was"]].forEach(
      ([mark, watched, why]) => {
        const b = document.createElement("button");
        b.className = "deckdone" + (watched ? " yes" : " no");
        b.innerHTML = mark;
        // A shuffle row is the shelf, so both buttons mean something else on it: the
        // tick marks the episode the hat drew and the shelf moves on to the next, and
        // the cross ends the round. Saying "forget where I was" over a button that
        // empties the hat is worse than saying nothing.
        b.title = it.shuffleId
          ? (watched ? "Watched - the shelf moves on to the next"
                     : "End this shuffle - everything back in the hat")
          : why;
        b.onclick = async (e) => {
          e.stopPropagation();
          // A shuffle row is the shelf, not the episode on it - that is only what the
          // hat drew last. Putting the episode aside brought the row back under the
          // next draw, so the cross ends the round instead: everything back in the
          // hat, and the row goes. The tick still means "I watched this one".
          if (it.shuffleId && !watched) {
            try {
              await fetch("/collections/shuffle/reset", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  id: String(it.shuffleId).replace(/^coll:/, "") }),
              });
            } catch (err) { return noServer(); }
            toast("Shuffle ended");
            const gone = el.parentElement;
            el.remove();
            if (gone && !gone.querySelector(".card")) viewHome();
            return;
          }
          await setWatched(it, watched);
          // and the programme goes with it: marking one episode watched used to hand
          // the shelf over to the next episode, which looked like nothing happening
          await fetch("/ondeck/aside", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key: String(it.ratingKey), on: true }),
          }).catch(() => {});
          toast(watched ? "Marked watched" : "Forgotten");
          // Take that one card away, rather than drawing the whole page again. The
          // shelf it leaves is the shelf that was there; redrawing threw away the
          // scroll position and every other row with it, for the sake of one poster.
          const row = el.parentElement;
          el.remove();
          // An emptied shelf is a page that has genuinely changed shape - its
          // heading has to go too - and that is the one case worth drawing again.
          if (row && !row.querySelector(".card")) viewHome();
        };
        pair.appendChild(b);
      });
    el.querySelector(".poster").appendChild(pair);
  }
  // The same two marks, put to a different question: in this collection, or not.
  // One press each way, and the card moves between the two rows.
  if (coll) {
    const pair = document.createElement("div");
    pair.className = "deckmarks";
    // Which poster stands for the collection. Only offered for what it holds - a
    // title that is not in it cannot be its face.
    if (coll.inside && coll.cover !== undefined) {
      const face = document.createElement("button");
      const mine = String(coll.cover) === String(it.ratingKey);
      face.className = "deckdone face" + (mine ? " on" : "");
      face.innerHTML = "★";
      face.title = mine ? "This poster stands for the collection"
                        : "Use this poster for the collection";
      face.onclick = async (e) => {
        e.stopPropagation();
        await saveCollection({ id: coll.id, cover: String(it.ratingKey) });
        toast("Poster set");
        if (coll.after) coll.after();
      };
      pair.appendChild(face);
    }
    const b = document.createElement("button");
    b.className = "deckdone" + (coll.inside ? " no" : " yes");
    b.innerHTML = coll.inside ? "✗" : "✓";
    b.title = coll.inside ? "Take it out of this collection"
                          : "Put it in this collection";
    b.onclick = async (e) => {
      e.stopPropagation();
      // a rule being tried is not a rule yet: the mark is held with it and both are
      // written together, or neither is
      if (coll.stage) return coll.stage(String(it.ratingKey), !coll.inside);
      await saveCollection(coll.inside
        ? { id: coll.id, drop: String(it.ratingKey) }
        : { id: coll.id, add: String(it.ratingKey) });
      toast(coll.inside ? "Taken out" : "Added");
      if (coll.after) coll.after();
    };
    pair.appendChild(b);
    el.querySelector(".poster").appendChild(pair);
  }
  // On the Continue watching shelf the picture is a Resume button: everything there
  // is something already begun, and an episode played straight away while a film
  // stopped at its page for a second press. A programme resumes at the episode it
  // is up to.
  el.onclick = onDeck ? () => resumeFromDeck(it) : () => open(it);
  // the picture plays it; the words say where it lives, and lead there
  if (isEp && it.grandparentRatingKey) {
    el.querySelectorAll(".t, .s").forEach((line) => {
      line.classList.add("link");
      line.onclick = (e) => {
        e.stopPropagation();
        CTX = srv;
        $("#back").classList.add("on");
        const open_ = () =>
          viewShow(it.grandparentRatingKey, it.parentRatingKey, it.ratingKey);
        pushView(open_);
        open_();
      };
    });
  }
  return el;
}

/* how many cards fit on one line at the current window width */
function fits() {
  const min = 170, gap = 16;
  const w = main.clientWidth || window.innerWidth - 36;
  return Math.max(1, Math.floor((w + gap) / (min + gap)));
}

function grid(list, cols, onDeck, coll) {
  const g = document.createElement("div");
  g.className = "grid";
  if (cols) g.style.gridTemplateColumns = "repeat(" + cols + ", 1fr)";
  list.forEach((it) => g.appendChild(card(it, onDeck, coll)));
  return g;
}

/* one clipped line of cards, with a header that opens the full list */
function rowSection(cat, list, total) {
  const box = document.createElement("section");
  box.className = "cat";
  const head = document.createElement("h2");
  head.innerHTML = '<span class="ct">' + esc(cat.title) + "</span>" +
    '<span class="seeall">See all ' + (total || list.length) + " &rsaquo;</span>";
  head.onclick = () => openCategory(cat);
  box.appendChild(head);
  const render = () => {
    const n = fits();
    const old = box.querySelector(".grid");
    const g = grid(list.slice(0, n), n, cat.id === "ondeck");
    if (old) box.replaceChild(g, old);
    else box.appendChild(g);
  };
  render();
  box._render = render;
  return box;
}

/* ---------------- categories ---------------- */

const CATS = [
  { id: "ondeck", title: "Continue watching", by: "lastViewedAt",
    path: (sec) => ["/library/onDeck", {}] },
  { id: "newfilms", title: "Recently added in films", by: "addedAt",
    path: (sec) => ["/library/sections/" + sec.movie + "/recentlyAdded", {}] },
  { id: "released", title: "Recently released movies", by: "originallyAvailableAt",
    path: (sec) => ["/library/sections/" + sec.movie + "/all",
                    { type: 1, sort: "originallyAvailableAt:desc" }] },
  { id: "newtv", title: "Recently added in TV shows", by: "addedAt",
    path: (sec) => ["/library/sections/" + sec.show + "/recentlyAdded", {}] },
  // for a series "released" is the date of its newest episode, not the year it began:
  // a show from 2008 airing this week is more recent than one from 2024 that ended
  { id: "releasedtv", title: "Recently released episodes", by: "originallyAvailableAt",
    path: (sec) => ["/library/sections/" + sec.show + "/recentlyReleased", {}] },
];

/* The shelves this viewer keeps on the front page, in their order. Held by the
   server, so it is the same front page on every screen. Null until it has been
   asked for; an empty list means everything, in the order the program lists them. */
let HOMEROWS = null;

async function homeRows() {
  if (HOMEROWS === null) {
    try {
      const said = await api("/settings");
      // what this viewer sees: their own arrangement, or the server's for anybody
      // who has not made one
      HOMEROWS = said.homeRows || [];
      HOMEMINE = said.homeRowsMine || [];
    } catch (e) { HOMEROWS = []; HOMEMINE = []; }
  }
  return HOMEROWS;
}

/* What this viewer arranged themselves, as against what the server arranged for
   everyone. Empty means they are taking the server's. */
let HOMEMINE = [];

/* What to draw, given what has been kept. A shelf the program has since gained is
   not in an old list at all, so it keeps its place among the rest rather than
   disappearing from a page nobody has edited since. */
function homeShelves(kept) {
  if (!kept || !kept.length) return CATS.slice();
  const known = CATS.map((c) => c.id);
  const out = [];
  kept.forEach((id) => {
    const cat = CATS.filter((c) => c.id === id)[0];
    if (cat) out.push(cat);
  });
  CATS.forEach((c, i) => {
    if (kept.indexOf(c.id) < 0 && known.indexOf(c.id) === i && !wasEverKept(kept, c.id))
      out.push(c);
  });
  return out;
}

/* Whether a shelf was left out on purpose. Anything the list has ever been saved
   with is accounted for; anything else is new since. */
function wasEverKept(kept, id) {
  return (HOMEKNOWN || []).indexOf(id) >= 0;
}

let HOMEKNOWN = null;

/* Each server sorted its own answer; merging them needs one more sort across the
   join. Dates are numbers except originallyAvailableAt, which is a date string and
   compares correctly as one. */
function mergeSort(list, by) {
  return list.sort((a, b) => {
    const x = a[by], y = b[by];
    if (typeof x === "string" || typeof y === "string")
      return String(y || "").localeCompare(String(x || ""));
    return (y || 0) - (x || 0);
  });
}

/* Pressing something on the Continue watching shelf. A film or an episode goes on
   where it was left; a programme or a season asks the server which episode is next
   and goes on with that. Anything the server cannot answer for opens its page, which
   is what the press used to do. */
async function resumeFromDeck(it) {
  // A row the hat is holding carries on being the hat's: pressing Next after it draws
  // the next thing from that shelf rather than the next episode of whatever programme
  // this happens to be. Resuming a shuffle and then being handed episode after
  // episode of one series is not a shuffle at all.
  casualOn = !!it.shuffle;
  shuffleOn = it.shuffleId || "";
  // said out loud, because whether the row carried a shelf is the whole question and
  // it cannot be seen from outside the browser
  dbg("deck-press", { key: it.ratingKey, shuffle: it.shuffle || "",
                      shelf: it.shuffleId || "", casual: casualOn });
  CTX = srvOf(it);
  const at = it.viewOffset ? Math.floor(it.viewOffset / 1000) : 0;
  if (it.type === "movie" || it.type === "episode") return play(it, at, {});
  let next = null;
  try {
    next = items(await api("/next", { key: it.ratingKey }, srvOf(it)))[0] || null;
  } catch (e) { next = null; }
  if (!next) return open(it);
  return play(next, next.viewOffset ? Math.floor(next.viewOffset / 1000) : 0, {});
}

/* Paging a merged list: ask each server for the top start+size of its own, sort the
   join, and take the window. Every item that belongs in the window is somewhere in
   those answers, so the page is exact even though no server saw the whole list. The
   reply keeps a container's shape because the callers page against totalSize. */
async function fetchCat(cat, start, size) {
  let total = 0;
  const lists = await Promise.all(shownServers().map(async (srv) => {
    try {
      const [p, q] = cat.path(await sectionsOf(srv));
      if (p.indexOf("null") >= 0) return [];       // that server has no such library
      const c = await api(p, { ...q, start: 0, count: start + size }, srv);
      total += c.totalSize || c.size || 0;
      const list = items(c);
      list.forEach((it) => { it.__s = srv ? srv.id : ""; });
      return list;
    } catch (e) { return []; }
  }));
  const merged = mergeSort([].concat.apply([], lists), cat.by);
  return { Metadata: merged.slice(start, start + size), totalSize: total };
}

async function viewHome() {
  await refreshMarks();
  CTX = null;
  setBackdrop(null);
  main.innerHTML = '<div class="empty">Loading&hellip;</div>';
  const kept = await homeRows();
  const shelves = homeShelves(kept);
  let newest = null;
  const boxes = await Promise.all(shelves.map(async (cat) => {
    try {
      const c = await fetchCat(cat, 0, 24);
      const list = items(c);
      if (!list.length) return null;
      if (cat.id === "ondeck") newest = list[0];
      return rowSection(cat, list, c.totalSize || c.size || list.length);
    } catch (e) { return null; }
  }));
  // at load the page wears the newest thing in Continue watching; opening a title
  // puts that title behind everything instead, for the rest of the session
  if (!visitedBackdrop && newest) {
    const art = newest.grandparentThumb || newest.parentThumb || newest.thumb;
    if (art) setBackdrop(img(art, 800, 1200), true);
  }
  main.innerHTML = "";
  const edit = document.createElement("button");
  edit.className = "makecoll homeedit";
  edit.textContent = "Edit rows";
  edit.onclick = (e) => { e.stopPropagation(); homeForm(); };
  let any = false;
  boxes.forEach((b) => { if (b) { main.appendChild(b); any = true; } });
  // on the first row's line, beside the See all at the end of it: both are about the
  // rows rather than about anything on them
  const first = main.querySelector(".cat h2");
  if (first) first.insertBefore(edit, first.querySelector(".seeall"));
  if (!any) {
    const bar = document.createElement("div");
    bar.className = "sortbar collbar";
    bar.appendChild(edit);                 // no row to hang it on: a bar of its own
    main.appendChild(bar);
    const none = document.createElement("div");
    none.className = "empty";
    none.textContent = kept.length
      ? "Every row is turned off. Press Edit rows to put one back."
      : "Nothing to show.";
    main.appendChild(none);
  }
  onResize = () => main.querySelectorAll(".cat").forEach((b) => b._render && b._render());
}

/* full vertical list for one category, paged in as you scroll */
async function openCategory(cat, push = true) {
  if (push) pushView(() => openCategory(cat, false));
  $("#back").classList.add("on");
  CTX = null;
  onResize = null;
  main.innerHTML = '<div class="empty">Loading&hellip;</div>';
  const PAGE = 120;
  const first = await fetchCat(cat, 0, PAGE);
  const total = first.totalSize || first.size || 0;
  main.innerHTML = "";
  const h = document.createElement("h2");
  h.innerHTML = '<span class="ct">' + esc(cat.title) + "</span>" +
    '<span class="count">' + total + " items</span>";
  main.appendChild(h);
  const g = grid(items(first), 0);
  main.appendChild(g);
  restoreListPlace();
  let loaded = items(first).length, busy = false;
  if (loaded < total) {
    const sentinel = document.createElement("div");
    sentinel.className = "sentinel";
    main.appendChild(sentinel);
    const io = new IntersectionObserver(async (ents) => {
      if (!ents[0].isIntersecting || busy || loaded >= total) return;
      busy = true;
      const c = await fetchCat(cat, loaded, PAGE);
      items(c).forEach((it) => g.appendChild(card(it)));
      loaded += items(c).length;
      busy = false;
      if (loaded >= total) { io.disconnect(); sentinel.remove(); }
    }, { rootMargin: "600px" });
    io.observe(sentinel);
  }
}

/* ---------------- sorting ----------------
   Sorting happens on the server, so the whole library is ordered, not just what
   is on screen.
   Picking the key that is already active flips the direction. */

const SORT_KEYS = { added: "addedAt", name: "titleSort", year: "year",
                    released: "originallyAvailableAt", quality: "quality" };
/* Films and series open on what arrived last: the shelf answers "what is new?" before
   it answers anything else. Each order has a natural direction of its own - newest
   first, A first, oldest first - and pressing the one already chosen reverses it. */
const SORT_DEFAULT_DIR = { added: "desc", name: "asc", year: "asc",
                           released: "desc", quality: "desc" };
// the shelves open on what came out last: "what is new in the world", which is a
// better first question than "what did this server notice last"
let sortState = { key: "released", dir: "desc" };

/* The one order every list of titles is put in - the shelves, the watchlist, a
   collection - so that choosing Year on Films means the same thing everywhere. */
function sortLike(list, state) {
  const on = state || sortState;
  const dir = on.dir === "asc" ? 1 : -1;
  const named = (x) => String(x.titleSort || x.title || "");
  list.sort((a, b) => {
    if (on.key === "year") {
      // a title with no year belongs at the end whichever way the list is turned
      if (!a.year !== !b.year) return a.year ? -1 : 1;
      return dir * ((a.year || 0) - (b.year || 0));
    }
    if (on.key === "added") return dir * ((a.addedAt || 0) - (b.addedAt || 0));
    if (on.key === "quality") {
      // nothing measured is unknown rather than small, so it goes last either way
      const A = a.maxHeight || 0, B = b.maxHeight || 0;
      if (!A !== !B) return A ? -1 : 1;
      return dir * (A - B) || named(a).localeCompare(named(b));
    }
    if (on.key === "released") {
      // a date where there is one, the year otherwise; nothing dated goes last
      const when = (x) => x.originallyAvailableAt ||
                          (x.year ? String(x.year) + "-01-01" : "");
      const A = when(a), B = when(b);
      if (!A !== !B) return A ? -1 : 1;
      return dir * (A < B ? -1 : A > B ? 1 : 0);
    }
    return dir * named(a).localeCompare(named(b));
  });
  return list;
}

function sortParam() {
  return SORT_KEYS[sortState.key] + ":" + sortState.dir;
}

function sortBar(rerender, state, dirs) {
  // the shelves keep one order between them; a collection keeps its own, which is
  // why the control is told which to read and write rather than assuming
  const on = state || sortState;
  const firstDir = dirs || SORT_DEFAULT_DIR;
  const bar = document.createElement("div");
  bar.className = "sortbar";
  bar.innerHTML = '<span class="lbl">Sort:</span>';
  // one control for the three orders; the arrow beside it turns the order round
  const sel = document.createElement("select");
  sel.className = "sortpick";
  [["added", "Added"], ["released", "Released"], ["name", "Name"],
   ["year", "Year"], ["quality", "Quality"]].forEach(([key, label]) => {
    sel.add(new Option(label, key));
  });
  sel.value = on.key;
  sel.onchange = () => {
    on.key = sel.value;
    on.dir = firstDir[sel.value] || "asc";
    rerender();
  };
  bar.appendChild(sel);
  const flip = document.createElement("button");
  flip.className = "sortbtn flip";
  flip.textContent = on.dir === "asc" ? "\u2191" : "\u2193";
  flip.title = on.dir === "asc" ? "Oldest first - press to reverse"
                                : "Newest first - press to reverse";
  flip.onclick = () => {
    on.dir = on.dir === "asc" ? "desc" : "asc";
    rerender();
  };
  bar.appendChild(flip);
  return bar;
}

/* Which shelf is being looked at, per kind, and the genres each library holds. */
const genreWanted = { movie: "", show: "" };
/* Set while a genre press redraws the page: the redraw keeps the old page up until
   the new one is ready, and the genre menu comes back open. */
let genreKeep = null;
/* Presses in quick succession start overlapping loads; only the newest draws. */
let sectionSeq = 0;
const genreCache = {};
//: which decade the shelf is narrowed to, per kind. Empty is all of them.
const decadeWanted = { movie: "", show: "" };
const decadeCache = {};

async function decadesFor(type, srv) {
  const at = (srv ? srv.origin : "") + "|" + type;
  if (decadeCache[at]) return decadeCache[at];
  try {
    decadeCache[at] = items(await api("/library/decades", { type: type }, srv));
  } catch (e) {
    decadeCache[at] = [];
  }
  return decadeCache[at];
}

async function genresFor(type, srv) {
  const at = (srv ? srv.origin : "") + "|" + type;
  if (genreCache[at]) return genreCache[at];
  try {
    genreCache[at] = items(await api("/library/genres", { type: type }, srv));
  } catch (e) {
    genreCache[at] = [];
  }
  return genreCache[at];
}

async function viewSection(type) {
  const seq = ++sectionSeq;
  await refreshMarks();
  onResize = null;
  CTX = null;
  setBackdrop(null);
  if (!genreKeep) main.innerHTML = '<div class="empty">Loading&hellip;</div>';
  const genre = genreWanted[type] || "";
  const decade = decadeWanted[type] || "";
  const list = await fromAll(async (srv) => {
    const sec = await sectionsOf(srv);
    if (!sec[type]) return [];
    return api("/library/sections/" + sec[type] + "/all", {
      sort: sortParam(), type: type === "movie" ? 1 : 2,
      ...(genre ? { genre: genre } : {}),
      ...(decade ? { decade: decade } : {}),
    }, srv);
  });
  if (seq !== sectionSeq) return;
  // each server sorted its own share; one pass puts the joined list back in order
  sortLike(list);
  main.innerHTML = "";
  const h = document.createElement("h2");
  h.innerHTML = '<span class="ct">' + (type === "movie" ? "Films" : "TV shows") + "</span>";
  h.appendChild(sortBar(() => viewSection(type)));       // sort next to the title
  h.appendChild(genreBar(type, null, list.length));       // and which shelves
  h.appendChild(decadeBar(type));                        // and from when
  const count = document.createElement("span");          // count stays on the right
  count.className = "count";
  const toFetch = list.filter((x) => x.offered).length;   // greyed films are in the count
  count.textContent = list.length + " items" + (toFetch ? " \u00b7 " + toFetch + " to download" : "");
  h.appendChild(count);
  // A shelf narrowed to one genre is a collection somebody is halfway through
  // making, so the way to keep it sits at the end of that line as it does on a
  // search. The rule is the genre itself, so what is added later joins it.
  if (genre || decade) {
    // A shelf narrowed by hand is a collection somebody is halfway through making,
    // and the rule is the narrowing itself, so what is added later joins it.
    const called = [genre.split(",").join(" "), decade && decade + "s"].filter(Boolean).join(" ");
    h.appendChild(makeCollectionButton(called, {
      words: [], type: type,
      ...(genre ? { genre: genre } : {}),
      ...(decade ? { from: Number(decade), to: Number(decade) + 9 } : {}),
    }));
  }
  main.append(h, grid(list, 0));
  restoreListPlace();
}

/**
 * Everything marked for later, newest mark first.
 *
 * This server's own list only: the marks are keys, and a key belongs to the server
 * that issued it.
 */

/* Which collection is open, or "" for the watchlist itself. A collection is a
   named shelf with a rule - words in the title, and the usual narrowings - plus
   whatever has been added or struck out by hand, because no rule fits a series
   exactly: "alien" catches four of the seven and two that do not belong. */
let collectionOn = "";
/* Which list the Watchlist page shows: the watchlist, or the favorites. */
let listTab = "list";
/* A collection just opened, whose saved sort and filters have not been applied yet. */
let collDefaultsPending = "";
/* A collection being played through: the keys still to come, and whether they were
   drawn in the order shown or shuffled. Emptied when the last one ends, and
   abandoned the moment anything else is put on. */
let collectionQueue = null;
/* set for the one call that the queue makes, so play() can tell it apart from
   somebody pressing a poster */
let queuePlaying = false;
/* Whether what is on screen came from the search box. Emptying the box then puts
   back the shelf that was there before it - and only then: clearing it while a
   film's page is open should leave the film alone. */
let inSearch = false;
/* The collection a search inside a collection was asked of, so emptying the box puts
   that collection back rather than the shelf of shelves. */
let collSearchOn = "";
/* While a filter test is on, the way to run it again: pressing a mark redraws the
   rows under the form, and the wash has to go back over what it drew. */
let collTestOn = null;
/* the poster standing for the collection, kept while a test redraws the rows */
let collLastCover = "";

/* 80, 1980 or "1980s" as the first year of that decade; nought for none */
function decadeYear(d) {
  const n = parseInt(d, 10);
  if (!n) return 0;
  const y = n < 100 ? n + (n >= 30 ? 1900 : 2000) : n;
  return y - (y % 10);
}

async function saveCollection(body) {
  const r = await fetch("/collections", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let said = {};
  try { said = await r.json(); } catch (e) { said = {}; }
  // a refusal says why - a name already taken, most of the time
  if (said.error) {
    toast(said.error);
    return null;
  }
  return said.collections || [];
}

async function viewWatchlist() {
  onResize = null;
  dropStaleForm();
  // a collection being edited keeps its form through the redraw a sort or a filter makes:
  // taken before "Loading" replaces the page, which ended editing at the first change
  const editing = collectionOn ? document.querySelector(".collform") : null;
  collSearchOn = "";
  CTX = null;
  setBackdrop(null);
  if (!genreKeep) main.innerHTML = '<div class="empty">Loading&hellip;</div>';
  await refreshMarks();
  let shelves = [];
  try {
    shelves = (await (await fetch("/collections")).json()).collections || [];
  } catch (e) { shelves = []; }
  if (collectionOn && !shelves.some((c) => c.id === collectionOn)) collectionOn = "";
  let list = [];
  // the favorites, drawn as a row of their own under the watchlist
  let favList = [];
  // eslint-disable-next-line no-unused-vars
  try {
    if (collectionOn) {
      const got = await (await fetch(
        "/collections/items?id=" + encodeURIComponent(collectionOn))).json();
      list = got.Metadata || [];
    } else {
      const [marked, kept] = await Promise.all([
        api("/library/watchlist", {}, null), api("/library/favorites", {}, null)]);
      favList = items(kept);
      const keptKeys = new Set(favList.map((x) => String(x.ratingKey)));
      list = items(marked).filter((x) => !keptKeys.has(String(x.ratingKey)));
    }
  } catch (e) {
    main.innerHTML = '<div class="empty">Could not read the watchlist.</div>';
    return;
  }
  main.innerHTML = "";
  const here = shelves.filter((c) => c.id === collectionOn)[0];
  if (here && collDefaultsPending === here.id) {
    // opened: the sort and filters it was saved with
    collDefaultsPending = "";
    const saved = here.view || {};
    const key = Object.keys(SORT_KEYS).find((k) => SORT_KEYS[k] === saved.sort);
    if (key) {
      collSortState.key = key;
      collSortState.dir = saved.dir === "desc" ? "desc" : "asc";
    }
    listFilter.genre = saved.genre || "";
    listFilter.decade = saved.decade || "";
  }
  const h = document.createElement("h2");
  h.innerHTML = '<span class="ct">' +
    (here ? esc(here.name) : "Watchlist") + "</span>";

  const count = document.createElement("span");
  count.className = "count";
  count.textContent = list.length + (here ? " in this collection" : " marked");
  // The same control Films and TV carry, in the same place: beside the name, with
  // the count pushed to the end of the line. A collection keeps the release order
  // it is sent in until an order is chosen.
  // while the collection is edited these are in its form instead
  if (list.length > 1 && !(editing && here && editing.dataset.shelf === String(here.id))) {
    h.appendChild(here
      ? sortBar(() => {
          setPref("collSortKey", collSortState.key);
          setPref("collSortDir", collSortState.dir);
          viewWatchlist();
        }, collSortState, COLL_FIRST_DIR)
      : sortBar(() => viewWatchlist()));
    listFilterBars(list).forEach((b) => h.appendChild(b));
  }
  h.appendChild(count);
  main.append(h);
  if (editing && here && editing.dataset.shelf === String(here.id)) {
    main.appendChild(editing);
    // and what the rule takes and leaves, tried again under the redrawn heading
    if (editing._retest) setTimeout(editing._retest, 0);
  }
  // A collection opened from its poster carries its own two actions: the way back to
  // the shelf of shelves, and the way into what it holds. The watchlist itself has
  // neither - collections stopped living there when they got a tab.
  if (here) {
    const bar = document.createElement("div");
    bar.className = "sortbar collbar";
    const act = (label, go, how) => {
      const b = document.createElement("button");
      // Play and Shuffle do the same kind of thing as Play on a film's page and are
      // drawn the same way. Edit is done to the shelf rather than with it, so it
      // keeps the outline and sits at the far end.
      b.className = (how || "btn collact alone") +
        (how === "btn" ? " collplay" : "");
      b.textContent = label;
      b.onclick = go;
      bar.appendChild(b);
    };
    // One Play, and a switch beside it for how. Two play buttons asked somebody to
    // choose the way of playing before there was anything to play, and both of them
    // said Play - so which one was the ordinary one had to be worked out by reading.
    //
    // Shuffled means a round that is kept: carrying on with whatever was left
    // part-way, nothing twice until the hat is empty, and one row on Continue
    // watching. In order means the shelf from the top, as it reads.
    let mixed = prefs()["shuffle-" + here.id] === true;
    // no way back of its own: the arrow in the top bar is already pointing at the
    // shelf this was opened from
    // Resume names the second it would start at when the round on this shelf was
    // left somewhere. "Play" on a button that carries on from eight minutes in is a
    // promise about what pressing it does that pressing it does not keep.
    const carryOn = mixed && Number(here.resumeAt || 0) > 30;
    act(carryOn ? "▶ Resume  " + clock(Number(here.resumeAt))
                : "▶ Play",
        () => (mixed ? shuffleDraw(here.id, true)
                     : playCollection(collectionItemsSorted(list).filter((x) => !x.offered),
                                      false)),
        "btn");
    const how = document.createElement("button");
    how.className = "btn ghost kind" + (mixed ? " on" : "");
    how.textContent = "↻ Shuffle";
    how.title = "Play this shelf shuffled, keeping where the round has got to";
    how.onclick = () => {
      mixed = !mixed;
      setPref("shuffle-" + here.id, mixed);
      how.classList.toggle("on", mixed);
      // the other button says what it will now do
      viewWatchlist();
    };
    bar.appendChild(how);
    act("Edit", () => collectionForm(here));
    main.appendChild(bar);
  }
  // no heading over a collection shown whole: the name is already at the top, and
  // "Included" only means something against the "Excluded" that a search puts below it
  const shown = (here ? collectionItemsSorted(list) : sortLike(list.slice()))
    .filter(passesListFilter);
  if (shown.length !== list.length) count.textContent = shown.length + " of " + count.textContent;
  // A shelf holding both is two kinds of thing, and one grid ordered by year put a
  // season of the programme between two of the films. Films first, then what is on
  // television, each in the order the sort asks for.
  const films = shown.filter((x) => (x.type || "movie") === "movie");
  const telly = shown.filter((x) => (x.type || "movie") !== "movie");
  if (here && films.length && telly.length) {
    [["Films", films], ["Series", telly]].forEach(([label, part]) => {
      const h = sectionTitle(label + "  " + part.length);
      h.className = "collpart";
      main.appendChild(h);
      main.appendChild(grid(part, 0, false, null));
    });
  } else {
    const g = grid(shown, 0, false, null);
    main.appendChild(g);
  }
  if (!list.length && (here || !favList.length)) {
    const none = document.createElement("div");
    none.className = "empty";
    none.textContent = here ? "Nothing matches this collection yet."
      : "Nothing marked yet. Press the star on a film or an episode.";
    main.appendChild(none);
  }
  if (!here) {
    // a favorite stays on the watchlist when it is watched, and is kept on both
    // machines. Its own row, under the rest; right-click or hold a poster to set it.
    const kept = favList.filter(passesListFilter);
    if (kept.length) {
      const fh = document.createElement("h2");
      fh.innerHTML = '<span class="ct">Favorites</span><span class="count">' +
        kept.length + "</span>";
      main.appendChild(fh);
      main.appendChild(grid(sortLike(kept.slice()), 0, false, null));
    }
    main.querySelectorAll(".card[data-key]").forEach((c) => { c.dataset.watch = "1"; });
  }
  restoreListPlace();
}

/* The genre and decade a watchlist, favorites or collection page is narrowed to. */
const listFilter = { genre: "", decade: "" };

function yearOf(it) {
  return it.year || (it.originallyAvailableAt ? +String(it.originallyAvailableAt).slice(0, 4) : 0);
}

function passesListFilter(it) {
  // several genres, comma-joined: the title carries all of them
  const wants = String(listFilter.genre || "").toLowerCase().split(",")
    .map((g) => g.trim()).filter(Boolean);
  if (!wants.every((w) => (it.genres || []).some((g) => g.toLowerCase() === w))) return false;
  if (listFilter.decade && Math.floor(yearOf(it) / 10) * 10 !== +listFilter.decade) return false;
  return true;
}

/** Genre and decade, as Films and TV have them, built from what is on this list. */
function listFilterBars(list) {
  const genres = {}, decades = {};
  list.forEach((it) => {
    (it.genres || []).forEach((g) => { genres[g] = (genres[g] || 0) + 1; });
    const y = yearOf(it);
    if (y) {
      const d = Math.floor(y / 10) * 10;
      decades[d] = (decades[d] || 0) + 1;
    }
  });
  // what this list no longer holds is dropped from the filter rather than emptying it
  const names = Object.keys(genres);
  listFilter.genre = String(listFilter.genre || "").split(",").map((g) => g.trim())
    .filter((g) => g && names.some((n) => n.toLowerCase() === g.toLowerCase())).join(",");
  const decadeOptions = Object.keys(decades).sort((a, b) => b - a)
    .map((d) => [d, d + "s (" + decades[d] + ")"]);
  if (listFilter.decade && !decadeOptions.some(([v]) => String(v) === String(listFilter.decade))) {
    listFilter.decade = "";
  }
  const genrePick = genreMenu(listFilter.genre,
    Promise.resolve(names.sort((a, b) => a.localeCompare(b))
      .map((g) => [g, g + " (" + genres[g] + ")"])),
    (v) => { listFilter.genre = v; viewWatchlist(); },
    list.filter(passesListFilter).length);
  const wrap = document.createElement("span");
  wrap.className = "sortbar genrebar";
  wrap.innerHTML = "<span class='lbl'>Decade:</span>";
  const sel = document.createElement("select");
  sel.className = "genrepick";
  sel.add(new Option("All", ""));
  decadeOptions.forEach(([v, t]) => sel.add(new Option(t, v)));
  sel.value = listFilter.decade || "";
  sel.onchange = () => {
    listFilter.decade = sel.value;
    viewWatchlist();
  };
  wrap.appendChild(sel);
  return [genrePick, wrap];
}

function sectionTitle(text) {
  const h = document.createElement("h2");
  h.innerHTML = '<span class="ct">' + esc(text) + "</span>";
  return h;
}

/* Searching while a collection is open. What is found splits in two: what the
   collection already holds, with a cross to take it out, and what it does not, with
   a tick to put it in. The collection's own titles stay on the page underneath. */
async function collectionSearch(q) {
  let shelves = [];
  try {
    shelves = (await (await fetch("/collections")).json()).collections || [];
  } catch (e) { shelves = []; }
  const shelf = shelves.filter((c) => c.id === collectionOn)[0];
  if (!shelf) return;
  let held = [];
  try {
    const got = await (await fetch(
      "/collections/items?id=" + encodeURIComponent(shelf.id))).json();
    held = got.Metadata || [];
  } catch (e) { held = []; }
  let found = [];
  try {
    const got = await api("/hubs/search", { query: q, limit: 40 });
    (got.Hub || []).forEach((h) => found.push(...items(h)));
  } catch (e) { found = []; }
  const inside = new Set(held.map((m) => String(m.ratingKey)));
  const mine = found.filter((m) => inside.has(String(m.ratingKey)));
  const rest = found.filter((m) => !inside.has(String(m.ratingKey)));

  inSearch = true;
  collSearchOn = shelf.id;
  main.innerHTML = "";
  const h = document.createElement("h2");
  h.innerHTML = '<span class="ct">' + esc(shelf.name) + "</span>";
  const count = document.createElement("span");
  count.className = "count";
  count.textContent = "\u201c" + q + "\u201d";
  h.appendChild(count);
  main.appendChild(h);
  main.appendChild(collectionBar(shelves, shelf));
  const again = () => collectionSearch(q);
  if (mine.length) {
    main.appendChild(sectionTitle("Included"));
    main.appendChild(grid(mine, 0, false,
                          { id: shelf.id, inside: true, after: again }));
  }
  if (rest.length) {
    main.appendChild(sectionTitle("Excluded"));
    main.appendChild(grid(rest, 0, false,
                          { id: shelf.id, inside: false, after: again }));
  }
  if (!mine.length && !rest.length) {
    const none = document.createElement("div");
    none.className = "empty";
    none.textContent = "Nothing in the library matches that.";
    main.appendChild(none);
  }
}

/* Playing a whole collection: the first goes on now and the rest follow as each
   one ends. Shuffled means the order is drawn once, not a new draw each time - so
   nothing repeats before the shelf is through. */
async function playCollection(list, shuffled) {
  // A season card on a shelf stands for the episodes underneath it, and those are
  // what plays. Queueing the season's own key asked the library for a season and got
  // the programme, so Play opened the show and started nothing.
  const keys = [];
  (list || []).forEach((m) => {
    if (m && m.holds && m.holds.length) keys.push(...m.holds.map(String));
    else keys.push(String(m.ratingKey));
  });
  if (!keys.length) return toast("Nothing in this collection to play");
  if (shuffled) {
    for (let i = keys.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      const swapped = keys[i]; keys[i] = keys[j]; keys[j] = swapped;
    }
  }
  collectionQueue = { keys: keys, at: 0, shuffled: !!shuffled };
  casualOn = false;
  shuffleOn = "";                      // a queue is not a round
  await playFromQueue();
}

async function playFromQueue() {
  if (!collectionQueue || collectionQueue.at >= collectionQueue.keys.length) {
    collectionQueue = null;
    return closePlayer();
  }
  const key = collectionQueue.keys[collectionQueue.at++];
  let meta = null;
  try {
    meta = items(await api("/library/metadata/" + key))[0] || null;
  } catch (e) { meta = null; }
  if (!meta) return playFromQueue();          // gone from the library; take the next
  const left = collectionQueue.keys.length - collectionQueue.at;
  CTX = null;
  queuePlaying = true;
  play(meta, 0, {});
  toast(left ? left + " more in this collection" : "last in this collection");
}

/* Which rows the front page carries, and in what order. One line each: a tick to
   keep it, and arrows to move it past its neighbours. */
function homeForm() {
  const old = document.querySelector(".homeform");
  if (old) { old.remove(); return; }
  const kept = (HOMEROWS && HOMEROWS.length) ? HOMEROWS.slice() : CATS.map((c) => c.id);
  // everything the program has, in the order this viewer keeps them, with whatever
  // they have turned off on the end
  let order = kept.filter((id) => CATS.some((c) => c.id === id));
  CATS.forEach((c) => { if (order.indexOf(c.id) < 0) order.push(c.id); });
  const box = document.createElement("div");
  box.className = "setblock homeform";
  box.innerHTML = "<h3>Rows on the front page</h3>" +
    "<div class='note'>What this page carries, in the order it carries them. Yours " +
    "alone - the server keeps it, so every screen you use agrees.</div>";
  const list = document.createElement("div");
  box.appendChild(list);

  const draw = () => {
    list.innerHTML = "";
    order.forEach((id, at) => {
      const cat = CATS.filter((c) => c.id === id)[0];
      if (!cat) return;
      const on = kept.indexOf(id) >= 0;
      const row = document.createElement("div");
      row.className = "addrow subrow homerow";
      const tick = document.createElement("button");
      tick.className = "btn ghost kind" + (on ? " on" : "");
      tick.textContent = on ? "\u2713" : "\u2717";
      tick.title = on ? "Shown - press to hide it" : "Hidden - press to show it";
      tick.onclick = () => {
        if (on) kept.splice(kept.indexOf(id), 1); else kept.push(id);
        draw();
      };
      row.appendChild(tick);
      const name = document.createElement("span");
      name.className = "homename" + (on ? "" : " off");
      name.textContent = cat.title;
      row.appendChild(name);
      const up = document.createElement("button");
      up.className = "btn ghost kind";
      up.textContent = "\u2191";
      up.disabled = at === 0;
      up.onclick = () => {
        order.splice(at - 1, 0, order.splice(at, 1)[0]);
        draw();
      };
      row.appendChild(up);
      const down = document.createElement("button");
      down.className = "btn ghost kind";
      down.textContent = "\u2193";
      down.disabled = at === order.length - 1;
      down.onclick = () => {
        order.splice(at + 1, 0, order.splice(at, 1)[0]);
        draw();
      };
      row.appendChild(down);
      list.appendChild(row);
    });
  };
  draw();

  const foot = document.createElement("div");
  foot.className = "addrow subrow";
  const save = document.createElement("button");
  save.className = "btn";
  save.textContent = "Save";
  save.onclick = async () => {
    // saved in the order shown, carrying only what is ticked - the order of the
    // hidden ones is remembered too, so putting one back returns it to its place
    const rows = order.filter((id) => kept.indexOf(id) >= 0);
    HOMEROWS = rows;
    HOMEMINE = rows;
    HOMEKNOWN = order.slice();
    await fetch("/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ homeRows: rows }),
    }).catch(() => {});
    box.remove();
    viewHome();
  };
  foot.appendChild(save);
  // the owner can make what is on screen the arrangement everybody starts with
  if (!(CFG && CFG.guest)) {
    const forAll = document.createElement("button");
    forAll.className = "btn ghost";
    forAll.textContent = "Make this everyone's";
    forAll.title = "The front page anybody sees before they arrange their own";
    forAll.onclick = async () => {
      const rows = order.filter((id) => kept.indexOf(id) >= 0);
      await fetch("/settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ homeRows: rows, everyone: true }),
      }).catch(() => {});
      toast("Everyone starts with this arrangement");
    };
    foot.appendChild(forAll);
  }
  const stop = document.createElement("button");
  stop.className = "btn ghost";
  stop.textContent = "Cancel";
  stop.onclick = () => box.remove();
  foot.appendChild(stop);
  const all = document.createElement("button");
  all.className = "btn ghost";
  all.style.marginLeft = "auto";
  all.textContent = HOMEMINE.length ? "Use the server's" : "Set default";
  all.title = HOMEMINE.length
    ? "Take the arrangement the server sets for everyone"
    : "Every row, in the order the program lists them";
  all.onclick = async () => {
    // an empty list is the default: every row, in the program's own order, and it
    // stays the default as rows are added to the program later
    HOMEROWS = null;
    HOMEMINE = [];
    HOMEKNOWN = null;
    await fetch("/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ homeRows: [] }),
    }).catch(() => {});
    toast("Front page back to the server's arrangement");
    box.remove();
    viewHome();
  };
  foot.appendChild(all);
  box.appendChild(foot);
  // at the top of the page and over on the right, under the button that opens it
  main.insertBefore(box, main.firstChild);
}

/* Every collection, drawn as a poster: the face it was given, or the first title it
   holds. A shelf of shelves. */
async function viewCollections() {
  onResize = null;
  dropStaleForm();
  CTX = null;
  setBackdrop(null);
  collectionOn = "";
  main.innerHTML = '<div class="empty">Loading&hellip;</div>';
  let shelves = [];
  try {
    shelves = (await (await fetch("/collections")).json()).collections || [];
  } catch (e) { shelves = []; }
  main.innerHTML = "";
  const h = document.createElement("h2");
  h.innerHTML = '<span class="ct">Collections</span>';
  const count = document.createElement("span");
  count.className = "count";
  count.textContent = shelves.length + (shelves.length === 1 ? " shelf" : " shelves");
  main.appendChild(h);
  if (shelves.length > 1) {
    // the same control Films carries, beside the name as it is there
    h.appendChild(sortBar(() => {
      setPref("collTileKey", collTileSort.key);
      setPref("collTileDir", collTileSort.dir);
      viewCollections();
    }, collTileSort));
  }
  // the way to make another sits at the right end of the line, beside the count,
  // drawn like the button a search offers
  const make = document.createElement("button");
  make.className = "makecoll";
  make.textContent = "+ New";
  make.onclick = () => collectionForm(null);
  h.appendChild(make);
  h.appendChild(count);

  if (!shelves.length) {
    const none = document.createElement("div");
    none.className = "empty";
    none.textContent = "No collections yet. Search for something and press " +
      "\u201cNew collection from this search\u201d, or make an empty one here.";
    main.appendChild(none);
    return;
  }
  const g = document.createElement("div");
  g.className = "grid";
  collectionsSorted(shelves).forEach((c) => {
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML =
      '<div class="poster"><span class="ph"></span>' +
      (c.art ? '<img loading="lazy" src="' + img(c.art, 300, 450) +
               '" alt="" onerror="this.remove()">' : "") +
      '<div class="uhd">' + c.count + "</div></div>" +
      '<div class="t">' + esc(c.name) + "</div>" +
      '<div class="s">' + c.count + (c.count === 1 ? " title" : " titles") + "</div>";
    el.onclick = () => openCollection(c.id);
    g.appendChild(el);
  });
  main.appendChild(g);
}

/* An edit form left behind on another shelf. It is drawn for one shelf and saves
   that one, so on any other it is at best confusing and at worst about to write the
   wrong name over the right shelf. */
function dropStaleForm() {
  const form = document.querySelector(".collform");
  if (!form || form.dataset.shelf === (collectionOn || "")) return;
  form.remove();
  [].forEach.call(document.querySelectorAll(".collsplit"), (e) => e.remove());
  collTestOn = null;
}

/* One collection, opened from its poster: what it holds, and the way back. */
function openCollection(id) {
  collectionOn = id;
  collDefaultsPending = id;
  // on the back stack as the collection itself, whichever one the chips have moved to since:
  // as the shelf of shelves, back from a title opened here landed on the shelves
  pushView(() => viewWatchlist());
  $("#back").classList.add("on");
  viewWatchlist();
}

/* Turning a search into a collection. One press makes a filter of the words that
   were searched for, which is what a collection usually is; holding the same idea,
   the titles found can be poured into a shelf that already exists. */
function makeCollectionButton(q, rule) {
  const b = document.createElement("button");
  // the same button as + New on the Collections page: outlined, no colour
  b.className = "btn collact makecoll";
  b.textContent = "Make collection";
  b.title = "A collection of everything answering \u201c" + q + "\u201d";
  b.onclick = async () => {
    const name = (q || "").trim();
    const back = await saveCollection({
      name: name.charAt(0).toUpperCase() + name.slice(1),
      mode: "filter",
      rule: rule || { words: [name], type: "" },
    });
    if (back === null) return;               // one of that name already exists
    if (back.length) collectionOn = back[back.length - 1].id;
    toast("Collection made from \u201c" + name + "\u201d");
    go("collections");
    setTimeout(() => { if (collectionOn) viewWatchlist(); }, 0);
  };
  return b;
}

/* The shelves, as a row of names above the grid: the watchlist itself, then each
   collection with what it holds, then a way to make another. */
/* The shelf of shelves keeps its own order, like a collection does, and takes the
   same control as the rest of the program. A collection has a name and a date it
   was made and nothing else to sort on, so the orders that ask about a picture
   leave the shelves as they were made - which is the useful answer anyway. */
const collTileSort = {
  key: prefs().collTileKey || "name",
  dir: prefs().collTileDir || "asc",
};

/* A collection reads in release order, oldest first - a series is watched from its
   beginning - so that is where its own sort starts. Kept apart from the shelves'
   order, which is usually newest first and means something else there. */
const collSortState = {
  key: prefs().collSortKey || "released",
  dir: prefs().collSortDir || "asc",
};
/* and released means oldest first here, where on the shelves it means newest */
const COLL_FIRST_DIR = { added: "desc", name: "asc", year: "asc",
                         released: "asc", quality: "desc" };

function collectionItemsSorted(list) {
  return sortLike((list || []).slice(), collSortState);
}

function collectionsSorted(shelves) {
  // dressed as titles so the one comparator can read them: the name is the title,
  // and where a shelf stands in the file is when it was made
  const dressed = shelves.map((c, at) => Object.assign({}, c, {
    title: c.name, titleSort: c.name, addedAt: at,
    year: 0, maxHeight: c.count || 0,
  }));
  return sortLike(dressed, collTileSort);
}

function collectionBar(shelves, here) {
  const bar = document.createElement("div");
  bar.className = "sortbar collbar";
  const chip = (label, on, go, kind) => {
    const b = document.createElement("button");
    // a shelf is a place and reads like one; New and Edit do something, and looking
    // the same as the shelves made them look like two more of them
    b.className = kind === "act" ? "btn collact" : "btn ghost kind" + (on ? " on" : "");
    b.textContent = label;
    b.onclick = go;
    bar.appendChild(b);
    return b;
  };
  // Leaving a shelf closes the form that was open on it. The form is drawn for one
  // shelf and edits that one, so a shelf changed underneath it means the next Save
  // writes the old shelf's name over the new one - and until then the page does not
  // move at all, which reads as a chip that does nothing.
  const leave = (go) => () => {
    const form = document.querySelector(".collform");
    if (form) form.remove();
    [].forEach.call(document.querySelectorAll(".collsplit"), (e) => e.remove());
    collTestOn = null;
    go();
  };
  chip("Watchlist", !here, leave(() => { collectionOn = ""; viewWatchlist(); }));
  collectionsSorted(shelves).forEach((c) => {
    // the name alone: "Alien  8" read as a collection called Alien 8, and how many
    // it holds is already said beside the heading when it is open
    chip(c.name, !!here && here.id === c.id, leave(() => {
      collectionOn = c.id;
      viewWatchlist();
    }));
  });
  chip("+ New", false, () => collectionForm(null), "act");
  if (here) chip("Edit", false, () => collectionForm(here), "act");
  // no order chosen here: this row is what a search inside a collection stands on,
  // and the order of the shelves belongs to the Collections page
  return bar;
}

/* Making one, or changing one: a name and the words a title must carry. Adding or
   striking out a single title is done from the title itself. */
function collectionForm(shelf) {
  const old = document.querySelector(".collform");
  if (old) old.remove();
  const box = document.createElement("div");
  box.className = "setblock collform";
  // which shelf this form edits: it outlives the page it was opened on, and a form
  // left open on one shelf while another is opened is a form about to save the wrong
  // name over the right one
  box.dataset.shelf = shelf ? shelf.id : "";
  box.innerHTML =
    "<h3>" + (shelf ? "Edit collection" : "New collection") + "</h3>" +
    "<div class='note'>Titles are matched on the words below. A rule rarely fits a " +
    "series exactly, so titles can be added or struck out one at a time. The sort, " +
    "genre and decade below are the collection's: it holds only those, in that order.</div>";
  const row = (label, value, hint, why) => {
    const r = document.createElement("div");
    r.className = "addrow subrow";
    r.innerHTML = "<span class='sublabel'>" + label + "</span>";
    const i = document.createElement("input");
    i.type = "text";
    i.value = value || "";
    i.placeholder = hint || "";
    if (why) i.title = why;
    r.appendChild(i);
    box.appendChild(r);
    return i;
  };
  const rule = (shelf && shelf.rule) || {};
  const name = row("Name", shelf ? shelf.name : "", "Alien");
  let mode = (shelf && shelf.mode) || "filter";
  const modeRow = document.createElement("div");
  modeRow.className = "addrow subrow";
  modeRow.innerHTML = "<span class='sublabel'>Kind</span>";
  box.appendChild(modeRow);
  const why = document.createElement("div");
  why.className = "note";
  why.style.margin = "2px 0 6px 82px";
  box.appendChild(why);
  const kindRow = document.createElement("div");
  kindRow.className = "addrow subrow";
  kindRow.innerHTML = "<span class='sublabel'>Holds</span>";
  let kind = rule.type || "";
  [["", "Anything"], ["movie", "Films"], ["show", "Episodes"]].forEach(([v, t]) => {
    const b = document.createElement("button");
    b.className = "btn ghost kind" + (kind === v ? " on" : "");
    b.textContent = t;
    b.onclick = () => {
      kind = v;
      [].forEach.call(kindRow.querySelectorAll("button"),
                      (x) => x.classList.remove("on"));
      b.classList.add("on");
      live();
    };
    kindRow.appendChild(b);
  });
  box.appendChild(kindRow);
  // The sort, the genre and the decade: the controls above the list, here instead of
  // there while a collection is edited, set as the collection has them. A change is
  // tried at once, so the rows show what the collection will hold.
  const tryAgain = () => (shelf ? runTest() : live());
  const sortRow = document.createElement("div");
  sortRow.className = "addrow subrow";
  sortRow.innerHTML = "<span class='sublabel'>Sort</span>";
  sortRow.appendChild(sortBar(() => {
    setPref("collSortKey", collSortState.key);
    setPref("collSortDir", collSortState.dir);
    if (shelf) viewWatchlist();
  }, collSortState, COLL_FIRST_DIR));
  box.appendChild(sortRow);
  const genreRow = document.createElement("div");
  genreRow.className = "addrow subrow";
  genreRow.innerHTML = "<span class='sublabel'>Genre</span>";
  const genre = document.createElement("select");
  genre.className = "collgenre";
  genre.add(new Option("Any", ""));
  genre.onchange = tryAgain;
  genreRow.appendChild(genre);
  box.appendChild(genreRow);
  Promise.all([genresFor("movie", CTX), genresFor("show", CTX)]).then(([films, shows]) => {
    const seen = new Set();
    films.concat(shows)
      .map((g) => g.title || "")
      .filter((t) => t && !seen.has(t.toLowerCase()) && seen.add(t.toLowerCase()))
      .sort((a, b) => a.localeCompare(b))
      .forEach((t) => genre.add(new Option(t, t)));
    // the rule's genre as the list spells it, whatever case it was saved in
    const named = [].find.call(genre.options, (o) =>
      rule.genre && o.value.toLowerCase() === String(rule.genre).toLowerCase());
    if (rule.genre && !named) genre.add(new Option(rule.genre, rule.genre));
    genre.value = named ? named.value : (rule.genre || "");
  });
  const yearsRow = document.createElement("div");
  yearsRow.className = "addrow subrow";
  yearsRow.innerHTML = "<span class='sublabel'>Years</span>";
  const yearBox = (value, hint) => {
    const i = document.createElement("input");
    i.type = "number";
    i.min = "1900";
    i.max = "2100";
    i.placeholder = hint;
    i.value = value ? String(value) : "";
    i.style.width = "90px";
    i.oninput = () => live();
    return i;
  };
  const early = yearBox(rule.from, "from");
  const late = yearBox(rule.to, "to");
  // the decade, as above the list; a span that is not one decade keeps the year boxes
  const decadeRow = document.createElement("div");
  decadeRow.className = "addrow subrow";
  decadeRow.innerHTML = "<span class='sublabel'>Decade</span>";
  const decade = document.createElement("select");
  decade.className = "colldecade";
  decade.add(new Option("Any", ""));
  const spanned = +rule.from && +rule.to === +rule.from + 9 && +rule.from % 10 === 0;
  const custom = !!(+rule.from || +rule.to) && !spanned;
  if (custom) decade.add(new Option((rule.from || "\u2026") + " \u2013 " + (rule.to || "\u2026"), "custom"));
  decade.onchange = () => {
    if (decade.value === "custom") return;
    const y = decadeYear(decade.value);
    early.value = y ? String(y) : "";
    late.value = y ? String(y + 9) : "";
    tryAgain();
  };
  decadeRow.appendChild(decade);
  box.appendChild(decadeRow);
  const wanted = custom ? "custom" : spanned ? String(rule.from) : "";
  decade.value = wanted;
  decadesFor("movie", CTX).then((list) => {
    list.forEach((d) => decade.add(new Option(d.title + " (" + d.count + ")", String(d.decade))));
    const held = [].find.call(decade.options, (o) => o.value && decadeYear(o.value) === +rule.from);
    if (spanned && !held) decade.add(new Option(rule.from + "s", String(rule.from)));
    decade.value = spanned ? (held ? held.value : String(rule.from)) : wanted;
  }).catch(() => {});
  if (!custom) yearsRow.style.display = "none";
  // what the rule takes and leaves, drawn again when the page is: a test while one is
  // on, the two rows otherwise
  box._retest = () => (collTestOn ? live() : (shelf && collectionRows(shelf)));
  const dash = document.createElement("span");
  dash.className = "note";
  dash.textContent = " \u2013 ";
  yearsRow.append(early, dash, late);
  box.appendChild(yearsRow);
  const words = row(
    "Include", (rule.words || []).join(" | "), "dragon | wizard",
    "Every word must appear in the title: two words together find only titles " +
    "carrying both. A bar starts another rule, and a title joins if it answers " +
    "any of them: dragon | wizard takes either. The rule keeps working: a " +
    "release added to the library later that answers it joins by itself.");
  // the other half of the rule: what the words above catch and should not
  const without = row(
    "Exclude", (rule.without || []).join(" | "), "resurrection | vs",
    "Read the same way. A title answering any of these stays out, whatever the " +
    "include rules said.");
  // the same thing shown rather than described, which is how anybody actually
  // learns a small syntax
  const shown = document.createElement("div");
  shown.className = "note ruleeg";
  shown.innerHTML =
    "<i>star wars</i> &mdash; both words, so only those films<br>" +
    "<i>alien | predator</i> &mdash; either rule will do<br>" +
    "<i>star trek | star wars</i> &mdash; two rules, each of two words<br>" +
    "excluding <i>resurrection</i> &mdash; that one is left out";
  box.appendChild(shown);
  // which rows the form shows depends on the kind: a manual shelf has no rule to
  // write, and leaving the boxes there suggests it has
  const showRule = () => {
    const on = mode !== "manual";
    words.parentNode.classList.toggle("hidden", !on);
    without.parentNode.classList.toggle("hidden", !on);
    shown.classList.toggle("hidden", !on);
    kindRow.classList.toggle("hidden", !on);
    genreRow.classList.toggle("hidden", !on);
    yearsRow.classList.toggle("hidden", !on);
    decadeRow.classList.toggle("hidden", !on);
    why.textContent = on
      ? "Every word in a rule must appear in the title; a bar (|) starts another "
        + "rule and any of them will do. Matching titles join as the library gains "
        + "them, and can still be struck out one at a time."
      : "Holds what is put into it and nothing else. Nothing new is ever added by "
        + "itself.";
  };
  [["filter", "Filter", "A rule, kept live: anything added to the library later "
    + "that answers it joins this collection by itself, and can still be struck "
    + "out one at a time."],
   ["manual", "Manual", "Holds what is put into it and nothing else. Nothing "
    + "added to the library later joins it."]].forEach(([v, t, why]) => {
    const b = document.createElement("button");
    b.className = "btn ghost kind" + (mode === v ? " on" : "");
    b.textContent = t;
    b.title = why;
    b.onclick = () => {
      mode = v;
      [].forEach.call(modeRow.querySelectorAll("button"),
                      (x) => x.classList.remove("on"));
      b.classList.add("on");
      showRule();
      live();
    };
    modeRow.appendChild(b);
  });
  showRule();
  const foot = document.createElement("div");
  foot.className = "addrow subrow";
  foot.innerHTML = "<span class='sublabel'></span>";
  const save = document.createElement("button");
  save.className = "btn";
  save.textContent = shelf ? "Save" : "Make it";
  save.onclick = async () => {
    const body = {
      name: name.value.trim() || "Collection",
      mode: mode,
      rule: {
        // one rule per bar; the words inside a rule all have to appear
        words: words.value.split("|").map((w) => w.trim()).filter(Boolean),
        without: without.value.split("|").map((w) => w.trim()).filter(Boolean),
        type: kind,
        // while editing, these follow the genre and decade picked above the list
        genre: genre.value,
        from: +early.value || 0,
        to: +late.value || 0,
      },
    };
    if (shelf) body.id = shelf.id;
    // the sort, genre and decade showing now become how this collection opens
    body.view = { sort: SORT_KEYS[collSortState.key] || "", dir: collSortState.dir,
                  genre: listFilter.genre || "", decade: listFilter.decade || "" };
    // what the marks moved while the rule was being tried goes with it
    if (stagePins) {
      body.pinned = stagePins;
      body.hidden = stageHides;
    }
    const back = await saveCollection(body);
    if (back === null) return;               // refused: the form stays, with the name
    if (!shelf && back.length) collectionOn = back[back.length - 1].id;
    collTestOn = null;
    box.remove();
    [].forEach.call(document.querySelectorAll(".collsplit"), (e) => e.remove());
    viewWatchlist();
  };
  foot.appendChild(save);
  // Trying the rule before keeping it: what it takes comes back green, what the
  // exclude words or the struck-out list knock out comes back red.
  const tryIt = document.createElement("button");
  tryIt.className = "btn ghost";
  tryIt.textContent = "Test filter";
  // Trying a rule moves the titles into the rows it would put them in, washed green
  // for taken and red for left out, so the shelf can be read before it is saved.
  // What the marks have moved while the rule is being tried. Null until a test is
  // run; thrown away by Cancel test, written by Save along with the rule itself.
  let stagePins = null, stageHides = null;
  const stage = (key, wantIn) => {
    const pins = (stagePins || []).filter((k) => k !== key);
    const hides = (stageHides || []).filter((k) => k !== key);
    if (wantIn) pins.push(key);
    else hides.push(key);
    stagePins = pins;
    stageHides = hides;
    runTest();
  };
  const unstage = (key) => {
    stagePins = (stagePins || []).filter((k) => k !== key);
    stageHides = (stageHides || []).filter((k) => k !== key);
    runTest();
  };
  const clearWash = () => {
    collTestOn = null;
    stagePins = null;
    stageHides = null;
    const said = document.querySelector(".colltest");
    if (said) said.remove();
    if (shelf) collectionRows(shelf);          // back to what is actually saved
  };
  // typing asks again before the last answer is back; the older one must not be the
  // one that draws
  let testRun = 0;
  const runTest = async () => {
    const mine = ++testRun;
    let said = { Metadata: [], Excluded: [] };
    try {
      const r = await fetch("/collections/test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: mode,
          rule: {
            words: words.value.split("|").map((w) => w.trim()).filter(Boolean),
            without: without.value.split("|").map((w) => w.trim()).filter(Boolean),
            type: kind,
            genre: genre.value,
            from: +early.value || 0,
            to: +late.value || 0,
          },
          pinned: stagePins || (shelf && shelf.pinned) || [],
          hidden: stageHides || (shelf && shelf.hidden) || [],
        }),
      });
      said = await r.json();
    } catch (e) {
      toast("The filter could not be tried");
      return;
    }
    if (mine !== testRun) return;              // a newer question has been asked
    const took = said.Metadata || [], out = said.Excluded || [];
    // the lists the marks move from here on, whether or not any has been pressed yet
    if (!stagePins) {
      stagePins = ((shelf && shelf.pinned) || []).map(String);
      stageHides = ((shelf && shelf.hidden) || []).map(String);
    }
    // set before the rows are drawn: they ask whether a test is on
    collTestOn = runTest;
    if (shelf) {
      await collectionRows(shelf,
                           { held: took, struck: out, stage: stage, clear: unstage });
    }
    const old = document.querySelector(".colltest");
    if (old) old.remove();
    const say = document.createElement("div");
    say.className = "note colltest";
    const byHand = took.filter((m) => m.hand).length + out.filter((m) => m.hand).length;
    say.textContent = took.length + (took.length === 1 ? " title" : " titles") +
      " taken" + (out.length ? ", " + out.length + " left out" : "") +
      (byHand ? ", " + byHand + " of them a manual edit" : "") +
      ". Marks move a poster here and are kept, with the rule, when Save is pressed.";
    box.appendChild(say);
    tryIt.textContent = "Cancel test";
  };
  // While a test is on the rows answer the boxes as they are typed in, a moment
  // after the last keystroke rather than on every one.
  let liveTick = null;
  const live = () => {
    if (!collTestOn) return;
    clearTimeout(liveTick);
    liveTick = setTimeout(runTest, 350);
  };
  words.oninput = live;
  without.oninput = live;
  tryIt.onclick = () => {
    // the same button turns the test off again and puts back the saved arrangement
    if (collTestOn) {
      clearWash();
      tryIt.textContent = "Test filter";
      return;
    }
    runTest();
  };
  foot.appendChild(tryIt);
  const stop = document.createElement("button");
  stop.className = "btn ghost";
  stop.textContent = "Cancel";
  stop.onclick = () => {
    collTestOn = null;
    box.remove();
    [].forEach.call(document.querySelectorAll(".collsplit"), (e) => e.remove());
    // the page is drawn again rather than patched back together: its own grid was
    // taken away when the two rows went up
    if (shelf) viewWatchlist();
  };
  foot.appendChild(stop);
  if (shelf) {
    const gone = document.createElement("button");
    gone.className = "btn ghost";
    gone.style.marginLeft = "auto";
    gone.textContent = "Delete";
    gone.onclick = async () => {
      if (!confirm("Delete the collection " + shelf.name +
                   "? The films stay in the library.")) return;
      await saveCollection({ id: shelf.id, remove: true });
      collectionOn = "";
      box.remove();
      viewWatchlist();
    };
    foot.appendChild(gone);
  }
  box.appendChild(foot);
  main.insertBefore(box, main.children[1] || null);
  if (shelf) collectionRows(shelf);
  // editing: the sort and filters above the list are in the form now, not shown twice,
  // and the list is not narrowed by a choice no longer on screen
  if (shelf) {
    main.querySelectorAll("h2 .sortbar").forEach((e) => e.remove());
    if (listFilter.genre || listFilter.decade) {
      listFilter.genre = "";
      listFilter.decade = "";
      viewWatchlist();
    }
  }
}

/* Editing one shows both sides of it: what it holds, and what its rule caught and
   the hand struck out. Either row moves a title across with one press, so a
   collection can be shaped without searching for anything. */
async function collectionRows(shelf, preview) {
  [].forEach.call(document.querySelectorAll(".collsplit"), (e) => e.remove());
  let held = [];
  let struck = [];
  let face = "";
  if (preview) {
    // a rule tried but not saved: the rows show where it would put everything
    held = preview.held || [];
    struck = preview.struck || [];
    face = collLastCover;
  } else {
    try {
      const got = await (await fetch(
        "/collections/items?id=" + encodeURIComponent(shelf.id))).json();
      held = got.Metadata || [];
      struck = got.Excluded || [];
      // asked for afresh rather than read off the cache this form was opened with,
      // which is a moment old the instant a poster is chosen
      face = got.cover || "";
      collLastCover = face;
    } catch (e) { return; }
  }
  const block = document.createElement("div");
  block.className = "collsplit";
  const again = () => {
    if (document.querySelector(".collform")) collectionRows(shelf);
    else viewWatchlist();
  };
  const empty = (words) => {
    const none = document.createElement("div");
    none.className = "empty";
    none.textContent = words;
    return none;
  };
  // the label on a title that is where it is by hand rather than by the rule, and
  // the wash of colour while a rule is being tried
  const dress = (g, list, how) => {
    [].forEach.call(g.querySelectorAll(".card"), (el, at) => {
      const m = list[at];
      if (!m) return;
      if (how) el.classList.add(how);
      if (!m.hand) return;
      const tag = document.createElement("button");
      tag.className = "byhand";
      tag.innerHTML = "manual edit \u2715";
      tag.title = "Take the manual edit back and let the rule decide this one";
      tag.onclick = async (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (preview && preview.clear) return preview.clear(String(m.ratingKey));
        await saveCollection({ id: shelf.id, clear: String(m.ratingKey) });
        collectionRows(shelf);
      };
      const poster = el.querySelector(".poster");
      if (poster) poster.appendChild(tag);
    });
    return g;
  };
  const hook = (preview && preview.stage) || null;
  block.appendChild(sectionTitle("Included"));
  block.appendChild(held.length
    ? dress(grid(held, 0, false,
                 // no choosing a face while a rule is being tried: the shelf on
                 // screen is not the shelf yet, and that button writes at once
                 { id: shelf.id, inside: true, after: again, stage: hook,
                   cover: hook ? undefined : face }),
            held, preview ? "hazein" : "")
    : empty("Nothing in it yet."));
  block.appendChild(sectionTitle("Excluded"));
  block.appendChild(struck.length
    ? dress(grid(struck, 0, false,
                 { id: shelf.id, inside: false, after: again, stage: hook }),
            struck, preview ? "hazeout" : "")
    : empty("Nothing struck out. Search to add a title the rule does not catch."));
  const form = document.querySelector(".collform");
  if (form && form.parentNode) form.parentNode.insertBefore(block, form.nextSibling);
  // The collection was already on the page underneath, so every title it holds was
  // drawn twice - once in Included and once in the grid below it. While the two
  // rows are up they are the only account of what is in and what is out.
  [].forEach.call(main.children, (e) => {
    if (e.classList.contains("grid")) e.remove();
  });
  // and nothing is played while it is being shaped: the two rows on screen are not
  // the order anything would go on in
  [].forEach.call(document.querySelectorAll(".collplay"), (e) => e.remove());
  // a mark redraws these rows; if a rule is being tried, it is tried again over them
  if (collTestOn && !preview) collTestOn();
}

/* A search answers in rows: the titles that carry the word, then the shelves the
   word names - a category, or a year. */
const FACET_ROWS = ["genre", "year"];
const SEARCH_ROWS = ["movie", "show", "episode"].concat(FACET_ROWS);

async function viewSearch(q) {
  onResize = null;
  CTX = null;
  setBackdrop(null);
  if (!genreKeep) main.innerHTML = '<div class="empty">Searching&hellip;</div>';
  // one hub per kind, gathered from every server that answers
  const hubs = {};
  await fromAll(async (srv) => {
    const c = await api("/hubs/search", { query: q, limit: 40 }, srv);
    const all = [];
    for (const hub of c.Hub || []) {
      if (SEARCH_ROWS.indexOf(hub.type) < 0) continue;
      hubs[hub.type] = hubs[hub.type] || { type: hub.type, title: hub.title, Metadata: [] };
      for (const it of items(hub)) { hubs[hub.type].Metadata.push(it); all.push(it); }
    }
    return all;                       // tagged by fromAll, in place
  });
  // The server's own order, because it puts first whatever the words asked for: an
  // episode code leads with episodes, plain words with the titles that carry them.
  // Which tab the search was typed on beats that: somebody looking through TV who
  // searches is looking for a programme, not for the film of the same name.
  const on = (document.querySelector(".tab.active") || {}).dataset;
  const first = { shows: ["show", "episode", "movie"],
                  movies: ["movie", "show", "episode"] }[(on && on.view) || ""];
  const found = Object.keys(hubs).map((t) => hubs[t]);
  // films and programmes as the Films and TV tabs have them: that genre, that decade and
  // that order, with the greyed films to download among the rest
  found.forEach((h) => {
    if (h.type !== "movie" && h.type !== "show") return;
    const g = String(genreWanted[h.type] || "").toLowerCase().split(",")
      .map((n) => n.trim()).filter(Boolean);
    const from = decadeYear(decadeWanted[h.type]);
    h.Metadata = h.Metadata.filter((x) =>
      g.every((w) => (x.genres || []).some((n) => String(n).toLowerCase() === w)) &&
      (!from || (x.year && x.year >= from && x.year <= from + 9)));
    sortLike(h.Metadata);
  });
  // What the word spells comes before what it means: a title carrying the word is a
  // closer answer than a shelf the word names. So the category and the year stay at
  // the bottom whichever tab the search was typed on.
  const rank = (h) => (first ? first.indexOf(h.type) : 0) +
                      (FACET_ROWS.indexOf(h.type) < 0 ? 0 : 10);
  found.sort((a, b) => rank(a) - rank(b));
  const c = { Hub: found };
  inSearch = true;
  main.innerHTML = "";
  let shown = 0;
  (found || []).forEach((h) => { shown += items(h).length; });
  // What was searched for is usually what a collection is, so the way to make one
  // sits beside the count of what was found rather than in a row of its own.
  const head = document.createElement("h2");
  head.innerHTML = '<span class="ct">' + esc(q) + "</span>";
  const total = document.createElement("span");
  total.className = "count";
  total.textContent = shown + (shown === 1 ? " found" : " found");
  head.appendChild(total);
  // the order and the narrowing, changed here and kept for the tab they belong to
  const kind = on && on.view === "shows" ? "show" : "movie";
  head.appendChild(sortBar(() => viewSearch(q)));
  head.appendChild(genreBar(kind, () => viewSearch(q)));
  head.appendChild(decadeBar(kind, () => viewSearch(q)));
  head.appendChild(makeCollectionButton(q, { words: [q], type: "" }));
  main.appendChild(head);
  let any = false;
  for (const hub of c.Hub || []) {
    if (SEARCH_ROWS.indexOf(hub.type) < 0) continue;
    const list = items(hub);
    if (!list.length) continue;
    const h = document.createElement("h2");
    h.innerHTML = '<span class="ct">' + esc(hub.title) + "</span>";
    main.append(h, grid(list, 0));
    any = true;
  }
  if (!any) main.innerHTML = '<div class="empty">No results for "' + esc(q) + '".</div>';
}

/* ---------------- detail ---------------- */

/* ---------------- subtitles ----------------

   The text of a subtitle *inside* the container cannot simply be handed over, so
   showing one means either pulling it out with ffmpeg or drawing it into the
   picture - and drawing it in costs a full video re-encode. A track that lives in
   its own file beside the video can be fetched as text and rendered by the browser
   for nothing.
   Casting always burns: the Chromecast cannot reach a blob: URL. */

/* What does the re-encoding when a file cannot be played as it stands. One entry
   today, kept as a list because the picker is written to show what a machine has. */
/* What does the re-encoding, named by the machine doing it rather than by the card
   the program was written on: a server with no NVIDIA in it was still offering
   "GPU (NVENC)". Filled in from /gpu/status once that answer arrives. */
let ENGINES = [["gpu", "This machine"], ["cpu", "Processor"]];
(function whatThisMachineHas() {
  fetch("/gpu/status").then((r) => r.json()).then((said) => {
    // The card by the name of the card this machine actually has, and the processor
    // beside it. The processor is slower and always there: it is the answer for a
    // card that is full, busy, or making a mess of one particular file.
    if (said && said.engine) ENGINES = [["gpu", said.engine], ["cpu", "Processor"]];
  }).catch(() => {});
})();
const engine = () => prefs().engine || "gpu";

/* Every library this client can reach is one of ours: this server's own, or a
   friend's, which answers the same endpoints. Kept as a function because it is asked
   in twenty places, and the answer used to depend on a setting. */
const isLocal = () => true;
function base(srv) {
  const s = srv !== undefined ? srv : CTX;
  return (s ? s.origin : location.origin) + "/local";
}

const LANGS = [
  ["", "Off"], ["en", "English"], ["sv", "Svenska"], ["da", "Dansk"],
  ["no", "Norsk"], ["fi", "Suomi"], ["de", "Deutsch"], ["fr", "Français"],
  ["es", "Español"], ["it", "Italiano"], ["nl", "Nederlands"], ["pt", "Português"],
];

function prefs() {
  try { return JSON.parse(localStorage.getItem("palladium-prefs") || "{}"); }
  catch (e) { return {}; }
}
function setPref(k, v) {
  const p = prefs();
  p[k] = v;
  localStorage.setItem("palladium-prefs", JSON.stringify(p));
}

/* which file on disk: a title can hold several versions (4K and 1080p, say) */
function mediaOf(meta, mi) {
  const list = (meta && meta.Media) || [];
  return list[mi || 0] || list[0] || null;
}

/* Which copy to play: the one settled on before, otherwise the best.

   The list arrives best picture first, so index 0 is the best copy on a title nobody
   has chosen for. A choice is remembered by the server against the title, so it holds
   in the browser, the app and on the television alike. */
function preferredCopy(meta) {
  const list = (meta && meta.Media) || [];
  const at = list.findIndex((md) => md.picked);
  return at < 0 ? 0 : at;
}

function rememberCopy(meta, mi) {
  const md = mediaOf(meta, mi), part = md && md.Part && md.Part[0];
  if (!meta || !part) return;
  // nothing is stored for the best copy, so a better rip added later becomes the
  // default rather than losing to a choice made about a file that is no longer best
  fetch("/copy/pick", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: meta.ratingKey,
                           file: mi === 0 ? "" : part.file }),
  }).catch(() => {});
}

/** What the cache is called on disk - the part that actually identifies it. */
function versionFile(md) {
  const part = md && md.Part && md.Part[0];
  // the character by its number: a backslash in a regular expression does not survive
  // the trip from the editor to the file
  const path = ((part && part.file) || "").split(String.fromCharCode(92)).pop();
  return path.split("/").pop();
}

function versionLabel(md) {
  const named = versionFile(md);
  const res = md.videoResolution === "4k" ? "4K" : (md.videoResolution || "") + "p";
  const mbps = Math.round((md.bitrate || 0) / 100) / 10;
  // h264 is copied through; anything else means a software re-encode on this server
  const cost = md.videoCodec === "h264" ? "remux" : "re-encode";
  const facts = res + " " + (md.videoCodec || "").toUpperCase() + "/" +
                (md.audioCodec || "").toUpperCase() + " " + mbps + " Mbps " +
                (md.container || "") + " - " + cost;
  // the name is what tells two copies apart; the facts are along the top of the player
  return named || facts;
}

function subOptions(m, mi) {
  const md = mediaOf(m, mi);
  // a picture track that cannot be painted in is a track this browser cannot show
  const part = md && md.Part && md.Part[0];
  const streams = (part && part.Stream) || [];
  return streams.filter((s) => s.streamType === 3).map((s) => ({
    id: s.id,
    index: s.index,                         // ffmpeg needs the stream index
    key: s.key || "",                       // present only for external files
    external: !!s.key,
    confirmed: !!s.confirmed,               // of the release proved on this series
    picked: !!s.picked,                     // and the one chosen here last time
    code: (s.languageTag || s.languageCode || "").toLowerCase(),
    lang: s.language || s.languageTag || "Unknown",
    codec: (s.codec || "").toLowerCase(),
    forced: !!s.forced,
    // a downloaded file is named for the release it came from, which is the only
    // thing that tells two English subtitles apart
    label: s.title || [s.language || s.languageTag || "Unknown",
                       (s.codec || "").toUpperCase(),
                       s.forced ? "forced" : ""].filter(Boolean).join(" "),
  })).filter((t) => BURNOK || isTextSub(t));
}

/* Which language the server keeps for this viewer - the one the app reads. A browser
   that has never been asked carries no choice of its own, and starting with subtitles
   off for somebody who asked for English on their television is the wrong answer. */
let SERVERLANG = "";
/* Whether this viewer may be sent a burned-in bitmap subtitle. Off for remote
   viewers unless the owner allows it: burning is a full re-encode plus the upload.
   The tracks are filtered out of the picker rather than offered and refused. */
let BURNOK = true;

/* first track matching the default language, e.g. pref "en" matches en/eng/en-US */
function preferredSub(m, mi) {
  const list = subOptions(m, mi);
  // what was chosen for this title last time beats any rule about languages
  const chosen = list.filter((t) => t.picked)[0];
  if (chosen) return chosen;
  // "" is a choice - off - and no key at all is no choice, so the answer given on the
  // other screens stands
  const mine = prefs();
  const want = "subLang" in mine ? mine.subLang : (SERVERLANG || "");
  if (!want) return null;
  const hit = (t) => t.code === want || t.code.indexOf(want + "-") === 0 ||
                     t.code.slice(0, 3) === { en: "eng", sv: "swe", da: "dan", no: "nor",
                       fi: "fin", de: "ger", fr: "fre", es: "spa", it: "ita",
                       nl: "dut", pt: "por" }[want];
  return list.filter((t) => hit(t) && !t.forced)[0] || list.filter(hit)[0] || null;
}

function subLabel(t, casting) {
  if (!t) return "Off";
  if (t.external && !casting) return t.label + " (sidecar file)";
  if (!casting && isTextSub(t)) return t.label;          // extracted during the remux
  // "uses CPU" was true when the encoder was software. The film is encoded on the
  // card; what the CPU still does is lay the picture over each frame, and even that
  // measured 3.3x faster on the card when it was tried.
  return t.label + " · burned in";                    // bitmap track, or casting
}

/* Only a track in its own file arrives as text, and browsers want WebVTT */
function srtToVtt(text, shift) {
  // the server converts what it serves, so this often arrives as WebVTT already
  const body = text.replace(/\r+/g, "")
    .replace(/^WEBVTT[^\n]*\n+/, "")
    .replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g, "$1.$2");
  if (!shift) return "WEBVTT\n\n" + body;
  const at = (t) => {
    const [h, m, s] = t.split(":");
    return +h * 3600 + +m * 60 + parseFloat(s) - shift;
  };
  const fmt = (v) => {
    if (v < 0) v = 0;
    const h = Math.floor(v / 3600), m = Math.floor((v % 3600) / 60), s = v % 60;
    return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" +
           s.toFixed(3).padStart(6, "0");
  };
  // Cue by cue, because one that finished before this stream began has no place in it.
  // Clamping such cues to zero - which is what a plain search and replace does - puts
  // the whole first hour of dialogue on screen at once the moment the film starts.
  const kept = [];
  const times = /(\d+:\d\d:\d\d\.\d+)\s*-->\s*(\d+:\d\d:\d\d\.\d+)/;
  for (const block of body.split(/\n\s*\n/)) {
    const lines = block.split("\n");
    const which = lines.findIndex((l) => l.indexOf("-->") >= 0);
    if (which < 0) continue;
    const found = times.exec(lines[which]);
    if (!found) continue;
    const from = at(found[1]), to = at(found[2]);
    if (to <= 0) continue;                    // over before this stream started
    lines[which] = fmt(from) + " --> " + fmt(to);
    const cue = lines.join("\n").trim();
    if (cue) kept.push(cue);
  }
  return "WEBVTT\n\n" + kept.join("\n\n") + "\n";
}

/**
 * Take off whatever subtitle is showing, however it got there.
 *
 * There are three ways a track reaches the video element - a <track> element for a
 * file beside the video, and two script-made tracks for text pulled out of the
 * container - and a script-made track cannot be removed at all, only silenced. Every
 * one of them has to be dealt with, or the new subtitle is drawn over the old one.
 */
let subGen = 0;                    // which subtitle request is the current one
let subTrack = null;               // the one track allowed on screen

/**
 * Switch off every text track except the one just attached.
 *
 * A track made by script cannot be removed from a video element, and anything that
 * wakes one up draws its lines over the top of the current subtitle. This is the rule
 * that holds regardless of how it happened. Returns how many strays it found.
 */
function oneTrackOnly() {
  const v = $("#video");
  const list = v ? v.textTracks : null;
  let strays = 0;
  for (let i = 0; list && i < list.length; i++) {
    const tt = list[i];
    // one track shows: the current one. Any other found showing is a leftover.
    if (tt.mode === "showing" && tt !== subTrack) {
      tt.mode = "disabled";
      strays++;
    }
  }
  return strays;
}

function clearSubtitles() {
  subGen++;                        // anything still in the air is now out of date
  drawSubtitles.shown = false;     // report the first line of the next one too
  subTrack = null;
  const box = $("#subs");
  if (box) { box.innerHTML = ""; box.dataset.said = ""; }
  const v = $("#video");
  v.querySelectorAll("track").forEach((t) => t.remove());
  const list = v.textTracks;
  for (let i = 0; list && i < list.length; i++) {
    const tt = list[i];
    // Silencing is not enough: a track that cannot be removed keeps its cues, and
    // anything that wakes it up again draws every line a second time. Emptying it
    // leaves nothing to draw. Cues are only reachable when the track is not disabled.
    tt.mode = "hidden";
    const cues = tt.cues;
    for (let j = cues ? cues.length - 1 : -1; j >= 0; j--) {
      try { tt.removeCue(cues[j]); } catch (e) { /* already gone */ }
    }
    tt.mode = "disabled";
  }
  if (S && S.vttUrl) { URL.revokeObjectURL(S.vttUrl); S.vttUrl = null; }
}

/**
 * Hang a subtitle file on the video element.
 *
 * The shift is worked out here rather than passed in, because only the session knows
 * it: a direct play reports film time, an encode reports time since it started.
 */
async function attachSidecar(track) {
  const v = $("#video");
  clearSubtitles();
  if (!track) return;
  const mine = subGen;
  try {
    const txt = await (await fetch(url(track.key))).text();
    if (mine !== subGen) return;              // overtaken while it was fetching
    // the browser draws these, and it compares them with its own clock: an encode
    // begins at zero however far into the film it starts, so the cues come back by
    // that much. The file is attached again whenever the origin changes.
    const move = ((S && !S.direct) ? (S.timeBase || 0) : 0) - SUBNUDGE;
    const blob = new Blob([srtToVtt(txt, move)], { type: "text/vtt" });
    const src = URL.createObjectURL(blob);
    if (S) S.vttUrl = src;
    const el = document.createElement("track");
    el.kind = "subtitles";
    el.label = track.lang;
    el.srclang = track.code || "und";
    el.src = src;
    el.default = true;
    v.appendChild(el);
    // this track, not whichever happens to be first in the list
    const show = () => {
      // a newer choice has taken the screen: this attach is over
      if (mine !== subGen) return;
      if (!el.track) return;
      subTrack = el.track;
      // The browser paints these. A page can be composited away from the video
      // entirely - a hardware overlay shows the picture and nothing drawn over it -
      // and the browser's own cue layer is the only one certain to be seen.
      const cues = el.track.cues || [];
      for (let i = 0; i < cues.length; i++) placeCue(cues[i]);
      showWhenSized(el.track);
      oneTrackOnly();
      startSubtitleDrawing();
    };
    el.onload = show;
    setTimeout(show, 300);
  } catch (e) {
    toast("Could not load subtitle file");
  }
}

/* ---- embedded text subtitles, without burning them in ----

   A text track is pulled out of the container during the remux and served as a
   separate stream on /video/:/transcode/universal/subtitles for the same session, so the
   video stays "copy". Two catches learned the hard way:
     * the session has to be producing already - ask before the first segment is fetched
       and the endpoint answers 404;
     * what arrives is ASS (FFmpeg's dialogue format) despite a text/vtt content-type, and
       it streams in step with the transcoder rather than arriving in one piece.
   So it is read progressively and each Dialogue line becomes a cue. */

const TEXT_SUBS = ["srt", "subrip", "ass", "ssa", "mov_text", "webvtt", "vtt", "text"];
const isTextSub = (t) => t && TEXT_SUBS.indexOf(t.codec) >= 0;

function assTime(t) {                       // 0:01:23.45 -> seconds
  const m = /(\d+):(\d\d):(\d\d)[.,](\d+)/.exec(t);
  if (!m) return null;
  return +m[1] * 3600 + +m[2] * 60 + +m[3] + parseFloat("0." + m[4]);
}

function assText(raw) {
  return raw.replace(/\{[^}]*\}/g, "")      // drop {\an8} override tags
            .replace(/\\N/g, "\n")          // ASS hard line break
            .replace(/\\n/g, "\n")
            .replace(/\\h/g, " ")           // non-breaking space
            .trim();
}

/* WebVTT straight from ffmpeg, turned into cues by hand.
   A <track> element would be simpler, but dash.js strips track elements off the
   video during its teardown, so the cues are added to a TextTrack instead - there
   is no API to remove one of those, which is exactly what we want here. */
function vttTime(t) {
  const m = /(?:(\d+):)?(\d+):(\d+)[.,](\d+)/.exec(t);
  if (!m) return null;
  return (+(m[1] || 0)) * 3600 + +m[2] * 60 + +m[3] + parseFloat('0.' + m[4]);
}

async function attachGpuSubtitles(url, track) {
  const v = $('#video');
  clearSubtitles();                 // a file from a moment ago is still hanging there
  const mine = subGen;
  const tt = v.addTextTrack('subtitles', track.lang, track.code || 'und');
  subTrack = tt;
  showWhenSized(tt);          // the browser paints it, once it knows the shape
  oneTrackOnly();
  startSubtitleDrawing();
  // A track inside the film is lifted out with ffmpeg before it can be drawn, and on
  // a large file that is minutes the first time. An empty screen with no explanation
  // reads as broken subtitles.
  let slow = setTimeout(function again() {
    if (mine !== subGen) return;
    toast('Lifting the subtitles out of the film…', 1);
    slow = setTimeout(again, 3000);          // the notice lasts three seconds
  }, 2500);
  try {
    const answer = await fetch(url);
    const text = await answer.text();
    clearTimeout(slow);
    if (mine !== subGen) { tt.mode = 'disabled'; return; }
    // The server may have handed over the first twenty minutes while it reads the
    // rest out of the film. Come back for the whole of it before the film reaches
    // the end of what is in hand.
    if (answer.headers.get('X-Palladium-Subs') === 'partial') {
      const covers = +(answer.headers.get('X-Palladium-Subs-Until') || 1200);
      const again = () => {
        if (mine !== subGen) return;
        const left = covers - (((S && S.timeBase) || 0) + playAt());
        // two minutes before it runs out, and never more than a minute between tries
        setTimeout(() => {
          if (mine !== subGen) return;
          attachGpuSubtitles(url, track);
        }, Math.max(5000, Math.min(60000, (left - 120) * 1000)));
      };
      again();
    }
    let added = 0;
    text.split(/\r?\n\r?\n/).forEach((block) => {
      const lines = block.split(/\r?\n/).filter((x) => x.trim());
      const idx = lines.findIndex((x) => x.indexOf('-->') > 0);
      if (idx < 0) return;
      const parts = lines[idx].split('-->');
      const a = vttTime(parts[0]), b = vttTime(parts[1]);
      const body = lines.slice(idx + 1).join(String.fromCharCode(10)).trim();
      if (a === null || b === null || !body) return;
      try { tt.addCue(placeCue(new VTTCue(a, b, body))); added++; } catch (e) {}
    });
    if (!added) toast('No subtitle cues came back');
  } catch (e) {
    clearTimeout(slow);
    toast('Subtitle extraction failed: ' + e.message);
  }
}
/* in-player version picker: switching file restarts the stream where it left off */
function fillPlayerEngine() {
  const sel = $("#pl-engine");
  sel.innerHTML = "";
  ENGINES.forEach((e) => sel.add(new Option(e[1], e[0])));
  sel.value = engine();
  sel.onchange = () => {
    setPref("engine", sel.value);
    if (!S) return;
    const meta = S.meta, at = Math.floor(S.pos || 0), subId = S.subId, mi = S.mi;
    stop(true);
    play(meta, at, { subId: subId || 0, mediaIndex: mi });
  };
}

function fillPlayerVersions() {
  const sel = $("#pl-ver");
  const only1 = $("#pl-verone");
  const list = ((S && S.meta && S.meta.Media)) || [];
  if (!sel) return;
  sel.innerHTML = "";
  const wrap = sel.closest(".pref");
  if (list.length < 2) {
    // Nothing to choose, but still worth knowing which file is playing. The name goes
    // in its own span: writing it into the label would take the select with it, and
    // the next film would find nothing to fill.
    const only = list[0] ? versionFile(list[0]) : "";
    wrap.classList.toggle("hidden", !only);
    sel.classList.add("hidden");
    if (only1) {
      only1.textContent = only;
      only1.title = only;
      only1.classList.toggle("hidden", !only);
    }
    return;
  }
  if (only1) only1.classList.add("hidden");
  sel.classList.remove("hidden");
  wrap.classList.remove("hidden");
  list.forEach((md, i) => sel.add(new Option(versionLabel(md), String(i))));
  sel.value = String(S.mi || 0);
  sel.onchange = () => {
    const meta = S.meta, at = Math.floor(S.pos || S.start), subId = S.subId;
    const idx = +sel.value;
    rememberCopy(meta, idx);
    stop(true);
    play(meta, at, { mediaIndex: idx, subId: subId || 0 });
  };
}

/* the in-player picker; switching a burned track needs a fresh transcode */
function fillPlayerSubs() {
  const sel = $("#pl-subs");
  sel.innerHTML = "";
  if (!S || !S.meta) return;
  const list = subOptions(S.meta, S.mi);
  sel.add(new Option("Off", ""));
  list.forEach((t) => sel.add(new Option(subLabel(t, S.casting), String(t.id))));
  sel.value = S.subId ? String(S.subId) : "";
  sel.onchange = async () => {
    if (sel.value === "making") return;   // a subtitle still being written
    const id = sel.value ? +sel.value : 0;
    const track = list.filter((t) => t.id === id)[0] || null;
    // Swapping in a file is free - unless the picture already has subtitles painted
    // into it. ffmpeg burned those in when the stream started, and no change here can
    // wash them off, so both would be drawn at once: that one needs a fresh stream.
    if (track && track.external && !S.casting && !S.burned) {
      S.subId = id;
      rememberPick(track);
      attachSidecar(track);
      // A file just chosen - or just downloaded, which selects it the same way - has
      // this viewer's own correction for it or none at all. loadSubShift reads that,
      // and puts it in step against the film when nobody has and auto is on.
      await loadSubShift(S.meta, track);
      return;
    }
    rememberPick(track);
    const meta = S.meta, at = Math.floor(S.pos || S.start);
    stop(true);
    play(meta, at, { subId: id });
  };
}

function playerBar(m, resume) {
  const versions = m.Media || [];
  const on = preferredCopy(m);
  const subs = subOptions(m, on);
  const verSel = versions.length > 1
    ? '<select id="copysel" class="verpick" title="' +
        esc(versionLabel(versions[on] || versions[0])) + '">' + versions.map((md, i) =>
        '<option value="' + i + '"' + (i === on ? " selected" : "") + '>' +
        esc(versionLabel(md)) + '</option>').join('') + '</select>'
    // one copy is not a choice: say which file it is and leave it at that
    : (versions.length === 1 && versionFile(versions[0])
        ? '<span class="verone" title="' + esc(versionFile(versions[0])) + '">' +
          esc(versionFile(versions[0])) + '</span>'
        : '');
  const pick = preferredSub(m, on);
  return '<div class="actions">' +
    // one action; the alternative lives behind the caret
    (resume
      ? '<span class="split"><span class="splitrow"><button class="btn" data-off="' + resume +
        '">Resume ' + clock(resume) + '</button>' +
        '<button class="btn caret" id="pmenu-toggle" title="More">\u25be</button></span>' +
        '<span class="menu hidden" id="pmenu">' +
        '<button data-off="0">Play from start</button></span></span>'
      : '<button class="btn" data-off="0">Play</button>') +
    // the same rungs the settings page offers, so a limit set there lines up with
    // what can be asked for here
    '<select id="q"><option value="0">Original quality</option>' +
    '<option value="20000">20 Mbps</option><option value="12000">12 Mbps</option>' +
    '<option value="8000">8 Mbps</option><option value="5000">5 Mbps</option>' +
    '<option value="4000">4 Mbps</option><option value="2000">2 Mbps</option></select>' +
    '<span id="watchedbtn"></span>' +
    "</div>" +
    // One row under the buttons for the two things that decide what actually plays:
    // which copy, and what to read it with. They were a row each, which left the
    // subtitles stranded at the foot of the page under everything else - and they
    // are the same kind of decision, made at the same moment.
    (verSel ? '<div class="actions verline">' +
              '<span class="rowlbl">File</span>' + verSel + "</div>" : "") +
    // a row of its own, directly under the file it belongs to
    '<div class="actions verline">' +
    '<span class="rowlbl">Subtitles</span>' +
    // the list of subtitles to fetch, and writing one from the sound, live at the
    // foot of this menu: it is the menu somebody opens when the words are missing
    '<select id="subpick"><option value="">Off</option>' +
    subs.map((s) => '<option value="' + s.id + '"' +
      (pick && pick.id === s.id ? " selected" : "") + ">" +
      esc(subLabel(s, !!castSession())) + "</option>").join("") +
    "</select>" +
    '<option value="get">Download…</option>' +
    (subs.length ? "" : '<span class="note">none in this file</span>') +
    "</div>";
}

/* A download's time left, the way a person says it. */
function etaWords(s) {
  s = Math.max(0, Math.round(s));
  return s < 60 ? s + " s left" : s < 3600 ? Math.round(s / 60) + " min left"
    : Math.floor(s / 3600) + " h " + Math.round((s % 3600) / 60) + " min left";
}

/* What a film on offer says across its poster: how far its download has got. */
/* a download's time left, short enough for a poster's corner */
function etaShort(s) {
  s = Math.max(0, Math.round(s));
  return s < 60 ? s + " s" : s < 3600 ? Math.round(s / 60) + " min"
    : Math.floor(s / 3600) + " h " + Math.round((s % 3600) / 60) + " min";
}

function offerWord(it) {
  const o = it.offer || {};
  return o.state === "downloading"
    ? Math.round((o.progress || 0) * 100) + "%" +
      (o.eta != null && o.eta >= 0 ? " \u00b7 " + etaShort(o.eta) : "")
    : o.state === "done" ? "Arriving" : o.state === "queued" ? "Queued"
    : o.state === "failed" ? "Failed" : "\u2913";
}

/* A film on offer from a torrent pack: its page, and the one thing to do about it. */
async function viewOffer(m) {
  main.innerHTML = "";
  const o = m.offer || {};
  const wrap = document.createElement("div");
  wrap.className = "detail offer";
  setBackdrop(m.thumb ? img(m.thumb, 800, 1200) : null);
  wrap.innerHTML =
    '<div class="poster"><span class="ph"></span>' +
    (m.thumb ? '<img src="' + img(m.thumb, 400, 600) + '" alt="" onerror="this.remove()">' : "") +
    '<div class="offerbadge" id="offerpct" style="display:none"></div>' +
    "</div>" +
    '<div class="meta"><h1>' + esc(m.title) + "</h1>" +
    '<div class="sub">' + [m.year, (m.genres || []).join(", "),
      o.size ? (o.size / 1e9).toFixed(1) + " GB" : "",
      o.free != null ? o.free + " GB free on the download drive" : ""].filter(Boolean).map(esc)
      .join(" &middot; ") + "</div>" +
    '<div class="actions"><button class="btn" id="offerget"></button>' +
    '<button class="btn ghost" id="offercancel" style="display:none">Cancel download</button>' +
    '<span class="note" id="offersaid"></span></div>' +
    '<p class="summary">' + esc(m.summary) + "</p>" +
    '<div class="note">Not in the library yet. Download fetches this film alone from its ' +
    "pack, and it appears in Films when it has arrived.</div></div>";
  main.appendChild(wrap);
  // one poster for a film its pack carries more than once: the release is chosen here,
  // and Download fetches the one chosen
  if ((o.versions || []).length > 1) {
    const row = document.createElement("div");
    row.className = "versions";
    row.innerHTML = '<span class="note">Version</span>';
    o.versions.forEach((v) => {
      const pick = document.createElement("button");
      pick.className = "btn ghost tiny" + (v.key === m.ratingKey ? " on" : "");
      pick.dataset.key = v.key;
      pick.textContent = [v.label, (v.size / 1e9).toFixed(1) + " GB",
        v.state === "downloading" ? Math.round((v.progress || 0) * 100) + "%"
          : v.state === "queued" ? "queued" : v.state === "done" ? "downloaded" : ""]
        .filter(Boolean).join(" \u00b7 ");
      pick.onclick = async () => {
        if (v.key === m.ratingKey) return;
        const other = items(await api("/library/metadata/" + v.key))[0];
        if (other && other.offered) viewOffer(other);
      };
      row.appendChild(pick);
    });
    wrap.querySelector(".actions").before(row);
  }
  const b = wrap.querySelector("#offerget");
  const said = wrap.querySelector("#offersaid");
  const pct = wrap.querySelector("#offerpct");
  const stop = wrap.querySelector("#offercancel");
  const draw = (state, progress, why, mbit, eta) => {
    const coming = state === "queued" || state === "downloading";
    // across the poster too: how far, and how long is left
    pct.style.display = coming ? "" : "none";
    pct.textContent = state === "queued" ? "Queued" : Math.round((progress || 0) * 100) + "%" +
      (eta != null && eta >= 0 ? " \u00b7 " + etaShort(eta) : "");
    stop.style.display = coming ? "" : "none";
    b.disabled = !!state && state !== "failed" && state !== "cancelled";
    b.textContent = state === "downloading"
      ? "Downloading " + Math.round((progress || 0) * 100) + "%" +
        (mbit > 0 ? "  \u00b7  " + mbit.toFixed(1) + " Mbit/s" : "") +
        (eta != null && eta >= 0 ? "  \u00b7  " + etaWords(eta) : "")
      : state === "queued" ? "Queued" + (o.place ? " \u00b7 " + o.place + " ahead" : "")
      : state === "done" ? "Downloaded - arriving"
      : "\u2913 Download";
    said.textContent = why || (o.who && coming ? "Asked for by " + o.who : "");
  };
  draw(o.state, o.progress, o.free != null && !o.state && o.size / 1e9 > o.free
    ? "Not enough room on the download drive for this film" : "");
  if (o.refused) {
    b.disabled = true;
    b.textContent = "Cannot download";
    said.textContent = o.refused;
    return;
  }
  // while it comes in: read again every few seconds, until it is done or the page is left
  const follow = async () => {
    if (!document.body.contains(wrap)) return;
    let now = null;
    try {
      now = items(await api("/library/metadata/" + m.ratingKey))[0];
    } catch (e) {
      now = null;
    }
    // come in: the film's own page, in place of the offer
    if (now && !now.offered && now.ratingKey) {
      viewMovie(String(now.ratingKey));
      return;
    }
    const off = (now && now.offer) || null;
    if (off) {
      o.place = off.place || 0;
      draw(off.state, off.progress, "", off.mbit, off.eta);
      if (off.state !== "queued" && off.state !== "downloading" && off.state !== "done") return;
    }
    setTimeout(follow, 5000);
  };
  if (o.state === "queued" || o.state === "downloading" || o.state === "done") {
    draw(o.state, o.progress, "", o.mbit, o.eta);
    setTimeout(follow, 5000);
  }
  stop.onclick = async () => {
    stop.disabled = true;
    let r = {};
    try {
      r = await (await fetch("/torrents/cancel", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: m.ratingKey }) })).json();
    } catch (e) {
      r = { ok: false, why: "The server did not answer" };
    }
    stop.disabled = false;
    if (r.ok) {
      draw("cancelled", 0, "Download cancelled");
      toast("Cancelled " + m.title);
      drawDownloadBanner();
    } else {
      said.textContent = r.why || "Could not cancel it";
    }
  };
  b.onclick = async () => {
    b.disabled = true;
    let r = {};
    try {
      r = await (await fetch("/torrents/get", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: m.ratingKey }) })).json();
    } catch (e) {
      r = { ok: false, why: "The server did not answer" };
    }
    o.place = r.place || 0;
    draw(r.ok ? (r.state || "queued") : "failed", r.progress, r.ok ? "" : r.why);
    if (r.ok) {
      toast((r.state === "queued" ? "Queued " : "Downloading ") + m.title);
      drawDownloadBanner();
      setTimeout(follow, 3000);
    }
  };
}

/* Something downloading: a line beside the tabs, on every page, saying what, how far and
   how long. The owner sees everyone's, a guest their own. */
async function drawDownloadBanner() {
  let rows = [];
  try {
    const got = await (await fetch("/torrents/active")).json();
    rows = (got.MediaContainer || got).Metadata || [];
  } catch (e) {
    rows = [];
  }
  let el = $("#dlbanner");
  if (!rows.length) {
    if (el) el.hidden = true;
    return;
  }
  if (!el) {
    el = document.createElement("button");
    el.id = "dlbanner";
    el.className = "dlbanner";
    $("#topbar nav").after(el);
  }
  const words = (x) => {
    const o = x.offer || {};
    return x.title + " " + (o.state === "queued"
      ? "queued" + (o.place ? " \u00b7 " + o.place + " ahead" : "")
      : Math.round((o.progress || 0) * 100) + "%" +
        (o.mbit > 0 ? " \u00b7 " + o.mbit.toFixed(1) + " Mbit/s" : "") +
        (o.eta != null && o.eta >= 0 ? " \u00b7 " + etaShort(o.eta) : ""));
  };
  el.hidden = false;
  el.textContent = "\u2913 " + words(rows[0]) + (rows.length > 1 ? "  +" + (rows.length - 1) : "");
  el.title = rows.map(words).join("\n");
  el.onclick = () => open(rows[0]);
}
setTimeout(drawDownloadBanner, 1500);
/* A second while anything is arriving, so the percentage and the rate move as
   they happen; ten when nothing is, because an idle library has nothing to say
   and the answer is read from memory either way. */
let bannerSoon = 0;
const bannerAgain = () => {
  const busy = !!document.querySelector("#dlbanner");
  clearTimeout(bannerSoon);
  bannerSoon = setTimeout(async () => {
    await drawDownloadBanner();
    bannerAgain();
  }, busy ? 1000 : 10000);
};
bannerAgain();

async function viewMovie(key) {
  onResize = null;
  main.innerHTML = '<div class="empty">Loading&hellip;</div>';
  const m = items(await api("/library/metadata/" + key))[0];
  if (m && m.offered) return viewOffer(m);
  await refreshMarks();
  // filled in after the page is built, from the item the page was built from
  setTimeout(() => {
    // and which of the qualities on offer this server will actually send
    if (CAPS) markCapped(); else loadCaps();
    const slot = $("#watchedbtn");
    if (slot) {
      // marking it watched clears the resume point, and Resume is a button on this
      // same row: draw the page again rather than leaving it offering a time that
      // is no longer there
      slot.appendChild(watchedButton(m, () => viewMovie(m.ratingKey)));
      slot.appendChild(markControl(m));
      // the library's own idea of what this is, when it has it wrong
      if (!CFG.guest && isLocal()) {
        slot.appendChild(fixMatchButton(m, () => viewMovie(m.ratingKey)));
      }
    }
  }, 0);
  const md = m.Media && m.Media[0];
  const specs = md
    ? [md.videoResolution === "4k" ? "4K" : (md.videoResolution || "") + "p",
       (md.videoCodec || "").toUpperCase(), (md.audioCodec || "").toUpperCase(),
       Math.round((md.bitrate || 0) / 100) / 10 + " Mbps"].join(" ")
    : "";
  const resume = m.viewOffset ? Math.floor(m.viewOffset / 1000) : 0;
  main.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "detail";
  setBackdrop(m.thumb ? img(m.thumb, 800, 1200) : null);
  wrap.innerHTML =
    '<div class="poster"><span class="ph"></span>' +
    (m.thumb ? '<img src="' + img(m.thumb, 400, 600) + '" alt="" onerror="this.remove()">' : "") +
    "</div>" +
    '<div class="meta"><h1>' + esc(m.title) + "</h1>" +
    '<div class="sub">' + [m.year, mins(m.duration), m.contentRating, specs].filter(Boolean).map(esc).join(" &middot; ") + "</div>" +
    playerBar(m, resume) +
    (md && md.videoCodec === "hevc"
      ? '<div class="warn">HEVC source - the transcoder re-encodes this one in software.</div>' : "") +
    '<p class="summary">' + esc(m.summary) + "</p></div>";
  wrap.querySelectorAll("[data-off]").forEach((b) => {
    b.onclick = () => play(m, +b.dataset.off, opts());
  });
  // The subtitle list belongs to the cache, not to the film: two files hold different
  // tracks under different numbers, so choosing the 4K copy and leaving the list
  // alone played it with a stream id from the other one - the wrong track, or none.
  const verPick = wrap.querySelector("#copysel");
  if (verPick) {
    verPick.onchange = () => {
      const mi = +verPick.value;
      rememberCopy(m, mi);
      const sel = wrap.querySelector("#subpick");
      const list = subOptions(m, mi), want = preferredSub(m, mi);
      sel.innerHTML = '<option value="">Off</option>';
      list.forEach((t) => {
        const o = new Option(subLabel(t, !!castSession()), String(t.id));
        sel.add(o);
      });
      sel.value = want ? String(want.id) : "";
    };
  }
  main.appendChild(wrap);
  // "Get subtitles" at the foot of the subtitle menu opens the same list the player
  // has: what can be fetched, and writing one from the sound of this film
  const subsel = $("#subpick");
  if (subsel) {
    const wasSub = subsel.value;
    subsel.addEventListener("change", (e) => {
      if (subsel.value !== "get") return;
      subsel.value = wasSub;
      e.stopPropagation();
      const old = document.getElementById("subpanel");
      if (old) { old.remove(); return; }
      const box = document.createElement("div");
      box.id = "subpanel";
      box.className = "subpanel onpage";
      const head = document.createElement("div");
      head.className = "subhead";
      head.innerHTML = "<h4>Subtitles for this " +
        (m.type === "episode" ? "episode" : "title") + "</h4>";
      box.appendChild(head);
      const shut = document.createElement("button");
      shut.className = "btn ghost";
      shut.textContent = "Close";
      shut.onclick = () => box.remove();
      subsel.parentNode.appendChild(box);
      // the list draws itself, and knows how to write one from the sound
      downloadSubtitles(box, () => viewMovie(m.ratingKey), "", m);
      box.appendChild(shut);
    });
  }
  const caret = $("#pmenu-toggle");           // reveals "Play from start"
  if (caret) {
    caret.onclick = (e) => {
      e.stopPropagation();
      $("#pmenu").classList.toggle("hidden");
    };
    document.addEventListener("click", () => {
      const menu = $("#pmenu");
      if (menu) menu.classList.add("hidden");
    });
  }
}

function opts() {
  const ver = $("#copysel");
  const q = $("#q"), s = $("#subpick");
  const chosen = s && s.value && s.value !== "making" ? +s.value : 0;
  return { maxBitrate: q ? +q.value : 0, subId: Number.isFinite(chosen) ? chosen : 0,
           mediaIndex: ver ? +ver.value : 0,
           // whichever soundtrack was chosen for the film in hand
           atrack: (S && S.atrack !== undefined) ? S.atrack : undefined };
}

/* The programme whose seasons are on screen, so a season's marks can say when the
   whole thing is already marked. */
let showNow = null;

async function viewShow(key, wantSeason, wantEpisode) {
  onResize = null;
  main.innerHTML = '<div class="empty">Loading&hellip;</div>';
  await refreshMarks();
  const [meta, kids] = await Promise.all([
    api("/library/metadata/" + key),
    api("/library/metadata/" + key + "/children"),
  ]);
  const s = items(meta)[0];
  const seasons = items(kids).filter((x) => x.type === "season");
  main.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "detail";
  setBackdrop(s.thumb ? img(s.thumb, 800, 1200) : null);
  wrap.innerHTML =
    '<div class="poster"><span class="ph"></span>' +
    (s.thumb ? '<img src="' + img(s.thumb, 400, 600) + '" alt="" onerror="this.remove()">' : "") +
    "</div>" +
    '<div class="meta"><h1>' + esc(s.title) + "</h1>" +
    '<div class="sub">' + [s.year, (s.childCount || seasons.length) + " seasons",
      s.leafCount ? s.leafCount + " episodes" : ""].filter(Boolean).map(esc).join(" &middot; ") + "</div>" +
    '<p class="summary">' + esc(s.summary) + "</p>" +
    '<div class="actions"><span id="showmark"></span>' +
    '<select id="season"></select>' +
    '<span id="seasonbar"></span></div>' +
    '<div class="eplist" id="eplist"></div></div>';
  main.appendChild(wrap);
  showNow = s;
  // the whole programme, which is what somebody usually means by "later"
  const mark = $("#showmark");
  if (mark) {
    mark.appendChild(markButtons(s, "Whole programme", () => {
      // the seasons inherit from it, so they are drawn again rather than left
      // saying the opposite of what is true
      const sel = $("#season");
      if (sel && sel.value) loadEpisodes(sel.value);
    }));
    if (!CFG.guest && isLocal()) {
      mark.appendChild(fixMatchButton(s, () => viewShow(key)));
    }
  }
  const sel = $("#season");
  seasons.forEach((se) => sel.add(new Option(se.title, se.ratingKey)));
  sel.onchange = () => loadEpisodes(sel.value);
  // the season asked for - the one an episode was just watched from - or the first
  const want = seasons.some((se) => se.ratingKey === wantSeason)
    ? wantSeason : (seasons[0] || {}).ratingKey;
  if (want) { sel.value = want; loadEpisodes(want, wantEpisode); }
}

async function loadEpisodes(seasonKey, findKey) {
  const list = $("#eplist");
  const bar = $("#seasonbar");
  if (bar) {
    bar.innerHTML = "";
    // the season's own marks, next to the season it is about; the programme's are up
    // by the title. Lit when the whole programme is marked, since the season is then
    // in the shuffle whatever this button says.
    bar.appendChild(markButtons({ ratingKey: seasonKey, type: "season" },
                                "This season", () => loadEpisodes(seasonKey)));
    // an episode changing underneath alters "all/some/none" up here
    window.redrawSeasonMarks = () => {
      const at = $("#season");
      const showBox = $("#showmark");
      if (showBox && showNow) {
        showBox.innerHTML = "";
        showBox.appendChild(markButtons(showNow, "Whole programme", () => {
          if (at && at.value) loadEpisodes(at.value);
        }));
      }
      if (bar.firstChild && at && at.value === seasonKey) {
        bar.replaceChild(markButtons({ ratingKey: seasonKey, type: "season" },
                                     "This season", () => loadEpisodes(seasonKey)),
                         bar.firstChild);
      }
    };
    // one button for the whole season, which is how anybody catalogues a series
    ["Mark season watched", "Mark season unwatched"].forEach((label, i) => {
      const b = document.createElement("button");
      b.className = "btn ghost";
      b.textContent = label;
      b.onclick = async () => {
        // a whole season in one press, and no way back to the places it forgets
        const many = (list.querySelectorAll(".ep") || []).length;
        if (!confirm(label + "?" + String.fromCharCode(10, 10) + (many ? many + " episodes" : "Every episode") +
                     (i === 0 ? " will be marked as seen."
                              : " will be marked unseen, and where each was left is " +
                                "forgotten."))) return;
        await fetch(url("/:/" + (i === 0 ? "scrobble" : "unscrobble"),
                        { key: seasonKey }));
        loadEpisodes(seasonKey);
      };
      bar.appendChild(b);
    });
  }
  list.innerHTML = '<div class="empty">Loading&hellip;</div>';
  const c = await api("/library/metadata/" + seasonKey + "/children");
  list.innerHTML = "";
  items(c).forEach((ep) => {
    const row = document.createElement("div");
    row.className = "ep";
    const off = ep.viewOffset ? Math.floor(ep.viewOffset / 1000) : 0;
    row.innerHTML =
      '<div class="n">' + ep.index + "</div>" +
      (ep.thumb ? '<img loading="lazy" src="' + img(ep.thumb, 300, 169) +
        '" alt="" onerror="this.remove()">' : '<div class="epph"></div>') +
      '<div class="info"><div class="t">' +
        // the machine that keeps copies holds this one: the same green dot the
        // posters wear, so a season says how much of it survives the server
        (isCopied(ep) ? '<i class="epcopy" title="Also on ' +
                        esc(standbyName || "the other server") + '"></i>'
                      : "") +
        esc(ep.title) + "</div>" +
      '<div class="d">' + esc(ep.summary) + "</div>" +
      '<div class="s">' + [mins(ep.duration), off ? "resume " + clock(off) : ""].filter(Boolean).join(" &middot; ") +
      "</div></div>";
    if (isWatched(ep)) row.classList.add("seen-row");
    // a tick of its own, which must not also start the episode
    const tick = document.createElement("button");
    const paint = () => {
      tick.className = "eptick" + (isWatched(ep) ? " on" : "");
      tick.innerHTML = "&#10003;";
      tick.title = isWatched(ep) ? "Watched - press to unmark" : "Mark as watched";
      row.classList.toggle("seen-row", isWatched(ep));
    };
    tick.onclick = async (e) => {
      e.stopPropagation();
      const now = !isWatched(ep);
      await setWatched(ep, now);
      paint();
    };
    paint();
    row.appendChild(tick);
    // Marks of its own: sometimes it is one episode that is being saved, not the
    // programme. Like the tick, they must not also start playing the thing - and
    // each shows what is true, which includes what it inherits: an episode of a
    // marked season, or of a marked programme, is already in that shelf.
    [["star", isMarked, setMarked, "\u2606", "\u2605", "your watchlist"]]
      .forEach(([kind, is, set, off, on, where]) => {
        const b = document.createElement("button");
        const shine = () => {
          b.className = "eptick " + kind + (is(ep) ? " on" : "");
          b.innerHTML = is(ep) ? on : off;
          b.title = is(ep) ? "In " + where + " - press to take it off"
                           : "Add this episode to " + where;
        };
        b.onclick = async (e) => {
          e.stopPropagation();
          await set(ep, !is(ep));
          shine();
          // the season and the programme are counts of these, so they change too
          if (window.redrawSeasonMarks) redrawSeasonMarks();
        };
        shine();
        row.appendChild(b);
      });
    // right-click for collections
    row.oncontextmenu = (e) => {
      e.preventDefault();
      pickCollection(ep, row);
    };
    row.onclick = () => play(ep, off, {});
    list.appendChild(row);
    // the episode just watched: put it in the middle of the screen and mark it, so a
    // long season does not have to be scrolled through to find where you were
    if (findKey && String(ep.ratingKey) === String(findKey)) {
      row.classList.add("here");
      requestAnimationFrame(() =>
        row.scrollIntoView({ block: "center", behavior: "smooth" }));
    }
  });
}

/** A button that marks the thing watched or not, and redraws whatever it is on. */
function watchedButton(item, after) {
  const b = document.createElement("button");
  const draw = () => {
    b.className = "btn ghost seenbtn" + (isWatched(item) ? " on" : "");
    b.innerHTML = (isWatched(item) ? "&#10003; Watched" : "Mark watched");
  };
  b.onclick = async () => {
    await setWatched(item, !isWatched(item));
    draw();
    if (after) after();
  };
  draw();
  return b;
}

function open(it, push) {
  // choosing a title by hand is the opposite of putting something on
  casualOn = false;
  shuffleOn = "";
  // An episode goes on the stack as the page it belongs to, not as itself. open()
  // sends an episode straight into play, so replaying the entry started the film
  // again at whatever offset it had when the entry was made - back, and it is
  // playing from ten minutes ago. Every other kind re-reads from its key.
  if (push !== false) {
    // the list this was opened from, and where in it
    returnTo = { key: String(it.ratingKey || ""), scroll: window.scrollY };
    pushView(it.type === "episode"
      ? () => viewShow(it.grandparentRatingKey, it.parentRatingKey, it.ratingKey)
      : () => open(it, false));
  }
  // from here down - details, playback, subtitles, progress - everything belongs to
  // whichever server this item came from
  CTX = srvOf(it);
  $("#back").classList.add("on");
  if (it.type === "movie") viewMovie(it.ratingKey);
  else if (it.type === "show") viewShow(it.ratingKey);
  // a season card opens its programme at that season, not at the first one: both
  // shelves are full of them now, and "Season 6" that opens on Season 3 is a fault
  else if (it.type === "season") {
    viewShow(it.parentRatingKey || it.grandparentRatingKey, it.ratingKey);
  }
  else if (it.type === "episode") play(it, it.viewOffset ? Math.floor(it.viewOffset / 1000) : 0, {});
}

/**
 * Open a title by its key alone, from a list that holds nothing else.
 *
 * The watch log knows what was watched and when, and a key. A film opens on its own
 * page; an episode opens its programme at the right season with that episode found,
 * which is where somebody wants to be taken - not straight back into playing it.
 */
window.openKey = async function (key) {
  if (!key) return;
  let it;
  try {
    it = items(await api("/library/metadata/" + key))[0];
  } catch (e) { /* deleted since, or another server's */ }
  if (!it) { toast("That title is not in the library any more"); return; }
  CTX = srvOf(it);
  $("#back").classList.add("on");
  if (it.type === "episode") {
    viewShow(it.grandparentRatingKey, it.parentRatingKey, it.ratingKey);
  } else if (it.type === "season") {
    viewShow(it.parentRatingKey || it.grandparentRatingKey, it.ratingKey);
  } else if (it.type === "show") {
    viewShow(it.ratingKey);
  } else {
    viewMovie(it.ratingKey);
  }
};

function go(view, keep) {
  inSearch = false;
  collSearchOn = "";
  // Settings reached from a title's page is a detour, not a destination: the way back
  // to the film is what somebody wants next, and wiping the stack took it away.
  if (keep && navStack.length) {
    const here = navStack[navStack.length - 1];
    navStack = [here];
  } else {
    navStack = [];
    $("#back").classList.remove("on");
  }
  $("#search").value = "";
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.view === view));
  if (view === "settings") {
    // the settings screen lives in its own file; a page that was open while that file
    // changed can be left without it
    if (window.viewSettings) {
      if (keep) {
        pushView(() => viewSettings());
        $("#back").classList.add("on");
      }
      viewSettings();
    } else {
      toast("Settings did not load - reload the page");
    }
    return;
  }
  if (view === "reports") {
    // Kept as a destination even though the tab has gone from the bar: a link in a
    // notice opens it, and so does the count beside the gear. It draws the settings
    // page on its Reports tab, which is where it lives now.
    if (window.viewReports) {
      if (keep) {
        pushView(() => viewReports());
        $("#back").classList.add("on");
      }
      viewReports();
    } else {
      toast("Reports did not load - reload the page");
    }
    return;
  }
  if (view === "home") viewHome();
  else if (view === "movies") viewSection("movie");
  else if (view === "watchlist") { collectionOn = ""; viewWatchlist(); }
  else if (view === "collections") viewCollections();
  else viewSection("show");
}

/**
 * The genre control: what the library actually holds, several at once.
 *
 * Built from the shelves rather than a fixed list, so it never offers a genre with
 * nothing on it. Marked genres narrow to titles carrying all of them.
 */
function genreBar(type, rerender, matching) {
  const options = genresFor(type, CTX).then((list) =>
    list.map((g) => [g.title, g.title + " (" + g.count + ")"]));
  return genreMenu(genreWanted[type] || "", options, (v) => {
    genreWanted[type] = v;
    (rerender || (() => viewSection(type)))();
  }, matching);
}

/**
 * A button naming the marked genres, over a list of checkboxes with Clear at the top.
 * chosen is comma-joined, options a promise of [value, label]. The menu stays open
 * while marking; the shelf is redrawn once, when it closes with something changed.
 */
function genreMenu(chosen, options, apply, matching) {
  const wrap = document.createElement("span");
  wrap.className = "sortbar genrebar genremulti";
  wrap.innerHTML = "<span class='lbl'>Genre:</span>";
  const marked = String(chosen || "").split(",").map((g) => g.trim()).filter(Boolean);
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "genrepick";
  const shown = marked.length ? marked.join(" + ") : "All";
  btn.textContent = shown + (marked.length > 1 && matching >= 0 ? " \u00b7 " + matching : "") +
    " \u25be";
  const menu = document.createElement("div");
  menu.className = "genremenu hidden";
  const outside = (e) => { if (!wrap.contains(e.target)) close(); };
  const open = () => {
    menu.classList.remove("hidden");
    document.addEventListener("mousedown", outside, true);
  };
  const close = () => {
    if (menu.classList.contains("hidden")) return;
    menu.classList.add("hidden");
    document.removeEventListener("mousedown", outside, true);
  };
  // every press redraws the shelf, which builds this control again: it reopens at the
  // same scroll, so marking several reads as one menu that stayed open
  const press = () => {
    genreKeep = { scroll: menu.scrollTop };
    document.removeEventListener("mousedown", outside, true);
    apply(marked.join(","));
  };
  btn.onclick = () => (menu.classList.contains("hidden") ? open() : close());
  wrap.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "genreclear";
  clear.textContent = "Clear";
  clear.onclick = () => { marked.length = 0; press(); };
  menu.appendChild(clear);
  const kept = genreKeep;
  genreKeep = null;
  if (kept) open();
  options.then((pairs) => {
    // a marked genre the list no longer offers stays listed, so it can be unmarked
    marked.forEach((m) => {
      if (!pairs.some(([v]) => v.toLowerCase() === m.toLowerCase())) pairs.unshift([m, m]);
    });
    pairs.forEach(([v, t]) => {
      const row = document.createElement("label");
      row.className = "genreopt";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = marked.some((m) => m.toLowerCase() === v.toLowerCase());
      // marked shows on the name itself; the checkbox stays for the keyboard only
      row.classList.toggle("on", box.checked);
      box.onchange = () => {
        row.classList.toggle("on", box.checked);
        const at = marked.findIndex((m) => m.toLowerCase() === v.toLowerCase());
        if (box.checked && at < 0) marked.push(v);
        if (!box.checked && at >= 0) marked.splice(at, 1);
        press();
      };
      row.append(box, document.createTextNode(t));
      menu.appendChild(row);
    });
    if (kept) menu.scrollTop = kept.scroll;
  });
  wrap.append(btn, menu);
  return wrap;
}

/**
 * The decade control: what the library holds, newest first.
 *
 * Built from the shelves like the genre beside it, so it never offers a decade with
 * nothing in it, and it says how many are there before you choose.
 */
function decadeBar(type, rerender) {
  const wrap = document.createElement("span");
  wrap.className = "sortbar genrebar";
  wrap.innerHTML = "<span class='lbl'>Decade:</span>";
  const sel = document.createElement("select");
  sel.className = "genrepick";
  sel.add(new Option("All", ""));
  sel.onchange = () => {
    decadeWanted[type] = sel.value;
    (rerender || (() => viewSection(type)))();
  };
  wrap.appendChild(sel);
  decadesFor(type, CTX).then((list) => {
    list.forEach((d) => sel.add(
      new Option(d.title + " (" + d.count + ")", String(d.decade))));
    sel.value = decadeWanted[type] || "";
  });
  return wrap;
}

/* ---------------- playback ---------------- */

let S = null; // active playback session (controls.js reads window.S)
let stoppedAt = 0;               // when the last transcode session was told to stop

/* What the playback target can handle natively. Anything listed here is
   copied instead of re-encoded. */
function profileExtra(forCast) {
  const t = (CFG.targets && (forCast ? CFG.targets.cast : CFG.targets.pc)) || {};
  const extra = [];
  (t.audioPassthrough || []).forEach((c) => extra.push(
    "append-transcode-target-audio-codec(type=videoProfile&context=streaming&protocol=dash&audioCodec=" + c + ")"));
  return extra.join("+");
}

function trackSessions(id) {
  const k = "palladium-sessions";
  const list = JSON.parse(localStorage.getItem(k) || "[]");
  if (id && list.indexOf(id) < 0) list.push(id);
  localStorage.setItem(k, JSON.stringify(list.slice(-10)));
  return list;
}

function untrackSession(id) {
  const k = "palladium-sessions";
  const list = JSON.parse(localStorage.getItem(k) || "[]").filter((x) => x !== id);
  localStorage.setItem(k, JSON.stringify(list));
}

/* Kill any transcode session this browser leaked (crash, refresh, closed tab).
   Each live session is an ffmpeg process on the server. */
function reapStaleSessions() {
  trackSessions().forEach((id) => {
    fetch(url("/video/:/transcode/universal/stop", { session: id })).catch(() => {});
  });
  localStorage.setItem("palladium-sessions", "[]");
}

/* ---------------- direct play ----------------

   The cheapest stream is the one the server never touches: hand the original file
   straight to the player. That needs a container the player can demux itself, which
   rules out MKV in a browser however friendly canPlayType() sounds about it.
   Anything not covered here falls through to the HLS transcoder path below. */

const DP_VIDEO = { h264: 'avc1.640028', vp8: 'vp8', vp9: 'vp09.00.10.08', av1: 'av01.0.05M.08' };
const DP_AUDIO = { aac: 'mp4a.40.2', mp3: 'mp3', opus: 'opus', flac: 'flac', vorbis: 'vorbis' };
const DP_CAST_VIDEO = ["h264", "vp8", "vp9"];          // Chromecast gen 3: no HEVC, no AV1
const DP_CAST_AUDIO = ["aac", "mp3", "vorbis", "opus"];

function mime(container, v, a) {
  const box = container === "webm" ? "video/webm" : "video/mp4";
  return box + '; codecs="' + [DP_VIDEO[v], DP_AUDIO[a]].filter(Boolean).join(",") + '"';
}

/* the file's own URL, playable as-is, or null to use the transcoder */
function directPlay(meta, casting, o, mi) {
  if (o.maxBitrate) return null;                  // a bitrate cap means re-encode by definition
  // and so does a smaller picture or a number of megabits chosen in the player
  if (o.height || o.mbit) return null;
  // a soundtrack other than the file's first one has to be picked out by ffmpeg
  if (o.atrack !== undefined && o.atrack !== null) return null;
  const md = mediaOf(meta, mi);
  const part = md && md.Part && md.Part[0];
  if (!md || !part || !part.key) return null;
  const container = (md.container || "").toLowerCase();
  const v = (md.videoCodec || "").toLowerCase();
  const a = (md.audioCodec || "").toLowerCase();
  if (["mp4", "m4v", "webm"].indexOf(container) < 0) return null;
  if (!DP_VIDEO[v] || !DP_AUDIO[a]) return null;
  const type = mime(container, v, a);
  if (casting) {
    if (DP_CAST_VIDEO.indexOf(v) < 0 || DP_CAST_AUDIO.indexOf(a) < 0) return null;
  } else {
    // ask this browser, rather than trusting a codec table
    const el = document.createElement("video");
    if (!el.canPlayType(type)) return null;
  }
  // A television is fetching this, not the browser: it needs a whole address it can
  // reach and the invitation with it. Everything else can keep the relative one.
  const src = casting
    ? castOrigin() + url(part.key, myToken() ? { t: myToken() } : {}).replace(
        location.origin, "")
    : url(part.key);
  return { src: src, type: container === "webm" ? "video/webm" : "video/mp4",
           label: container + "/" + v + "/" + a };
}

function streamParams(ratingKey, offset, o, burn, forCast, mi) {
  const session = (crypto.randomUUID ? crypto.randomUUID()
    : "s" + Math.random().toString(36).slice(2) + Date.now());
  const params = Object.assign({
    path: "/library/metadata/" + ratingKey,
    mediaIndex: mi || 0, partIndex: 0, protocol: "dash",
    fastSeek: 1, directPlay: 0, directStream: 1, copyts: 1, hasMDE: 1,
    offset: offset || 0,
    // no cap on "Original": a bitrate ceiling below the source forces a
    // software re-encode of video that could otherwise be copied
    maxVideoBitrate: o.maxBitrate || 200000,
    videoQuality: 100,
    // burning a subtitle in forces a full video re-encode - opt in only
    subtitles: burn ? "burn" : "none",
    subtitleSize: 100, audioBoost: 100, location: "lan",
    session: session,
  });
  trackSessions(session);
  return { session: session, params: params };
}

/* A transcoder answers start.m3u8 with 400 ("session lacking decision") unless the
   session has been through the media decision engine first. */
async function decide(params) {
  try { return await api("/video/:/transcode/universal/decision", params); }
  catch (e) { return null; }
}

/* resolution / codec / fps of the source, shown next to the playback decision */
function mediaLine(meta, mi) {
  const md = mediaOf(meta, mi);
  if (!md) return "";
  const part = md.Part && md.Part[0];
  const vs = (part && part.Stream || []).filter((x) => x.streamType === 1)[0];
  let fps = (vs && vs.frameRate) || md.frameRate;      // Stream gives 23.976, Media gives "24p"
  if (typeof fps === "string") fps = fps.replace(/p$/, "").replace("NTSC", "29.97").replace("PAL", "25");
  const label = md.videoResolution === "4k" ? "4K" : (md.videoResolution || "") + "p";
  const px = md.width && md.height ? md.width + "×" + md.height : "";
  const bits = [px ? label + " (" + px + ")" : label, (md.videoCodec || "").toUpperCase()];
  if (fps && !isNaN(parseFloat(fps))) bits.push(parseFloat(fps).toFixed(3).replace(/\.?0+$/, "") + " fps");
  return bits.filter((b) => b && b !== "p").join(" · ");
}

function showDecision(c) {
  const el = $("#pl-decision");
  if (!c) { el.textContent = ""; return; }
  const part = (((items(c)[0] || {}).Media || [])[0] || {}).Part || [];
  const streams = (part[0] || {}).Stream || [];
  const v = (streams.filter((s) => s.streamType === 1)[0] || {}).decision;
  const a = (streams.filter((s) => s.streamType === 2)[0] || {}).decision;
  const info = S ? mediaLine(S.meta, S.mi) : "";
  // name the engine, so the line under the picture says what is doing the work
  const engine = v === "transcode" ? "transcoder, software CPU encode"
    : (a === "transcode" ? "transcoder, remux + audio encode" : "transcoder, remux only");
  el.textContent = (info ? info + "  —  " : "") + engine + "  —  " +
    "video " + (v || "?") + ", audio " + (a || "?");
  el.className = v === "transcode" ? "hot" : "";
}

/* The stream marked selected on the part is the one that plays, so pick it first */
async function selectSubtitle(meta, subId, mi) {
  const md = mediaOf(meta, mi);
  const part = md && md.Part && md.Part[0];
  if (!part) return;
  await fetch(url("/library/parts/" + part.id, {
    subtitleStreamID: subId || 0, allParts: 1,
  }), { method: "PUT" }).catch(() => {});
}

async function play(item, offset, o) {
  // Anything put on by hand ends the collection that was playing itself. Without
  // this, finishing an unrelated film half an hour later would start the next one
  // from a shelf nobody had asked for since.
  if (!queuePlaying) collectionQueue = null;
  queuePlaying = false;
  offset = offset || 0;
  // The engine is asked for a whole second and starts its clock exactly there, so
  // everything measured from the start of the encode has to use that same whole
  // second. Resuming asks for 208.7 and the encode begins at 208: reading the
  // subtitles against 208.7 put every line seven tenths early for the whole film,
  // which is the difference between a film played over the LAN and one re-encoded.
  const base = Math.floor(offset);
  o = o || {};
  const meta = item.Media ? item : items(await api("/library/metadata/" + item.ratingKey))[0];
  const casting = !!castSession();
  // which file on disk: what was asked for, or what this title was settled on
  const mi = o.mediaIndex === undefined ? preferredCopy(meta) : o.mediaIndex;

  // which subtitle track: an explicit pick, otherwise the default language
  const tracks = subOptions(meta, mi);
  let track = null;
  if (o.subId === undefined) track = preferredSub(meta, mi);
  else if (o.subId) track = tracks.filter((t) => t.id === o.subId)[0] || null;
  const sidecar = !!track && track.external && !casting;   // its own file: read directly
  // an embedded TEXT track can be pulled out of the container during the remux and
  // rendered here; only bitmap tracks (PGS/VobSub) must be burned into the picture
  const burn = !!track && !sidecar;
  // only written when burning: this is server-side state shared with every
  // a subtitle is chosen per play rather than set on the file, so nothing has to
  // be selected on the server first

  if (S) stop(true);
  // burning a subtitle needs the transcoder, so direct play is only on offer without one
  const dp = burn ? null : directPlay(meta, casting, o, mi);
  // our engine only makes sense for a real re-encode on this machine, never for
  // a direct play and never for the Chromecast, which fetches for itself
  // the Chromecast can use our engine too, as long as it can reach us: the
  // stream URL has to be the LAN address, not localhost
  // there is no separate transcoder behind the library, so anything that
  // cannot direct play must go through our own ffmpeg engine
  const useGpu = !dp && (isLocal() || engine() === "gpu") &&
                 (!casting || !!CFG.lan);
  let src, dec = null, session = null, params = null, gpuInfo = null;
  if (useGpu) {
    // a bitmap track has no text to extract, so the GPU burns it in
    const burnTrack = track && !isTextSub(track) && track.index !== undefined
      ? track.index : null;
    // a friend's film is encoded by their machine, so the stream comes from there
    const gpuBase = casting ? castOrigin() : (CTX ? CTX.origin : "");
    // A receiver gets the playlist, not the pipe: a Chromecast plays HLS natively,
    // can seek in it, and survives a moment of bad network. Safari gets it for the
    // same reasons plus one of its own. Everybody else keeps the single connection.
    src = gpuBase + (casting || isApple() || isTvBrowser() ? "/gpu/hls?"
                                                            : "/gpu/stream?") +
      new URLSearchParams({
      key: meta.ratingKey, offset: base, device: DEVICE,
      // a television holds no cookie, so the invitation travels in the address
      ...(CTX ? { t: CTX.token } : (casting && myToken() ? { t: myToken() } : {})),
      ...(isLocal() ? { src: "local" } : {}),
      height: o.height !== undefined && o.height !== null ? o.height : 0,
      // The megabits asked for on the film page mean megabits. They used to mean
      // "1080p and hope", which is not what the words on the control say.
      ...((o.mbit || o.maxBitrate)
          ? { mbit: o.mbit || Math.round(o.maxBitrate / 1000) } : {}),
      mi: mi,
      ...(o.atrack !== undefined && o.atrack !== null ? { atrack: o.atrack } : {}),
      ...(burnTrack !== null ? { burn: burnTrack } : {}),
      // and what to encode with, when it is not the card
      ...(engine() === "cpu" ? { engine: "cpu" } : {}),
    }).toString();
    gpuInfo = { engine: "NVENC", base: base, burn: burnTrack };
  } else if (dp) {
    src = dp.src;                       // the file itself - no session, no ffmpeg
  } else {
    // give the transcoder a moment to retire the session we just stopped, or its
    // replacement answers 404 for the first segments
    if (stoppedAt && Date.now() - stoppedAt < 500) {
      await new Promise((r) => setTimeout(r, 500 - (Date.now() - stoppedAt)));
    }
    const s = streamParams(meta.ratingKey, offset, o, burn, casting, mi);
    params = s.params;                  // needed later for the subtitle stream
    session = s.session;
    dec = await decide(s.params);
    src = url("/video/:/transcode/universal/start.mpd", s.params);
  }
  // nothing of the last film survives into this one, subtitles least of all
  clearSubtitles();
  await loadSubShift(meta, track);
  // both paths report absolute source time, so the playhead needs no origin added
  window.S = S = { meta: meta, session: session, start: 0,
        pos: useGpu ? base : offset,
        casting: casting, state: "playing", subId: track ? track.id : 0,
        direct: !!dp, seekTo: dp ? offset : 0, mi: mi,
        gpu: !!useGpu, timeBase: useGpu ? base : 0,
        atrack: o.atrack,
        // what the picture was asked for at, so the gear can show it and the next
        // stream can be started the same way
        height: o.height || 0, mbit: o.mbit || 0,
        // whether ffmpeg is drawing the subtitles into the picture: changing their
        // look then needs a new stream, not a new stylesheet
        burned: !!(useGpu && track && !isTextSub(track) && track.index !== undefined) };

  // something may be writing a subtitle for this film: say how far along it is, and
  // put it on when it arrives
  followTheMaking(meta);
  // this film's own subtitle look, if it has one
  loadSubtitleLook((isLocal() ? "l" : "p") + meta.ratingKey);
  $("#pl-title").textContent = meta.type === "episode"
    ? meta.grandparentTitle + " - S" + meta.parentIndex + "E" + meta.index + " - " + meta.title
    : meta.title;
  $("#pl-where").textContent = casting ? "Casting to " + castSession().getCastDevice().friendlyName : "";
  $("#player").classList.remove("hidden");
  document.body.classList.add("watching");   // the library's cast button steps aside
  applyFit();                      // remember the picture-size choice
  showStepping();                  // and whether stepping through makes sense here
  // the surround mix and the stereo downmix remember separate levels
  S.channels = (mediaOf(meta, mi) || {}).audioChannels ||
               (((mediaOf(meta, mi) || {}).Part || [{}])[0].Stream || [])
                 .filter((x) => x.streamType === 2).map((x) => x.channels)[0] || 2;
  if (window.ctlPrime) ctlPrime(offset, (meta.duration || 0) / 1000,
                                useGpu ? (offset || 0) : 0);
  loading = true;                  // controls stay up until the first frame plays

  if (casting) {
    $("#video").classList.add("hidden");
    $("#cast-ui").classList.remove("hidden");
    $("#cast-img").src = img(meta.type === "episode" ? meta.grandparentThumb : meta.thumb, 400, 600);
    await castLoad(src, meta, offset, dp);
  } else {
    $("#cast-ui").classList.add("hidden");
    $("#video").classList.remove("hidden");
    localLoad(src, dp || useGpu);      // both are plain files to the element
    if (sidecar) attachSidecar(track);
    // A file beside the video is already attached above. It is a text track too, and
    // asking ffmpeg for it as well drew every line twice, one copy over the other.
    if (useGpu && track && !track.external && isTextSub(track) &&
        track.index !== undefined) {
      // our own engine has no session behind it: pull the track with ffmpeg
      const subsUrl = (CTX ? CTX.origin : "") + "/gpu/subs?" + new URLSearchParams({
        key: meta.ratingKey, index: track.index,
        ...(CTX ? { t: CTX.token } : {}),
        ...(isLocal() ? { src: "local" } : {}),
        offset: base, mi: mi,
      }).toString();
      attachGpuSubtitles(subsUrl, track);
    }
  }
  startStats();
  showChrome(false, "start");
  if (window.ctlApplyVolume) ctlApplyVolume();
  startKeepalive();
  if (useGpu) {
    $("#pl-decision").textContent = mediaLine(meta, mi) +
      "  —  RTX 5070: " + (gpuInfo && gpuInfo.burn !== null && gpuInfo.burn !== undefined
        ? "NVDEC decode, subtitles burned on CPU, NVENC encode"
        : "NVDEC decode, NVENC encode") + ", 5.1 kept";
    $("#pl-decision").className = "good";
  } else if (dp) {
    const info = mediaLine(meta, mi);
    // which player has it, since on a television that is the whole question: AVPlay
    // decodes what the browser engine will not open
    const by = (S && S.tz) ? "direct play on the television's own decoder (AVPlay)"
                           : "direct play — no server processing";
    $("#pl-decision").textContent = (info ? info + "  —  " : "") + by;
    $("#pl-decision").className = "good";
  } else {
    showDecision(dec);
  }
  fillPlayerSubs();
  fillPlayerVersions();
  fillPlayerEngine();
  // give the player its own entry so back closes it before it walks the library
  if (!playerEntry) {
    playerEntry = true;
    window.history.pushState({ player: 1 }, "");
  }
}

/* ---------------- player chrome ----------------

   Chrome keeps its native control bar up for as long as the pointer rests over
   the video, so the bar is driven from our own idle timer instead: the controls
   attribute comes off when the timer runs out, wherever the mouse happens to be.
   Anything that looks like activity puts it back. */

const IDLE_MS = 3000;
let idleTimer = null;

/* ---- player diagnostics ----
   Every show/hide of the control bar, plus how the mouse is behaving, is posted to
   the local server so the real browser's behaviour can be read back from debug.log. */
const dbgBuf = [];
// every page gets its own tag: several open tabs all post here, and without it
// one tab's hide reads as another tab's bounce
const PAGE_ID = Math.random().toString(36).slice(2, 6);
let moveStats = { n: 0, maxDelta: 0, lastX: null, lastY: null };

function dbg(event, data) {
  dbgBuf.push(Object.assign({ page: PAGE_ID, t: Math.round(performance.now()),
                              event: event }, data || {}));
}

setInterval(() => {
  if (moveStats.n) {
    dbg("mousemove-rate", { count: moveStats.n, maxDelta: Math.round(moveStats.maxDelta) });
    moveStats.n = 0;
    moveStats.maxDelta = 0;
  }
  if (!dbgBuf.length) return;
  const body = dbgBuf.splice(0, dbgBuf.length).map((e) => JSON.stringify(e)).join(chr10());
  fetch("/log", { method: "POST", body: body }).catch(() => {});
}, 2000);
function chr10() { return String.fromCharCode(10); }

/* every event that could plausibly wake the bar, logged but not acted on */
["pointermove", "mouseover", "mouseout", "mouseenter", "mouseleave", "focus", "blur",
 "fullscreenchange", "webkitfullscreenchange", "seeking", "seeked", "waiting", "stalled",
 "ratechange", "volumechange"].forEach((name) =>
  $("#player").addEventListener(name, (e) => {
    dbg("evt", { type: e.type, target: (e.target && e.target.id) || (e.target && e.target.tagName) });
  }, { passive: true, capture: true }));

function showChrome(deliberate, why) {
  why = why || "?";
  dbg("show", { why: why, deliberate: !!deliberate,
                paused: $("#video").paused, ptr: [ptrX, ptrY], anchor: [anchorX, anchorY] });
  clearTimeout(idleTimer);
  $("#player").classList.remove("idle");
  if (S && !S.casting) idleTimer = setTimeout(hideChrome, IDLE_MS);
}

/* never yank the chrome away while it is being used: an open dropdown dies the
   moment its container becomes pointer-events:none */
function chromeInUse() {
  if (hoverChrome) return true;
  const a = document.activeElement;
  return !!(a && a.closest && a.closest("#player") && a.tagName === "SELECT");
}

function hideChrome(hard) {
  if (loading || chromeInUse()) {  // still loading, or you are using the controls
    clearTimeout(idleTimer);
    idleTimer = setTimeout(hideChrome, IDLE_MS);
    return;
  }
  dbg("hide", { hard: !!hard, paused: $("#video").paused, ptr: [ptrX, ptrY] });
  clearTimeout(idleTimer);
  if (!S || S.casting) return;
  if (!hard && $("#video").paused) {        // a paused film keeps its controls
    idleTimer = setTimeout(hideChrome, IDLE_MS);
    return;
  }
  $("#player").classList.add("idle");
  anchorX = ptrX;                  // re-anchor where the pointer is resting
  anchorY = ptrY;
}

/* The bar hides a few seconds after you stop using it and wakes on the smallest
   deliberate nudge. The only guards left are the two that were actually needed:
   a few pixels of slack so sensor noise from a resting mouse does not count, and
   re-anchoring when the pointer comes back from another monitor - returning to the
   window is not a gesture, and treating it as one is what made the bar bounce. */
const IDLE_MOVE_PX = 10;
let anchorX = null, anchorY = null, ptrX = null, ptrY = null;
let hoverChrome = false;         // pointer resting on the bars themselves

[".pl-top", "#ctl"].forEach((sel) => {
  const el = document.querySelector(sel);
  el.addEventListener("mouseenter", () => { hoverChrome = true; showChrome(true, "hover"); });
  el.addEventListener("mouseleave", () => { hoverChrome = false; });
});

["mouseenter", "mouseover"].forEach((name) =>
  $("#player").addEventListener(name, (e) => {
    if (e.relatedTarget) return;             // moving between elements inside the page
    anchorX = ptrX = e.clientX;
    anchorY = ptrY = e.clientY;
    dbg("reenter", { ptr: [ptrX, ptrY] });
  }, { passive: true, capture: true }));

function pointerActivity(e) {
  if (e.type === "mousemove") {
    ptrX = e.clientX;
    ptrY = e.clientY;
    if (anchorX === null) { anchorX = ptrX; anchorY = ptrY; return; }
    const dx = ptrX - anchorX, dy = ptrY - anchorY;
    if (Math.sqrt(dx * dx + dy * dy) < IDLE_MOVE_PX) return;
    anchorX = ptrX;
    anchorY = ptrY;
  }
  showChrome(true, e.type);
}

["mousemove", "mousedown", "wheel", "touchstart"].forEach((e) =>
  $("#player").addEventListener(e, pointerActivity, { passive: true }));

/* closing from the UI consumes the player's history entry too */
function closePlayer() {
  // the player is the element that went full screen, so leaving it full screen with
  // the library behind it shows a black rectangle nobody can navigate
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  stop();
  // straight back the way you came - the season, a search, or continue watching
  if (playerEntry) {
    playerEntry = false;
    suppressPop = true;
    window.history.back();
  }
  // And out onto the homepage, drawn again. Whatever was behind the player is a
  // page from before the film: Continue watching still says the place it was at when
  // it started, the shelf still says what it said when the draw was made, and a
  // shuffle that has moved on says nothing at all. Coming out of a film is the one
  // moment all of that has changed.
  go("home");
}

/** The season this episode belongs to, with the episode itself on screen. */
function showInPlace(was) {
  if (!was) return;
  closePlayer();
  CTX = srvOf(was);
  // this is the top of the walk, not a step into it: an arrow here would point at
  // the film that was just left
  $("#back").classList.remove("on");
  if (was.type === "episode" && was.grandparentRatingKey) {
    viewShow(was.grandparentRatingKey, was.parentRatingKey, was.ratingKey);
  } else {
    viewMovie(was.ratingKey);
  }
}

/* One dash.js instance for the life of the page. Destroying it and building a new
   one on every stream left the element attached to a dead MediaSource - dash.js just
   logged "No video bytes to push" forever - so the source is swapped instead. */
let dashPlayer = null;
let dashDetached = false;        // true once a plain file has taken the element

function dashLoad(url) {
  const v = $("#video");
  if (!dashPlayer || dashDetached) {
    dashPlayer = dashjs.MediaPlayer().create();
    dashPlayer.updateSettings({
      streaming: {
        // the init segment 404s for a moment while the previous session is
        // torn down; without generous retries dash.js gives up and the seek dies
        retryAttempts: { MPD: 6, InitializationSegment: 12, MediaSegment: 12 },
        retryIntervals: { MPD: 500, InitializationSegment: 500, MediaSegment: 500 },
        buffer: { bufferTimeAtTopQuality: 30,
                  bufferTimeAtTopQualityLongForm: 30,
                  bufferToKeep: 20 },
        text: { defaultEnabled: false },
      },
    });
    dashPlayer.on(dashjs.MediaPlayer.events.ERROR, (e) => {
      const msg = (e.error && (e.error.message || e.error.code)) || "dash";
      toast("Playback error: " + msg);
    });
    if (dashDetached) {              // a plain file had taken the element
      try { dashPlayer.reset(); } catch (e) {}
      dashPlayer = dashjs.MediaPlayer().create();
    }
    dashDetached = false;
    dashPlayer.initialize(v, url, true);
    return;
  }
  dashPlayer.attachSource(url);       // reuses the live MediaSource
  dashPlayer.play();
}

/**
 * Where the film has got to, in seconds, from whichever player holds it.
 *
 * A Samsung television plays through AVPlay, which is not a media element and has no
 * currentTime; everything else reads the <video>. One question, two answers, so the
 * rest of the client does not have to know which.
 */
function playAt() {
  if (S && S.tz && window.TZ && TZ.ready) return TZ.at();
  const v = $("#video");
  return v ? (v.currentTime || 0) : 0;
}
window.playAt = playAt;


function localLoad(src, plain) {
  clearSubtitles();                   // no leftovers, of either kind
  const v = $("#video");
  // A Samsung set decodes what its browser will not open. Direct play of an MKV is
  // the whole point: the alternative is asking the server to re-encode a file the
  // television could have played untouched.
  if (window.TZ && TZ.there && plain) {
    v.removeAttribute("src");
    v.classList.add("hidden");
    S.tz = true;
    TZ.ended = () => rollOn();
    if (TZ.open(src, (S.seekTo || 0) * 1000)) {
      startSubtitleDrawing();          // AVPlay has no cue layer; we draw them
      return;
    }
    // it would not take it: fall through to the element, which at least says so
    S.tz = false;
    v.classList.remove("hidden");
  }
  if (plain) {                        // a file, or our own GPU pipe: play it directly
    if (dashPlayer && !dashDetached) {   // hand the element over cleanly
      try { dashPlayer.reset(); } catch (e) {}
      dashDetached = true;
    }
    v.src = src;
    v.onloadedmetadata = () => { if (S && S.seekTo) v.currentTime = S.seekTo; };
    v.play().catch(() => {});
  } else {
    v.removeAttribute("src");
    dashLoad(src);
  }
  v.ontimeupdate = () => {
    if (!S) return;
    S.pos = (S.timeBase || 0) + v.currentTime;
    if (v.currentTime > 0) {
      loading = false;
    }
  };
  v.onpause = () => {
    if (S) S.state = "paused";
    setTimeout(() => { if (S && $("#video").paused) showChrome(); }, 300);
  };
  v.onplay = () => {
    if (S) S.state = "playing";
    comeBackTries = 0;                // it is running: start the count over
  };
  v.onended = () => rollOn();
  // the pipe ended in the middle, which is what a server being replaced looks like
  v.onerror = () => reopenStream("the stream ended");
}

let askedAhead = "";           // the episode we have already fetched ahead for

/**
 * Ask the server for the next episode's subtitle, five minutes from the end.
 *
 * Early enough that the file is in place before the next episode starts, late enough
 * that nothing is fetched for an episode nobody finishes. The server decides whether
 * there is anything to fetch: it holds the series' choice, and it answers at once if
 * the episode already has subtitles of its own.
 */
async function askAheadForSubtitles() {
  if (!S || !S.meta || S.meta.type !== "episode") return;
  if (askedAhead === S.meta.ratingKey) return;
  const dur = (S.meta.duration || 0) / 1000;
  if (!dur || dur - (S.pos || 0) > 300) return;
  askedAhead = S.meta.ratingKey;
  await fetchNextSubtitle(S.meta.ratingKey);
}

/**
 * Fetch whatever this series is watching with, for whatever comes after `key`.
 *
 * Which is the next episode ordinarily, and the shuffle's next draw when something is
 * being put on casually - the server settles that when the current one starts, so it
 * is a fact by the time this runs and not another roll of the dice. Five minutes of
 * warning is what makes an episode start with its subtitles already there, and casual
 * watching is exactly where nobody wants to be asked about subtitles.
 */
async function fetchNextSubtitle(key) {
  try {
    let next = null;
    if (casualOn && shuffleOn) {
      const d = await (await fetch("/collections/shuffle", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: shuffleOn, peek: true }),
      })).json();
      next = d && d.item ? d.item : null;
    } else {
      next = items(await api("/next", { key: key }))[0];
    }
    if (!next) return null;
    await fetch("/subs/auto", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: next.ratingKey, after: key }),
    });
    return next;
  } catch (e) {
    return null;                 // subtitles are a convenience, never a blocker
  }
}

/**
 * What happens when an episode finishes: the next one, after five seconds.
 *
 * A card names it and counts down, and any key or click calls the whole thing off -
 * the same bargain the television app offers. A film, or the last episode of a series,
 * simply closes as before.
 */
async function rollOn() {
  // a collection being played through comes first: it is a list somebody started,
  // and it outranks both the shuffle and the next episode of whatever is on
  if (collectionQueue) return playFromQueue();
  const was = S && S.meta;
  // something put on casually leads to the next draw, film or episode alike: that
  // is the difference between a shuffle and a list
  if (casualOn && shuffleOn) return shuffleDraw(shuffleOn, false);
  if (!was || was.type !== "episode") return closePlayer();
  let on = true;
  try {
    on = (await (await fetch("/settings")).json()).autoNext !== false;
  } catch (e) { /* the setting is a preference, not a requirement */ }
  if (!on) return closePlayer();
  let next = null;
  try {
    next = items(await api("/next", { key: was.ratingKey }))[0] || null;
  } catch (e) { next = null; }
  if (!next) return closePlayer();

  const card = document.createElement("div");
  card.id = "nextup";
  let left = 5;
  const draw = () => {
    card.innerHTML = "<div class='lbl'>Next episode in " + left + "</div>" +
      "<div class='t'>" + esc("S" + next.parentIndex + " E" + next.index + "  " +
                              next.title) + "</div>" +
      "<div class='row'><button class='btn' id='nx-go'>Play now</button>" +
      "<button class='btn ghost' id='nx-no'>Stop</button></div>";
    // looked up inside the card, not in the document: draw() runs before the card is
    // attached, so a document-wide search finds nothing and throws - which killed the
    // countdown before it could ever appear, once a second, silently
    card.querySelector("#nx-go").onclick = go;
    card.querySelector("#nx-no").onclick = off;
  };
  // in case the five-minute mark went by unseen - joined late, or skipped through
  const fetching = fetchNextSubtitle(was.ratingKey);
  const tick = setInterval(() => {
    left--;
    if (left <= 0) go(); else draw();
  }, 1000);
  function done() {
    clearInterval(tick);
    card.remove();
    document.removeEventListener("keydown", off, true);
  }
  function off() { done(); closePlayer(); }
  async function go() {
    done();
    // give a subtitle still arriving a moment to land, but never more than that
    await Promise.race([fetching, new Promise((r) => setTimeout(r, 4000))]);
    let meta = next;
    try {
      meta = items(await api("/library/metadata/" + next.ratingKey))[0] || next;
    } catch (e) { /* the cache in hand will do */ }
    stop(true);
    play(meta, 0, {});
  }
  document.addEventListener("keydown", off, true);
  $("#player").appendChild(card);
  draw();
}

/**
 * Put down whatever is playing.
 *
 * `keepOpen` is for the callers that start something else in the same breath - a
 * seek in a live encode, the next episode, a change of quality or of subtitle, all
 * of which are a fresh stream behind the same picture. Without it the player is
 * hidden and the page underneath is drawn again for the tenth of a second before the
 * new stream arrives, which reads on screen as the library flashing through the film.
 */
function stop(keepOpen) {
  leftPlayerOpen = false;
  askedAhead = "";
  // the page behind says "Resume 7:19"; after twenty minutes of watching it should
  // not still say 7:19
  const wasWatching = S && S.meta ? S.meta.ratingKey : null;
  clearSubtitles();               // whatever was on screen belongs to the old stream
  stopStats();
  if (!S) return;
  const sess = S.session;
  timeline("stopped");
  // dash.js keeps the element between streams: resetting here is what broke seeking
  const v = $("#video");
  if (S && S.tz && window.TZ) {
    TZ.ended = null;
    TZ.stop();
    v.classList.remove("hidden");
  }
  v.pause();
  v.removeAttribute("src");
  v.load();
  clearInterval(S.ka);
  clearTimeout(idleTimer);
  if (S.subAbort) { try { S.subAbort.abort(); } catch (e) {} }   // stop reading subtitles
  window.S = S = null;
  if (sess) {
    fetch(url("/video/:/transcode/universal/stop", { session: sess })).catch(() => {});
    untrackSession(sess);
    stoppedAt = Date.now();
  }
  $("#pl-decision").textContent = "";
  if (keepOpen) return;               // another stream is on its way behind the same screen
  $("#player").classList.add("hidden");
  document.body.classList.remove("watching");
  // the arrow would point back at the film that was just closed
  $("#back").classList.remove("on");
  // and the film's own page, if that is what is behind the player, is drawn again
  // with the place it was left at
  if (wasWatching && document.getElementById("watchedbtn")) {
    setTimeout(() => {
      // not while the page is going away: stop() also runs on the way out, and a
      // fetch started then fails as the tab closes, which is a fault nobody caused
      if (document.visibilityState === "hidden") return;
      Promise.resolve(viewMovie(wasWatching)).catch(() => {});
    }, 60);
  }
}

/**
 * The stream died under a film that is still on screen: open it again where it was.
 *
 * The server is replaced while people are watching - that is the whole point of a
 * silent installer - and what the browser sees is a pipe that ends in the middle.
 * The element goes to an error, or simply stops with the film still up, and nothing
 * came back until somebody pressed something. So it is opened again at the second it
 * had reached, a few times, with a wait between: a server being replaced is away for
 * about ten seconds.
 */
let comingBack = 0;                    // when the last attempt was made
let comeBackTries = 0;

function reopenStream(why) {
  if (!S || S.casting || S.tz) return;
  const now = Date.now();
  if (now - comingBack < 4000) return;         // one attempt at a time
  comingBack = now;
  comeBackTries += 1;
  if (comeBackTries > 6) { offerTheCopy("This server is not answering"); return; }               // it is not coming back
  const at = Math.floor(playAt()) + (S.timeBase || 0);
  const o = opts();
  o.height = S.height || 0;
  o.mbit = S.mbit || 0;
  dbg("stream-reopen", { why: why || "", at: at, tries: comeBackTries });
  // twice is enough to know this server is not coming back in a moment: the machine
  // keeping copies has the film too, and the viewer never had to hear about it
  if (comeBackTries >= 2 && !CTX && standbyAddress()) {
    handOver().then((moved) => {
      if (!moved) play(S.meta, at, o, false, S.mi || 0).catch(() => {});
    });
    return;
  }
  play(S.meta, at, o, false, S.mi || 0).catch(() => {});
}

/* Whether the film is running: what the element says, not what was last set.
 *
 * A page that comes back with a film in place has never been played - the browser
 * will not start sound without a press - and it went on telling the server it was
 * playing for as long as it stayed open. */
function playingNow() {
  if (!S) return "stopped";
  if (S.tz || S.casting) return S.state;
  const v = $("#video");
  if (!v) return S.state;
  return v.paused ? "paused" : (v.readyState < 3 ? "buffering" : "playing");
}

/**
 * Carry on from the machine that keeps copies, when this server has gone.
 *
 * The viewer added one address and knows nothing about a second: the main server handed
 * this page the cache's address while it was up, and this is where that is spent.
 * The other machine numbered its own library, so the film is asked for by what it is
 * - a programme, a season and an episode, or a title and a year - and comes back with
 * whatever key it has there. Then the film is opened again at the second it reached.
 */
/* The server this page belongs to, while it is being watched from the cache. Set when
 * the handover happens and cleared when it answers again. */
let cameFrom = "";
let watchingForHome = 0;

/* Ask the server with the library on it whether it is back, once in a while.
 *
 * The cache holds what the main server has been watching and nothing else, so it is where
 * an evening carries on, not where it lives. A film already playing from the cache is
 * left alone - taking it back would cut the picture a second time - and the shelves
 * return to the real server as soon as it answers. */
function watchForHome() {
  clearInterval(watchingForHome);
  if (!cameFrom) return;
  watchingForHome = setInterval(async () => {
    if (!cameFrom) { clearInterval(watchingForHome); return; }
    try {
      await fetch(cameFrom + "/app/version", { cache: "no-store" });
    } catch (e) {
      return;                          // still off
    }
    const home = cameFrom;
    cameFrom = "";
    clearInterval(watchingForHome);
    CTX = null;                        // the shelves are its own again
    toast("Back on " + home.replace(/^https?:\/\//, ""));
    dbg("came-home", { to: home, watching: !!S });
    if (!S) go(document.querySelector(".tab.active").dataset.view);
  }, 30000);
}

/* How this machine is dressed, asked of the machine itself.
 *
 * Not a setting anybody chose: a server that only keeps copies, after dark, is not
 * the one the main server usually watches, and one whose card has been handed to a game is
 * not serving films at all. Both are worth seeing at a glance rather than reading.
 * Asked once at the start and every ten minutes, because the hour turns.
 */
async function dressUp() {
  let said = {};
  try {
    said = await (await fetch(base(CTX ? CTX : undefined).replace(/\/local$/, "") +
                              "/mood", { cache: "no-store" })).json();
  } catch (e) {
    return;
  }
  const look = String(said.look || "house");
  document.documentElement.dataset.mood = look;
  // The six colours the whole page is drawn from, as the server hands them over.
  // Nothing here knows what a skin is called or what it looks like: adding one is an
  // entry in a file on the server and every screen wears it the next time it asks.
  const root = document.documentElement.style;
  [["bg", said.bg], ["panel", said.panel], ["panel2", said.panel2],
   ["line", said.line], ["fg", said.fg], ["dim", said.dim],
   ["accent", said.accent]].forEach(([name, hex]) => {
    if (hex) root.setProperty("--" + name, hex);
    else root.removeProperty("--" + name);
  });
  // and the washes of colour that keep a dark skin from reading as a black rectangle
  const glow = (said.glow || []).map((g) =>
    "radial-gradient(" + g[2] + "px " + Math.round(g[2] * 0.45) + "px at " +
    g[0] + "% " + g[1] + "%, " + g[3] + ", transparent 70%)").join(",");
  document.body.style.background = glow ? glow + ", var(--bg)" : "";
  // a look nobody chose says so, once
  if (said.imposed && said.why && !dressUp.said) {
    dressUp.said = true;
    toast(said.name ? said.name + ": " + said.why : said.why);
  }
}

/* ---- both ways in to a machine ----
 *
 * A server answers to two addresses: the one on this network and the one the router
 * forwards. A page opened at home knows the first and cannot reach the main server from a
 * train; one opened from away knows the second and goes out to the router and back to
 * reach a machine three feet away. Neither is a secret to somebody who already has
 * the other, so each server is asked for both and told apart from its neighbours by
 * the id it answers with.
 *
 * What that buys: the picker shows one row per machine with both doors under it, and
 * a door that stops answering is stepped over without anybody choosing anything.
 */
const DOORS_KEY = "pd-doors";

function doors() {
  try {
    return JSON.parse(localStorage.getItem(DOORS_KEY) || "{}") || {};
  } catch (e) {
    return {};
  }
}

function rememberDoors(id, said) {
  if (!id) return;
  const all = doors();
  all[id] = { lan: said.lan || "", outside: said.outside || "",
              name: said.name || "", when: Date.now() };
  try {
    localStorage.setItem(DOORS_KEY, JSON.stringify(all));
  } catch (e) { /* a private window keeps nothing */ }
}

/* Ask a server what it is and how else it can be reached. */
async function askWhere(origin, token) {
  const stop = new AbortController();
  setTimeout(() => stop.abort(), 4000);
  const said = await (await fetch(
    origin.replace(/\/$/, "") + "/where" + (token ? "?t=" + token : ""),
    { cache: "no-store", signal: stop.signal })).json();
  if (said && said.id) rememberDoors(said.id, said);
  return said;
}

const isHomeAddress = (u) =>
  /^https?:\/\/(192\.168\.|10\.|127\.|172\.(1[6-9]|2\d|3[01])\.|localhost|[^\/]*\.local)/
    .test(u || "");

const doorKind = (u) => (isHomeAddress(u) ? "lan" : "wan");

/* ---- which server this page is reading ----
 *
 * The shelves merge every server that is shown, but a page is always reading one of
 * them for the title it has open, and after a server goes off it may not be the one
 * anybody chose. So the name is in the bar, and pressing it is how to change which.
 *
 * Each is knocked on as the list opens: an address that is off should not be offered
 * as though it were there.
 */
function whoseNow() {
  return CTX ? (friendById(CTX.id) || CTX) : null;
}

function nameOfServer(f) {
  if (!f) return (CFG && CFG.serverName) || "This server";
  return f.name || (f.origin || "").replace(/^https?:\/\//, "");
}

/**
 * Every machine this page can offer, and which of them is the one answering.
 *
 * Pure, and the only thing that decides it: the list was worked out inside the
 * drawing, where the only way to find out what it would do was to press it. Four
 * pages, four kinds of row, and every combination is a case somebody hits.
 *
 * `at` is this page's address; `copy` is the machine this server says keeps copies
 * for it; `home` is the machine this server says it copies from. One of those two is
 * always empty - a server is one or the other.
 */
/* ---- the list of servers ----------------------------------------------------
 *
 * A machine is a set of addresses. There are up to four in a house: this server on
 * the network and through the router, and the other machine the same two ways. The
 * list is built from three sources and folded where they share an address:
 *
 *   - this page's own server, which names both its addresses in /cache
 *   - whatever that server names: the machine keeping copies of it, or the machine
 *     it copies from. A browser's list of servers belongs to the address it is on,
 *     so a page opened on the cache starts empty and this is the only way back
 *   - servers added by hand, kept in this browser
 *
 * Two rules decide everything the list says. A machine is the open one when its
 * addresses include this page's address - not when its shelves are the ones on
 * screen, which is a different question with a different answer. And the open one is
 * called whatever this server calls itself, because a name stored in this browser
 * can be another machine's.
 */
function pickerMachines(at, list, copy, home, alsoMine) {
  const tidy = (u) => String(u || "").replace(/\/$/, "");
  const here = tidy(at);
  const near = isHomeAddress(here);
  const seed = [];
  const add = (name, urls, token, tag) => {
    // once each: this page's address is also one of the two the machine names, and
    // a stored row repeats whatever the browser has learned about it
    const addrs = urls.map(tidy)
      .filter((u, i, all) => u && all.indexOf(u) === i);
    if (addrs.length) seed.push({ name: name, at: addrs, token: token || "", tag: tag });
  };
  add((CFG && CFG.serverName) || "This server",
      [here].concat(alsoMine || []), (CFG && CFG.key) || "", "");
  (list || []).forEach((f) => add(nameOfServer(f),
    [f.origin].concat(f.alsoAt || [], addressesKnownFor(f)), f.token, ""));
  if (copy) add(copy.name, [copy.origin].concat(copy.alsoAt || []), copy.token, "cache");
  if (home) add(home.name, [home.origin].concat(home.alsoAt || []), home.token, "");

  const out = [];
  seed.forEach((one) => {
    const same = out.find((had) => had.at.some((u) => one.at.indexOf(u) >= 0));
    if (!same) {
      out.push(one);
      return;
    }
    one.at.forEach((u) => { if (same.at.indexOf(u) < 0) same.at.push(u); });
    same.token = same.token || one.token;
    same.tag = same.tag || one.tag;
  });
  out.forEach((one) => {
    one.on = one.at.indexOf(here) >= 0;
    // the kind of address this page is on first: on the network it is the quick one
    // and always there, and from outside it cannot be reached at all
    one.at.sort((x, y) => (isHomeAddress(x) === near ? 0 : 1) -
                          (isHomeAddress(y) === near ? 0 : 1));
  });
  if (!out.some((one) => one.on)) out[0].on = true;
  out.forEach((one) => {
    if (one.on && CFG && CFG.serverName) one.name = CFG.serverName;
  });
  return out;
}

/* Every address this browser has been told a stored server answers to. */
function addressesKnownFor(f) {
  const known = (f && f.machine && doors()[f.machine]) || {};
  return [known.lan || "", known.outside || ""];
}

/* Whether an address answers, remembered briefly against the address itself. It was
 * remembered against the machine, and a row that had never been asked which machine
 * it is shared that with whatever was serving the page - so pressing it was answered
 * with this page's own address and went nowhere. */
const doorOpen = {};
async function answersAt(where) {
  const had = doorOpen[where];
  if (had && Date.now() - had.when < 60000) return had.up;
  let up = false;
  try {
    const stop = new AbortController();
    setTimeout(() => stop.abort(), 3000);
    up = (await fetch(where + "/app/version",
                      { cache: "no-store", signal: stop.signal })).ok;
  } catch (e) {
    up = false;
  }
  doorOpen[where] = { up: up, when: Date.now() };
  return up;
}

/* Leave this page for that address, carrying the invitation this page was opened
 * with. Nothing else travels: the other server is asked afresh for everything. */
function goToServer(where, token) {
  const to = String(where).replace(/\/$/, "");
  if (to === location.origin.replace(/\/$/, "")) {
    go("home");
    return;
  }
  try {
    localStorage.setItem("pd-on-server", "");
  } catch (e) { /* a private window keeps nothing */ }
  location.href = to + "/" + (token ? "?t=" + encodeURIComponent(token) : "");
}

function drawServerPick() {
  const chip = document.getElementById("srvpick");
  if (!chip) return;
  chip.innerHTML = "<i class='dot'></i>" +
    esc((CFG && CFG.serverName) || "This server") + "<u>▾</u>";
}

async function openServerPick() {
  const box = document.getElementById("srvlist");
  if (!box) return;
  if (!box.classList.contains("hidden")) return box.classList.add("hidden");
  // Out of the bar and onto the page: the bar carries a backdrop filter, which makes
  // it the containing block for anything fixed inside it however high its z-index.
  const chip = document.getElementById("srvpick");
  if (box.parentElement !== document.body) document.body.appendChild(box);
  if (chip) {
    const at = chip.getBoundingClientRect();
    box.style.top = Math.round(at.bottom + 6) + "px";
    box.style.left = Math.round(at.left) + "px";
  }
  const kept = standbyWhere || standbyOut;
  const copy = kept && !friends().some((f) => f.origin === kept)
    ? { name: standbyName || kept.replace(/^https?:\/\//, ""), origin: kept,
        token: (CFG && CFG.key) || "",
        alsoAt: [standbyWhere, standbyOut].filter(Boolean) }
    : null;
  const homeAt = houseWhere || houseOut;
  const home = homeAt && !friends().some((f) => f.origin === homeAt)
    ? { name: houseName || homeAt.replace(/^https?:\/\//, ""), origin: homeAt,
        token: (CFG && CFG.key) || "",
        alsoAt: [houseWhere, houseOut].filter(Boolean) }
    : null;
  const machines = pickerMachines(location.origin, friends(), copy, home,
                                  [myLan, myOut]);

  box.innerHTML = "";
  const lit = [];
  // Two headings, because the two kinds of address behave differently, and which one
  // a machine is filed under is exactly the thing that goes wrong.
  let said = "";
  machines.slice().sort((x, y) => {
    const kind = (m) => (isHomeAddress(m.at[0]) ? 0 : 1);
    return kind(x) - kind(y) ||
           x.name.toLowerCase().localeCompare(y.name.toLowerCase());
  }).forEach((one) => {
    const kind = isHomeAddress(one.at[0]) ? "On this network" : "From outside";
    if (kind !== said) {
      said = kind;
      const head = document.createElement("div");
      head.className = "srvhead";
      head.textContent = kind;
      box.appendChild(head);
    }
    const row = document.createElement("button");
    row.className = "srvrow" + (one.on ? " on" : "");
    row.innerHTML = "<i class='dot waiting'></i><span>" + esc(one.name) + "</span>" +
      (one.tag && !one.on ? "<b>" + esc(one.tag) + "</b>" : "");
    row.onclick = async () => {
      box.classList.add("hidden");
      if (one.on) {
        toast("Already on " + one.name, true);
        return;
      }
      // whichever of its addresses answers from where we are standing, nearest kind
      // first. Knocking first because leaving for a machine that does not answer
      // gives a blank page with no list on it and no way back but typing an address.
      toast("Reaching " + one.name + "…", true);
      for (const where of one.at) {
        if (await answersAt(where)) {
          goToServer(where, one.token);
          return;
        }
      }
      toast(one.name + " is not answering - staying here", true);
    };
    box.appendChild(row);
    lit.push({ row: row, at: one.at });

    // and each of its addresses under it. A guest who is not on this network is not
    // shown the address on it: it is of no use from where they are, and the inside
    // of somebody else's house is not theirs to be told about.
    one.at.filter((where) => !(CFG && CFG.guest) || !isHomeAddress(where) ||
                             isHomeAddress(location.origin)).forEach((where) => {
      const door = document.createElement("button");
      door.className = "srvdoor";
      door.innerHTML = "<i class='dot waiting'></i><span>" +
        esc(where.replace(/^https?:\/\//, "")) + "</span><b>" +
        doorKind(where) + "</b>";
      door.onclick = async () => {
        box.classList.add("hidden");
        if (where === location.origin.replace(/\/$/, "")) {
          go("home");
          return;
        }
        toast("Reaching " + one.name + "  ·  " + doorKind(where), true);
        if (await answersAt(where)) {
          goToServer(where, one.token);
          return;
        }
        toast(doorKind(where) === "lan"
                ? "That address is on the house network - not reachable from here"
                : one.name + " is not answering there - staying here", true);
      };
      box.appendChild(door);
      lit.push({ row: door, at: [where] });
    });
  });
  box.classList.remove("hidden");
  // and then the knocking, which fills the lights in as the answers come. A machine
  // is up if any of its addresses opens: knocking on one and calling the machine down
  // marked the cache red while it sat there working, because the address it is filed
  // under from outside does not answer from inside the main server.
  lit.forEach(async (one) => {
    const dot = one.row.querySelector(".dot");
    let up = false;
    for (const where of one.at) {
      if (await answersAt(where)) {
        up = true;
        break;
      }
    }
    // and the server's own word about the machine it keeps copies on, which it hears
    // from every few seconds and a browser cannot always reach
    if (!up && standbyWhere && one.at.indexOf(standbyWhere) >= 0 && standbyAlive) {
      up = true;
    }
    if (dot) dot.className = "dot " + (up ? "up" : "down");
  });
}


async function handOver() {
  const where = standbyAddress();
  if (!where || !S || S.casting) return false;
  const key = (CFG && CFG.key) || "";
  const at = Math.floor(playAt()) + (S.timeBase || 0);
  const there = { origin: where.replace(/\/$/, ""), token: key };
  try {
    // is it there at all
    await fetch(there.origin + "/app/version", { cache: "no-store" });
    const m = S.meta;
    const ask = m.type === "episode"
      ? { type: "episode", show: m.grandparentTitle || "",
          season: m.parentIndex || 0, episode: m.index || 0 }
      : { type: "movie", title: m.title || "", year: m.year || "" };
    const said = await api("/library/find", ask, there);
    const found = said && said.key;
    if (!found) {
      toast((standbyName || "The other machine") + " does not hold this one");
      return false;
    }
    const meta = items(await api("/library/metadata/" + found, {}, there))[0];
    if (!meta) return false;
    // The subtitle that was on screen, found again in the other machine's list. A
    // track number belongs to the machine that numbered it, so the language and the
    // name are what carry across. The hand-over asked for subtitle nought - none at
    // all - so an evening that moved machines lost its subtitles at the moment
    // nobody was in a position to notice why.
    const was = currentSubTrack();
    let subId = 0;
    if (was) {
      const theirs = subOptions(meta, 0) || [];
      const same = theirs.find((t) => t.code && t.code === was.code &&
                                      (t.label || "") === (was.label || ""))
                || theirs.find((t) => t.code && t.code === was.code)
                || theirs.find((t) => (t.lang || "") === (was.lang || ""));
      if (same) subId = same.id;
    }
    // and how it is drawn, put to the other machine before anything is asked of it,
    // so its own menus open on what is on screen rather than on its defaults
    try {
      await fetch(there.origin + "/settings" +
                  (there.token ? "?t=" + encodeURIComponent(there.token) : ""), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: DEVICE, subtitles: SUBLOOK }),
      });
    } catch (e) { /* an older copy: it will use what it has */ }
    cameFrom = (CTX ? CTX.origin : location.origin);
    nowOn(there);                      // everything from here comes from that machine
    watchForHome();                    // and back again when the other one returns
    toast("Carrying on from " + (standbyName || "the other server"), true);
    dbg("handover", { to: there.origin, key: found, at: at });
    await play(meta, at, { subId: subId }, false, 0);
    return true;
  } catch (e) {
    dbg("handover-failed", { to: there.origin, why: String(e).slice(0, 80) });
    return false;
  }
}

/**
 * How many seconds of film are ready to play beyond where we are.
 *
 * The one honest measure of whether a machine is keeping up: a server that has
 * slowed is one whose buffer is shrinking, and it shrinks for half a minute before
 * anybody sees a stall.
 */
function bufferAhead() {
  const v = document.getElementById("v");
  if (!v || !v.buffered || !v.buffered.length) return 0;
  const at = v.currentTime;
  for (let i = 0; i < v.buffered.length; i++) {
    if (v.buffered.start(i) <= at + 0.5 && at <= v.buffered.end(i) + 0.5) {
      return Math.max(0, v.buffered.end(i) - at);
    }
  }
  return 0;
}

//: How long a picture may stand still before the machine it comes from is given up
//: on. Two rounds of the keepalive, which is longer than any seek.
//:
//: What is left to play used to decide this, and it cannot: a browser streaming a
//: file keeps a couple of seconds in front of itself and no more, however well it is
//: being fed, so "running low" was true for the whole of every film. It moved house
//: on a healthy stream, again and again, and moved to a slower machine to do it.
const STAND_STILL = 2;

/**
 * Move to the machine that keeps copies while there is still film in hand.
 *
 * The main server is used for the whole of an evening and nothing else is touched. Only
 * when what is left to play starts falling - the disk is busy, the line is busy, the
 * machine is doing something else - is the cache asked for anything, and then it is
 * asked before the buffer runs out rather than after the picture stops. Switching
 * with ten seconds in hand is a pause nobody sees; switching at nought is a stall.
 */
function leaveThisMachine(why) {
  if (!S || S.casting || S.handedOver || S.state !== "playing") return false;
  if (!standbyAddress()) return false;                 // nowhere to go
  // A machine that would not have us is not asked again for half a minute.
  if (S.triedAt && Date.now() - S.triedAt < 30000) return false;
  S.triedAt = Date.now();
  S.handedOver = true;                                 // once per playing
  dbg("moving", { why: why });
  toast("Moving to " + (standbyName || "the other server"), true);
  handOver().then((went) => {
    if (!went) S.handedOver = false;                   // it would not have us
  });
  return true;
}

function startKeepalive() {
  clearInterval(S.ka);
  let wasAt = -1, still = 0, hadAhead = 1e9, wasGood = -1;
  S.ka = setInterval(() => {
    if (!S) return;
    if (S.session) fetch(url("/video/:/transcode/universal/ping", { session: S.session })).catch(() => {});
    const state = playingNow();
    S.state = state;
    timeline(state);
    // a film that says it is running and has not moved has lost its stream: the
    // server was replaced under it, or the pipe broke. Two rounds of standing still
    // is sixteen seconds, which no seek takes.
    const at = playAt();
    still = (state === "playing" && at === wasAt) ? still + 1 : 0;
    wasAt = at;
    if (still >= STAND_STILL) {
      still = 0;
      // Opening it again is the first answer and usually the whole of it. A picture
      // that stands still a second time after that is this machine, not the stream,
      // and only then is there anything to gain by moving.
      if (S.reopened) leaveThisMachine("stood still twice");
      else { S.reopened = true; reopenStream("stood still"); }
    }
    // and before it comes to that: what is left to play, and whether it is going
    const ahead = bufferAhead();
    if (state === "playing") {
      // playing along without standing still: whatever went wrong is behind us
      if (at > wasGood) { S.reopened = false; wasGood = at; }
      hadAhead = ahead;
    } else {
      hadAhead = 1e9;
    }
  }, 8000);
}

/**
 * What to call this browser when reporting progress.
 *
 * Reports are filed by device, so a browser that gives no name shares a record with
 * every other browser. The invitation's name where there is one, the kind of machine,
 * and a short mark that stays with this browser so two in one house are two.
 */
function myDeviceName() {
  let mark = localStorage.getItem("palladium-mark");
  if (!mark) {
    mark = Math.random().toString(36).slice(2, 6);
    localStorage.setItem("palladium-mark", mark);
  }
  const kind = /Mobi|Android|iPhone/.test(navigator.userAgent) ? "phone" : "computer";
  const who = (CFG && CFG.name) ? CFG.name + "'s " : "";
  return who + kind + " " + mark;
}

/**
 * The subtitle on screen, in the words the server files things under: the name of a
 * file beside the video, or "t" and the number of a track inside it.
 *
 * "on" told the server that something was showing and nothing about what, so only a
 * subtitle the server had fetched itself could ever be verified by watching.
 */
function subInUse() {
  if (!S || !S.subId) return "off";
  try {
    const t = subOptions(S.meta, S.mi).filter(
      (x) => String(x.id) === String(S.subId))[0];
    if (!t) return "on";
    return t.external ? (t.label || "on")
                      : (t.index === undefined ? "on" : "t" + t.index);
  } catch (e) { return "on"; }
}

function timeline(state) {
  if (!S) return;
  fetch(url("/:/timeline", Object.assign({
    ratingKey: S.meta.ratingKey,
    key: "/library/metadata/" + S.meta.ratingKey + "/",
    state: state,
    time: Math.floor((S.pos || S.start) * 1000),
    duration: S.meta.duration,
    device: myDeviceName(),
    client: "web",
    // putting something on is not watching it: the server keeps this one's place in
    // the shuffle's own notes and leaves watched state alone
    ...(casualOn ? { casual: 1 } : {}),
    // and which shelf it was drawn from, so that shelf keeps the place: it is what
    // Play resumes and what the one row on Continue watching is built out of
    ...(casualOn && shuffleOn ? { shelf: shuffleOn } : {}),
    // Which subtitle is on screen - not merely that one is. An episode watched
    // through without them is how a series says it does not want them fetched, and
    // with one it is how that one earns its mark.
    sub: subInUse(),
  }))).then((r) => r.json()).then((said) => {
    // The server marks a subtitle verified as this very report arrives - the episode
    // has just passed the credits - and says so here. Reading the title again once, at
    // that moment, is what turns the tick green while the panel is open.
    const news = said && said.MediaContainer && said.MediaContainer.subsVerified;
    if (!news || !S || String(news.key) !== String(S.meta.ratingKey)) return;
    return subtitleVerifiedNow();
  }).catch(() => {});
}

/** The one on screen has earned its mark: read the title again and say so. */
async function subtitleVerifiedNow() {
  try {
    const fresh = items(await api("/library/metadata/" + S.meta.ratingKey))[0];
    if (fresh) S.meta = fresh;
  } catch (e) { return; }               // the tick can wait for the next opening
  if (window.fillPlayerSubs) fillPlayerSubs();
  if (window.__ccDraw) window.__ccDraw();
  toast("Subtitle verified", 1);
}

/* ---------------- Chromecast ---------------- */

let remote = null, remoteCtl = null;

window.__onGCastApiAvailable = function (ok) {
  // the framework is fetched from Google and does not always arrive; without it every
  // mention of `cast` throws, and the fault log fills with noise
  if (!ok || !window.cast || !window.cast.framework) return;
  // it is here after all: whatever was hidden while waiting comes back
  document.querySelectorAll("#castbtn, #c-castbtn").forEach((b) => {
    b.style.display = "";
  });
  const ctx = cast.framework.CastContext.getInstance();
  ctx.setOptions({
    receiverApplicationId: chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID,
    autoJoinPolicy: chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED,
  });
  ctx.addEventListener(cast.framework.CastContextEventType.SESSION_STATE_CHANGED, (e) => {
    const st = cast.framework.SessionState;
    if (e.sessionState === st.SESSION_STARTED) {
      toast("Connected to " + castSession().getCastDevice().friendlyName);
      if (S && !S.casting) {           // hand a local stream over to the TV
        const m = S.meta, o = Math.floor(S.pos || S.start);
        stop(true);
        play(m, o, {});
      }
    } else if (e.sessionState === st.SESSION_ENDED && S && S.casting) {
      closePlayer();
    }
  });
  remote = new cast.framework.RemotePlayer();
  remoteCtl = new cast.framework.RemotePlayerController(remote);
  remoteCtl.addEventListener(cast.framework.RemotePlayerEventType.CURRENT_TIME_CHANGED, updateCastUi);
  remoteCtl.addEventListener(cast.framework.RemotePlayerEventType.IS_PAUSED_CHANGED, updateCastUi);
};

function castSession() {
  return window.cast && window.cast.framework
    ? cast.framework.CastContext.getInstance().getCurrentSession()
    : null;
}

async function castLoad(src, meta, offset, dp) {
  const s = castSession();
  // our own transcode goes as HLS; a cast device handed a manifest gets DASH
  const kind = dp ? dp.type
    : src.indexOf("/gpu/hls") >= 0 ? "application/x-mpegURL"
    : "application/dash+xml";
  const info = new chrome.cast.media.MediaInfo(src, kind);
  info.streamType = chrome.cast.media.StreamType.BUFFERED;
  info.duration = (meta.duration || 0) / 1000 - (dp ? 0 : offset);
  if (!dp) {
    // DASH needs no HLS segment hints; the receiver reads the manifest

  }
  const md = new chrome.cast.media.GenericMediaMetadata();
  md.title = meta.type === "episode"
    ? meta.grandparentTitle + " - S" + meta.parentIndex + "E" + meta.index : meta.title;
  md.subtitle = meta.type === "episode" ? meta.title : String(meta.year || "");
  const thumb = img(meta.type === "episode" ? meta.grandparentThumb : meta.thumb, 500, 750);
  if (thumb) md.images = [new chrome.cast.Image(thumb)];
  info.metadata = md;
  const req = new chrome.cast.media.LoadRequest(info);
  req.autoplay = true;
  req.currentTime = dp ? (offset || 0) : 0;   // transcode starts at the offset already
  try {
    await s.loadMedia(req);
    toast("Playing on " + s.getCastDevice().friendlyName);
  } catch (e) {
    toast("Cast failed: " + (e && e.description ? e.description : e));
  }
}

function updateCastUi() {
  if (!S || !S.casting || !remote) return;
  S.pos = remote.currentTime || 0;
  S.state = remote.isPaused ? "paused" : "playing";
  const dur = remote.duration || 1;
  $("#c-seek").value = Math.round(((remote.currentTime || 0) / dur) * 1000);
  $("#c-time").textContent = clock(S.pos) + " / " + clock(dur);
  $("#c-playpause").textContent = remote.isPaused ? "▶" : "⏸";
}

/* ---------------- wiring ---------------- */

$("#c-playpause").onclick = () => { if (remoteCtl) remoteCtl.playOrPause(); };
$("#c-stop").onclick = () => closePlayer();
$("#c-seek").oninput = (e) => {
  if (!remote || !remoteCtl) return;
  remote.currentTime = (e.target.value / 1000) * (remote.duration || 0);
  remoteCtl.seek();
};
$("#pl-close").onclick = () => closePlayer();
/* fit keeps the aspect (black bars), fill crops to the panel, stretch distorts */
// no stretch option: filling a 16:9 panel with a 2.39:1 film means cropping the
// sides, never distorting faces
const FITS = [["", "Fit (whole picture)"], ["fit-fill", "Zoom to fill (crops edges)"]];
/* The picture-size control sits in the transport bar now; it answered to another
   name when it lived in the top bar, and an old tab may still have that one. */
const fitButton = () => $("#c-fit") || $("#pl-fit");

/* Settings, without losing the film.
   The player is an overlay, so it is put out of sight rather than stopped - the sound
   carries on - and Back brings it straight back to where it was. */
let leftPlayerOpen = false;
let pausedForSettings = false;     // we stopped it, so we are the ones to start it

if ($("#pl-settings")) {
  $("#pl-settings").onclick = (e) => {
    e.stopPropagation();
    if (!S) return;
    leftPlayerOpen = true;
    // full screen belongs to the film; settings is a page and wants the window back
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    // held, not stopped - and only if it was running
    const v = $("#video");
    pausedForSettings = !S.casting && !v.paused;
    if (pausedForSettings) v.pause();
    $("#player").classList.add("hidden");
    // an entry of its own, so the mouse's back button lands on the film rather than
    // on the entry the player pushed - which would stop it
    window.history.pushState({ settings: 1 }, "");
    go("settings");
    // the way back wears the film's name rather than a bare arrow
    const b = $("#back");
    b.classList.add("on", "named");
    b.innerHTML = "&#8592; " + esc($("#pl-title").textContent || "the film");
  };
}

function backToFilm() {
  if (!leftPlayerOpen || !S) return false;
  leftPlayerOpen = false;
  const b = $("#back");
  b.classList.remove("on", "named");
  b.innerHTML = "&#8592;";
  $("#player").classList.remove("hidden");
  if (pausedForSettings) {
    pausedForSettings = false;
    $("#video").play().catch(() => {});
  }
  if (window.showChrome) showChrome(false, "back");
  return true;
}

/* The title is a way in as well as a label: it opens the season this episode is from,
   with the episode itself in view. */
if ($("#pl-title")) {
  $("#pl-title").onclick = (e) => {
    e.stopPropagation();
    if (S) showInPlace(S.meta);
  };
}

/* How subtitles look, for the film that is playing. */
if ($("#pl-subcss")) $("#pl-subcss").onclick = () => subtitleLookPanel();

/* Cast, from the transport bar. The SDK's own launcher element draws a grey box
   wherever it likes; asking for a session directly is the same thing without that. */
/* The cast control in the player is Google's own launcher now. Asking for a session
   by hand looked right and did nothing: without the framework's own button there is
   no device list, and a rejected promise is silent. */

/* The transport bar is inside the player, which swallows clicks to keep the chrome
   from hiding; these two are controls, not the picture, so they get their own. */
["#c-fit", "#pl-subcss", "#pl-quality", "#c-castbtn"].forEach((sel) => {
  const el = $(sel);
  if (el) el.addEventListener("click", (e) => e.stopPropagation());
});

function applyFit() {
  const cls = prefs().fit === "fit-fill" ? "fit-fill" : "";   // drops the old stretch value
  const v = $("#video");
  v.classList.remove("fit-fill", "fit-stretch");
  if (cls) v.classList.add(cls);
  const name = (FITS.filter((f) => f[0] === cls)[0] || FITS[0])[1];
  // the letter of the mode, so the button says where it stands: F fit, Z zoom
  fitButton().textContent = cls ? "Z" : "F";
  fitButton().title = "Picture size: " + name + " (z to cycle)";
  return name;
}
fitButton().onclick = () => {
  const cur = prefs().fit || "";
  const next = FITS[(FITS.findIndex((f) => f[0] === cur) + 1) % FITS.length][0];
  setPref("fit", next);
  toast("Picture size: " + applyFit());
  // zoom changes how much of the frame is visible, so the cues move with it
  if (window.restyleCues) restyleCues();
};

// the top bar had a second fullscreen button; the keyboard shortcut still works
if ($("#pl-full")) $("#pl-full").onclick = () => $("#c-fs").click();
const _unusedFullscreen = () => {
  if (document.fullscreenElement) leaveFullscreen();
  else askFullscreen($("#player"));
};
/* one path for every way of going back: the arrow just asks the browser */
$("#back").onclick = () => window.history.back();
document.querySelectorAll(".tab").forEach((t) => {
  t.onclick = () => {
    // Settings and Reports are somewhere to step aside to. Reached from a film's page
    // - which is where somebody goes to change how its subtitles look - the way back
    // to that film is what they want next, so it is kept. The library tabs are
    // destinations and start afresh.
    const aside = t.dataset.view === "settings" || t.dataset.view === "reports";
    go(t.dataset.view, aside && navStack.length > 0);
  };
});

/* The version, next to the name: this server's own release, because this page is
   part of it. The app's number is a different sequence - they were built together
   once and drifted - and it belongs in the tooltip, where somebody comparing a phone
   against a browser can find it. */
(async () => {
  try {
    const v = await (await fetch("/app/version")).json();
    const el = $("#ver");
    const mine = v.serverVersion || v.versionName;
    if (el && mine) {
      el.textContent = mine;
      el.title = "Palladium " + mine +
        (v.versionName ? "  ·  app " + v.versionName : "") +
        (v.built ? "  ·  app built " + v.built : "");
    }
  } catch (e) { /* the name alone will do */ }
})();
let searchTimer;
$("#search").oninput = (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  // The cross in the box, or the last letter deleted: put back what was on screen
  // before the search. The box fires this with an empty value either way.
  if (!q) {
    if (!inSearch) return;
    // A search inside a collection asked about that collection: the cross puts it
    // back on screen, not the shelf of shelves it was opened from.
    if (collSearchOn) {
      collectionOn = collSearchOn;
      inSearch = false;
      viewWatchlist();
      return;
    }
    const on = (document.querySelector(".tab.active") || {}).dataset;
    go((on && on.view) || "home");
    return;
  }
  searchTimer = setTimeout(() => {
    // Inside an open collection the same box asks a narrower question: of what it
    // finds, what is in this collection and what is not. Leaving for the ordinary
    // results would take the collection off the screen, which is the thing being
    // edited.
    if (collectionOn &&
        (CTX === null || CTX === undefined) && q.length > 1 &&
        document.querySelector(".collbar")) {
      collectionSearch(q);
      return;
    }
    if (q.length > 1) {
      navStack = [];
      $("#back").classList.add("on");
      pushView(() => viewSearch(q));
      viewSearch(q);
    }
  }, 350);
};
document.addEventListener("keydown", (e) => {
  if (S) showChrome(true, "key");            // keyboard counts as deliberate
  if (e.key === "Escape" && S && !document.fullscreenElement) closePlayer();
  if ((e.key === "z" || e.key === "Z") && S && document.activeElement !== $("#search"))
    fitButton().click();
  if ((e.key === "f" || e.key === "F") && S && document.activeElement !== $("#search"))
    $("#c-fs").click();
  if (e.key === "/" && document.activeElement !== $("#search")) {
    e.preventDefault();
    $("#search").focus();
  }
});
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (onResize) onResize(); }, 120);
});

/* The cues carry their height as a number worked out from the size of the element and
   the size of the picture inside it. Both change when the window is resized, when full
   screen is entered or left, and when a new stream arrives with another shape - so the
   cues are placed again each time rather than keeping the old window's arithmetic. */
(function followTheWindow() {
  let again = null;
  const replace = () => {
    clearTimeout(again);
    again = setTimeout(() => {
      const v = $("#video");
      if (!v) return;
      applySubtitleCss();                 // the size is in pixels, not in vh
      if (v.textTracks && v.textTracks.length) restyleCues();
    }, 60);
  };
  const v = $("#video");
  if (v && window.ResizeObserver) new ResizeObserver(replace).observe(v);
  else window.addEventListener("resize", replace);
  if (v) v.addEventListener("resize", replace);       // a picture of another shape
  document.addEventListener("fullscreenchange", replace);
})();
/* sendBeacon survives the tab closing; fetch() usually does not */
window.addEventListener("pagehide", () => {
  if (!S || !S.session) return;
  navigator.sendBeacon(url("/video/:/transcode/universal/stop", { session: S.session }));
});

/* seeking a live encode restarts it at the new position */
window.ctlSeek = (seconds) => {
  if (!S) return;
  const meta = S.meta, subId = S.subId, mi = S.mi;
  stop(true);
  play(meta, Math.floor(seconds), { subId: subId || 0, mediaIndex: mi });
};


/* The picker lives in Settings now; this runs only if the element is still there,
   so the page works either way. */
(function initEnginePref() {
  const sel = $("#engine");
  if (!sel) return;
  ENGINES.forEach((e) => sel.add(new Option(e[1], e[0])));
  sel.value = engine();
  sel.onchange = () => {
    setPref("engine", sel.value);
    toast(sel.value === "gpu"
      ? "Heavy titles will be re-encoded here on the RTX 5070"
      : "Playback goes through the other server");
  };
})();

/* The default-language picker lives in Settings now; the top bar keeps only the
   choices worth making mid-browse. */
(function initSubPref() {
  const sel = $("#deflang");
  if (!sel) return;
  LANGS.forEach((l) => sel.add(new Option(l[1], l[0])));
  sel.value = prefs().subLang || "";
  // the server keeps this for the viewer, and it is the one every screen reads; the
  // browser's copy is only what this page had before the answer arrived
  fetch("/settings").then((r) => r.json()).then((said) => {
    const theirs = said.language || said.subLang;
    if (theirs && theirs !== sel.value) {
      sel.value = theirs;
      setPref("subLang", theirs);
    }
  }).catch(() => {});
  sel.onchange = () => {
    setPref("subLang", sel.value);
    saveLanguage(sel.value);
    const name = (LANGS.filter((l) => l[0] === sel.value)[0] || ["", "Off"])[1];
    toast(sel.value ? "Default subtitles: " + name +
      " - burned in unless the track is an external file, which costs CPU"
      : "Subtitles off by default");
  };
})();

/* How subtitles are drawn - the server's answer, shared by every client.
   Appearance is a ::cue rule; position cannot be, because a cue carries its own line
   and CSS has no say over it, so it is applied to each cue as it is created. */
let SUBLOOK = { size: 1, position: 0.08, colour: "white", background: "shadow" };

/* What 100% means, here and on every other screen: the text is this much of the height
   of what it is drawn on. The same number lives in pd-server.py (SUB_BASE), in the app
   and in the burn-in filter, because each of them draws its own text; changing it in
   one place only makes a percentage mean two things again. */
const SUB_BASE_VH = 5;

/* The faces on offer, and what each one actually resolves to. All of them are on the
   machine already - a subtitle that waits for a web font is a subtitle that arrives
   after the line has been spoken. */
const SUB_FONTS = {
  sans: '"Segoe UI",system-ui,-apple-system,sans-serif',
  serif: 'Georgia,"Times New Roman",serif',
  condensed: '"Arial Narrow","Roboto Condensed","Segoe UI",sans-serif',
  rounded: '"Segoe UI Variable Display","Trebuchet MS",system-ui,sans-serif',
  mono: 'Consolas,"Courier New",monospace',
};

function applySubtitleCss() {
  const colours = { white: "#ffffff", yellow: "#ffe94d", cyan: "#6fd3ff",
                    green: "#8dea6a", grey: "#c9d3dc" };
  const behind = SUBLOOK.background;
  // The browser draws the cues, so the settings have to reach its own cue layer.
  // vh rather than px: the text keeps its size relative to the picture, which is what
  // "150%" means to anyone who has set this on a television.
  let sheet = document.getElementById("subcss");
  if (!sheet) {
    sheet = document.createElement("style");
    sheet.id = "subcss";
    document.head.appendChild(sheet);
  }
  sheet.textContent = "video::cue{" +
    "font-family:" + (SUB_FONTS[SUBLOOK.font] || SUB_FONTS.sans) + ";" +
    "font-size:" + cueFontPx().toFixed(2) + "px;" +
    "color:" + (colours[SUBLOOK.colour] || "#fff") + ";" +
    "background:" + (behind === "dark" ? "rgba(0,0,0,.55)"
                     : behind === "black" ? "#000" : "transparent") + ";" +
    (behind === "shadow" ? "text-shadow:0 2px 4px #000,0 0 6px #000;" : "") +
    "}";
  const box = $("#subs");
  if (!box) return;
  box.style.fontSize = cueFontPx().toFixed(2) + "px";
  box.style.color = colours[SUBLOOK.colour] || "#fff";
  box.style.textShadow = behind === "shadow" ? "0 2px 4px #000,0 0 6px #000" : "none";
  // Where the layer sits, worked out the way a real cue is: the number is a share of
  // the picture, except at the last step, which is not a share of anything - it means
  // the bottom of the screen. Read as a fraction it put the text at 99% of the way UP,
  // which is the top of the window, and the setting called Very bottom drew there.
  box.style.bottom = (subsLayerUp() * 100).toFixed(2) + "%";
  box.querySelectorAll(".line").forEach((line) => {
    line.style.background = behind === "dark" ? "rgba(0,0,0,.55)"
      : behind === "black" ? "#000" : "transparent";
  });
}

/**
 * Put the live cues on screen - all of it, every tick.
 *
 * Emptying and refilling is the whole point: there is no state to go stale, so a
 * subtitle switched off leaves nothing behind and two tracks cannot stack.
 */
function drawSubtitles() {
  const box = $("#subs");
  const v = $("#video");
  if (!box || !v) {
    // an old page has no element to draw into: say so rather than doing nothing
    if (!drawSubtitles.warned) {
      drawSubtitles.warned = true;
    }
    return;
  }
  // The cues carry the film's own times. An encode starts at zero wherever it begins,
  // so where the player is has to be read as film time before the two can be compared;
  // on a direct play the origin is zero and this is the same number.
  const at = ((S && S.timeBase) || 0) + playAt();
  let text = [];
  // The browser draws the cues when there is a browser drawing the picture. Under
  // AVPlay the picture is on a plane of its own with no cue layer over it, so the
  // text is ours to put on screen.
  if (S && S.tz && subTrack) {
    const cues = subTrack.cues || [];
    const live = [];
    for (let i = 0; i < cues.length; i++) {
      const cue = cues[i];
      if (cue.startTime > at || cue.endTime <= at) continue;
      if ((cue.text || "").trim()) live.push(cue);
    }
    // A file usually has the next line starting on the frame the last one ends, and
    // sometimes a little before: drawn literally the old line flashes beside the new
    // one. The later cue is the current one; anything that began appreciably earlier
    // has had its turn.
    if (live.length > 1) {
      const newest = Math.max.apply(null, live.map((c) => c.startTime));
      for (let i = live.length - 1; i >= 0; i--) {
        if (newest - live[i].startTime > 0.25) live.splice(i, 1);
      }
    }
    live.forEach((cue) => {
      const said = (cue.text || "").replace(/<[^>]*>/g, "").trim();
      if (said) text.push(said);
    });
    // No subtitle in the world is more than a couple of lines at a time. Whatever
    // else may be wrong upstream, the screen cannot fill with text: the newest two
    // are what a person is reading, and the rest were over before them.
    if (text.length > 2) text = text.slice(-2);
  }
  // the placing follows the picture: filling the screen takes the black away, and
  // the cues that were sitting above it have to come down with the picture
  if (subTrack && drawSubtitles.crop !== letterbox()) {
    drawSubtitles.crop = letterbox();
    const all = subTrack.cues || [];
    for (let i = 0; i < all.length; i++) placeCue(all[i]);
  }
  const now = text.join("\n");
  if (box.dataset.said === now) return;         // nothing has changed; leave it be
  box.dataset.said = now;
  box.innerHTML = "";
  if (!now) return;
  text.forEach((said) => {
    const line = document.createElement("div");
    line.className = "line";
    line.textContent = said;
    box.appendChild(line);
  });
  applySubtitleCss();                            // the background rides on each line
}

let subTimer = null;

function startSubtitleDrawing() {
  clearInterval(subTimer);
  subTimer = setInterval(drawSubtitles, 100);
}

/* The look belongs to the film while one is open, and to the server otherwise. */
let SUBKEY = "";

/* Which kind of screen this is. A browser on a desk is not a television and not a
   phone, and the three want different sizes. */
const DEVICE = "web";

async function loadSubtitleLook(key) {
  SUBKEY = key === undefined ? SUBKEY : key;
  try {
    const url = "/settings?device=" + DEVICE +
      (SUBKEY ? "&key=" + encodeURIComponent(SUBKEY) : "");
    const got = await (await fetch(url)).json();
    if (got && got.subtitles) SUBLOOK = got.subtitles;
    if (got && got.language) SERVERLANG = got.language;
    if (got && got.accent) paintAccent(got.accent);
    SUBOVERRIDE = !!(got && got.override);
  } catch (e) { /* the defaults are perfectly good */ }
  applySubtitleCss();
  // Height and base live on the cues, not in the stylesheet: without this a change
  // made on the settings page while a film is open only took effect on the next film.
  if ($("#video")) restyleCues();
  return SUBLOOK;
}

let SUBOVERRIDE = false;

/** The accent the server keeps, on this page. One property; the stylesheet does the
    rest, because everything coloured reads it from --accent. */
function paintAccent(code) {
  if (!/^#[0-9a-f]{6}$/i.test(code || "")) return;
  document.documentElement.style.setProperty("--accent", code);
}

/**
 * Show a track once the browser knows the shape of the picture.
 *
 * The black bar under a film is worked out from the video's own width and height, and
 * until the file has been read far enough those are zero: the first cues were placed as
 * if there were no bar, drawn, and then moved. Hold them until the shape is in.
 */
function showWhenSized(tt) {
  const v = $("#video");
  if (!v) return;
  if (v.videoWidth && v.videoHeight) { tt.mode = "showing"; return; }
  tt.mode = "hidden";
  const ready = () => {
    v.removeEventListener("loadedmetadata", ready);
    v.removeEventListener("resize", ready);
    clearTimeout(waited);
    if (subTrack !== tt) return;         // another track took the screen
    applySubtitleCss();
    restyleCues();
    tt.mode = "showing";
  };
  // and never hold them for ever: a stream that never reports a size still has cues
  const waited = setTimeout(ready, 4000);
  v.addEventListener("loadedmetadata", ready);
  v.addEventListener("resize", ready);
}

/** Tell the server which language this viewer wants, so every screen agrees. "Off" is
    not a language and the server keeps none: that choice stays in the browser. */
function saveLanguage(code) {
  if (!code) return;
  SERVERLANG = code;
  fetch("/settings", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ language: code }) }).catch(() => {});
}

/** Restyle the cues that are already on screen, without reloading anything. */
function restyleCues() {
  const v = $("#video");
  const list = v.textTracks;
  for (let i = 0; list && i < list.length; i++) {
    const cues = list[i].cues;
    for (let j = 0; cues && j < cues.length; j++) placeCue(cues[j]);
  }
}

/**
 * The panel behind the Aa button: the same four choices as the settings page, saved
 * against this film. A burned track is drawn by ffmpeg, so changing it means starting
 * the stream again - which is done at the same second, not from the beginning.
 */
async function subtitleLookPanel() {
  const old = document.getElementById("subpanel");
  if (old) { old.remove(); return; }
  shutPanels("subpanel");                 // one at a time, in that corner
  const look = await loadSubtitleLook();
  const box = document.createElement("div");
  box.id = "subpanel";
  box.className = "subpanel";      // what the outside-press handler tests for
  box.tabIndex = -1;               // focusable without the page scrolling to it
  // the steps read the other way round off the picture: the first is the row nearest
  // the film and each one after it is further down into the black
  const heights = () => (look.base === "screen"
    ? [[0, "Nearest"], [0.08, "One down"], [0.16, "Two down"], [0.99, "Very bottom"]]
    : [[0, "Bottom"], [0.08, "Just up"], [0.16, "Raised"], [0.28, "High"],
       [0.99, "Very bottom"]]);
  const rowsNow = () => [
    ["Size", "size", [[0.8, "80%"], [1, "100%"], [1.25, "125%"], [1.5, "150%"], [1.8, "200%"]]],
    ["Face", "font", [["sans", "Sans"], ["serif", "Serif"], ["condensed", "Narrow"],
                      ["rounded", "Round"], ["mono", "Mono"]]],
    ["Colour", "colour", [["white", "White"], ["yellow", "Yellow"], ["cyan", "Cyan"],
                          ["green", "Green"], ["grey", "Grey"]]],
    ["Behind", "background", [["none", "None"], ["shadow", "Shadow"], ["dark", "Dark"],
                             ["black", "Black"]]],
    ["Position", "position", heights()],
    ["Placed", "base", [["picture", "On screen"], ["screen", "Off screen"]]],
  ];
  const draw = () => {
    // a redraw would otherwise send a scrolled panel back to its heading, and the
    // next thing to press would no longer be under the eye that was looking at it
    const wasAt = box.scrollTop;
    box.innerHTML = "";
    // Aa answers one question: how subtitles are drawn. Which subtitle - including
    // having none - is CC's, on every client.
    const head = document.createElement("div");
    head.className = "subhead";
    head.innerHTML = "<h4>How subtitles look</h4>";
    box.appendChild(head);
    // A burned track is a picture painted into the film, so only the two settings
    // the server can act on while painting it mean anything. Its typeface, its
    // colour and what sits behind it were decided when the disc was made, and it
    // cannot be put below the picture because it is inside the picture. Those rows
    // stay, faded: a control that vanishes reads as one that has been lost.
    const burned = !!(S && S.burned);
    const IN_PICTURE = ["font", "colour", "background", "base"];
    rowsNow().forEach(([label, key, options]) => {
      const row = document.createElement("div");
      row.className = "subrow";
      row.innerHTML = "<span class='sublabel'>" + label + "</span>";
      const dead = burned && IN_PICTURE.indexOf(key) >= 0;
      const bynumber = key === "size" || key === "position";
      const peg = bynumber
        ? nearestOption(options.map((o) => +o[0]), +look[key] || 0) : -1;
      options.forEach(([value, text], i) => {
        const b = document.createElement("button");
        b.className = "btn ghost kind" +
          ((bynumber ? i === peg : String(look[key]) === String(value)) ? " on" : "") +
          (dead ? " dim" : "");
        b.textContent = text;
        b.onclick = dead
          ? () => toast("Burned-in track - only size and position apply")
          : () => change(key, value);
        row.appendChild(b);
      });
      box.appendChild(row);
    });
    if (burned) {
      const why = document.createElement("div");
      why.className = "note";
      why.style.margin = "2px 0 8px 82px";
      why.textContent = "Burned into the video. Face, colour, backing and screen " +
        "placement come from the disc; size and position are applied at re-encode.";
      box.appendChild(why);
    }
    const foot = document.createElement("div");
    foot.className = "subrow";
    foot.innerHTML = "<span class='sublabel'></span>";
    // One button: resets when an override exists, opens the defaults when not.
    const openDefaults = () => {
      box.remove();
      if (window.settingsTab) settingsTab("subs");
      const gear = $("#pl-settings");
      if (gear) return gear.click();  // holds the film and names it in the top bar
      go("settings", true);           // from a title's page: keep the way back
    };
    const reset = document.createElement("button");
    reset.className = "btn ghost";
    reset.textContent = SUBOVERRIDE ? "Reset to default" : "Default settings";
    reset.onclick = async () => {
      if (!SUBOVERRIDE) return openDefaults();
      await fetch("/settings", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: SUBKEY, device: DEVICE, reset: true }) });
      Object.assign(look, await loadSubtitleLook());
      afterChange();
      draw();
    };
    foot.appendChild(reset);
    box.appendChild(foot);
    box.scrollTop = wasAt;
  };

  /** Read the title again - a file has been verified or deleted - and redraw. */
  async function refreshTracks() {
    try {
      const fresh = items(await api("/library/metadata/" + S.meta.ratingKey))[0];
      if (fresh) S.meta = fresh;
    } catch (e) { /* the cache in hand will do */ }
    fillPlayerSubs();
    draw();
  }

  const afterChange = () => {
    // a burned track lives in the picture: only a new stream can change it
    if (S && S.burned) {
      const at = Math.floor(S.pos || 0);
      toast("Redrawing the subtitles\u2026");
      if (window.ctlSeek) ctlSeek(at);
    } else {
      restyleCues();
    }
  };

  const change = async (key, value) => {
    look[key] = value;
    const r = await (await fetch("/settings", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: SUBKEY, device: DEVICE, subtitles: look }) })).json();
    Object.assign(look, r.subtitles);
    SUBLOOK = r.subtitles;
    SUBOVERRIDE = !!r.override;
    applySubtitleCss();
    afterChange();
    draw();
  };

  draw();
  $("#player").appendChild(box);

  // left open over a running film, it fades out of the way like the transport bar
  let idle = null;
  const bump = () => {
    clearTimeout(idle);
    idle = setTimeout(() => {
      if (S && !$("#video").paused && box.isConnected) box.remove();
    }, 12000);
  };
  ["mousemove", "click", "keydown", "wheel"].forEach((e) =>
    box.addEventListener(e, bump, { passive: true }));
  bump();
}

/**
 * What OpenSubtitles has for the film in hand.
 *
 * Ordered as the server ranks them: an exact match to this very file first - matched on
 * a hash of the video, so it was cut for the same release - then by how many people
 * have taken it. Choosing one downloads it, and the track appears in the row above.
 */
const SUBLANGS = [["en", "English"], ["sv", "Svenska"], ["no", "Norsk"],
                  ["da", "Dansk"], ["fi", "Suomi"], ["de", "Deutsch"],
                  ["fr", "Français"], ["es", "Español"]];

/* The subtitle chosen in a dropdown while it is still being written.

   It stands in the list as soon as it is asked for, carries the percentage as it
   goes, and says when there is enough of it to start watching - the first stretch is
   ten minutes of film, and the rest arrives while it plays. When the file exists the
   entry becomes the real track, so pressing Play uses it. */
let pickerTimer = null;
async function watchThePicker(meta, saidAs) {
  clearTimeout(pickerTimer);
  const pickers = () => [$("#pl-subs"), $("#subpick")].filter(Boolean);
  const put = (text, value) => {
    pickers().forEach((sel) => {
      let opt = [...sel.options].filter((o) => o.value === "making" ||
                                              o.dataset.making === "1")[0];
      if (!opt) {
        opt = new Option(text, value);
        opt.dataset.making = "1";
        sel.add(opt);
        sel.value = opt.value;
      } else {
        const chosen = sel.value === opt.value;
        opt.textContent = text;
        opt.value = value;
        if (chosen) sel.value = value;
      }
    });
  };
  let state = {};
  try {
    state = await (await fetch("/subs/making")).json();
  } catch (e) { return; }
  const key = String(meta.ratingKey);
  const on = (state.on && (String(state.on.key || "") === key)) ? state.on : null;
  const per = on ? Math.round((on.at || 0) * 100) + "%" : "";
  // is there anything to play yet? the track appears in the title once the file does
  let real = null;
  if (on && on.file) {
    try {
      const fresh = items(await api("/library/metadata/" + key))[0];
      const part = ((mediaOf(fresh, 0) || {}).Part || [{}])[0] || {};
      real = (part.Stream || []).filter(
        (t) => t.streamType === 3 && /ai-gen/i.test(t.title || ""))[0] || null;
      if (real && S && S.meta && String(S.meta.ratingKey) === key) S.meta = fresh;
    } catch (e) { /* ask again in a moment */ }
  }
  if (real) {
    put(saidAs + " ai-gen - ready to play  " + per, String(real.id));
  } else if (on) {
    put(saidAs + " ai-gen (writing… " + per + ")", "making");
  } else {
    // finished, stopped, or never started: leave nothing behind that cannot be played
    [$("#pl-subs"), $("#subpick")].filter(Boolean).forEach((sel) => {
      const opt = [...sel.options].filter((o) => o.dataset.making === "1")[0];
      if (!opt) return;
      const was = sel.value === opt.value;
      opt.remove();
      if (was) sel.value = "";
    });
    return;
  }
  pickerTimer = setTimeout(() => watchThePicker(meta, saidAs), 5000);
}

/* The line in the subtitle panel that says how far the writing has got. It asks for
   itself while the panel is open, and takes itself away when there is nothing to say. */
let ccMakingTimer = null;
async function sayTheMaking(into) {
  clearTimeout(ccMakingTimer);
  let state = {};
  try {
    state = await (await fetch("/subs/making")).json();
  } catch (e) { return; }
  if (!into.isConnected) return;           // the panel was closed
  const key = String((S && S.meta && S.meta.ratingKey) || "");
  const on = state.on;
  const mine = on && (String(on.key || "") === key ||
                      (makingFor && (on.file || "").indexOf(makingFor) === 0));
  const waiting = (state.queued || []).some(
    (j) => String(j.key || "") === key ||
           (makingFor && (j.file || "").indexOf(makingFor) === 0));
  if (mine) {
    into.textContent = "Writing subtitles from the sound: " +
      Math.round((on.at || 0) * 100) + "% - " + (on.what || "");
    // and in the Show list, as though it were already chosen: the film is what
    // somebody is looking at, and this is the subtitle they asked for
    const sel = (into.closest("#ccpanel, #subpanel") || document)
      .querySelector("select.subpick");
    if (sel) {
      const per = Math.round((on.at || 0) * 100) + "%";
      // once the file is a track of its own, the placeholder goes: two entries in
      // the list for one subtitle is two answers to one question
      const ready = [...sel.options].some((o) => /ai-gen/i.test(o.textContent || "") &&
                                                 o.dataset.making !== "1");
      let opt = [...sel.options].filter((o) => o.dataset.making === "1")[0];
      if (ready && opt) {
        opt.remove();                      // the real track is in the list now
      } else if (!ready) {
        if (!opt) {
          // offered, not forced: a film playing with subtitles off stays that way
          // until somebody chooses this
          opt = new Option("", "making");
          opt.dataset.making = "1";
          sel.add(opt, 1);
        }
        // say plainly when it can be started: the first ten minutes of film are
        // enough, and the rest arrives while it plays
        opt.textContent = "Written from the sound - " + per +
          ", not yet enough to start";
      }
    }
  } else if (waiting && on) {
    into.textContent = "Subtitles queued, waiting for " + (on.title || "another film") +
      " at " + Math.round((on.at || 0) * 100) + "%";
  } else {
    into.textContent = "";
  }
  ccMakingTimer = setTimeout(() => sayTheMaking(into), mine || waiting ? 3000 : 10000);
}

/* A film being watched while its subtitle is written: say how far along it is, and
   put it on as soon as there is a line in it. The file grows as the film is heard.  */
let followTimer = null;
async function followTheMaking(meta) {
  clearTimeout(followTimer);
  const name = ((((mediaOf(meta, 0) || {}).Part || [{}])[0].file || "")
    .split(/[\/]/).pop() || "").replace(/\.[^.]+$/, "");
  if (!name) return;
  // A subtitle chosen before pressing play is a decision: this must not overrule it.
  // Only a film playing with none, or with the one being written, has it turned on.
  // it comes on for somebody who asked for it, and for nobody else: Off is a
  // decision as much as English is
  let turnedOn = !wantsTheMade;
  const look = async () => {
    let again = 20000;
    try {
      let state = {};
      try {
        state = await (await fetch("/subs/making")).json();
      } catch (e) { return; }
      if (!S || !S.meta) return;               // the session is a moment behind
      if (String(S.meta.ratingKey) !== String(meta.ratingKey)) return;  // another film
      const on = state.on;
      const mine = on && (String(on.key || "") === String(meta.ratingKey) ||
                          (on.file || "").indexOf(name) === 0);
      const mineWaiting = (state.queued || []).some(
        (j) => String(j.key || "") === String(meta.ratingKey) ||
               (j.file || "").indexOf(name) === 0);
      if (!mine) {
        if (mineWaiting && on) {
          toast("Subtitles queued - waiting for " + (on.title || "another film") +
                ", " + Math.round((on.at || 0) * 100) + "%");
        } else {
          again = 30000;
        }
        return;
      }
      if (turnedOn) { again = 60000; return; }
      // said over the picture only for somebody who asked for it: a number in the
      // corner of a film nobody asked to subtitle is noise
      if (wantsTheMade || !(S && S.subId)) {
        toast("Subtitles " + Math.round((on.at || 0) * 100) + "%  " + (on.what || ""));
      }
      let found = null;
      const picker = $("#pl-subs");
      if (picker) {
        for (const opt of picker.options) {
          if (/ai-gen/i.test(opt.textContent || "")) { found = opt; break; }
        }
      }
      if (!found) {
        // the track appears in the title once the file does
        const fresh = items(await api("/library/metadata/" + meta.ratingKey))[0];
        if (fresh) {
          S.meta = fresh;
          fillPlayerSubs();
          for (const opt of ($("#pl-subs") || { options: [] }).options) {
            if (/ai-gen/i.test(opt.textContent || "")) { found = opt; break; }
          }
        }
      }
      if (!found) return;
      if (!(S.pos > 1)) {
        // changing a track on a picture with subtitles burned into it needs a fresh
        // stream, and that stream begins where the film is now - at nought it would
        // begin again
        again = 8000;
        return;
      }
      turnedOn = true;
      const picked = $("#pl-subs");
      picked.value = found.value;
      picked.dispatchEvent(new Event("change"));
      toast("Subtitles from the sound are on");
      again = 60000;
    } catch (e) {
      // a slow line, a title that would not load: say nothing and ask again
    } finally {
      clearTimeout(followTimer);
      followTimer = setTimeout(look, again);
    }
  };

  look();
}

/* How far the machine has got with the soundtrack. Asked while there is a job, and
   the list is drawn again when one finishes, because a new file has appeared. */
let makingTimer = null;
//: whether this browser asked for the one that is being written, so it is turned on
//: here and not on every screen that happens to have the list open
let makingAsked = false;
//: the file the open list belongs to, so a failure about another film is not reported
//: against this one
let makingFor = "";
//: whether somebody chose the subtitle being written before it existed, so it comes
//: on when it does - a film playing with none stays with none unless they did
let wantsTheMade = false;
async function watchTheMaking(say, redraw) {
  clearTimeout(makingTimer);
  let state = {};
  try {
    state = await (await fetch("/subs/making")).json();
  } catch (e) { return; }
  if (!say || !say.isConnected) return;
  // asked again while there is nothing running, so a job somebody else started - or
  // this one, before the panel was reopened - shows up by itself
  if (!state.on) {
    makingTimer = setTimeout(() => watchTheMaking(say, redraw), 8000);
  }
  // a job for another film is not this film's business: the list showed its
  // percentage against whatever was open
  const forThisOne = state.on && makingFor &&
    (state.on.file || "").indexOf(makingFor) === 0;
  // this film waiting its turn: one at a time, and the wait is the other one's
  // percentage
  const waiting = makingFor && (state.queued || []).some(
    (j) => (j.file || "").indexOf(makingFor) === 0);
  if (waiting && state.on) {
    say.textContent = "queued - waiting for " + (state.on.title || "another film") +
                      ", " + Math.round((state.on.at || 0) * 100) + "%";
    makingTimer = setTimeout(() => watchTheMaking(say, redraw), 4000);
    return;
  }
  if (state.on && !forThisOne) {
    say.textContent = "written from the sound of this file";
    makingTimer = setTimeout(() => watchTheMaking(say, redraw), 4000);
    return;
  }
  if (state.on) {
    say.textContent = Math.round((state.on.at || 0) * 100) + "% - " +
                      (state.on.what || "") + "  ·  ";
    // stopping it is the whole point of showing what it is doing: a film put on by
    // mistake holds the card for half an hour
    const stop = document.createElement("a");
    stop.href = "#";
    stop.className = "stopgen";          // red: it throws away what has been done
    stop.textContent = "stop";
    stop.onclick = async (e) => {
      e.preventDefault();
      e.stopPropagation();
      await fetch("/subs/stop", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
      watchTheMaking(say, redraw);
    };
    say.appendChild(stop);
    makingTimer = setTimeout(() => watchTheMaking(say, redraw), 3000);
    return;
  }
  const last = (state.done || [])[(state.done || []).length - 1];
  if (last && last.ok) {
    say.textContent = "completed";
    // it exists now: read the title again so the new track is in the picker, and
    // turn it on, the same as a subtitle that has just been downloaded
    if (last.file && window.S && S.meta && makingAsked) {
      makingAsked = false;
      const fresh = items(await api("/library/metadata/" + S.meta.ratingKey))[0];
      if (fresh) {
        S.meta = fresh;
        const picker = $("#pl-subs");
        if (picker) {
          for (const opt of picker.options) {
            if ((opt.textContent || "").indexOf("ai-gen") >= 0) {
              picker.value = opt.value;
              picker.dispatchEvent(new Event("change"));
              break;
            }
          }
        }
      }
    }
    if (redraw) redraw();
  } else if (last && makingAsked && makingFor &&
             (last.file || "").indexOf(makingFor) === 0) {
    // a failure is worth reporting only when it is about the file this list is for
    say.textContent = last.what || "it did not work";
  } else if (state.can === false) {
    say.textContent = "not set up on this server";
  }
}

async function downloadSubtitles(box, redraw, lang, about) {
  if (!about && (!S || !S.meta)) return;
  // Held, not read again. Everything below happens after an await or on a click, and
  // the film can be closed in between - which left the list reading the meta of a
  // session that no longer existed. On a title's own page there is no session at all,
  // and the title is handed in.
  let meta = about || S.meta;
  const mi = about ? 0 : S.mi;
  // the language is the viewer's, kept by the server with the rest of their subtitle
  // settings - so it is the same on the television and in here
  // A language passed in is a button somebody pressed in this panel: it says which
  // subtitles to list now, not which language they read. Writing it down as the
  // standing default meant fetching one Swedish subtitle stopped English being the
  // language on every screen in the house.
  let want = lang;
  if (!want) {
    try {
      const said = await (await fetch("/settings")).json();
      want = said.language || said.subLang;
    } catch (e) { /* fall through to what this browser remembers */ }
    want = want || prefs().subLang || "en";
    // only what the server said, or what this browser already believed: either way
    // it is the standing answer rather than a new one
    setPref("subLang", want);
  }
  // whatever was listed before belongs to another language
  box.querySelectorAll(".sublist, .langrow").forEach((n) => n.remove());

  // the language first: a subtitle is downloaded per language and they sit side by
  // side, so this is a choice rather than a setting to change elsewhere
  const langs = document.createElement("div");
  langs.className = "subrow langrow";
  langs.innerHTML = "<span class='sublabel'>Language</span>";
  SUBLANGS.forEach(([code, name]) => {
    const b = document.createElement("button");
    b.className = "btn ghost kind" + (code === want ? " on" : "");
    b.textContent = name;
    b.onclick = () => downloadSubtitles(box, redraw, code);
    langs.appendChild(b);
  });
  box.appendChild(langs);
  // Which release a subtitle is cut to is the whole question here, and it cannot be
  // answered without the name of the file it is being chosen for.
  const mine = (((mediaOf(meta, 0) || {}).Part || [{}])[0].file || "")
    .split(/[\\/]/).pop();
  makingFor = (mine || "").replace(/\.[^.]+$/, "");
  if (mine) {
    const says = document.createElement("div");
    says.className = "subrow langrow";
    says.innerHTML = "<span class='sublabel'>This file</span>" +
      "<span class='dim filename'>" + esc(mine) + "</span>";
    box.appendChild(says);
  }

  const list = document.createElement("div");
  list.className = "subrow sublist";
  // A job started before this panel was opened is still a job: the percentage has to
  // appear on its own, without anybody pressing anything.
  list.dataset.watch = "1";
  list.innerHTML = "<span class='sublabel'>Searching\u2026</span>";
  box.appendChild(list);
  let found = {};
  try {
    // this one belongs to the server itself rather than to the library, so it is
    // asked for directly: api() would put /local in front and unwrap a MediaContainer
    // that is not there
    found = await (await fetch("/subs/find?key=" +
      encodeURIComponent(meta.ratingKey) + "&lang=" + encodeURIComponent(want))).json();
  } catch (e) {
    found = { error: String(e).slice(0, 120) };
  }
  const results = found.results || [];
  list.innerHTML = "";
  if (!results.length) {
    list.innerHTML = "<span class='sublabel'></span><span class='dim'>" +
      esc(found.error || "Nothing found for this one.") + "</span>";
  }
  // The last entry in the list, in the language the list is showing: when nobody has
  // published one that fits this release, the soundtrack is still there to listen to.
  const madeSay = document.createElement("small");
  const saidAs = (SUBLANGS.find(([code]) => code === want) || [want, want])[1];
  const mk = document.createElement("button");
  mk.className = "btn ghost kind";
  // one already written for this file in this language: say so rather than offering
  // to write the same thing again
  const already = ((mediaOf(meta, 0) || {}).Part || [{}])[0].Stream || [];
  const made = already.filter((t) => t.streamType === 3 && t.external &&
    /ai-gen/i.test(t.title || "") &&
    String(t.language || "").slice(0, 2) === want.slice(0, 2))[0];
  // one whose last line lands well before the end is still being written, or was
  // stopped part way: saying "completed" of it is a lie the list can check
  const part = made && made.short;
  mk.textContent = !made ? "Make " + saidAs + " (ai-gen)"
                   : part ? saidAs + " ai-gen, part of it"
                          : saidAs + " ai-gen completed";
  madeSay.textContent = !made ? "written from the sound of this file"
                        : part ? "unfinished - press to write it again"
                               : "press to write it again";
  mk.appendChild(madeSay);
  mk.onclick = async () => {
    mk.disabled = true;
    let r = {};
    try {
      r = await (await fetch("/subs/make", {
        method: "POST", headers: { "Content-Type": "application/json" },
        // the cache in play, not the first one: a film held twice would otherwise be
      // written down beside the cache nobody is watching
      body: JSON.stringify({ key: meta.ratingKey,
                             mi: (S && S.meta && String(S.meta.ratingKey) ===
                                  String(meta.ratingKey)) ? (S.mi || 0) : 0,
                             language: want }),
      })).json();
    } catch (e) { r = { error: String(e).slice(0, 120) }; }
    mk.disabled = false;
    if (r.error) { madeSay.textContent = r.error; return; }
    makingAsked = true;
    wantsTheMade = true;                   // asked for: it may come on when ready
    // Show it as the chosen subtitle at once, and keep the number on it: there is no
    // file for about a minute, and the first stretch is enough to start watching.
    watchThePicker(meta, saidAs);
    watchTheMaking(madeSay, redraw);
  };
  // Only the two the machine can actually write: it hears any language, but it can
  // only translate into English, and Swedish comes from English through a second
  // model. Offering the rest would be offering a refusal.
  const offerToMake = () => {
    if (want !== "en" && want !== "sv") return;
    list.appendChild(mk);
    watchTheMaking(madeSay, redraw);
  };
  if (!results.length) { offerToMake(); return; }
  // every hit: which release matches is the whole question, so nothing is hidden
  // whichever track is on carries the name of the release it came from
  const picker = $("#pl-subs");
  const onNow = picker && picker.selectedIndex >= 0
    ? (picker.options[picker.selectedIndex].textContent || "") : "";
  results.forEach((r) => {
    const b = document.createElement("button");
    const bare = r.name.replace(/\\.srt$/, "");
    const using = onNow && onNow.indexOf(bare) >= 0;
    // The tick means somebody watched a film through with this subtitle - the server
    // decides that, from the release this series was proved on, which is a different
    // name on every episode. A name that matches the file says where the subtitle came
    // from, not that anybody has found it to be in time with the picture.
    const sure = !!r.confirmed;
    b.className = "btn ghost kind" + (using ? " on" : "") + (sure ? " proved" : "");
    b.innerHTML = (sure ? "<span class='tick'>\u2713</span> " : "") +
      esc(r.name) + "<small>" + (sure ? "confirmed \u00b7 " : "") +
      (using ? "in use \u00b7 " : "") +
      (r.sameName ? "matches this file by name \u00b7 "
        : r.hashOdd ? "hash says this file, name says another \u00b7 "
        : r.fromHash ? "matches this file \u00b7 " : "") +
      r.downloads + " downloads</small>";
    b.onclick = async () => {
      b.disabled = true;
      b.innerHTML = esc(r.name) + "<small>downloading\u2026</small>";
      let out = {};
      try {
        out = await (await fetch("/subs/get", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key: meta.ratingKey, id: r.id,
                                 language: r.language || want, release: r.name })
        })).json();
      } catch (e) { out = { error: String(e).slice(0, 120) }; }
      if (!out.ok) {
        b.disabled = false;
        b.innerHTML = esc(r.name) + "<small>" + esc(out.error || "did not work") +
          "</small>";
        return;
      }
      // the file is a track now: read the title again, then turn it on
      const fresh = items(await api("/library/metadata/" + meta.ratingKey))[0];
      // the library's answer replaces both: the session's, so the player sees the
      // new track, and the one this list is working from
      if (fresh) { meta = fresh; if (S) S.meta = fresh; }
      fillPlayerSubs();
      // The one that was taken. A film with three subtitles beside it turned on
      // whichever the library happened to list last, which was rarely this one.
      const picker = $("#pl-subs");
      const bare = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
      const want = bare(out.file || r.name);
      const opts = Array.from(picker.options);
      const took = opts.find((o) => {
        const had = bare(o.textContent);
        return had && want && (had.indexOf(want) >= 0 || want.indexOf(had) >= 0);
      }) || opts[opts.length - 1];
      if (took) {
        picker.value = took.value;
        picker.dispatchEvent(new Event("change"));
      }
      list.remove();
      redraw();
      toast("Subtitles on: " + (out.file || r.name));
    };
    list.appendChild(b);
  });
  offerToMake();
}

/**
 * Tell the server which subtitle was chosen for this title.
 *
 * It comes back at the top of the list next time, already selected - which is the
 * whole point of having found, once, which of three files is in time with the film.
 */
function rememberPick(track) {
  if (!S || !S.meta) return;
  // A file beside the video is remembered by its name and a track inside the film by
  // its number. Sending nothing for the second meant "subtitles off", so choosing the
  // English track inside a film was recorded as choosing none - and the next time the
  // film opened, nothing had been chosen.
  const name = !track ? ""
    : track.external ? track.label
    : (track.index === undefined ? "" : "t" + track.index);
  fetch("/subs/pick", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: S.meta.ratingKey, name: name }),
  }).catch(() => { /* a preference, not a requirement */ });
}


/**
 * The player shows one panel at a time.
 *
 * Four of them open from the same row of buttons, in the same corner. Pressing a
 * second while the first is up used to leave both there, one over the other.
 */
/* The ceiling this viewer is under: the server's, for the side of the door they
   are on, and their own if they have set one. Read once per page. */
let CAPS = null;

async function loadCaps() {
  try {
    const said = await (await fetch("/settings")).json();
    const cap = (said.quality || {})[said.here || "away"] || {};
    const mine = said.mine || {};
    CAPS = {
      here: said.here || "away",
      // whichever is lower, and 0 for no limit at all
      mbit: [cap.mbit || 0, mine.mbit || 0].filter(Boolean).sort((a, b) => a - b)[0] || 0,
      serverMbit: cap.mbit || 0,
      ownMbit: mine.mbit || 0,
    };
  } catch (e) { CAPS = null; }
  markCapped();
}

/**
 * Grey out - in red - the qualities that will not be honoured.
 *
 * Asking for eight megabits under a four-megabit ceiling is not refused; it is
 * quietly reduced, which looks like the choice being ignored. Better to say which
 * ones are out of reach, and where the limit lives.
 */
function markCapped() {
  const pick = document.getElementById("q");
  if (!pick || !CAPS || !CAPS.mbit) return;
  const owner = !(CFG && CFG.guest);
  [...pick.options].forEach((opt) => {
    const asked = (+opt.value || 0) / 1000;             // the select speaks kbit
    const over = asked === 0 || asked > CAPS.mbit;      // "Original" is over any cap
    opt.classList.toggle("capped", over);
    // and not merely marked: a choice that cannot be honoured should not be
    // offered. The server refuses it as well, for a page opened before the limit
    // was set - a ceiling only the client respects is not a ceiling.
    opt.disabled = over;
    if (!over) { opt.title = ""; return; }
    const whose = CAPS.ownMbit && CAPS.ownMbit <= (CAPS.serverMbit || 1e9)
      ? "You have set yourself a limit of " + CAPS.ownMbit + " Mbit/s"
      : "This server allows " + CAPS.serverMbit + " Mbit/s " +
        (CAPS.here === "home" ? "on this network" : "from where you are watching");
    opt.title = whose + ", so this is sent at " + CAPS.mbit + " Mbit/s. " +
      (owner ? "Settings → Quality raises it."
             : "Ask whoever runs the server to raise it, or raise your own in "
               + "Settings → Quality.");
  });
  // and start on the best one that will actually be sent: choosing a quality that
  // is going to be reduced anyway only costs the encoder work and the viewer time
  if (pick.selectedOptions.length && pick.selectedOptions[0].classList.contains("capped")) {
    const usable = [...pick.options].filter((o) => !o.classList.contains("capped"));
    if (usable.length) {
      pick.value = usable.sort((a, b) => (+b.value || 0) - (+a.value || 0))[0].value;
    }
  }
}

function shutPanels(except) {
  ["subpanel", "ccpanel", "audiopanel", "qpanel"].forEach((id) => {
    if (id === except) return;
    const box = document.getElementById(id);
    if (box) box.remove();
  });
}

/* A press anywhere else shuts whichever panel is open.
   Where the press landed is read on the way down rather than on the way up: a button
   inside a panel redraws it, and by the time the click has bubbled to the document its
   target is no longer in the page - which read as "outside" and shut the panel the
   press had just used. The buttons that open the panels are left alone; they toggle. */
(function shutOnOutsidePress() {
  // The buttons that open the panels toggle them, so they are not "outside". Nor is
  // play/pause: stopping the film to read what a panel says is the reason somebody
  // opens one, and having it shut in the same press was the fault.
  const KEEP = "#pl-cc, #pl-subcss, #pl-audio, #pl-quality, #c-play";
  let inside = false;
  let swallow = false;
  const PANELS = "#subpanel, #ccpanel, #audiopanel, #qpanel";
  const within = (node) =>
    !!(node && node.closest &&
       (node.closest(".subpanel") || node.closest(PANELS) || node.closest(KEEP)));
  const anyOpen = () => ["subpanel", "ccpanel", "audiopanel", "qpanel"]
    .some((id) => document.getElementById(id));
  const landed = (e) => {
    inside = within(e.target);
    if (inside || !anyOpen()) return;
    // Shut it here, and let nothing else have the press: the picture toggles the film
    // on a click of its own, so dismissing a panel by pressing the film paused it.
    shutPanels(null);
    swallow = true;
    e.stopPropagation();
    if (e.cancelable) e.preventDefault();
  };
  document.addEventListener("mousedown", landed, true);
  document.addEventListener("touchstart", landed, true);
  // a button worked by the keyboard raises a click with no press before it
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") inside = within(document.activeElement);
  }, true);
  document.addEventListener("click", (e) => {
    if (swallow) {                    // the press that shut a panel, and nothing more
      swallow = false;
      e.stopPropagation();
      if (e.cancelable) e.preventDefault();
      return;
    }
    // the press landed inside, or the thing pressed is still inside: either will do,
    // and between them they cover a press that redrew the panel under itself
    if (inside || within(e.target)) return;
    shutPanels(null);
  }, true);
})();

/* How far this film's subtitles are moved by hand, in seconds. Positive is later. */
let SUBNUDGE = 0;

/**
 * What the correction is filed under: this episode, or this film.
 *
 * Not the series. Subtitles in a season are not out by one amount - one episode is
 * half a second late and the next is two seconds early, because they were cut from
 * whatever release each of them came from. Only the ones somebody has corrected are
 * written down, which is a handful.
 */
function shiftKey(meta) {
  const m = meta || (S && S.meta);
  return m ? "l" + m.ratingKey : "";
}

/**
 * Which subtitle the correction belongs to.
 *
 * Two files for one episode are out by two different amounts - that is usually why
 * there are two - so a track inside the film is known by its stream number and a file
 * beside it by its name.
 */
function shiftSub(track) {
  const t = track || currentSubTrack();
  if (!t) return "";
  return t.external ? (t.label || t.key || "") : "t" + t.index;
}

/** The subtitle now on screen, as the film's own list describes it. */
function currentSubTrack() {
  if (!S || !S.meta || !S.subId) return null;
  const all = subOptions(S.meta, S.mi || 0) || [];
  return all.find((t) => String(t.id) === String(S.subId)) || null;
}

/** Read back what somebody - anybody - has already worked out for this subtitle. */
async function loadSubShift(meta, track) {
  SUBNUDGE = 0;
  const key = shiftKey(meta);
  if (!key) return;
  try {
    const said = await (await fetch("/settings?key=" + encodeURIComponent(key) +
                                    "&sub=" + encodeURIComponent(shiftSub(track)) +
                                    "&device=" + DEVICE)).json();
    SUBNUDGE = +(said.subShift || 0);
    // whether the film asked for that number or somebody dialled it in
    SUBSOURCE = said.shiftBy || "";
    AUTOSYNC = !!said.autoSync;
    if ("burnAllowed" in said) BURNOK = !!said.burnAllowed;
    // Nobody has placed this one yet and this viewer asked for it to be done for
    // them: listen to the film and put it in step, quietly, while it plays. Only a
    // file fetched from elsewhere - a track inside the container came with the
    // release and is in step with it already, and measuring one costs minutes of
    // reading the film to be told nought.
    const beside = track || currentSubTrack();
    if (AUTOSYNC && !SUBNUDGE && beside && beside.external) {
      syncSubtitles(beside, true);
    }
  } catch (e) { /* none is a fine answer */ }
}

/* Whether this viewer wants subtitles put in step by themselves. */
let AUTOSYNC = false;
/* Who set the correction in force: "sync" for the film, "hand" for a person. */
let SUBSOURCE = "";
let SYNCING = false;

/**
 * Ask the server to place this subtitle against the film's own sound.
 *
 * It takes a few seconds and happens while the film plays; the answer moves the cues
 * already in hand, so nothing restarts. Quiet mode says nothing when it fails, which
 * is what "do it automatically" should feel like - a subtitle that cannot be placed is
 * left exactly where it was.
 */
/**
 * What the server is already doing to this subtitle, in words.
 *
 * A correction may be one number, a rate, or a number per part of the film - and only
 * the first of those fits in the box beside the plus and minus. The rest have to be
 * said, or a viewer sees a timing of nought over a subtitle the server has quietly put
 * right, and wonders which of them is lying.
 */
function planInWords(parts, howLong) {
  if (!parts || !parts.length) return "";
  const say = (n) => (n > 0 ? "+" : "") + n.toFixed(1) + "s";
  const clockAt = (t) => Math.floor(t / 60) + ":" + String(Math.round(t % 60)).padStart(2, "0");
  if (parts.length > 1) {
    return "steps " + say(parts[0][2]) + parts.slice(1).map(
      (p) => ", then " + say(p[2]) + " from " + clockAt(p[0])).join("");
  }
  const [, rate, shift] = parts[0];
  if (Math.abs(rate - 1) > 1e-9) {
    const ends = (rate - 1) * (howLong || 0) + shift;
    return "drift " + say(shift) + " \u2192 " + say(ends);
  }
  return "static " + say(shift);
}

/** Ask what is stored for this subtitle, without measuring anything. */
async function loadPlan(track) {
  const t = track || currentSubTrack();
  if (!t || !S || !S.meta) return "";
  const index = t.external
    ? -(+((/[?&]n=(\d+)/.exec(t.key || "") || [0, 0])[1]) + 1)
    : t.index;
  try {
    const said = await (await fetch("/subs/plan?" + new URLSearchParams({
      key: S.meta.ratingKey, mi: S.mediaIndex || 0, index: index,
    }))).json();
    return planInWords(said && said.parts, (S.meta.duration || 0) / 1000);
  } catch (e) {
    return "";
  }
}

async function syncSubtitles(track, quiet) {
  const t = track || currentSubTrack();
  if (!t || !S || !S.meta || SYNCING) return null;
  SYNCING = true;
  if (!quiet) toast("Analysing the film\u2026", true);
  try {
    const said = await (await fetch("/subs/sync?" + new URLSearchParams({
      key: S.meta.ratingKey, mi: S.mediaIndex || 0,
      // a file beside the film is a negative number, the same way the player asks
      // for its text: its own address says which of them it is
      index: t.external ? -(+((/[?&]n=(\d+)/.exec(t.key || "") || [0, 0])[1]) + 1)
                        : t.index,
      skey: shiftKey(), sub: shiftSub(t), save: "1",
    }))).json();
    if (said && said.sure) {
      nudgeSubtitles(+said.offset - SUBNUDGE, true);
      // say which of the three it turned out to be, since they mean different things:
      // one number, a rate, or a number per part of the film
      const mode = planInWords(said.parts, (S.meta.duration || 0) / 1000);
      if (!quiet || Math.abs(said.offset) >= 0.2 || (said.parts || []).length > 1) {
        toast(mode ? "Subtitles: " + mode : "Subtitles are already in step");
      }
      return said;
    }
    if (!quiet) toast(said && said.error ? said.error :
                      "Could not place this subtitle against the film");
    return said;
  } catch (e) {
    if (!quiet) noServer("Could not reach the server to do that");
    return null;
  } finally {
    SYNCING = false;
    // the CC panel, if it is open, is holding the answer from before this ran
    if (document.getElementById("ccpanel") && window.__ccDraw) window.__ccDraw();
  }
}

/**
 * Move every cue on screen, without fetching anything.
 *
 * A subtitle cut for another release runs a second or two out and no amount of
 * choosing another file fixes it. The cues the browser is holding have times that can
 * simply be written to, so the correction is instant - and it is remembered for this
 * film, so a track fetched later comes back in step.
 */
function nudgeSubtitles(by, already) {
  if (!already) SUBSOURCE = "hand";
  const to = Math.max(-1800, Math.min(1800, Math.round((SUBNUDGE + by) * 10) / 10));
  const moved = to - SUBNUDGE;
  SUBNUDGE = to;
  // kept by the server, for everybody: the file is out by this much whoever plays it
  const key = shiftKey();
  const track = currentSubTrack();
  if (key && !already) {
    fetch("/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subShift: {
        key: key, seconds: to, sub: shiftSub(track),
        // putting a subtitle right is vouching for it: the server writes the
        // verified mark at the same time
        ...(track && track.external ? { name: track.label } : {}),
        ...(track && !track.external ? { index: track.index } : {}),
      } }),
    }).catch(() => { /* the correction still applies to what is on screen */ });
  }
  const cues = subTrack && subTrack.cues;
  if (cues) {
    for (let i = 0; i < cues.length; i++) {
      cues[i].startTime += moved;
      cues[i].endTime += moved;
    }
  }
  // the browser redraws from the times themselves; nothing else to do
}

/**
 * Which subtitle: the tracks beside and inside the film, and a way to fetch another.
 *
 * CC answers this question and Aa answers how it looks; keeping them apart means
 * changing the size does not mean walking past a list of releases.
 */
async function subtitleTrackPanel() {
  const old = document.getElementById("ccpanel");
  if (old) { old.remove(); return; }
  shutPanels("ccpanel");                 // one at a time, in that corner
  const box = document.createElement("div");
  box.id = "ccpanel";
  box.className = "subpanel";
  box.tabIndex = -1;

  const draw = () => {
    const wasAt = box.scrollTop;
    box.innerHTML = "";
    // so a measurement that finishes while this is open can say so here - and so can
    // a subtitle that earns its mark as the credits are reached
    window.__ccDraw = draw;
    const head = document.createElement("div");
    head.className = "subhead";
    // an episode is not a film; and when the release counts the episodes differently
    // from the season, say by how much - the number in the filename and the number on
    // the screen disagree by exactly that, and nothing is wrong
    const here = S && S.meta;
    const ep = here && here.type === "episode";
    const shift = (here && here.numberShift) || 0;
    head.innerHTML = "<h4>Subtitles for this " + (ep ? "episode" : "film") + "</h4>" +
      (shift ? "<span class='shift' title='The scanner put these episodes on the " +
               "numbers their titles say. Settings → Library → Episode " +
               "numbering has the last word.'>File numbers " +
               (shift > 0 ? "+" : "") + shift + " auto shifted</span>" : "");
    box.appendChild(head);
    // The release this copy is, because that is what a subtitle has to match: two
    // files for the same episode are out by two different amounts, and the name is
    // the only thing that says which one a subtitle was cut for. Looking for it
    // meant leaving the player.
    const md = mediaOf(S && S.meta, S && S.mi);
    const named = md ? versionFile(md) : "";
    if (named) {
      const which = document.createElement("div");
      which.className = "note playing";
      which.title = named;
      which.textContent = named;
      box.appendChild(which);
    }
    // A subtitle being written from the sound of this film, said here as well: this
    // is the panel somebody opens when the words are missing.
    const madeLine = document.createElement("div");
    madeLine.className = "note making";
    box.appendChild(madeLine);
    sayTheMaking(madeLine);

    const picker = $("#pl-subs");
    if (picker && picker.options.length) {
      // One dropdown rather than a row of pills. A film can carry a dozen tracks with
      // release names forty characters long; as buttons they wrapped over four lines
      // and the panel was mostly filenames.
      // Fetched files first - one was fetched because what came with the film would
      // not do - then this browser's language, then a full track before a forced one
      // or one that describes sounds. The container's own order is whatever the
      // encoder felt like.
      const want = (navigator.language || "en").slice(0, 2).toLowerCase();
      const rank = (t) => [
        t.external ? 0 : 1,
        (t.code || t.lang || "").slice(0, 2).toLowerCase() === want ? 0 : 1,
        t.forced ? 1 : 0,
        t.sdh ? 1 : 0,
      ];
      const known = subOptions(S.meta, S.mi).slice().sort((a, b) => {
        const x = rank(a), y = rank(b);
        for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] - y[i];
        return 0;
      });
      const row = document.createElement("div");
      row.className = "subrow tracks";
      row.innerHTML = "<span class='sublabel'>Show</span>";
      const sel = document.createElement("select");
      sel.className = "subpick";
      const none = new Option("Off", "");
      none.selected = !S || !S.subId;
      sel.add(none);
      known.forEach((track) => {
        const opt = [...picker.options].filter(
          (o) => String(o.value) === String(track.id))[0];
        if (!opt) return;
        const name = track ? (track.label || opt.textContent) : opt.textContent;
        const kind = !track ? "" : isTextSub(track) ? "  · text" : "  · burned in";
        // a disc for a track that came inside the film, as the app marks them
        const from = track && !track.external ? "💿 " : "";
        const o = new Option(from + (track && track.confirmed ? "✓ " : "") +
                             name + kind, opt.value);
        o.selected = opt.selected;
        sel.add(o);
      });
      sel.onchange = () => {
        if (sel.value === "making") {
          // chosen before it exists: it comes on by itself when there is enough of it
          wantsTheMade = true;
          toast("It will come on when there is enough of it to read");
          return;
        }
        wantsTheMade = false;
        if (sel.value === "") {
          picker.value = "";
          picker.dispatchEvent(new Event("change"));
        } else {
          picker.value = sel.value;
          picker.dispatchEvent(new Event("change"));
        }
        setTimeout(draw, 400);
      };
      row.appendChild(sel);

      // the two things that can be done to whichever one is showing
      const chosen = known.filter((t) => String(t.id) === sel.value)[0];
      if (chosen) {
        const ok = document.createElement("button");
        ok.className = "btn ghost mark" + (chosen.confirmed ? " tick" : "");
        ok.textContent = "✓";
        ok.title = chosen.confirmed ? "Verified - press to take it back"
                                    : "This one is in time - mark it verified";
        ok.onclick = async () => {
          const said = await (await fetch("/subs/verify", { method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              key: S.meta.ratingKey,
              name: chosen.confirmed || !chosen.external ? "" : chosen.label,
              index: chosen.confirmed || chosen.external ? undefined : chosen.index,
              language: chosen.code || "en" }) })).json();
          if (!said.ok) return toast(said.error || "Could not mark it");
          await refreshTracks();
          toast(said.verified ? "Verified: " + said.verified : "No longer verified");
        };
        row.appendChild(ok);
        if (chosen.external) {
          const no = document.createElement("button");
          no.className = "btn ghost mark drop";
          // a bin, not a cross: a cross beside a chooser reads as "close this"
          no.innerHTML = BIN_ICON;
          no.title = "Delete this subtitle file";
          const remove = async () => {
            const out = await (await fetch("/subs/remove", { method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ key: S.meta.ratingKey, name: chosen.label,
                                     mi: S.mi || 0 }) })).json();
            if (!out.ok) return toast(out.error || "Could not delete it");
            attachSidecar(null);
            await refreshTracks();
            toast("Deleted " + out.removed);
          };
          // deleting a file cannot be undone, so it is asked for twice
          no.onclick = () => {
            row.innerHTML = "<span class='sublabel'>Delete</span>";
            const name = document.createElement("span");
            name.className = "note";
            name.textContent = chosen.label;
            const yes = document.createElement("button");
            yes.className = "btn ghost kind danger";
            yes.textContent = "Delete file";
            yes.onclick = remove;
            const nope = document.createElement("button");
            nope.className = "btn ghost kind";
            nope.textContent = "Cancel";
            nope.onclick = draw;
            row.appendChild(name);
            row.appendChild(yes);
            row.appendChild(nope);
          };
          row.appendChild(no);
        }
      }
      box.appendChild(row);
    }
    // nothing on the file that fits? go and get one
    const getRow = document.createElement("div");
    getRow.className = "subrow";
    getRow.innerHTML = "<span class='sublabel'>Get</span>";
    const get = document.createElement("button");
    get.className = "btn ghost kind";
    get.textContent = "Download\u2026";
    get.onclick = () => downloadSubtitles(box, draw);
    getRow.appendChild(get);

    // Timing, on the same line as Download: a subtitle cut for another release runs
    // a second or two out, and a tenth of a second a press puts it right. The number
    // itself is the way back to nought.
    const shown = () => (SUBNUDGE > 0 ? "+" : "") + SUBNUDGE.toFixed(1) + "s";
    const label = document.createElement("span");
    label.className = "sublabel";
    label.textContent = "Timing";
    const at = document.createElement("button");
    at.className = "btn ghost kind";
    at.title = "Back to where the file has it";
    at.textContent = shown();
    const step = (by) => { nudgeSubtitles(by); at.textContent = shown(); };
    at.onclick = () => step(-SUBNUDGE);
    const earlier = document.createElement("button");
    earlier.className = "btn ghost kind";
    earlier.textContent = "−";
    earlier.title = "Earlier by a tenth of a second";
    earlier.onclick = () => step(-0.1);
    const later = document.createElement("button");
    later.className = "btn ghost kind";
    later.textContent = "+";
    later.title = "Later by a tenth of a second";
    later.onclick = () => step(0.1);
    const timeRow = document.createElement("div");
    timeRow.className = "subrow";
    timeRow.appendChild(label);
    timeRow.appendChild(earlier);
    timeRow.appendChild(at);
    timeRow.appendChild(later);

    // What the server is applying to this subtitle. Built before the buttons, so
    // they can ask it to say so again the moment they have finished - a measurement
    // takes seconds, and a row refreshed on a timer says "none" over a correction
    // that landed a moment later.
    const inForce = document.createElement("div");
    inForce.className = "subrow inforce";
    const sayPlan = async () => {
      let said = await loadPlan();
      if (!said && SUBNUDGE) {
        // the same number means different things depending on who put it there
        const by = SUBSOURCE === "sync" ? "static " : "by hand ";
        said = by + (SUBNUDGE > 0 ? "+" : "") + SUBNUDGE.toFixed(1) + "s";
      }
      inForce.innerHTML = "<span class='sublabel'>Sync</span><span class='note'>" +
        esc(said || "none") + "</span>";
      // beside what it says, the way back to what the file itself has
      if (said) inForce.appendChild(undo);
    };

    // Do it for me: the film's own sound says where the dialogue is, and the subtitle
    // is placed against it. The button does it now; the box does it from now on,
    // whenever a subtitle arrives with nobody's correction on it yet.
    const auto = document.createElement("button");
    auto.className = "btn ghost kind";
    auto.textContent = "Sync to film";
    auto.title = "Analyse the film and put this subtitle in step";
    auto.onclick = async () => {
      auto.disabled = true;
      // the mark the library wears everywhere else, so a wait of a few seconds
      // plainly belongs to Palladium rather than to the film
      auto.innerHTML = "<b class='pmark'>P</b> Analysing\u2026";
      await syncSubtitles(null, false);
      auto.disabled = false;
      auto.textContent = "Sync to film";
      at.textContent = shown();
      await sayPlan();
    };
    // and the way back: whatever the server is applying, and whatever anybody has
    // nudged, gone. The file's own timing is always recoverable; a correction is not.
    const undo = document.createElement("button");
    undo.className = "btn ghost kind";
    undo.textContent = "Reset";
    undo.title = "Put this subtitle back where its own file has it";
    undo.onclick = async () => {
      const t = currentSubTrack();
      if (!t || !S || !S.meta) return;
      const index = t.external
        ? -(+((/[?&]n=(\d+)/.exec(t.key || "") || [0, 0])[1]) + 1)
        : t.index;
      try {
        await fetch("/subs/reset", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key: S.meta.ratingKey, mi: S.mediaIndex || 0,
                                 index: index, skey: shiftKey(), sub: shiftSub(t) }),
        });
      } catch (e) { noServer("Could not reach the server to do that"); return; }
      SUBNUDGE = 0;
      SUBSOURCE = "";
      at.textContent = shown();
      // the served file changes when a plan goes, so the cues in hand are stale
      if (t.external) attachSidecar(t);
      await sayPlan();
      toast("Subtitles put back as the file has them");
    };

    const tick = document.createElement("label");
    tick.className = "sublabel autosync";
    tick.title = "Do this by itself for every subtitle that nobody has corrected yet";
    const box2 = document.createElement("input");
    box2.type = "checkbox";
    box2.checked = AUTOSYNC;
    box2.onchange = () => {
      AUTOSYNC = box2.checked;
      fetch("/settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ autoSync: AUTOSYNC }),
      }).catch(() => { /* it still applies to this film */ });
      if (AUTOSYNC && !SUBNUDGE) {
        syncSubtitles(null, false).then(() => {
          at.textContent = shown();
          sayPlan();
        });
      }
    };
    tick.appendChild(box2);
    tick.appendChild(document.createTextNode(" auto"));
    // everything about timing on one line: the tenths, the measurement, and whether
    // it happens by itself
    timeRow.appendChild(auto);
    timeRow.appendChild(tick);
    box.appendChild(getRow);
    box.appendChild(timeRow);

    // a line of its own beneath the buttons, so it never pushes them about
    box.appendChild(inForce);
    sayPlan();
    // and the tenth-of-a-second buttons change what is in force too
    [earlier, later, at].forEach((b) => b.addEventListener("click", () => {
      setTimeout(sayPlan, 60);
    }));
  };

  /** Read the title again - a file has been verified or deleted - and redraw. */
  async function refreshTracks() {
    try {
      const fresh = items(await api("/library/metadata/" + S.meta.ratingKey))[0];
      if (fresh) S.meta = fresh;
    } catch (e) { /* the cache in hand will do */ }
    fillPlayerSubs();
    draw();
  }

  draw();
  $("#player").appendChild(box);

  // left open over a running film, it goes the way the transport bar goes
  let idle = null;
  const bump = () => {
    clearTimeout(idle);
    idle = setTimeout(() => {
      if (S && !$("#video").paused && box.isConnected) box.remove();
    }, 12000);
  };
  ["mousemove", "click", "keydown", "wheel"].forEach((e) =>
    box.addEventListener(e, bump, { passive: true }));
  bump();
}

if ($("#pl-cc")) {
  $("#pl-cc").addEventListener("click", (e) => {
    e.stopPropagation();
    subtitleTrackPanel();
  });
}


/**
 * Which soundtrack: everything the file holds, and which one is playing.
 *
 * Kept apart from the subtitle panel on purpose - they are different questions, and a
 * film with three soundtracks and eleven subtitles is unreadable if they share a list.
 */
function audioPanel() {
  const old = document.getElementById("audiopanel");
  if (old) { old.remove(); return; }
  shutPanels("audiopanel");                 // one at a time, in that corner
  if (!S || !S.meta) return;
  const md = mediaOf(S.meta, S.mi || 0);
  const part = md && md.Part && md.Part[0];
  const tracks = ((part && part.Stream) || []).filter((s) => s.streamType === 2);
  const box = document.createElement("div");
  box.id = "audiopanel";
  box.className = "subpanel";
  box.tabIndex = -1;
  const head = document.createElement("div");
  head.className = "subhead";
  head.innerHTML = "<h4>Soundtrack</h4>";
  box.appendChild(head);

  if (tracks.length < 2) {
    const one = document.createElement("div");
    one.className = "note";
    one.textContent = tracks.length
      ? "This film has one soundtrack: " + soundName(tracks[0])
      : "Nothing is known about this film's sound.";
    box.appendChild(one);
  }
  tracks.forEach((t, n) => {
    const row = document.createElement("button");
    // the one playing: what was chosen, or the one the server plays when nobody
    // has - English where the file has it, not simply the first
    const playing = (S.atrack !== undefined && S.atrack !== null)
      ? t.index === S.atrack
      : (tracks.some((x) => x.selected) ? !!t.selected : n === 0);
    row.className = "subrow track" + (playing ? " on" : "");
    row.innerHTML = "<span class='name'></span><span class='note'></span>";
    row.querySelector(".name").textContent = soundName(t);
    row.querySelector(".note").textContent = playing ? "playing" : "";
    row.onclick = () => {
      box.remove();
      if (playing) return;
      const at = Math.floor($("#video").currentTime || 0) + (S.timeBase || 0);
      const meta = S.meta, mi = S.mi || 0;
      const o = opts();
      o.atrack = t.index;
      // the sound is chosen while ffmpeg reads the file, so this is a new stream -
      // started where the old one had got to
      play(meta, at, o, false, mi);
    };
    box.appendChild(row);
  });
  $("#player").appendChild(box);
}

/** A language code as a person would say it: "eng" is English, "fre" is French. */
const LANG_NAMES = {
  eng: "English", swe: "Swedish", nor: "Norwegian", dan: "Danish", fin: "Finnish",
  ger: "German", deu: "German", fre: "French", fra: "French", spa: "Spanish",
  ita: "Italian", por: "Portuguese", pol: "Polish", rus: "Russian", jpn: "Japanese",
  kor: "Korean", chi: "Chinese", zho: "Chinese", nld: "Dutch", dut: "Dutch",
  ara: "Arabic", hin: "Hindi", tur: "Turkish", ces: "Czech", cze: "Czech",
  hun: "Hungarian", gre: "Greek", ell: "Greek", heb: "Hebrew", tha: "Thai",
  ukr: "Ukrainian", ron: "Romanian", rum: "Romanian", und: "",
};
function langName(code) {
  const c = (code || "").toLowerCase().slice(0, 3);
  return LANG_NAMES[c] !== undefined ? LANG_NAMES[c] : c.toUpperCase();
}

/** "English 5.1 (AC-3)", or as much of that as the file admits to. */
function soundName(t) {
  const lang = langName(t.languageTag || t.language || "");
  const ch = t.channels === 6 ? "5.1" : t.channels === 8 ? "7.1"
    : t.channels === 2 ? "stereo" : t.channels === 1 ? "mono" : "";
  const codec = (t.codec || "").toUpperCase().replace("EAC3", "E-AC-3")
    .replace("AC3", "AC-3");
  return [t.title, lang, ch, codec ? "(" + codec + ")" : ""]
    .filter(Boolean).join("  \u00b7  ") || "Soundtrack";
}

if ($("#pl-audio")) {
  $("#pl-audio").addEventListener("click", (e) => {
    e.stopPropagation();
    audioPanel();
  });
}

//: what the player may ask for: the film as it is, or smaller
const QUALITY_SIZES = [[0, "Original"], [1080, "1080p"], [720, "720p"]];
//: and how much line to use. 1080p looks like the file at eight or more; 720p is
//: comfortable at four, and two is the figure that survives a bad hotel.
const QUALITY_RATES = [[0, "As it comes"], [20, "20 Mbit"], [12, "12 Mbit"],
                       [8, "8 Mbit"], [5, "5 Mbit"], [4, "4 Mbit"], [2, "2 Mbit"]];

/**
 * Picture size and megabits, while the film is playing.
 *
 * Changing either is a new stream - ffmpeg is already encoding at the old size - so
 * the film is started again where it had got to, which takes a second or two.
 */
async function qualityPanel() {
  const old = document.getElementById("qpanel");
  if (old) { old.remove(); return; }
  shutPanels("qpanel");                 // one at a time, in that corner
  if (!S) return;
  const box = document.createElement("div");
  box.id = "qpanel";
  box.className = "subpanel";
  box.innerHTML = "<div class='subhead'><h4>Quality</h4>" +
    "<button class='xclose' id='qx' title='Close'>✕</button></div>";
  box.querySelector("#qx").onclick = () => box.remove();

  const restart = (what) => {
    box.remove();
    const at = Math.floor($("#video").currentTime || 0) + (S.timeBase || 0);
    const o = opts();
    o.height = what.height !== undefined ? what.height : (S.height || 0);
    o.mbit = what.mbit !== undefined ? what.mbit : (S.mbit || 0);
    play(S.meta, at, o, false, S.mi || 0);
  };
  const group = (label, list, now, key) => {
    const line = document.createElement("div");
    line.className = "qrow";
    line.innerHTML = "<span class='qlabel'>" + label + "</span>";
    list.forEach(([value, text]) => {
      const b = document.createElement("button");
      b.className = "btn ghost kind" + (now === value ? " on" : "");
      b.textContent = text;
      b.onclick = () => { if (now !== value) restart({ [key]: value }); };
      line.appendChild(b);
    });
    box.appendChild(line);
  };
  group("Picture", QUALITY_SIZES, S.height || 0, "height");
  group("Line", QUALITY_RATES, S.mbit || 0, "mbit");

  const note = document.createElement("div");
  note.className = "note";
  note.textContent = "Original sends the film at the size it was made. Changing "
    + "either starts the stream again from here.";
  box.appendChild(note);
  $("#player").appendChild(box);

  // and what the server will allow, which is the lower of the two
  try {
    const s = await (await fetch("/settings")).json();
    const cap = (s.quality || {})[s.here || "away"] || {};
    if (cap.height || cap.mbit) {
      const said = document.createElement("div");
      said.className = "note";
      said.textContent = "This server allows at most "
        + (cap.height ? cap.height + "p" : "the original size")
        + (cap.mbit ? " and " + cap.mbit + " Mbit/s" : "")
        + " from where you are watching.";
      box.appendChild(said);
    }
  } catch (e) { /* the ceiling is the server's business anyway */ }
}

if ($("#pl-quality")) {
  $("#pl-quality").addEventListener("click", (e) => {
    e.stopPropagation();
    qualityPanel();
  });
}

/** Put one cue where the settings say, counting up from the bottom of the picture. */
/**
 * How much of the picture's height zoom is throwing away at the bottom.
 *
 * Zoom scales the frame until it covers the window, and the browser lays cues out
 * against the whole frame - so at the bottom of the picture they fall outside the
 * window entirely. This is the fraction to lift them by.
 */
/**
 * Where one cue sits, counted from the bottom of the picture.
 *
 * `line` is a percentage of the height, and by default it names the *top* of the cue
 * box: the old fixed six per cent below it was a guess at how tall the text would be,
 * and at the size 100% now means it left the bottom line sitting a twentieth of the
 * screen up. Anchoring the bottom edge instead - lineAlign "end" - makes the number
 * mean what the setting says, and a second line grows upward from the same place
 * rather than off the bottom of the screen.
 *
 * AIR is the daylight left underneath: measured in Chrome at 640x360, a cue at 100
 * puts the tail of a "y" on the last pixel row of the picture, and at 99 leaves three
 * pixels under it. One per cent of the height is the difference between "along the
 * bottom" and "cut off by the edge of the television".
 */
const CUE_AIR = 0.01;

/* How tall a row of subtitle is, as a multiple of the text size.
   Not a choice, a measurement: a browser will not take line-height on ::cue - Chrome
   accepts only a handful of properties there - so this is what it does anyway. Read off
   the player by putting a one-line and a two-line cue at the same place and comparing
   where the top line started: 40 px apart at a 30.3 px font.

   Counting the browser's own rows instead (snapToLines) would be the obvious way to do
   this, and it is wrong: with two lines showing, Chrome puts the cue at the bottom for
   both -1 and -2, so half the settings stop meaning anything for exactly the subtitles
   that need them most. Measured: two-line cue at -1 and at -2, ink 546..607 both. */
const CUE_LEADING = 1.32;

/* One step of the Position setting is one row of text, whatever size the text is and
   however many lines the subtitle happens to be showing - the same as the app, which
   counts in rows because a fraction of the screen means a different amount on every
   screen. The four settings are stored as the fractions they always were, so a screen
   set from an older client still lands on the right step. */
const CUE_STEPS = [0, 0.08, 0.16, 0.28];

/**
 * Which of the offered numbers a stored one belongs to.
 *
 * Not equality: a size or a height can come from an older file, from another client,
 * or from the day 100% changed meaning and every stored size was rescaled to keep the
 * screen looking the same. 0.81 is not 0.8, and a menu with nothing marked at all
 * tells a viewer less than one marked at the nearest peg. Pressing any peg saves that
 * peg's own number, so the first press tidies it up.
 */
/* A waste bin, drawn rather than typed: the cross it replaces is the same glyph the
   panels use for "close", and beside a chooser it read as one. */
const BIN_ICON =
  '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<path d="M3 6h18M9 6V4h6v2M7 6l1 14h8l1-14M10 10v7M14 10v7"/></svg>';

function nearestOption(values, value) {
  let best = 0;
  values.forEach((v, i) => {
    if (Math.abs(v - value) < Math.abs(values[best] - value)) best = i;
  });
  return best;
}

/* How far up the drawn cue layer sits, as a share of the player.

   The same three cases the cue placer answers: the floor step is the bottom of the
   screen whatever shape the film is; off the picture the text sits in the black under
   it, and as low as the screen goes when there is no black to sit in; on the picture
   the number is the share of the picture it was chosen as. Zoom cropping the bottom
   away is added on in each case. */
function subsLayerUp() {
  if ((SUBLOOK.position || 0) >= 0.9) return 0;
  if (SUBLOOK.base === "screen") {
    const rows = Math.min(cueRowsUp(), CUE_STEPS.length - 2);
    return Math.max(0, letterbox() - (rows + 1) * cueRow());
  }
  return SUBLOOK.position || 0;
}

function cueRowsUp() {
  // "very bottom" is not a number of rows; it is handled where the cue is placed
  if ((SUBLOOK.position || 0) >= 0.9) return 0;
  const at = CUE_STEPS.findIndex((s) => Math.abs(s - (SUBLOOK.position || 0)) < 0.005);
  return at >= 0 ? at : Math.round((SUBLOOK.position || 0) / 0.08);
}

/**
 * How tall 100% is, in pixels.
 *
 * Five per cent of a 16:9 picture as wide as the player - not of the player itself. A
 * window taller than 16:9, which is what a phone held upright is, has a picture across
 * a fraction of its height, and a share of the whole window is text the height of a
 * thumb. On a 16:9 screen the two are the same number, which is what a television and
 * a maximised window are.
 */
function cueFontPx() {
  const v = $("#video");
  const tall = (v && v.clientHeight) || window.innerHeight;
  const wide = (v && v.clientWidth) || window.innerWidth;
  return SUB_BASE_VH / 100 * Math.min(tall, wide * 9 / 16) * (SUBLOOK.size || 1);
}

/* A row, as a fraction of the height of the element the cues are placed in. */
function cueRow() {
  const v = $("#video");
  const tall = (v && v.clientHeight) || window.innerHeight;
  return cueFontPx() * CUE_LEADING / tall;
}

/* How much black sits under a letterboxed film, as a fraction of the element. */
function letterbox() {
  const v = $("#video");
  if (!v || !v.videoWidth || !v.videoHeight || !v.clientHeight) return 0;
  if (v.classList.contains("fit-fill")) return 0;      // filled: no bars, only a crop
  const shown = v.clientWidth * (v.videoHeight / v.videoWidth);
  const spare = v.clientHeight - shown;
  return spare > 1 ? (spare / 2) / v.clientHeight : 0;
}

function placeCue(cue) {
  try {
    cue.snapToLines = false;
    // The number is the bottom of the text, and a second line grows upward from it.
    // Chrome places it there of its own accord; lineAlign says so out loud for the
    // browsers that read the specification instead. Testing for the property first
    // was the mistake: Chrome does not expose lineAlign on a cue, so the test failed,
    // a guess at the height of two lines was subtracted, and every subtitle sat two
    // rows above where it was asked to be.
    cue.lineAlign = "end";
    // On screen, the text sits inside the picture and each step lifts it a row: the
    // bar under the film is added on so that nought is the picture's own bottom edge.
    // Off screen, it sits in that bar, and the steps run the other way - the first is
    // as close to the picture as it goes and each one after it is a row further down,
    // which is the direction somebody means when they put the text off the picture.
    const bar = letterbox();
    // Off the picture the list stops two rows down; a deeper number stored from the
    // picture's own list is drawn at that last step rather than shoved to the floor,
    // which is a different setting and reads as one.
    const rowsUp = SUBLOOK.base === "screen"
      ? Math.min(cueRowsUp(), CUE_STEPS.length - 2) : cueRowsUp();
    // one step means neither the picture nor the bar but the panel itself: as low as
    // the screen goes, whatever shape the film is
    const floorIt = (SUBLOOK.position || 0) >= 0.9;
    // How tall this cue is, in shares of the element. One row per line it was written
    // with, plus the rows a long line takes when the browser wraps it: a cue box is
    // 90% of the width, and a character of this face is about half its height wide.
    // Counting only the written lines put a wrapped subtitle a row over the picture.
    const v = $("#video");
    const wide = (v && v.clientWidth) || window.innerWidth;
    const perRow = Math.max(12, Math.floor(0.9 * wide / (cueFontPx() * 0.5)));
    const lines = String(cue.text || "").split(String.fromCharCode(10))
      .reduce((n, one) => n + Math.max(1, Math.ceil(one.length / perRow)), 0);
    const box = lines * cueRow();
    let up;
    if (floorIt) {
      up = CUE_AIR;
    } else if (SUBLOOK.base === "screen") {
      // the whole line below the picture, not merely its last pixel: the top of the
      // text starts at the picture's bottom edge, so nothing overlaps the film
      up = bar - box - rowsUp * cueRow() - CUE_AIR;
      // never off the bottom of the window, however deep the steps go
      up = Math.max(CUE_AIR, up);
    } else {
      // Filling the screen leaves no black: letterbox() is nought, so this is the
      // panel's own bottom edge, which is where the picture now ends. The share zoom
      // crops away is not on the screen at all and lifting the text by it only moved
      // the line up the picture.
      up = rowsUp * cueRow() + bar + CUE_AIR;
    }
    // The number is not a distance from the bottom: the browser slides the box across
    // what is left over, as a percentage background position does, so 100% is flush at
    // the bottom and the gap it leaves is up * (height - the box). Divide by that share
    // and the gap comes out as asked. Measured: at 90 the box sat at 0.9 * (708 - 42.5)
    // in a 708 px element, and a two-line cue at 0.9 * (708 - 85).
    const span = Math.max(0.2, 1 - Math.min(0.5, box));
    cue.line = Math.max(0, Math.min(100, (1 - up / span) * 100));
  } catch (e) { /* a browser that will not be told: leave the cue where it lands */ }
  return cue;
}

/**
 * The statistics line across the top of the player, as the app draws it.
 *
 * Throughput comes from the server rather than the browser: a page cannot see how many
 * bytes a <video> has fetched, but the server counts every byte it sends. Everything
 * else is the element's own account of itself.
 */
let statsTimer = null;
// frames a second, counted rather than declared: the browser keeps a running total of
// what it has shown, so the difference between two ticks is the rate
let frameMark = null;

function stopStats() {
  clearInterval(statsTimer);
  statsTimer = null;
  frameMark = null;                  // the next film starts its own count
  const el = $("#pl-stats");
  if (el) el.textContent = "";
}

function startStats() {
  clearInterval(statsTimer);
  const tick = async () => {
    const el = $("#pl-stats");
    const v = $("#video");
    if (!el || !S) return;
    const bits = [];
    let dropped = 0;
    // Grouped left to right, nearest thing first: where the film is coming from,
    // then what it is, then how it is flowing, then anything wrong with it. Which
    // machine is serving it was never said at all, and it is the first thing worth
    // knowing on a page that can move house mid-film.
    const machine = (CTX && (CTX.name || CTX.origin)) ||
                    (CFG && CFG.serverName) || "";
    if (machine) bits.push(machine);
    bits.push(S.casting ? "casting" : S.direct ? "direct play"
              : S.gpu ? "transcode (NVENC)" : "transcoder");
    if (S.meta) {
      const media = mediaOf(S.meta, S.mi) || {};
      const h = media.height || 0;
      const mbps = Math.round((media.bitrate || 0) / 100) / 10;
      bits.push([h >= 1700 ? "4K" : h ? h + "p" : "",
                 (media.videoCodec || "").toUpperCase(),
                 (media.audioCodec || "").toUpperCase(),
                 // what the version control used to say, now where it is watched
                 mbps ? mbps + " Mbps" : "",
                 media.container || ""].filter(Boolean).join(" "));
    }
    if (v && v.videoWidth) bits.push(v.videoWidth + "x" + v.videoHeight);
    try {
      const q = v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality() : null;
      if (q) {
        const now = performance.now();
        const shown = q.totalVideoFrames - q.droppedVideoFrames;
        if (frameMark && now - frameMark.t >= 2000 && !v.paused) {
          const rate = (shown - frameMark.n) / ((now - frameMark.t) / 1000);
          if (rate > 0.5) frameMark = { t: now, n: shown, fps: rate };
          else frameMark = { t: now, n: shown, fps: frameMark.fps };
        } else if (!frameMark) {
          frameMark = { t: now, n: shown, fps: 0 };
        }
        if (frameMark.fps) bits.push(frameMark.fps.toFixed(1) + " fps");
        dropped = q.droppedVideoFrames || 0;   // said at the end, with the rest
                                               // of what is going wrong
      }
    } catch (e) { /* not every browser keeps count */ }
    try {
      const mine = await (await fetch("/mystream")).json();
      if (mine && mine.mbps) {
        // a rate is a line speed; only the amount that has gone by is in bytes
        bits.push((mine.mbit || mine.mbps * 8).toFixed(1) + " Mbit/s");
        if (mine.peak) bits.push("peak " + (mine.peak * 8).toFixed(1));
        if (mine.mb) bits.push(Math.round(mine.mb) + " MB sent");
      }
    } catch (e) { /* the server is the only one who knows; never mind */ }
    if (v && v.buffered && v.buffered.length) {
      const ahead = Math.round(v.buffered.end(v.buffered.length - 1) - v.currentTime);
      if (ahead > 0) bits.push("buffer " + ahead + "s");
    }
    if (dropped) bits.push("dropped " + dropped);
    // Held to one subtitle at a time; a stray is switched off here and said so. This
    // and the count below were put on the line after it had already been written out,
    // so neither of them has ever appeared on it.
    const strays = oneTrackOnly();
    if (strays) bits.push(strays + " stray subtitle track" + (strays > 1 ? "s" : ""));
    if (subTrack) {
      const held = (subTrack.cues || []).length;
      const box = $("#subs");
      const drawn = box ? box.querySelectorAll(".line").length : 0;
      bits.push("subs " + drawn + " on / " + held + " held");
    }
    el.textContent = bits.filter(Boolean).join("   \u00b7   ");
    // held true rather than set once: whatever clears it, this puts it back
    document.body.classList.toggle(
      "watching", !$("#player").classList.contains("hidden"));
    askAheadForSubtitles();          // five minutes from the end, fetch the next one
    const note = $("#pl-decision");        // the line already says all of this
    if (note) note.textContent = "";
  };
  tick();
  statsTimer = setInterval(tick, 1000);
}

/* Faults the page notices by itself.
   Nobody watching a film should have to describe a stack trace, and mostly cannot, so
   anything that throws goes to the server on its own - once per distinct message, so a
   loop cannot fill the list. */

/* Reporting a fault, or asking for something.
   The people using this are not going to open a terminal, and a message that only
   reaches a log they cannot see is no use to anyone - so it goes to the server, where
   the owner reads it in Settings. */
function reportSomething() {
  const kind = confirm("Is something broken?\n\nOK for a problem, Cancel to ask for " +
                       "something instead.") ? "problem" : "request";
  const text = prompt(kind === "problem"
    ? "What went wrong? Which film, and what happened?"
    : "What would you like Palladium to do?", "");
  if (!text || !text.trim()) return;
  fetch("/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: kind, text: text, app: "web" }),
  }).then(() => toast("Sent - thank you"))
    .catch(() => toast("Could not send that"));
}

/* A guest has no business in the library settings, so those controls are removed
   rather than disabled: what is left is all real. */
function applyGuest() {
  if (!CFG.guest) return;
  // a guest does not choose what somebody else's machine encodes with
  if (engine() !== "gpu" && engine() !== "cpu") setPref("engine", "gpu");
  document.querySelectorAll("#topbar .pref").forEach((el) => {
    if (el.querySelector("#engine")) el.remove();
  });
  // The gear stays: how subtitles look is kept per viewer, and the reports are a
  // noticeboard everybody using this server can read. The page itself decides which
  // tabs a guest is offered, and the reports pane leaves out the owner's controls.
  const gear = document.querySelector('#topbar [data-view="settings"]');
  if (gear) gear.title = "Subtitles, and what has been reported";
  document.body.classList.add("guest");
  // casting is between a person, their phone and their television: the film comes from
  // here either way, so it is not the owner's to withhold
  const caster = $("#castbtn");
  if (caster) caster.classList.remove("hidden");
}

async function init2(stay) {
  CFG = await (await fetch("/config")).json();
  applyGuest();
  // The bar names this machine, and it can only do that once the machine has said
  // what it is called. It was drawn while the page was still loading, so it read
  // "This server" and stayed that way - and on two machines that answer the same
  // pages, not knowing which one you are on is the whole difficulty.
  drawServerPick();
  // A server pointed at nothing has nothing to draw. The wizard takes the screen
  // until there are folders, a key and something scanned - and never again after
  // that, whatever the library ends up holding.
  if (!CTX && window.needsSetup && await window.needsSetup()) {
    window.openSetup();
    return;
  }
  await loadSubtitleLook();
  try {
    const c = await api("/library/sections");
    for (const d of items(c)) if (SECTIONS[d.type] === null) SECTIONS[d.type] = d.key;
  } catch (e) {
    const where = CTX ? "that server" : "the library";
    main.innerHTML = '<div class="empty">Cannot reach ' + esc(where) +
      " - " + esc(e.message) + "</div>";
    return;
  }
  if (!window.__booted) {          // only on the first load, not on a source switch
    window.__booted = true;
    // what is marked, so a star can appear on a card anywhere without asking again
    loadMarks();
    loadFavs();
    castControls();          // Google Cast, or AirPlay, or neither
    reapStaleSessions();
    watchForNewBuild();
    watchForReports();
  }
  // changing the library from Settings should leave you in Settings
  if (!stay) {
    // A link that names a screen opens that screen: the tray icon's "Library
    // settings" points at /#settings/library, and until now it opened the front page
    // like any other link. Drawn instead of home rather than on top of it - home is
    // rendered without waiting, so anything opened after it was drawn over.
    const [where, which] = (location.hash || "").replace(/^#/, "").split("/");
    go("home");
    // and then the screen the address asked for, on top. Not instead of home: the
    // front page draws itself without waiting to be asked, so a screen opened before
    // it was simply drawn over. Twice, because the first draw finishes when it
    // finishes.
    const open = () => {
      if (where === "settings" && window.viewSettings) {
        if (window.settingsTab) settingsTab(which || "library");
        go("settings");
      } else if (where === "reports" && window.viewReports) {
        go("reports");
      }
    };
    // Home draws itself asynchronously and finishes whenever its fetches finish, so
    // one attempt lands under it. Asked again until the screen is actually up.
    if (where === "settings" || where === "reports") {
      let tries = 0;
      const nag = setInterval(() => {
        open();
        const up = document.querySelector(where === "settings"
          ? ".settabs, .setblock" : ".reports, .setblock");
        if (up || ++tries > 12) clearInterval(nag);
      }, 250);
    }
  }
}

/**
 * A number on the gear when somebody has written in.
 *
 * Asked for every couple of minutes and on the way back from anywhere: a request that
 * nobody sees is a request nobody answers.
 */
async function watchForReports() {
  // Reports is a page inside Settings now, so the count goes back on the gear -
  // which is where somebody looks for it when there is no tab of its own.
  const badge = document.querySelector('#topbar [data-view="settings"]');
  if (!badge) return;
  const look = async () => {
    try {
      const d = await (await fetch("/feedback")).json();
      const n = d.unseen || 0;
      badge.classList.toggle("hasnews", n > 0);
      badge.dataset.news = n > 99 ? "99+" : String(n);
      badge.title = n ? n + " new report" + (n > 1 ? "s" : "")
                      : "What is new, faults, and requests";
    } catch (e) { /* the count is a courtesy, not a duty */ }
  };
  look();
  setInterval(look, 120000);
}

/* an old tab left open runs old code and confuses everything it touches */
function watchForNewBuild() {
  const myBuild = CFG.build;
  setInterval(async () => {
    try {
      const now = await (await fetch("/config")).json();
      if (now.build && myBuild && now.build !== myBuild) {
        const note = $("#stale");
        note.classList.remove("hidden");
        document.body.classList.add("has-stale");   // make room, do not cover
        // full screen draws the player and nothing else, so the notice has to live
        // inside it while a film is up - otherwise it is hidden exactly when a page
        // is most likely to have been open long enough to go stale
        const player = $("#player");
        const watching = !player.classList.contains("hidden");
        if (watching && note.parentElement !== player) player.appendChild(note);
        if (!watching && note.parentElement === player) document.body.appendChild(note);
        dbg("stale-page", { mine: myBuild, server: now.build });
      }
    } catch (e) { /* server restarting */ }
  }, 15000);
}

init2();
learnStandby();
learnCopies();
setInterval(learnCopies, 300000);
sayItIsTheCopy();
dressUp();
setInterval(dressUp, 600000);      // the hour turns, and so does the mood
// the server this page is reading, in the bar, and a press away from the others
(() => {
  let was = "";
  try {
    was = localStorage.getItem("pd-on-server") || "";
  } catch (e) { /* nothing remembered */ }
  const f = was ? friendById(was) : null;
  // and only if it is this page's own server. What is remembered here is which
  // machine's shelves were chosen, and it is kept by the browser against the address
  // it was chosen at - so opening the cache, on its own address, restored a choice
  // made on the main server: the bar named the main server, the row for it said "already on
  // it", and every request on the page went to the cache.
  const hereNow = location.origin.replace(/\/$/, "");
  const itsDoors = f
    ? [f.origin].concat(f.alsoAt || []).map((u) => (u || "").replace(/\/$/, ""))
    : [];
  if (f && itsDoors.indexOf(hereNow) >= 0) {
    nowOn({ id: f.id, origin: f.origin, token: f.token });
  } else if (was) {
    try {
      localStorage.setItem("pd-on-server", "");
    } catch (e) { /* a private window keeps nothing */ }
  }
  drawServerPick();
  // what each machine calls itself and how else it can be reached: asked once, so
  // the picker can offer both doors and step over one that has stopped answering
  [null].concat(friends()).forEach(async (one) => {
    try {
      const said = await askWhere(one ? one.origin : location.origin,
                                  one ? one.token : "");
      if (one && said && said.id) {
        const fixed = putRight(one, said);
        if (fixed.machine !== one.machine || fixed.name !== one.name) {
          saveFriends(friends().map((f) => (f.id === one.id ? fixed : f)));
          drawServerPick();
        }
      }
    } catch (e) { /* an older server, or one that is off */ }
  });
  const chip = document.getElementById("srvpick");
  if (chip) chip.onclick = openServerPick;
  document.addEventListener("click", (e) => {
    const box = document.getElementById("srvlist");
    if (!box || box.classList.contains("hidden")) return;
    if (e.target.closest("#srvlist") || e.target.closest("#srvpick")) return;
    box.classList.add("hidden");
  });
})();
