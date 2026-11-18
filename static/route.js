/* Finding a server: the rules the app keeps in Route.kt, the same here for the web page, so
 * one handler decides everywhere (owner, 2026-11-15: "web handler need to work same way as
 * app"). Kept function for function with Route.kt - settle, requestMiss, state, rowUp,
 * houseSaysDown, confirmDown, knownEnough, stillSending - and tested in pd-test-web.js
 * against the same simulated cases as the app's ReachSimTest. Pure: no page, no network. */
(function (root) {
  /** How many looks in a row must miss before an address is called down. */
  const MISSES = 2;
  /** an answer older than this is no answer */
  const FRESH_MS = 45000;
  /** a first miss is looked at again this soon */
  const AGAIN_MS = 1700;
  /** a copy's word about its main server counts this long */
  const HOUSE_WORD_MS = 30000;
  /** film arrived from an address this recently: it is up, whatever a look says */
  const SENDING_MS = 10000;

  /** One look that got no answer. */
  function miss(now, id) {
    return { up: false, id: id || "", ms: 0, at: now, misses: 1, follows: "", followsId: "",
             houseDown: false };
  }

  function isDown(d) { return !!d && !d.up && d.misses >= MISSES; }

  /** What is believed after a look: an answer at once, a miss on the second in a row. */
  function settle(had, raw, now) {
    if (raw.up || isDown(raw)) return raw;
    if (!had || now - had.at >= FRESH_MS) return raw;
    const n = had.misses + 1;
    if (had.up) {
      return n >= MISSES ? Object.assign({}, raw, { id: had.id, misses: n })
                         : Object.assign({}, had, { misses: n });
    }
    return Object.assign({}, raw, { id: had.id, misses: Math.min(n, MISSES) });
  }

  /** A request that could not connect: at most the first miss; null for nothing. */
  function requestMiss(had, began, now) {
    if (had && now - had.at < FRESH_MS) {
      if (had.up && had.misses === 0 && had.at >= began) return null;
      if (had.misses >= 1) return null;
    }
    return settle(had, miss(now, had ? had.id : ""), now);
  }

  /** Up, down, or not known (null). */
  function state(d, now) {
    if (!d || now - d.at >= FRESH_MS) return null;
    if (d.up) return true;
    if (isDown(d)) return false;
    return null;
  }

  /** A row of addresses: up on one, down on all, or not known. */
  function rowUp(ways, looks, now) {
    if (ways.some((w) => { const d = looks[w]; return d && now - d.at < FRESH_MS && d.up; })) return true;
    return ways.length && ways.every((w) => state(looks[w], now) === false) ? false : null;
  }

  /** A copy's /where about its main server: down when it did not answer the copy's own asks. */
  function houseSaysDown(up, agoSeconds) {
    return up === false && agoSeconds * 1000 <= HOUSE_WORD_MS;
  }

  /** The copy's verdict makes every address of its main server down that has no clean answer
   *  of its own; opening, the copy answering is enough for an address that missed once. */
  function confirmDown(looks, opening, waysOf, now) {
    const copies = Object.values(looks).filter((d) => d && d.up && d.followsId);
    const verdict = new Set(copies.filter((d) => d.houseDown).map((d) => d.followsId));
    const opened = new Set(opening ? copies.map((d) => d.followsId) : []);
    if (!verdict.size && !opened.size) return looks;
    const out = Object.assign({}, looks);
    verdict.forEach((id) => {
      const keys = (waysOf[id] || []).concat(Object.keys(looks).filter((k) => looks[k] && looks[k].id === id));
      keys.forEach((k) => {
        const d = out[k];
        if (!d) out[k] = { up: false, id: id, ms: 0, at: now, misses: MISSES, follows: "", followsId: "", houseDown: false };
        else if (!(d.up && d.misses === 0)) out[k] = Object.assign({}, d, { up: false, misses: MISSES });
      });
    });
    const theirs = new Set();
    opened.forEach((id) => (waysOf[id] || []).forEach((k) => theirs.add(k)));
    Object.keys(looks).forEach((k) => {
      const d = looks[k];
      if (d && !d.up && d.misses >= 1 && d.misses < MISSES && (opened.has(d.id) || theirs.has(k)))
        out[k] = Object.assign({}, d, { misses: MISSES });
    });
    return out;
  }

  /** Enough to decide before every address has answered. */
  function knownEnough(mainUp, copyUp) {
    return mainUp === true || (mainUp === false && copyUp === true);
  }

  /** Whether film arrived from a server this recently. */
  function stillSending(lastBytesAt, now) {
    return !!lastBytesAt && now - lastBytesAt >= 0 && now - lastBytesAt <= SENDING_MS;
  }

  const ROUTE = { MISSES, FRESH_MS, AGAIN_MS, HOUSE_WORD_MS, SENDING_MS, miss, isDown, settle,
                  requestMiss, state, rowUp, houseSaysDown, confirmDown, knownEnough, stillSending };
  if (typeof module !== "undefined" && module.exports) module.exports = ROUTE;
  else root.ROUTE = ROUTE;
})(typeof window !== "undefined" ? window : this);
