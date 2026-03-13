"""Provide network related functions."""

import socket
from typing import NamedTuple

import psutil

# Arbitrary routable IP used to determine the local network interface.
# No traffic is actually sent; the OS just picks the right source address.
IP_DETECTION_IP = "168.119.98.102"
IP_DETECTION_PORT = 80
LOCALHOST_IP = '127.0.0.1'


class NetworkInfo(NamedTuple):
    """Network information tuple."""

    ip: str
    mac: str


def get_local_ip_and_mac() -> NetworkInfo:
    """Get the local IP and MAC address of the machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    try:
        # doesn't even have to be reachable
        # we just need to know the local ip
        # so we can send it to simplyprint
        s.connect((IP_DETECTION_IP, IP_DETECTION_PORT))
        local_ip = s.getsockname()[0]
    except socket.error:
        local_ip = LOCALHOST_IP
    finally:
        s.close()

    nics = psutil.net_if_addrs()
    for iface in nics:
        if iface == 'lo':
            continue
        mac = None
        found = False
        for addr in nics[iface]:
            if addr.family == socket.AF_INET and addr.address == local_ip:
                found = True
            if addr.family == psutil.AF_LINK:
                mac = addr.address
        if found:
            break
    return NetworkInfo(ip=local_ip, mac=mac)
