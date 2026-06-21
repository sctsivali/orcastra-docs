"""Host/IP detection and port checks (stdlib only)."""
import ipaddress
import socket


def default_route_ip():
    """The source IP the kernel would use for the default route, found without sending a
    packet (UDP connect to TEST-NET-1, which is never routed). Best single candidate."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _is_offerable(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return False
    # Skip the default Docker bridge range so we never offer a container-internal IP.
    if addr in ipaddress.ip_network("172.17.0.0/16"):
        return False
    return True


def candidate_ips(proc):
    """Ordered, de-duplicated list of usable IPv4 candidates: default-route IP first, then
    any others parsed from `ip -json addr` (falls back to hostname resolution)."""
    out = []
    primary = default_route_ip()
    if primary and _is_offerable(primary):
        out.append(primary)
    res = proc.run(["ip", "-json", "addr"])
    if res.ok and res.out.strip():
        import json
        try:
            for iface in json.loads(res.out):
                for a in iface.get("addr_info", []):
                    if a.get("family") == "inet":
                        ip = a.get("local")
                        if ip and _is_offerable(ip) and ip not in out:
                            out.append(ip)
        except ValueError:
            pass
    if not out:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if _is_offerable(ip) and ip not in out:
                    out.append(ip)
        except socket.gaierror:
            pass
    return out


def fqdn():
    name = socket.getfqdn()
    if name and "." in name and name != "localhost.localdomain":
        return name
    return None


def is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def tcp_port_free(host: str, port: int) -> bool:
    """True if we can bind the port (i.e. it is free)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()
