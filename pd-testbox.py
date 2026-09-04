#!/usr/bin/env python3
"""A Linux machine in a box, to try the container on before anybody else does.

The server is built and run on Windows every day; the image is not. This makes an
Ubuntu virtual machine with Docker in it, builds the image from this folder, runs it
and asks it for /health - which is the whole of what "does the container work" means
before somebody with a real library tries it.

    py pd-testbox.py up          fetch the image, make the machine, boot it
    py pd-testbox.py build       build and run the container in it, then check
    py pd-testbox.py arm         build the ARM image and run it under emulation
    py pd-testbox.py publish     build both architectures and push them to ghcr
    py pd-testbox.py shell       print the ssh line for looking around
    py pd-testbox.py logs        what the container has said
    py pd-testbox.py down        stop the machine, keep the disk
    py pd-testbox.py destroy     stop it and delete everything

Everything it makes lives in .testbox beside this file and is ignored by git. The
machine has no port open to the network: ssh and the server are forwarded to
127.0.0.1 on this computer only.
"""
import os
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BOX = os.path.join(HERE, ".testbox")
NAME = "PalladiumLinux"

#: Ubuntu's own cloud image, which arrives with cloud-init in it and no installer to
#: sit through. The VMDK is the one VirtualBox can read as it stands.
IMAGE = ("https://cloud-images.ubuntu.com/noble/current/"
         "noble-server-cloudimg-amd64.vmdk")

SSH_PORT = 2222        # to the machine's 22
WEB_PORT = 8766        # to the container's 8765, which is 8765 inside

VBOX = [r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe",
        r"C:\Program Files (x86)\Oracle\VirtualBox\VBoxManage.exe"]


def vbox():
    for p in VBOX:
        if os.path.exists(p):
            return p
    raise SystemExit("VirtualBox is not installed where it usually is.")


def vb(*args, quiet=False):
    """One VBoxManage call. Returns its output; raises if it failed."""
    said = subprocess.run([vbox()] + list(args), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if said.returncode:
        out = (said.stdout or "") + (said.stderr or "")
        raise SystemExit("VBoxManage %s failed:\n%s" % (args[0], out.strip()[-800:]))
    if not quiet and said.stdout.strip():
        print(said.stdout.strip()[:400])
    return said.stdout


def exists():
    return NAME in vb("list", "vms", quiet=True)


def running():
    return NAME in vb("list", "runningvms", quiet=True)


def keypair():
    """A key of its own for this machine. Not a key that opens anything else."""
    key = os.path.join(BOX, "id_ed25519")
    if not os.path.exists(key):
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "palladium-testbox",
                        "-f", key], check=True, capture_output=True)
    with open(key + ".pub", encoding="utf-8") as f:
        return key, f.read().strip()


def seed(pub):
    """The cloud-init disk: who may log in, and what to install before we arrive."""
    try:
        import pycdlib
    except ImportError:
        print("> pip install pycdlib")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pycdlib"],
                       check=True)
        import pycdlib

    user = "\n".join([
        "#cloud-config",
        "hostname: palladium-test",
        "users:",
        "  - name: pd",
        "    sudo: 'ALL=(ALL) NOPASSWD:ALL'",
        "    shell: /bin/bash",
        "    ssh_authorized_keys:",
        "      - " + pub,
        "package_update: true",
        "packages:",
        "  - docker.io",
        "runcmd:",
        "  - [systemctl, enable, --now, docker]",
        "  - [usermod, -aG, docker, pd]",
        "",
    ]).encode()
    meta = b"instance-id: palladium-test\nlocal-hostname: palladium-test\n"

    iso = os.path.join(BOX, "seed.iso")
    if os.path.exists(iso):
        os.remove(iso)
    disk = pycdlib.PyCdlib()
    disk.new(interchange_level=3, joliet=3, vol_ident="CIDATA")
    disk.add_fp(_bytes(user), len(user), "/USERDATA.;1", joliet_path="/user-data")
    disk.add_fp(_bytes(meta), len(meta), "/METADATA.;1", joliet_path="/meta-data")
    disk.write(iso)
    disk.close()
    return iso


def _bytes(b):
    import io
    return io.BytesIO(b)


def fetch():
    """Ubuntu's disk, once. 600 MB, and it is not fetched again."""
    got = os.path.join(BOX, "ubuntu.vmdk")
    if os.path.exists(got) and os.path.getsize(got) > 100_000_000:
        return got
    print("> fetching", IMAGE)
    part = got + ".part"
    with urllib.request.urlopen(IMAGE, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                print("\r  %d%%" % (done * 100 // total), end="", flush=True)
    print()
    os.replace(part, got)
    return got


def up():
    os.makedirs(BOX, exist_ok=True)
    key, pub = keypair()
    if exists():
        if not running():
            vb("startvm", NAME, "--type", "headless")
        wait_for_ssh(key)
        return

    vmdk = fetch()
    vdi = os.path.join(BOX, "disk.vdi")
    if not os.path.exists(vdi):
        print("> making a disk with room to build in")
        vb("clonemedium", "disk", vmdk, vdi, "--format", "VDI")
        vb("modifymedium", "disk", vdi, "--resize", "24576")
    iso = seed(pub)

    print("> making the machine")
    vb("createvm", "--name", NAME, "--ostype", "Ubuntu_64",
       "--basefolder", BOX, "--register")
    vb("modifyvm", NAME, "--memory", "4096", "--cpus", "4", "--audio-driver", "none",
       "--graphicscontroller", "vmsvga", "--vram", "16", "--firmware", "efi",
       "--nic1", "nat",
       "--natpf1", "ssh,tcp,127.0.0.1,%d,,22" % SSH_PORT,
       "--natpf1", "web,tcp,127.0.0.1,%d,,8765" % WEB_PORT)
    vb("storagectl", NAME, "--name", "SATA", "--add", "sata", "--portcount", "2")
    vb("storageattach", NAME, "--storagectl", "SATA", "--port", "0", "--device", "0",
       "--type", "hdd", "--medium", vdi)
    vb("storageattach", NAME, "--storagectl", "SATA", "--port", "1", "--device", "0",
       "--type", "dvddrive", "--medium", iso)
    vb("startvm", NAME, "--type", "headless")
    wait_for_ssh(key)


def wait_for_ssh(key, minutes=8):
    """Boot, then cloud-init, then Docker. The first boot installs it."""
    print("> waiting for the machine")
    until = time.time() + minutes * 60
    while time.time() < until:
        try:
            with socket.create_connection(("127.0.0.1", SSH_PORT), timeout=3):
                break
        except OSError:
            time.sleep(4)
    else:
        raise SystemExit("no ssh after %d minutes" % minutes)
    print("> waiting for cloud-init to finish installing docker")
    said = ssh(key, "cloud-init status --wait; docker --version", check=False)
    print(said.strip()[-300:])


def ssh_line(key):
    return ["ssh", "-i", key, "-p", str(SSH_PORT),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR", "pd@127.0.0.1"]


def ssh(key, script, check=True):
    said = subprocess.run(ssh_line(key) + [script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and said.returncode:
        raise SystemExit((said.stdout or "") + (said.stderr or ""))
    return (said.stdout or "") + (said.stderr or "")


def context():
    """Exactly what the Dockerfile copies, and nothing else, in a tar.

    Not the whole folder: the folder holds settings, invitations, the library and a
    build tree, and none of that belongs on a machine being handed a build context.
    """
    import re
    src = open(os.path.join(HERE, "Dockerfile"), encoding="utf-8").read()
    named = re.findall(r"[\w.-]+\.py|changes\.json", src.split("COPY static/")[0])
    tar = os.path.join(BOX, "context.tar")
    with tarfile.open(tar, "w") as t:
        t.add(os.path.join(HERE, "Dockerfile"), "Dockerfile")
        for n in sorted(set(named)):
            t.add(os.path.join(HERE, n), n)
        # the same things .dockerignore keeps out, so what is tried here is what
        # somebody building from a clone would get
        out = ("Palladium-Setup", "palladium.apk", "version.json")
        t.add(os.path.join(HERE, "static"), "static",
              filter=lambda i: None if any(x in i.name for x in out) else i)
    return tar


#: What the image is published for. A NAS, a Raspberry Pi and most small home
#: servers are ARM, and an image built only for a desktop processor is one they
#: cannot pull at all.
ARCHES = "linux/amd64,linux/arm64"

#: Where the published image lives. The same name the compose file names.
PUBLISHED = "ghcr.io/grovestick/palladium"


def machinery(key):
    """buildx and QEMU, installed once. Building for a processor this machine has
    not got means emulating it, and neither comes with Docker."""
    if "buildx" in ssh(key, "docker buildx version 2>&1 || true", check=False):
        return
    print("> installing buildx and QEMU (once)")
    ssh(key, "sudo apt-get install -y -q docker-buildx qemu-user-static "
             "binfmt-support >/dev/null 2>&1; "
             "docker buildx create --name pd --driver docker-container --use "
             ">/dev/null 2>&1 || docker buildx use pd")


def arm():
    """Build for ARM and actually run it, which is the only proof worth having."""
    key, _ = keypair()
    if not running():
        raise SystemExit("the machine is not up: py pd-testbox.py up")
    send(key)
    machinery(key)
    print("> building for ARM (emulated, so slow)")
    print(ssh(key, "cd ~/ctx && docker buildx build --platform linux/arm64 "
                   "-t palladium:arm64 --load . 2>&1 | tail -6"))
    print("> running it, on an emulated processor")
    print(ssh(key, "docker rm -f pd-arm >/dev/null 2>&1; "
                   "docker run -d --name pd-arm --platform linux/arm64 -p 8767:8765 "
                   "palladium:arm64 >/dev/null && sleep 25 && "
                   "docker ps --filter name=pd-arm --format '{{.Status}}'; "
                   "docker exec pd-arm python -c \"import urllib.request as u;"
                   "print(u.urlopen('http://127.0.0.1:8765/health',timeout=20).read())\" "
                   "2>&1 | tail -2", check=False))
    print(ssh(key, "docker exec pd-arm uname -m", check=False).strip())


def publish():
    """Both architectures, straight to the registry.

    Log in first, inside the machine, with a token that may write packages:

        py pd-testbox.py shell
        echo <token> | docker login ghcr.io -u <github user> --password-stdin
    """
    key, _ = keypair()
    if not running():
        raise SystemExit("the machine is not up: py pd-testbox.py up")
    import json
    with open(os.path.join(HERE, "changes.json"), encoding="utf-8") as f:
        version = str(json.load(f)[0]["version"])
    if "ghcr.io" not in ssh(key, "cat ~/.docker/config.json 2>/dev/null || true",
                            check=False):
        raise SystemExit(
            "not logged in to ghcr inside the machine. Open it with\n"
            "  py pd-testbox.py shell\n"
            "and:  echo <token> | docker login ghcr.io -u <user> --password-stdin")
    send(key)
    machinery(key)
    print("> building %s for %s and pushing" % (version, ARCHES))
    print(ssh(key, "cd ~/ctx && docker buildx build --platform %s "
                   "-t %s:%s -t %s:latest --push . 2>&1 | tail -8"
                   % (ARCHES, PUBLISHED, version, PUBLISHED)))


def send(key):
    """The build context, into the machine."""
    tar = context()
    print("> sending the build context (%d KB)" % (os.path.getsize(tar) // 1024))
    ssh(key, "rm -rf ~/ctx && mkdir -p ~/ctx")
    subprocess.run(["scp", "-i", key, "-P", str(SSH_PORT),
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=/dev/null",
                    "-o", "LogLevel=ERROR", tar, "pd@127.0.0.1:ctx/context.tar"],
                   check=True)
    ssh(key, "cd ~/ctx && tar xf context.tar")


def build():
    key, _ = keypair()
    if not running():
        raise SystemExit("the machine is not up: py pd-testbox.py up")
    send(key)
    print("> building")
    print(ssh(key, "cd ~/ctx && docker build -t palladium:test . 2>&1 | tail -25"))
    print("> running it")
    print(ssh(key, "docker rm -f palladium-test >/dev/null 2>&1; "
                   "docker run -d --name palladium-test -p 8765:8765 "
                   "-v pd-data:/data palladium:test && sleep 12 && "
                   "docker ps --filter name=palladium-test "
                   "--format '{{.Status}}'"))
    print("> asking it for /health, from this computer")
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % WEB_PORT,
                                    timeout=20) as r:
            print("  %d %s" % (r.status, r.read(200).decode()))
    except Exception as why:
        print("  no answer:", why)
        print(ssh(key, "docker logs --tail 40 palladium-test", check=False))


def main():
    what = (sys.argv[1] if len(sys.argv) > 1 else "up").lower()
    key = os.path.join(BOX, "id_ed25519")
    if what == "up":
        up()
        print("\nup. ssh:  " + " ".join(ssh_line(key)))
        print("then:     py pd-testbox.py build")
    elif what == "build":
        build()
    elif what == "arm":
        arm()
    elif what == "publish":
        publish()
    elif what == "shell":
        print(" ".join(ssh_line(key)))
    elif what == "logs":
        print(ssh(key, "docker logs --tail 60 palladium-test", check=False))
    elif what == "down":
        if running():
            vb("controlvm", NAME, "acpipowerbutton")
        print("stopping")
    elif what == "destroy":
        if running():
            vb("controlvm", NAME, "poweroff")
            time.sleep(3)
        if exists():
            vb("unregistervm", NAME, "--delete")
        print("gone. The disk in .testbox stays until you delete the folder.")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
