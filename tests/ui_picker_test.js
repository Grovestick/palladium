/* The picker as somebody uses it: open the list, read the buttons, press each one.
 *
 * The real drawing code from static/app.js runs against a small stand-in for a
 * browser, so the buttons are the buttons - the same labels and the same click
 * handlers - and what this prints is where each press takes you.
 */
const fs = require('fs');
const src = fs.readFileSync('static/app.js', 'utf8');

function lift(name) {
  let at = src.indexOf('function ' + name + '(');
  if (at < 0) throw new Error('no function ' + name);
  if (src.slice(Math.max(0, at - 8), at).includes('async')) at = src.lastIndexOf('async', at);
  let i = src.indexOf('{', at), d = 0, end = i;
  for (; end < src.length; end++) {
    if (src[end] === '{') d++; else if (src[end] === '}') { d--; if (!d) { end++; break; } }
  }
  return src.slice(at, end);
}
const isHomeSrc = src.match(/const isHomeAddress = [\s\S]*?;\n/)[0];
const doorKindSrc = src.match(/const doorKind = [\s\S]*?;\n/)[0];

function El(tag) {
  const e = {
    tag, className: '', style: {}, children: [], onclick: null, _html: '', _text: '',
    classList: { s: new Set(), add(c) { this.s.add(c); }, remove(c) { this.s.delete(c); },
                 contains(c) { return this.s.has(c); } },
    appendChild(c) { e.children.push(c); return c; },
    querySelector() { return { className: '' }; },
    getBoundingClientRect() { return { bottom: 0, left: 0 }; },
    parentElement: null,
  };
  Object.defineProperty(e, 'innerHTML', {
    get() { return e._html; }, set(v) { e._html = v; } });
  Object.defineProperty(e, 'textContent', {
    get() { return e._text; }, set(v) { e._text = v; } });
  Object.defineProperty(e, 'label', { get() {
    const t = (e._html || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
    return t || e._text;
  } });
  return e;
}

async function press(page, answers) {
  const box = El('div'), chip = El('div');
  box.classList.add('hidden');   // the page starts with it closed
  const WENT = [], SAID = [];
  const location = {
    origin: page, pathname: '/', search: '',
    reload() { WENT.push('reloads this page'); },
  };
  Object.defineProperty(location, 'href', {
    get() { return page + '/'; },
    set(v) { WENT.push('goes to ' + v.replace(/[?].*$/, '').replace(/\/$/, '')); },
  });
  const document = {
    body: El('body'),
    getElementById: (id) => (id === 'srvlist' ? box : id === 'srvpick' ? chip : null),
    createElement: El, addEventListener() {},
  };
  const names = ['document', 'location', 'localStorage', 'fetch', 'AbortController',
                 'setTimeout', 'CFG', 'CTX', 'standbyWhere', 'standbyOut', 'standbyName',
                 'houseWhere', 'houseOut', 'houseName', 'myLan', 'myOut', 'standbyAlive',
                 'FRIENDS', 'DOORS', 'SAID', 'WENT'];
  const h = answers.standby.follows || {};
  const values = [document, location,
    { s: {}, getItem(k) { return this.s[k] || null; }, setItem(k, v) { this.s[k] = v; } },
    async (u) => ({ ok: answers.up.some((a) => u.startsWith(a)) }),
    class { constructor() { this.signal = null; } abort() {} },
    () => 0,
    answers.cfg, null,
    answers.standby.where || '', answers.standby.outside || '', answers.standby.name || '',
    h.lan || '', h.outside || '', h.name || '',
    (answers.standby.mine || {}).lan || '', (answers.standby.mine || {}).outside || '',
    !!answers.standby.alive,
    answers.friends || [], answers.doors || {}, SAID, WENT];
  const body = `
    const friends = () => FRIENDS, doors = () => DOORS;
    const esc = (s) => String(s);
    const toast = (m) => { SAID.push(m); };
    const nowOn = () => {}, go = () => {}, drawServerPick = () => {};
        ${isHomeSrc}
    ${doorKindSrc}
    function nameOfServer(f) { return f ? (f.name || f.origin) : (CFG && CFG.serverName); }
    const doorOpen = {};
    ${lift('addressesKnownFor')}
    ${lift('answersAt')}
    ${lift('goToServer')}
    ${lift('pickerMachines')}
    ${lift('openServerPick')}
    return openServerPick;
  `;
  const openIt = new Function(...names, body)(...values);
  await openIt();
  const out = [];
  for (const b of box.children.filter((c) => c.tag === 'button')) {
    WENT.length = 0; SAID.length = 0;
    await b.onclick();
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setImmediate(r));
    out.push({ label: b.label, said: SAID.slice(), went: WENT.slice() });
  }
  return out;
}

const MAIN = { lan: 'http://192.168.0.181:8765', wan: 'http://82.196.100.58:8765' };
const CACHE = { lan: 'http://192.168.0.25:8764',  wan: 'http://82.196.100.58:8764' };
const ALL = [MAIN.lan, MAIN.wan, CACHE.lan, CACHE.wan];

const AS_MASTER = { where: CACHE.lan, outside: CACHE.wan, name: 'Cache', follows: {},
                    mine: { lan: MAIN.lan, outside: MAIN.wan, name: 'Main' } };
const AS_COPY = { where: '', outside: '', name: '',
                  follows: { lan: MAIN.lan, outside: MAIN.wan, name: 'Main' },
                  mine: { lan: CACHE.lan, outside: CACHE.wan, name: 'Cache' } };
// at home every address answers; from a train only the two the router forwards do,
// which is the case that matters and the one nobody can try from the sofa
const HOME = ALL, AWAY = [MAIN.wan, CACHE.wan];
// a row of yours that is really this machine under another machine's name: added
// from a link that was handed on, or written while the cache answered for the main
const MISLABELLED = { id: 'x1', name: 'Main', origin: CACHE.wan };
const pages = [
  ['Main', MAIN.lan, AS_MASTER, HOME, 'at home'],
  ['Main', MAIN.wan, AS_MASTER, AWAY, 'away'],
  ['Cache', CACHE.lan, AS_COPY, HOME, 'at home'],
  ['Cache', CACHE.wan, AS_COPY, AWAY, 'away'],
  ['Cache', CACHE.wan, AS_COPY, AWAY, 'away, with a row of mine mislabelled Main',
   [MISLABELLED]],
];

(async () => {
  let bad = 0;
  for (const [name, at, standby, up, mood, mine] of pages)
  for (const guest of [false, true]) {
    console.log(`\nStanding on ${name}, at ${at}`);
    const pressed = await press(at, { cfg: { serverName: name, key: 'T' },
                                      standby, up, friends: mine || [] });
    let leaves = 0;
    for (const p of pressed) {
      const goes = p.went.find((w) => w.startsWith('goes to'));
      const what = goes || p.said[p.said.length - 1] || p.went[0] || '(nothing happened)';
      console.log(`  press "${p.label}"`.padEnd(52) + what);
      if (/^Already on /.test(p.said[0] || '') &&
          !(p.said[0] || '').includes(name)) {
        console.log('      ^^ says you are already on a machine you are not on');
        bad++;
      }
      if (goes && !goes.endsWith(at)) {
        leaves++;
        const to = goes.replace('goes to ', '');
        if (!up.includes(to)) {
          console.log('      ^^ sent to an address that does not answer from here');
          bad++;
        }
      }
    }
    if (!leaves) { console.log('      ^^ nothing here leads to the other machine'); bad++; }
    // this machine's own other address has to be pressable, or opening it from
    // outside leaves no way back to the address on the network
    const me = standby.mine || {};
    [me.lan, me.outside].filter((u) => u && u !== at).forEach((u) => {
      const short = u.replace(/^https?:\/\//, '');
      if (!pressed.some((p) => p.label.includes(short))) {
        console.log('      ^^ its own other address ' + short + ' is not offered');
        bad++;
      }
    });
  }
  console.log(bad ? `\nFAIL: ${bad} page(s) with no way out` : '\nPASS');
  if (bad) process.exitCode = 1;
})();
