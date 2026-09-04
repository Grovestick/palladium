#!/usr/bin/env python3
"""Put the subtitle scripts on the site, as one zip a server can fetch for itself.

The models and the libraries belong to whoever wrote them and are fetched from them.
These scripts are Palladium's own, so they come from Palladium - and separately from
the server, which means a fault in one is mended for everybody the moment this runs,
without anybody installing a new build.

    py pd-publish-tools.py           write site/tools/, then deploy with the site

What lands there:

    tools.zip     the scripts
    tools.json    the version, the size and the hash of that zip

Run before pd-publish-server.py, which deploys the whole site folder.
"""
import hashlib
import io
import json
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "site", "tools")

#: What goes in. Scripts only - anything a server is asked to run comes from here and
#: is checked against the hash below before it is unpacked.
CARRIED = ["pd_subs_make.py"]


def version_now():
    with open(os.path.join(HERE, "changes.json"), encoding="utf-8") as f:
        return str(json.load(f)[0]["version"])


def main():
    os.makedirs(OUT, exist_ok=True)
    version = version_now()

    # written to memory first: the hash goes in the file that describes the zip, and
    # a zip on disk that nobody has hashed yet is a file two things can disagree about
    held = io.BytesIO()
    with zipfile.ZipFile(held, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for name in CARRIED:
            source = os.path.join(HERE, name)
            if not os.path.exists(source):
                raise SystemExit("missing: " + name)
            zip_file.write(source, name)
    body = held.getvalue()

    with open(os.path.join(OUT, "tools.zip"), "wb") as f:
        f.write(body)
    said = {"version": version, "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "files": CARRIED}
    with open(os.path.join(OUT, "tools.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(said, indent=2) + "\n")
    print("tools %s, %.1f KB, %s" % (version, len(body) / 1024, said["sha256"][:16]))
    print("in", OUT, "- deploy it with pd-publish-server.py")


if __name__ == "__main__":
    main()
