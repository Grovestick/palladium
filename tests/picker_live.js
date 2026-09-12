/* The server list against the machines as they actually answer.
 *
 * Every fault in this list has been in what a server said rather than in how the
 * list was drawn, and a test made of invented answers cannot see those. This asks
 * both machines what they say and builds the list a page on each of their addresses
 * would build.
 *
 *   PD_TOKEN=<a guest token> node tests/picker_live.js
 */
const fs = require('fs');
const src = fs.readFileSync('static/app.js', 'utf8');

function lift(name) {
  let at = src.indexOf('function ' + name + '(');
  if (at < 0) throw new Error('no function ' + name);
  if (src.slice(Math.max(0, at - 8), at).includes('async')) at = src.lastIndexOf('async', at);
  let i = src.indexOf('{', at), d = 0, end = i;
  for (; end < src.length; end++) {
    if (src[end] === '{') d++;
    else if (src[end] === '}') { d--; if (!d) { end++; break; } }
  }
  return src.slice(at, end);
}
const isHomeSrc = src.match(/const isHomeAddress = [\s\S]*?;\n/)[0];

let CFG, DOORS = {};
const machinesOf = new Function('getCFG', 'getDOORS', 'nameOfServer', `
  ${isHomeSrc}
  const doors = getDOORS;
  Object.defineProperty(globalThis, 'CFG', { get: getCFG, configurable: true });
  ${lift('addressesKnownFor')}
  ${lift('pickerMachines')}
  return pickerMachines;
`)(() => CFG, () => DOORS, (f) => (f ? (f.name || f.origin) : (CFG && CFG.serverName)));

const TOK = process.env.PD_TOKEN || '';
async function get(base, path) {
  const url = base + path + (path.includes('?') ? '&' : '?') + 't=' + TOK;
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(path + ' -> ' + r.status);
  return r.json();
}

(async () => {
  const where = (process.env.PD_SERVERS ||
    'http://192.168.0.181:8765,http://82.196.100.58:8765,' +
    'http://192.168.0.25:8764,http://82.196.100.58:8764').split(',');
  const fails = [];

  for (const at of where) {
    let standby, cfg;
    try {
      standby = await get(at, '/standby');
      cfg = await get(at, '/config');
    } catch (e) {
      fails.push(at + ': ' + e.message);
      continue;
    }
    CFG = { serverName: cfg.serverName, key: TOK, guest: !!cfg.guest };
    const kept = String(standby.where || standby.outside || '').replace(/\/$/, '');
    const copy = kept && kept !== at
      ? { name: standby.name || kept, origin: kept,
          alsoAt: [standby.where, standby.outside].filter(Boolean) }
      : null;
    const h = standby.follows || {};
    const homeAt = String(h.lan || h.outside || '').replace(/\/$/, '');
    const home = homeAt && homeAt !== at
      ? { name: h.name || homeAt, origin: homeAt,
          alsoAt: [h.lan, h.outside].filter(Boolean) }
      : null;
    const me = standby.mine || {};
    const got = machinesOf(at, [], copy, home, [me.lan || '', me.outside || '']);

    console.log(String(cfg.serverName || '?').padEnd(9) + ' ' + at +
                (cfg.guest ? '   (as a guest)' : ''));
    got.forEach((m) => console.log('   ' + (m.on ? '*' : ' ') + ' ' +
                                   m.name.padEnd(11) + m.at.join('  ')));

    const on = got.filter((m) => m.on);
    if (on.length !== 1) {
      fails.push(at + ': ' + on.length + ' machines claim to be this page');
    } else if (on[0].name !== cfg.serverName) {
      fails.push(at + ': the open one is called "' + on[0].name +
                 '", the server is "' + cfg.serverName + '"');
    }
    if (got.length < 2) {
      fails.push(at + ': offers no other machine - nothing to press to leave');
    }
    got.filter((m) => !m.on).forEach((m) => {
      if (m.at.every((u) => u === at)) {
        fails.push(at + ': "' + m.name + '" only leads back here');
      }
    });
    [me.lan, me.outside].filter((u) => u && u !== at).forEach((u) => {
      if (!got.some((m) => m.at.includes(u))) {
        fails.push(at + ': its own ' + u + ' is not offered');
      }
    });
  }

  console.log(fails.length ? '\n' + fails.join('\n') + '\n\nFAIL: ' + fails.length
                           : '\nPASS: ' + where.length + ' addresses');
  if (fails.length) process.exitCode = 1;
})();
