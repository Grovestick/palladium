#!/usr/bin/env python3
"""Asking the router to let the outside in.

A home router hides everything behind one address, so an invitation link reaches
nothing until the router is told to forward the port. Most routers will do that on
request, over UPnP, which is three steps and no dependencies:

  1. shout on the local network for an Internet Gateway Device (SSDP, UDP 1900);
  2. read its description to find the service that handles port mappings;
  3. post one SOAP call - AddPortMapping - and read the answer.

It is deliberately not done at startup. Opening a port to the internet is the kind of
thing that should happen because somebody pressed a button, not because a program
started.
"""
import re
import socket
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

SSDP = ("M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n")

WANIP = "urn:schemas-upnp-org:service:WANIPConnection:1"
WANPPP = "urn:schemas-upnp-org:service:WANPPPConnection:1"


def discover(timeout=3.0, lan_ip=None):
    """The description URL of the first gateway that answers, or None.

    Bound to the card that faces the house where one is given. A machine with a
    virtual switch on it - a VM, a Docker network - sends multicast out of whichever
    interface the routing table prefers, and on this machine that is not the one the
    router is on: the search went nowhere and the answer was "no router speaks UPnP"
    for a router that does.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if lan_ip:
        try:
            s.bind((lan_ip, 0))
        except OSError:
            pass                      # the address moved: send it however we can
    s.settimeout(timeout)
    try:
        s.sendto(SSDP.encode(), ("239.255.255.250", 1900))
        while True:
            data, _ = s.recvfrom(4096)
            m = re.search(r"(?im)^location:\s*(\S+)", data.decode("utf-8", "replace"))
            if m:
                return m.group(1)
    except socket.timeout:
        return None
    finally:
        s.close()


def control_url(desc_url):
    """(control address, service type) for whichever connection service it offers."""
    try:
        with urllib.request.urlopen(desc_url, timeout=5) as r:
            xml = r.read()
    except Exception:
        return None, None
    root = ET.fromstring(xml)
    ns = "{urn:schemas-upnp-org:device-1-0}"
    for svc in root.iter(ns + "service"):
        stype = (svc.findtext(ns + "serviceType") or "").strip()
        if stype in (WANIP, WANPPP):
            ctrl = (svc.findtext(ns + "controlURL") or "").strip()
            return urllib.parse.urljoin(desc_url, ctrl), stype
    return None, None


def soap(url, stype, action, body):
    envelope = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body><u:%s xmlns:u=\"%s\">%s</u:%s></s:Body></s:Envelope>"
        % (action, stype, body, action))
    req = urllib.request.Request(url, data=envelope.encode(), headers={
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": '"%s#%s"' % (stype, action),
    })
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read().decode("utf-8", "replace")


def forward(lan_ip, port, description="Palladium"):
    """Ask the router to send `port` to us. Returns (ok, message)."""
    desc = discover(lan_ip=lan_ip)
    if not desc:
        return False, ("No router answered. Either it has UPnP turned off, or it does "
                       "not speak it - the port has to be forwarded by hand.")
    url, stype = control_url(desc)
    if not url:
        return False, "The router answered but offers no port mapping service."
    body = ("<NewRemoteHost></NewRemoteHost>"
            "<NewExternalPort>%d</NewExternalPort>"
            "<NewProtocol>TCP</NewProtocol>"
            "<NewInternalPort>%d</NewInternalPort>"
            "<NewInternalClient>%s</NewInternalClient>"
            "<NewEnabled>1</NewEnabled>"
            "<NewPortMappingDescription>%s</NewPortMappingDescription>"
            "<NewLeaseDuration>0</NewLeaseDuration>" % (port, port, lan_ip, description))
    try:
        soap(url, stype, "AddPortMapping", body)
        return True, "The router is now forwarding port %d to %s." % (port, lan_ip)
    except Exception as e:
        detail = ""
        if hasattr(e, "read"):
            try:
                m = re.search(r"<errorDescription>([^<]+)", e.read().decode("utf-8", "replace"))
                detail = " (" + m.group(1) + ")" if m else ""
            except Exception:
                pass
        return False, "The router refused the request%s." % detail


def unforward(lan_ip, port):
    """Take a mapping away. Returns (ok, message).

    A router will not quietly re-point a port that is already forwarded - it answers
    ConflictInMappingEntry - so moving one to another machine is a delete and an add.
    """
    desc = discover(lan_ip=lan_ip)
    if not desc:
        return False, "No router answered."
    url, stype = control_url(desc)
    if not url:
        return False, "The router answered but offers no port mapping service."
    body = ("<NewRemoteHost></NewRemoteHost>"
            "<NewExternalPort>%d</NewExternalPort>"
            "<NewProtocol>TCP</NewProtocol>" % port)
    try:
        soap(url, stype, "DeletePortMapping", body)
        return True, "The forwarding of port %d is gone." % port
    except Exception as e:
        return False, "The router refused to remove it (%s)." % str(e)[:60]


def status(lan_ip, port):
    """Whether a mapping for this port already exists, as the router sees it."""
    desc = discover(lan_ip=lan_ip)
    if not desc:
        return None
    url, stype = control_url(desc)
    if not url:
        return None
    try:
        body = ("<NewRemoteHost></NewRemoteHost><NewExternalPort>%d</NewExternalPort>"
                "<NewProtocol>TCP</NewProtocol>" % port)
        answer = soap(url, stype, "GetSpecificPortMappingEntry", body)
        m = re.search(r"<NewInternalClient>([^<]*)", answer)
        return m.group(1) if m else None
    except Exception:
        return None
