/* The server list, decided. Every page against every shape of what a browser might
 * be holding: a stored row for the other machine or none, the server naming its cache
 * or its main or neither, and the browser's note of which machine answers where both
 * right and wrong. The deciding function is lifted out of static/app.js rather than
 * copied, so this fails if the real one changes shape.
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

const MAIN_LAN = 'http://192.168.0.181:8765', MAIN_WAN = 'http://82.196.100.58:8765';
const CACHE_LAN = 'http://192.168.0.25:8764',  CACHE_WAN = 'http://82.196.100.58:8764';
const MAIN = [MAIN_LAN, MAIN_WAN], CACHE = [CACHE_LAN, CACHE_WAN];

const fails = [];
let cases = 0;

for (const at of [MAIN_LAN, MAIN_WAN, CACHE_LAN, CACHE_WAN]) {
  const mine = MAIN.includes(at) ? 'Main' : 'Cache';
  const other = mine === 'Main' ? 'Cache' : 'Main';
  const theirs = mine === 'Main' ? CACHE : MAIN;
  const ours = mine === 'Main' ? MAIN : CACHE;
  CFG = { serverName: mine, key: 'T' };
  const stored = { id: 'f1', name: other, origin: theirs[0],
                   machine: mine === 'Main' ? 'cache' : 'main', alsoAt: theirs };
  const named = { name: other, origin: theirs[0], alsoAt: theirs };
  const rightDoors = { main: { lan: MAIN_LAN, outside: MAIN_WAN },
                       cache: { lan: CACHE_LAN, outside: CACHE_WAN } };
  const wrongDoors = { main: { lan: CACHE_LAN, outside: CACHE_WAN },
                       cache: { lan: MAIN_LAN, outside: MAIN_WAN } };

  for (const [how, table] of [['right', rightDoors], ['wrong', wrongDoors]]) {
    DOORS = table;
    for (const list of [[], [stored]]) {
      for (const says of [null, named]) {
        cases++;
        const cache = mine === 'Main' ? says : null;
        const home = mine === 'Main' ? null : says;
        const got = machinesOf(at, list, cache, home, ours);
        const where = 'page ' + at + ' stored=' + list.length +
                      ' named=' + (says ? 'yes' : 'no') + ' doors=' + how;

        const on = got.filter((m) => m.on);
        if (on.length !== 1) {
          fails.push(where + ': ' + on.length + ' machines say "already on"');
        } else {
          if (!on[0].at.includes(at)) {
            fails.push(where + ': the open one does not hold this address');
          }
          if (on[0].name !== mine) {
            fails.push(where + ': the open one is called "' + on[0].name + '"');
          }
        }
        ours.filter((u) => u !== at).forEach((u) => {
          if (!got.some((m) => m.at.includes(u))) {
            fails.push(where + ': its own ' + u + ' is not offered');
          }
        });
        if ((list.length || says) &&
            !got.some((m) => m.at.some((u) => theirs.includes(u)))) {
          fails.push(where + ': no way to ' + other);
        }
        const seen = new Set();
        got.forEach((m) => m.at.forEach((u) => {
          if (seen.has(u)) fails.push(where + ': ' + u + ' is on two machines');
          seen.add(u);
        }));
      }
    }
  }
}

console.log(fails.length ? fails.join('\n') + '\n\nFAIL: ' + fails.length
                         : 'PASS: ' + cases + ' cases');
if (fails.length) process.exitCode = 1;
