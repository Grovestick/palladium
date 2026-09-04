#!/usr/bin/env python3
"""Build the container image, and push it where the compose file points.

The Windows installer and this image are the same server; only the wrapping differs.
The version comes from the top of the changelog, as everywhere else, so an image is
never tagged with a number nobody shipped.

    py pd-build-image.py                build it for this machine, to try it here
    py pd-build-image.py --push         build amd64 and arm64 and push both tags
    py pd-build-image.py --check        say what would happen, do nothing

Pushing needs a login first, once per machine:

    docker login ghcr.io -u <github user> --password-stdin

with a token that has write:packages. The package is public or private on GitHub's
side, not here - the repository it belongs to can stay closed either way.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "ghcr.io/grovestick/palladium"

#: What it is built for. Half of self-hosting happens on a NAS or a small ARM
#: box, and an image that only runs on a desktop processor is one most of them
#: cannot pull. A multi-architecture image cannot be loaded into the local
#: daemon, so both are built only on the way to the registry - which needs
#: buildx and QEMU:
#:
#:     sudo apt install docker-buildx qemu-user-static binfmt-support
#:     docker buildx create --name pd --driver docker-container --use
ARCHES = "linux/amd64,linux/arm64"


def version_now():
    """The version at the top of the changelog, which is what shipped."""
    with open(os.path.join(HERE, "changes.json"), encoding="utf-8") as f:
        return str(json.load(f)[0]["version"])


def run(cmd):
    print(">", " ".join(cmd))
    if subprocess.run(cmd, cwd=HERE).returncode:
        raise SystemExit("failed: " + " ".join(cmd))


def main():
    args = sys.argv[1:]
    version = version_now()
    tags = ["%s:%s" % (NAME, version), "%s:latest" % NAME]
    push = "--push" in args

    if "--check" in args:
        print("would build", tags[0], "and", tags[1])
        print("for", ARCHES if push else "this machine")
        print("from", HERE)
        return
    if not shutil.which("docker"):
        raise SystemExit(
            "docker is not on PATH. On Windows that means Docker Desktop, which needs "
            "WSL2 - or build this on the Linux machine that will run it. There is one "
            "for exactly this:  py pd-testbox.py up")
    if push:
        # both architectures, straight to the registry: a multi-architecture image
        # cannot be loaded into the local daemon, so there is nothing to try first
        cmd = ["docker", "buildx", "build", "--platform", ARCHES]
        for tag in tags:
            cmd += ["-t", tag]
        run(cmd + ["--push", "."])
        print("pushed", version, "for", ARCHES)
    else:
        run(["docker", "build", "-t", tags[0], "-t", tags[1], "."])
        print("built", version, "for this machine - add --push for both architectures")



if __name__ == "__main__":
    main()
