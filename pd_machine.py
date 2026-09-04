#!/usr/bin/env python3
"""The two things the installer used to ask, asked where they can be answered.

A wizard is the wrong place for either. "Start when I sign in" is a shortcut in one
folder, and "let the house reach it" is a firewall rule - both are decisions somebody
changes later, and neither means anything until the program has a library to serve.
So the installer asks nothing and this answers both, from Settings.

Windows only. On anything else the panel says so and offers nothing.
"""
import os
import subprocess
import sys
import time

STARTUP = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                       "Start Menu", "Programs", "Startup")
LINK = os.path.join(STARTUP, "Palladium.lnk")
RULE = "Palladium"


def windows():
    return sys.platform == "win32"


def packaged():
    return bool(globals().get("__compiled__")) or getattr(sys, "frozen", False)


def program():
    """What a shortcut should point at: the tray, whichever form it takes."""
    here = os.path.dirname(os.path.abspath(
        sys.executable if packaged() else __file__))
    built = os.path.join(here, "palladium.exe")
    if os.path.exists(built):
        return built, ""
    # from source: the tray script, run by the windowless interpreter so no console
    # opens at every sign-in
    quiet = sys.executable.replace("python.exe", "pythonw.exe")
    return quiet, '"%s"' % os.path.join(here, "pd-tray.py")


def powershell(script):
    """One PowerShell line, without a window. Returns its output, or ''."""
    try:
        said = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (said.stdout or "").strip()
    except Exception:
        return ""


def starts_at_login():
    return os.path.exists(LINK)


def set_start_at_login(on):
    """Put the shortcut in the Startup folder, or take it out again.

    A .lnk is a COM object rather than a file format anybody should write by hand, so
    the shell is asked to make it - the same object Explorer uses.
    """
    if not windows():
        return False
    if not on:
        try:
            os.remove(LINK)
        except OSError:
            pass
        return not os.path.exists(LINK)
    target, args = program()
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath = '%s';"
        "$s.Arguments = '%s';"
        "$s.WorkingDirectory = '%s';"
        "$s.Description = 'Palladium';"
        "$s.Save()"
        % (LINK.replace("'", "''"), target.replace("'", "''"),
           args.replace("'", "''"), os.path.dirname(target).replace("'", "''")))
    powershell(script)
    return os.path.exists(LINK)


#: The answer to the firewall question, and when it was asked. Asking costs a
#: PowerShell start and a second and a half of it, and the settings page asks every
#: time it is opened - while the rule changes about once in the life of the install.
FIREWALL = {"port": 0, "open": False, "at": 0.0}
FIREWALL_FOR = 300


def forget_firewall():
    """After the rule is written, the cached answer is a lie. Drop it."""
    FIREWALL["at"] = 0.0


def firewall_open(port, fresh=False):
    """Whether a rule of ours already lets the house in on this port."""
    if not windows():
        return False
    if (not fresh and FIREWALL["at"] and FIREWALL["port"] == int(port)
            and time.time() - FIREWALL["at"] < FIREWALL_FOR):
        return FIREWALL["open"]
    said = powershell(
        "(Get-NetFirewallRule -DisplayName '%s' -ErrorAction SilentlyContinue | "
        "Get-NetFirewallPortFilter | Where-Object { $_.LocalPort -eq %d } | "
        "Measure-Object).Count" % (RULE, int(port)))
    try:
        answer = int((said or "0").splitlines()[-1]) > 0
    except Exception:
        answer = False
    FIREWALL.update({"port": int(port), "open": answer, "at": time.time()})
    return answer


#: What Windows calls the network this machine is on, and when it was asked. Another
#: PowerShell start, and the answer changes about as often as the cable moves.
NETWORK = {"kind": "", "at": 0.0}
NETWORK_FOR = 300


def network_kind(lan_ip="", fresh=False):
    """"Private", "Public", "Domain", or "" if it could not be read.

    The rule Palladium writes covers private and domain networks, because a home
    network is one of those. A machine whose network Windows has decided is public
    is closed to the house whatever rule is written, and the switch that says On is
    then telling the truth about the rule and a lie about the result.
    """
    if not windows():
        return ""
    if not fresh and NETWORK["kind"] and time.time() - NETWORK["at"] < NETWORK_FOR:
        return NETWORK["kind"]
    # the card the house is on, not whichever profile comes first: a machine with a
    # virtual switch on it has several, and the VM's is not the one that matters
    said = ""
    if lan_ip:
        said = powershell(
            "$i=(Get-NetIPAddress -IPAddress '%s' -ErrorAction SilentlyContinue)."
            "InterfaceIndex; if ($i) { (Get-NetConnectionProfile -InterfaceIndex $i "
            "-ErrorAction SilentlyContinue).NetworkCategory }" % lan_ip)
    if not (said or "").strip():
        said = powershell(
            "(Get-NetConnectionProfile | Where-Object { $_.IPv4Connectivity -ne "
            "'NoTraffic' } | Select-Object -First 1 -ExpandProperty NetworkCategory)")
    kind = (said or "").strip().splitlines()[-1].strip() if said else ""
    NETWORK.update({"kind": kind, "at": time.time()})
    return kind


def make_private():
    """Ask Windows to treat this network as a home one. Needs an administrator."""
    if not windows():
        return False, "not Windows"
    line = ("Get-NetConnectionProfile | Where-Object { $_.IPv4Connectivity -ne "
            "\"NoTraffic\" } | Set-NetConnectionProfile -NetworkCategory Private")
    said = powershell(
        "Start-Process powershell -Verb RunAs -WindowStyle Hidden -Wait "
        "-ArgumentList '-NoProfile','-Command','%s'; $?" % line)
    NETWORK["at"] = 0.0
    if "True" in said:
        return True, ""
    return False, "Windows refused, or the prompt was dismissed"


def close_firewall(port):
    """Take the rule away again. Also an administrator's business."""
    if not windows():
        return False, "not Windows"
    line = "netsh advfirewall firewall delete rule name=\\\"%s\\\"" % RULE
    said = powershell(
        "Start-Process powershell -Verb RunAs -WindowStyle Hidden -Wait "
        "-ArgumentList '-NoProfile','-Command','%s'; $?" % line)
    forget_firewall()
    if "True" in said:
        return True, ""
    return False, "Windows refused, or the prompt was dismissed"


def open_firewall(port):
    """Ask for the rule. This is the one thing here that needs administrator.

    Windows will put up its own prompt; nothing can be done about that from a program
    running as an ordinary user, and doing it quietly would be worse.
    """
    if not windows():
        return False, "not Windows"
    line = ("netsh advfirewall firewall delete rule name=\\\"%s\\\" ; "
            "netsh advfirewall firewall add rule name=\\\"%s\\\" dir=in action=allow "
            "protocol=TCP localport=%d profile=private,domain"
            % (RULE, RULE, int(port)))
    said = powershell(
        "Start-Process powershell -Verb RunAs -WindowStyle Hidden -Wait "
        "-ArgumentList '-NoProfile','-Command','%s'; $?" % line)
    forget_firewall()          # whatever happened, the cached answer is stale
    if "True" in said:
        return True, ""
    return False, "Windows refused, or the prompt was dismissed"


#: the box everything goes through, worked out once - it does not move
GATE = {"at": 0.0, "ip": ""}


def gateway():
    """The router's address on this network, or nothing if it will not say.

    Everything the house does leaves through it, and the one thing anybody ever wants
    from a diagram of the house is to open its page - so it is worth the one call to
    find out what to open.
    """
    if time.time() - GATE["at"] < 600 and GATE["ip"]:
        return GATE["ip"]
    GATE["at"] = time.time()
    try:
        out = subprocess.run(["route", "print", "0.0.0.0"], capture_output=True,
                             text=True, timeout=6,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        for line in out.splitlines():
            bits = line.split()
            # 0.0.0.0  0.0.0.0  <gateway>  <interface>  <metric>
            if len(bits) >= 5 and bits[0] == "0.0.0.0" and bits[1] == "0.0.0.0":
                if bits[2].count(".") == 3 and bits[2] != "0.0.0.0":
                    GATE["ip"] = bits[2]
                    break
    except Exception:
        pass
    return GATE["ip"]


def state(port, lan_ip=""):
    """What the panel shows."""
    if not windows():
        return {"windows": False}
    return {"windows": True, "startup": starts_at_login(),
            "firewall": firewall_open(port), "port": int(port),
            # what Windows calls this network: the rule only covers a private one
            "network": network_kind(lan_ip),
            # and the box it all goes through, so the drawing can be opened
            "gateway": gateway(),
            "program": program()[0]}
