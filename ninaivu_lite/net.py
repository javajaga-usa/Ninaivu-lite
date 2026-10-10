"""Ports and addresses (taken from Ninaivu's ``__main__`` and ``utils/tls``).

:func:`pick_port` keeps the app startable without anyone hunting down what
holds the preferred port; :func:`lan_addresses` finds the address a phone on
the same Wi-Fi should type, most likely first.
"""

from __future__ import annotations

import socket
import sys


def port_is_free(host: str, port: int) -> bool | None:
    """True if *port* can be bound on *host* now, False if something holds it,
    None if this user may not bind it at all."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        # Asked the way the server will bind: exclusive on Windows, reusable
        # elsewhere so a restart's TIME_WAIT does not read as "busy".
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except PermissionError:
            return None
        except OSError:
            return False


def pick_port(host: str, preferred: int, tries: int = 20) -> int:
    """*preferred* if free, else the next free one above it, else any free port."""
    bind_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    for candidate in range(preferred, min(preferred + tries, 65536)):
        # Both this computer's own address and the one the server will
        # really listen on: another program holding the port on a network
        # address leaves 127.0.0.1 free, and the start then failed.
        free = port_is_free(bind_host, candidate)
        if free is None and bind_host != host:
            free = port_is_free(host or "0.0.0.0", candidate)
        elif free and bind_host != host:
            free = port_is_free(host or "0.0.0.0", candidate) is not False
        if free:
            return candidate
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((bind_host, 0))
        return int(sock.getsockname()[1])


def _probe(target: str) -> str | None:
    """Which of our addresses the OS would use to reach *target*. No packet is
    sent: connecting a UDP socket only fixes the route."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((target, 9))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def _rank(address: str) -> int:
    """Lower sorts first: how likely is this the address to type on a phone?"""
    if address.startswith("192.168."):
        return 0
    if address.startswith("10."):
        return 1
    try:
        second = int(address.split(".")[1])
    except (IndexError, ValueError):
        return 9
    if address.startswith("172.") and 16 <= second <= 31:
        return 6 if second == 17 else 2      # 172.17 is Docker's bridge
    if address.startswith("169.254."):
        return 8                             # link-local: no DHCP happened
    if address.startswith("100.") and 64 <= second <= 127:
        return 7                             # CGNAT, Tailscale's range
    return 3


def lan_addresses() -> list[str]:
    """Every IPv4 address another device might reach this machine on, best first."""
    found: list[str] = []

    def note(address: str | None) -> None:
        if address and address not in found and not address.startswith("127."):
            found.append(address)

    for target in ("192.0.2.1", "192.168.0.1", "192.168.1.1", "10.0.0.1", "172.16.0.1"):
        note(_probe(target))
    if not found:
        # Only when the probes found nothing, and never for long: resolving this
        # computer's own name can stall for half a minute on a Mac (its .local
        # name goes out to the network), and the server must not wait for that.
        for address in _addresses_by_name(timeout=2.0):
            note(address)
    return sorted(found, key=lambda a: (_rank(a), a))


def _addresses_by_name(timeout: float) -> list[str]:
    """This computer's addresses by its own name, or nothing if that takes too long."""
    import threading

    result: list[str] = []

    def look() -> None:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                result.append(str(info[4][0]))
        except (OSError, UnicodeError):     # UnicodeError: a name the idna codec refuses
            pass

    thread = threading.Thread(target=look, name="own-name", daemon=True)
    thread.start()
    thread.join(timeout)
    return list(result) if not thread.is_alive() else []


def already_running(port: int, instance: str | None = None) -> bool:
    """Is Ninaivu Lite itself already answering on this port, and, given
    *instance* (the data folder's name, see lock.instance_id), is it the one
    on this data folder? A server too old to say which folder it is on
    counts as this one. A folder with no name yet has never had a server
    of 1.7.0 or later (one names its folder before it answers), so a server
    that names its own folder is another copy's."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            data = json.load(r)
    except Exception:  # noqa: BLE001 — nothing there, or something else is
        return False
    if not isinstance(data, dict) or data.get("app") != "Ninaivu Lite":
        return False
    theirs = data.get("instance")
    return theirs is None or theirs == instance
