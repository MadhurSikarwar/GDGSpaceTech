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


def serve(host="127.0.0.1", port=None, http=False, redirect_port=None):
    from cheroot import wsgi
    from orbitwatch.web import create_app

    if http:
        os.environ["ORBITWATCH_HTTP"] = "1"
    app = create_app(https=not http)
    port = port or (5000 if http else config.HTTPS_PORT)
    server = wsgi.Server((host, port), app, numthreads=THREADS, server_name="OrbitWatch", request_queue_size=128,
                         shutdown_timeout=5)
    servers = [server]
    if not http:
        from cheroot.ssl.builtin import BuiltinSSLAdapter
        from orbitwatch.certs import make_cert
        cert = os.getenv("ORBITWATCH_TLS_CERT")
        key = os.getenv("ORBITWATCH_TLS_KEY")
        if not (cert and key):
            cert, key = make_cert()
        server.ssl_adapter = BuiltinSSLAdapter(cert, key)
        rport = config.HTTP_REDIRECT_PORT if redirect_port is None else redirect_port
        if rport:
            servers.append(wsgi.Server((host, rport), _redirect_app(port), numthreads=4, server_name="OrbitWatch"))
    scheme = "http" if http else "https"
    print(f"OrbitWatch on {scheme}://{host}:{port}"
          + (f" (http://{host}:{servers[1].bind_addr[1]} redirects here)" if len(servers) > 1 else ""), flush=True)
    for extra in servers[1:]:
        threading.Thread(target=extra.safe_start, daemon=True, name="http-redirect").start()

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
