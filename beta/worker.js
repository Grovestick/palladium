/**
 * The gate in front of the installer.
 *
 * A beta key is checked against KV and, if it is good for another download, the file
 * comes straight out of R2 through this Worker. No redirect and no signed URL: a link
 * that works for five minutes is a link that gets pasted into a chat and works for
 * five minutes for everybody in it.
 *
 * Three routes:
 *   POST /check   {key}          is this key good, and how many downloads are left
 *   GET  /get?key=…              the installer itself, and one use spent
 *   GET  /build                  the same file, no key, for a server updating itself
 *
 * The count is written back after the body has been handed over, so a download that
 * never starts does not cost the tester one of theirs.
 */

const FILE = "Palladium-Setup.exe";      // the name in the bucket, kept stable
const APK = "palladium.apk";            // and the app, beside it

/* What the current build is called. The object keeps one name so this Worker always
   knows what to fetch; the download is named for the version, so a tester's Downloads
   folder says which build they ran rather than holding three copies of the same
   name. Written by pd-publish-server.py at the moment the file is uploaded. */
async function named(env) {
  try {
    const said = await env.KEYS.get("_build", { type: "json" });
    if (said && said.version) return "Palladium-Setup-" + said.version + ".exe";
  } catch (e) { /* the plain name will do */ }
  return FILE;
}

/* What a download is counted as.
 *
 * A day, which file, the version, the country Cloudflare says the request came from,
 * and the browser and system as broad families - nothing else. No address is stored,
 * no identifier is set, nothing is written to the machine asking, and no row belongs
 * to a person: these are counters, and a counter of "Sweden, Chrome, Windows, the
 * thirtieth" cannot be turned back into anybody.
 *
 * That is deliberate rather than lazy. Under the GDPR an IP address is personal data
 * and would need a lawful basis, a retention period and a way to answer somebody who
 * asks what is held about them. Aggregate counts avoid all three questions by not
 * holding anything to answer about.
 */
function browserOf(agent) {
  const said = String(agent || "");
  if (/palladium/i.test(said)) return "Palladium";      // a server fetching its update
  if (/edg\//i.test(said)) return "Edge";
  if (/opr\/|opera/i.test(said)) return "Opera";
  if (/firefox/i.test(said)) return "Firefox";
  if (/chrome|crios/i.test(said)) return "Chrome";
  if (/safari/i.test(said)) return "Safari";
  if (/curl|wget|python|powershell/i.test(said)) return "Tool";
  return "Other";
}

function systemOf(agent) {
  const said = String(agent || "");
  if (/windows/i.test(said)) return "Windows";
  if (/android/i.test(said)) return "Android";
  if (/iphone|ipad|ios/i.test(said)) return "iOS";
  if (/mac os|macintosh/i.test(said)) return "macOS";
  if (/linux/i.test(said)) return "Linux";
  return "Other";
}

async function tally(env, ctx, what, request) {
  const now = new Date();
  const day = now.toISOString().slice(0, 10);
  const country = (request.cf && request.cf.country) || "??";
  const agent = request.headers.get("user-agent") || "";
  const key = ["dl", day, what, country, browserOf(agent), systemOf(agent)].join("|");
  // after the answer has gone out: counting is never a reason to make somebody wait,
  // and a count lost because the file was served is the right way round
  ctx.waitUntil((async () => {
    try {
      const had = parseInt(await env.KEYS.get(key), 10) || 0;
      await env.KEYS.put(key, String(had + 1));
    } catch (e) { /* a count is not worth an error page */ }
  })());
}

function tidy(key) {
  return String(key || "").toUpperCase().replace(/[^0-9A-Z]/g, "");
}

/** What a key is worth right now: an answer, and the row it came from. */
async function look(env, key) {
  if (!key) return { ok: false, why: "Enter your beta key" };
  const said = await env.KEYS.get(key, { type: "json" });
  if (!said) return { ok: false, why: "That key is not one of ours" };
  const now = Math.floor(Date.now() / 1000);
  if (said.until && said.until < now) {
    return { ok: false, why: "That key has expired" };
  }
  if (said.used >= said.uses) {
    return { ok: false, why: "That key has been used its number of times" };
  }
  return { ok: true, left: said.uses - said.used, row: said };
}

/* The page lives on palladium.video and this on get.palladium.video, which to a
   browser are two different origins: it sends an OPTIONS first and refuses to read
   the answer unless the reply says who may. Only our own pages are named. */
const MAY = ["https://palladium.video", "https://www.palladium.video",
             "https://palladium-video.pages.dev"];

function allow(request) {
  const from = request.headers.get("origin") || "";
  const head = {
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "content-type",
    "access-control-max-age": "86400",
    vary: "origin",
  };
  // a preview deployment is the same project under another name, and is let in too
  if (MAY.includes(from) || /^https:\/\/[a-z0-9]+\.palladium-video\.pages\.dev$/.test(from)) {
    head["access-control-allow-origin"] = from;
  }
  return head;
}

const asJson = (body, status, request) =>
  new Response(JSON.stringify(body), {
    status: status || 200,
    headers: Object.assign({ "content-type": "application/json; charset=utf-8" },
                           request ? allow(request) : {}),
  });

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // the question the browser asks before the question it wants to ask
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: allow(request) });
    }

    if (request.method === "POST" && url.pathname === "/check") {
      const body = await request.json().catch(() => ({}));
      const key = tidy(body.key);
      const said = await look(env, key);
      return asJson(said.ok ? { ok: true, left: said.left }
                            : { ok: false, why: said.why },
                    said.ok ? 200 : 403, request);
    }

    // A server that is already installed asking for its own next build. No key:
    // the copy asking was let in once already, and making an update depend on a key
    // means an update that stops working the day the key does - which is the wrong
    // thing to have happen to somebody's server.
    // The Android app. Out of the same bucket as the server so that both downloads
    // are counted in one place, and named for its version so a phone's Downloads
    // folder says which build it holds.
    if (url.pathname === "/app") {
      await tally(env, ctx, "app", request);
      const file = await env.BUILDS.get(APK);
      if (!file) {
        return asJson({ ok: false, why: "The app is not there" }, 404, request);
      }
      let called = APK;
      try {
        const said = await env.KEYS.get("_app", { type: "json" });
        if (said && said.version) called = "palladium-" + said.version + ".apk";
      } catch (e) { /* the plain name will do */ }
      const headers = new Headers();
      file.writeHttpMetadata(headers);
      headers.set("content-type", "application/vnd.android.package-archive");
      headers.set("content-disposition", 'attachment; filename="' + called + '"');
      headers.set("etag", file.httpEtag);
      return new Response(file.body, { headers });
    }

    /* ---- faults sent in by servers ----
     *
     * A server that has been told it may will post a fault here: what went wrong,
     * which build it was, and - if the person running it went that far - what kind of
     * machine it happened on. Nothing that identifies anybody is accepted: the
     * sending server strips names, addresses and titles before it leaves, and what
     * arrives is capped and stored under a key nobody can guess.
     *
     * Kept for ninety days and then gone, because a fault older than that is a fault
     * about a build nobody runs.
     */
    if (url.pathname === "/faults" && request.method === "POST") {
      let said = null;
      try {
        said = await request.json();
      } catch (e) {
        return asJson({ ok: false, why: "not json" }, 400, request);
      }
      const text = String(said && said.text || "").slice(0, 4000);
      if (!text) return asJson({ ok: false, why: "nothing to say" }, 400, request);
      const row = {
        when: Date.now(),
        kind: String(said.kind || "error").slice(0, 20),
        build: String(said.build || "").slice(0, 20),
        app: String(said.app || "").slice(0, 40),
        // a name the sender chose for itself, so two reports from one house can be
        // seen as one house without anybody being named
        from: String(said.from || "").slice(0, 24),
        text: text,
        machine: said.machine && typeof said.machine === "object"
          ? Object.fromEntries(Object.entries(said.machine).slice(0, 12)
              .map(([k, v]) => [String(k).slice(0, 24), String(v).slice(0, 80)]))
          : null,
        country: (request.cf && request.cf.country) || "",
      };
      const id = "fault:" + Date.now().toString(36) + "-" +
        Math.random().toString(36).slice(2, 8);
      await env.KEYS.put(id, JSON.stringify(row),
                         { expirationTtl: 90 * 24 * 3600 });
      return asJson({ ok: true }, 200, request);
    }

    /* The faults, for whoever holds the key. A page rather than JSON: it is read by
       a person, once in a while, and a page can say when something happened in words
       rather than in milliseconds since 1970. */
    if (url.pathname === "/faults" && request.method === "GET") {
      const key = tidy(url.searchParams.get("key"));
      const want = (env.ADMIN_KEY || "").trim();
      if (!want || key !== want) {
        return new Response("Not here", { status: 404 });
      }
      const listed = await env.KEYS.list({ prefix: "fault:", limit: 400 });
      const rows = [];
      for (const one of listed.keys) {
        const row = await env.KEYS.get(one.name, { type: "json" });
        if (row) rows.push({ ...row, id: one.name });
      }
      rows.sort((a, b) => (b.when || 0) - (a.when || 0));
      const ago = (ms) => {
        const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
        if (s < 90) return "just now";
        if (s < 3600) return Math.round(s / 60) + " min ago";
        if (s < 86400) return Math.round(s / 3600) + " h ago";
        return Math.round(s / 86400) + " d ago";
      };
      const esc = (t) => String(t == null ? "" : t)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const body = rows.map((r) => {
        const kit = r.machine
          ? Object.entries(r.machine).map(([k, v]) => esc(k) + " " + esc(v)).join(" · ")
          : "";
        return "<article><h2>" + esc(r.kind) + " <small>" + esc(r.build) +
          (r.app ? " · " + esc(r.app) : "") +
          (r.from ? " · " + esc(r.from) : "") +
          (r.country ? " · " + esc(r.country) : "") +
          " · " + ago(r.when) + "</small></h2>" +
          (kit ? "<p class='kit'>" + kit + "</p>" : "") +
          "<pre>" + esc(r.text) + "</pre></article>";
      }).join("");
      return new Response(
        "<!doctype html><meta charset=utf-8><title>Faults</title>" +
        "<meta name=viewport content='width=device-width,initial-scale=1'>" +
        "<style>" +
        "body{margin:0;padding:24px;background:#0b0d10;color:#e8ecf1;" +
        "font:15px/1.5 system-ui,sans-serif}" +
        "h1{font-size:18px;letter-spacing:.04em;text-transform:uppercase;color:#93a0b0}" +
        "article{margin:16px 0;padding:14px 16px;background:#161a20;border-radius:10px}" +
        "h2{margin:0 0 8px;font-size:14px}" +
        "small{color:#93a0b0;font-weight:400}" +
        ".kit{margin:0 0 8px;color:#93a0b0;font-size:12px}" +
        "pre{margin:0;white-space:pre-wrap;word-break:break-word;font:12px/1.45 " +
        "ui-monospace,Consolas,monospace;color:#c8d2dc}" +
        "</style><h1>Faults · " + rows.length + "</h1>" +
        (body || "<p>Nothing has been sent.</p>"),
        { headers: { "content-type": "text/html;charset=utf-8",
                     "cache-control": "no-store" } });
    }

    if (url.pathname === "/build") {
      await tally(env, ctx, "server", request);
      const file = await env.BUILDS.get(FILE);
      if (!file) {
        return asJson({ ok: false, why: "The build is not there" }, 404, request);
      }
      const headers = new Headers();
      file.writeHttpMetadata(headers);
      headers.set("content-type", "application/vnd.microsoft.portable-executable");
      headers.set("content-disposition",
                  'attachment; filename="' + (await named(env)) + '"');
      headers.set("etag", file.httpEtag);
      return new Response(file.body, { headers });
    }

    if (url.pathname === "/get") {
      const key = tidy(url.searchParams.get("key"));
      const said = await look(env, key);
      if (!said.ok) return asJson({ ok: false, why: said.why }, 403, request);

      const file = await env.BUILDS.get(FILE);
      if (!file) {
        return asJson({ ok: false, why: "The build is not there" }, 404, request);
      }
      await tally(env, ctx, "server-key", request);

      // spent after the fact: a browser that never takes the body has not had a copy
      ctx.waitUntil(env.KEYS.put(
        key, JSON.stringify({ ...said.row, used: said.row.used + 1 })));

      const headers = new Headers();
      file.writeHttpMetadata(headers);
      headers.set("content-type", "application/vnd.microsoft.portable-executable");
      headers.set("content-disposition",
                  'attachment; filename="' + (await named(env)) + '"');
      headers.set("etag", file.httpEtag);
      for (const [k, v] of Object.entries(allow(request))) headers.set(k, v);
      return new Response(file.body, { headers });
    }

    return new Response("Not here", { status: 404 });
  },
};
