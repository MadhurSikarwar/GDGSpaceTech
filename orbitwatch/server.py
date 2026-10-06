"""Production serving: cheroot (a multi-threaded WSGI server) with TLS, plus an HTTP port that only redirects.

    ow serve                 HTTPS on ORBITWATCH_HTTPS_PORT (8443) + redirect from ORBITWATCH_HTTP_PORT (8080)
    ow serve --http --port N plain HTTP (development only; the session cookie is then not Secure)
    ow service               web server + scheduler as supervised child processes (restart on crash)

The certificate is the self-signed localhost one from `ow make-cert` unless
ORBITWATCH_TLS_CERT / ORBITWATCH_TLS_KEY point at a real one.
"""
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from orbitwatch import config

log = logging.getLogger(__name__)
THREADS = int(os.getenv("ORBITWATCH_THREADS", "48"))   # live streams hold a thread each
# Idle keep-alive connections kept open per server. 0 (the default here) closes each connection after its response.
# On Windows cheroot only notices a request on a reused connection at its next poll of the idle sockets, which cost
# every request about 62 ms (measured: 62 ms reused, 1.4 ms on a fresh connection over HTTP, 5 ms over HTTPS, on
# localhost). Behind a reverse proxy, or anywhere the connection set-up is expensive, set ORBITWATCH_KEEPALIVE=10.
KEEPALIVE = int(os.getenv("ORBITWATCH_KEEPALIVE", "0"))


CERT = config.CERT_DIR / "orbitwatch-localhost.crt"
KEY = config.CERT_DIR / "orbitwatch-localhost.key"


def make_cert():
    """(cert, key) paths of the self-signed certificate for localhost / 127.0.0.1 (SRS 4.2), created if needed."""
    import ipaddress
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    if CERT.exists() and KEY.exists():
        cert = x509.load_pem_x509_certificate(CERT.read_bytes())
        if cert.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=7):
            return str(CERT), str(KEY)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "OrbitWatch (development)")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    KEY.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                      serialization.NoEncryption()))
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(CERT), str(KEY)


def _no_delay_connection():
    """A connection class that sets TCP_NODELAY on every accepted socket.

    cheroot sets it on the listening socket only, and Windows does not pass it on to accepted sockets, so a response
    written in several pieces (headers, then a large body) can wait for the browser's delayed ACK between them."""
    import socket

    from cheroot.server import HTTPConnection

    class NoDelayConnection(HTTPConnection):
        def __init__(self, server, sock, *args, **kwargs):
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass            # a socket that is already closing; the connection handles that itself
            super().__init__(server, sock, *args, **kwargs)
    return NoDelayConnection


def _redirect_app(https_port):
    def app(environ, start_response):
        host = (environ.get("HTTP_HOST") or "localhost").split(":")[0]
        path = environ.get("PATH_INFO", "/")
        query = environ.get("QUERY_STRING")
        target = f"https://{host}{'' if https_port == 443 else f':{https_port}'}{path}{'?' + query if query else ''}"
        start_response("301 Moved Permanently", [("Location", target), ("Content-Length", "0"),
                                                 ("Cache-Control", "no-store")])
        return [b""]
    return app


def _ipv6_loopback_free(port):
    """True when this machine has IPv6 and ::1:port can be bound."""
    import socket
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", port))
        return True
    except OSError:
        return False


def _warm_up(app):
    """Do the first visitor's slow work before the first visitor: the 32,000 SGP4 records behind the globe take about
    1.5 s to build, and the dashboard counts, filter lookups and data coverage 0.2 to 0.6 s each. The requests go
    through the app itself, so they fill exactly the caches that real requests use."""
    client = app.test_client()
    for path in ("/api/visual/positions", "/api/stats", "/api/lookups", "/api/provenance"):
        try:
            client.get(path)
        except Exception:  # noqa: BLE001 - a failed warm-up (say, the database is still starting) must not stop the server
            log.warning("warm-up request %s failed", path, exc_info=True)


def serve(host="127.0.0.1", port=None, http=False, redirect_port=None):
    from cheroot import wsgi
    from orbitwatch.web import create_app

    if http:
        os.environ["ORBITWATCH_HTTP"] = "1"
    app = create_app(https=not http)
    port = port or (5000 if http else config.HTTPS_PORT)
    connection_class = _no_delay_connection()

    def make(bind, wsgi_app, threads, tls=None):
        srv = wsgi.Server(bind, wsgi_app, numthreads=threads, server_name="OrbitWatch", request_queue_size=128,
                          shutdown_timeout=5)
        srv.ConnectionClass = connection_class
        srv.keep_alive_conn_limit = KEEPALIVE
        if tls:
            from cheroot.ssl.builtin import BuiltinSSLAdapter
            srv.ssl_adapter = BuiltinSSLAdapter(*tls)
        return srv

    tls = None
    if not http:
        cert = os.getenv("ORBITWATCH_TLS_CERT")
        key = os.getenv("ORBITWATCH_TLS_KEY")
        tls = (cert, key) if (cert and key) else make_cert()
    rport = 0 if http else (config.HTTP_REDIRECT_PORT if redirect_port is None else redirect_port)

    # "localhost" resolves to ::1 first on Windows. With nothing listening there the browser waits about 300 ms
    # before it falls back to IPv4, on every new connection. When serving on the IPv4 loopback, listen on the IPv6
    # loopback too (loopback only, so the server is exactly as private as before).
    v6 = host in ("127.0.0.1", "localhost")
    server = make((host, port), app, THREADS, tls)
    servers = [server]
    if v6 and _ipv6_loopback_free(port):
        servers.append(make(("::1", port), app, THREADS, tls))
    if rport:
        servers.append(make((host, rport), _redirect_app(port), 4))
        if v6 and _ipv6_loopback_free(rport):
            servers.append(make(("::1", rport), _redirect_app(port), 2))
    scheme = "http" if http else "https"
    print(f"OrbitWatch on {scheme}://{host}:{port}"
          + (f" (http://{host}:{rport} redirects here)" if rport else ""), flush=True)
    for extra in servers[1:]:
        threading.Thread(target=extra.safe_start, daemon=True, name="extra-listener").start()
    threading.Thread(target=_warm_up, args=(app,), daemon=True, name="warm-up").start()

    def stop(*_):
        for s in servers:
            s.stop()
    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)
    try:
        server.safe_start()
    finally:
        stop()


def _kill_children_with_me():
    """Windows: put this process in a Job Object that kills every child when the supervisor dies, even if it
    is force-killed (otherwise orphaned children keep the ports and the scheduler lock). Elsewhere a no-op."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                       "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):   # 9 = extended limits
        return None
    if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
        return None
    return job   # keep the handle open for the supervisor's lifetime; children inherit the job


def service():
    """Run the web server and the scheduler as child processes; restart either if it dies."""
    try:
        job = _kill_children_with_me()  # noqa: F841 - must stay referenced while the supervisor runs
    except (OSError, AttributeError, ValueError) as exc:   # supervision still works without it
        log.warning("could not tie child processes to the supervisor: %s", exc)
        job = None
    exe = [sys.executable, str(config.REPO_ROOT / "manage.py")]
    specs = {"web": exe + ["serve"], "scheduler": exe + ["scheduler"]}
    procs, restarts, stopping = {}, {k: 0 for k in specs}, threading.Event()
    log_dir = config.LOG_DIR

    def start(name):
        out = open(log_dir / f"service-{name}.log", "ab")
        procs[name] = subprocess.Popen(specs[name], stdout=out, stderr=subprocess.STDOUT, cwd=str(config.REPO_ROOT))
        print(f"[service] started {name} (pid {procs[name].pid})", flush=True)

    def stop_all(*_):
        stopping.set()
        for p in procs.values():
            if p.poll() is None:
                p.terminate()
    signal.signal(signal.SIGINT, stop_all)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop_all)
    for name in specs:
        start(name)
    base = urlsplit(config.APP_BASE_URL)
    print(f"[service] OrbitWatch at {base.scheme}://{base.netloc}/ ; logs in {log_dir}", flush=True)
    while not stopping.is_set():
        time.sleep(2)
        for name, p in list(procs.items()):
            if p.poll() is not None and not stopping.is_set():
                restarts[name] += 1
                delay = min(60, 2 ** min(restarts[name], 5))
                print(f"[service] {name} exited with {p.returncode}; restarting in {delay}s "
                      f"(restart #{restarts[name]})", flush=True)
                time.sleep(delay)
                start(name)
    for p in procs.values():
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()
