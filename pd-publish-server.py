#!/usr/bin/env python3
"""Put a built server where the world can fetch it.

Three things in one order that matters:

    1. the installer into the R2 bucket, under the name the gate serves
    2. the site, which carries server.json - the version and the hash
    3. a check that the two agree

If the site went up first, every running server would see a new version, fetch the
old file and refuse it for a hash that does not match. So the file goes first.

    py pd-publish-server.py            publish what is in build/
    py pd-publish-server.py --check    say what is published, change nothing
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BUCKET = "palladium-builds"
AS_NAMED = "Palladium-Setup.exe"          # what the gate serves it as
SITE = "palladium-video"
SAYS = "https://palladium.video/server.json"


# wrangler draws boxes and arrows; printed through a console codepage that has no
# such characters, the publish died at the last step with everything already uploaded
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def wrangler(*args):
    """One wrangler call, with its output shown as it goes."""
    line = ["npx", "--yes", "wrangler@latest"] + list(args)
    # wrangler draws boxes in its output; decoded as the console's codepage they
    # raise rather than print, and the publish reads as a failure it was not
    said = subprocess.run(line, cwd=HERE, shell=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    out = (said.stdout or "") + (said.stderr or "")
    if said.returncode:
        print(out.strip()[-1200:])
        raise SystemExit("wrangler %s failed" % args[0])
    return out


def sha256(path):
    d = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(262144), b""):
            d.update(chunk)
    return d.hexdigest()


def announced():
    """What the site currently tells a running server."""
    try:
        # Cloudflare answers Python's default user-agent with a 403, which reads as
        # "the file is not published" when the file is fine
        req = urllib.request.Request(SAYS, headers={"User-Agent": "palladium"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def main(argv):
    if "--check" in argv:
        said = announced()
        print(json.dumps(said, indent=2))
        return

    with open(os.path.join(HERE, "site", "server.json"), encoding="utf-8") as f:
        plan = json.load(f)
    version = plan["version"]
    made = os.path.join(HERE, "build", "Palladium-Setup-%s.exe" % version)
    if not os.path.exists(made):
        raise SystemExit("no build for %s - run pd-build-installer.py first" % version)

    # the file the site is about to describe: checked here rather than trusted
    here = sha256(made)
    if here != plan.get("sha256"):
        raise SystemExit("build/%s does not match site/server.json - rebuild"
                         % os.path.basename(made))

    print("uploading %s (%.1f MB)" % (os.path.basename(made),
                                      os.path.getsize(made) / 1e6))
    wrangler("r2", "object", "put", "%s/%s" % (BUCKET, AS_NAMED),
             "--file", made,
             "--content-type", "application/vnd.microsoft.portable-executable",
             "--remote")

    # what the gate should call the download: the object keeps its stable name
    print("naming the build")
    wrangler("kv", "key", "put", "_build",
             json.dumps({"version": version, "sha256": here}),
             "--binding", "KEYS", "--config", os.path.join("beta", "wrangler.toml"),
             "--remote")

    # the app into the bucket as well, so both downloads come out of one door and are
    # counted in one place
    apk = os.path.join(HERE, "static", "palladium.apk")
    if os.path.exists(apk):
        print("uploading the app (%.1f MB)" % (os.path.getsize(apk) / 1e6))
        wrangler("r2", "object", "put", "%s/palladium.apk" % BUCKET, "--file", apk,
                 "--content-type", "application/vnd.android.package-archive",
                 "--remote")
        try:
            with open(os.path.join(HERE, "static", "version.json"), encoding="utf-8") as f:
                app_says = json.load(f)
            wrangler("kv", "key", "put", "_app",
                     json.dumps({"version": app_says.get("versionName", ""),
                                 "code": app_says.get("versionCode", 0)}),
                     "--binding", "KEYS", "--config", os.path.join("beta", "wrangler.toml"),
                     "--remote")
        except Exception as why:
            print("the app version was not written:", why)

    # the app goes up with the site as well: a page that can hand the file over itself
    # keeps working if the gate is ever down
    for name in ("palladium.apk", "version.json"):
        near = os.path.join(HERE, "static", name)
        if os.path.exists(near):
            shutil.copy2(near, os.path.join(HERE, "site", name))
            print("site carries", name)

    # the scripts a server can fetch for itself, rebuilt from what is in this folder
    # so the zip and the server are never a version apart
    try:
        import subprocess as run_it
        run_it.run([sys.executable, os.path.join(HERE, "pd-publish-tools.py")],
                   cwd=HERE, check=True)
    except Exception as why:
        print("the subtitle tools were not rebuilt:", why)

    print("deploying the site")
    wrangler("pages", "deploy", "site", "--project-name", SITE,
             "--branch", "main", "--commit-dirty=true")

    said = announced()
    if said.get("sha256") != here:
        print("published, but the site still says", said.get("version"),
              "- it may be a moment behind")
    else:
        print("published %s, %s" % (version, here[:16]))


if __name__ == "__main__":
    main(sys.argv[1:])
