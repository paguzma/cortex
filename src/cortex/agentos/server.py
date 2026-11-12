"""cortex-agentos: sirve el panel Agent OS y sus pulsos EN VIVO.

  - `/`, `/index.html`, `/app.js`: el panel (web/)
  - `/data/state.json`, `/data/notes.json`, `/data/logs.json`: lo que escribe el recolector
  - `/events`: Server-Sent Events con cada lectura/escritura de cortex y cada corrida de
    rutina, 2-3 s después de ocurrir

Un proxy delante lo publica bajo `/os/` (quitando el prefijo) y hace la autenticación;
este servidor no autentica nada y solo escucha en 127.0.0.1. El recolector corre acá
mismo, en un hilo, al arrancar y cada 120 s: no hay otro proceso ni cron.

Sigue siendo «muestra, nunca almacena»: solo reenvía lo que aparece en basic-memory.log,
en el access.log del proxy y en los reportes de rutinas.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import collector, pulses

WEB = Path(__file__).resolve().parent / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}
DATA_FILES = ("state.json", "notes.json", "logs.json")
DEFAULT_PORT = 8702
COLLECT_EVERY_S = 120
COLLECT_DEBOUNCE_S = 3   # tras una escritura: se espera a que termine la ráfaga y se recolecta
MAX_CLIENTS = 8
HEARTBEAT_S = 15
TICK_S = 0.5
ROUTINE_POLL_S = 1.0


def _log(msg: str) -> None:
    print(f"cortex-agentos: {msg}", file=sys.stderr, flush=True)


# ---------- índices del grafo (para resolver destinos y enlaces de rutinas) ----------
class Indexes:
    def __init__(self):
        self.ids: set[str] = set()
        self.by_title: dict[str, str] = {}
        self.touches: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def update(self, graph: dict) -> None:
        ids, by_title, touches = collector.indexes(graph or {})
        with self._lock:
            self.ids, self.by_title, self.touches = ids, by_title, touches

    def snapshot(self):
        with self._lock:
            return self.ids, self.by_title, self.touches


# ---------- difusión a los clientes SSE ----------
class Broadcaster:
    def __init__(self, max_clients: int = MAX_CLIENTS):
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.max_clients = max_clients

    def subscribe(self) -> queue.Queue | None:
        with self._lock:
            if len(self._clients) >= self.max_clients:
                return None
            q: queue.Queue = queue.Queue(maxsize=500)
            self._clients.append(q)
            return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def emit(self, event: dict) -> None:
        data = json.dumps(event, ensure_ascii=False)
        with self._lock:
            for q in self._clients:
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass        # un cliente lento pierde pulsos, no frena a los demás


# ---------- rutinas en vivo: sondeo de current_run() y de los reportes ----------
class RoutineWatcher:
    """Convierte el estado de las rutinas en eventos de inicio y fin.

    `poll(current, reports)` recibe lo que devuelve runner.current_run() y los reportes
    vigentes (None si el directorio no cambió desde el último sondeo) y devuelve eventos
    {"type": "routine", "routine": "routine:<name>", "state": running|finished|failed, "ts"}.
    Un reporte nuevo es un fin aunque el inicio no se haya visto (corridas de < 1 s)."""

    def __init__(self):
        self.running: str | None = None
        self.seen: dict[str, str | None] | None = None   # rutina -> ts del último reporte

    def poll(self, current: dict | None, reports: dict[str, dict | None] | None) -> list[dict]:
        out: list[dict] = []
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        name = (current or {}).get("routine")
        ended: set[str] = set()
        if reports is not None:
            ts_now = {n: (r or {}).get("ts") for n, r in reports.items()}
            if self.seen is not None:
                for n, ts in sorted(ts_now.items()):
                    if ts and ts != self.seen.get(n):
                        ok = (reports[n] or {}).get("status") == "ok"
                        out.append({"type": "routine", "routine": "routine:" + n,
                                    "state": "finished" if ok else "failed", "ts": ts, "ok": ok})
                        ended.add(n)
            self.seen = ts_now
        if self.running and self.running != name and self.running not in ended:
            # terminó sin reporte (p. ej. saltada por falta de credencial)
            out.append({"type": "routine", "routine": "routine:" + self.running,
                        "state": "finished", "ts": now, "ok": None})
        if name and name != self.running:
            out.append({"type": "routine", "routine": "routine:" + name, "state": "running",
                        "ts": (current or {}).get("since") or now})
        self.running = name
        return out


def running_now() -> list[str]:
    try:
        from ..routines import runner  # noqa: PLC0415
        current = runner.current_run()
    except Exception:  # noqa: BLE001
        current = None
    return ["routine:" + current["routine"]] if current else []


class Live:
    """Todo el estado en vivo del servidor: índices, difusor, correlador y vigía de rutinas."""

    def __init__(self):
        self.indexes = Indexes()
        self.broadcaster = Broadcaster()
        self.correlator = pulses.Correlator()
        self.lock = threading.Lock()
        self.watcher = RoutineWatcher()
        self._reports_mtime: float | None = None
        self.stop = threading.Event()
        self.wake = threading.Event()   # una escritura o una corrida terminada adelantan la recolección
        self._clients: dict = {}
        self._clients_at = -1e9

    def publish(self, e: dict) -> None:
        ids, by_title, _ = self.indexes.snapshot()
        target = e.get("target") or pulses.resolve_target(e["ref"], ids, by_title)
        event = pulses.serialize(e, target)
        self.broadcaster.emit(event)
        if event.get("type") == "write":
            self.wake.set()

    # --- seguidores de logs ---
    def on_cortex_line(self, line: str) -> None:
        for e in pulses.cortex_events([line], datetime.min.replace(tzinfo=pulses.LOCAL_TZ)):
            with self.lock:
                self.correlator.feed_cortex(e, time.monotonic())

    def clients(self) -> dict:
        """IP -> nombre, de la config; se relee a lo sumo cada 10 s (el access.log es ruidoso)."""
        if time.monotonic() - self._clients_at > 10:
            from .. import config  # noqa: PLC0415
            try:
                self._clients = config.load().get("clients") or {}
            except Exception:  # noqa: BLE001
                self._clients = {}
            self._clients_at = time.monotonic()
        return self._clients

    def on_access_line(self, line: str) -> None:
        for p in pulses.mcp_posts([line], datetime.min.replace(tzinfo=pulses.LOCAL_TZ), self.clients()):
            with self.lock:
                self.correlator.feed_post(p, time.monotonic())

    def correlate_once(self) -> None:
        with self.lock:
            out = self.correlator.tick(time.monotonic())
        for e in out:
            self.publish(e)

    # --- rutinas ---
    def poll_routines(self) -> None:
        from ..routines import report, runner  # noqa: PLC0415
        try:
            current = runner.current_run()
        except Exception:  # noqa: BLE001
            current = None
        reports = None
        directory = report.reports_dir()
        try:
            mtime = directory.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime != self._reports_mtime or self.watcher.seen is None:
            self._reports_mtime = mtime
            reports = {n: report.read_report(n) for n in runner.ROUTINES}
        for ev in self.watcher.poll(current, reports):
            self.broadcaster.emit({k: v for k, v in ev.items() if k != "ok"})
            if ev["state"] in ("finished", "failed"):
                self.wake.set()
            if ev["state"] == "finished" and ev.get("ok"):
                _, _, touches = self.indexes.snapshot()
                ts = datetime.fromisoformat(ev["ts"])
                name = ev["routine"].removeprefix("routine:")
                for e in collector.run_events([{"routine": name, "ts": ts, "ok": True}], touches):
                    self.publish(e)

    # --- recolector ---
    def collect_once(self) -> None:
        try:
            state = collector.collect()
            self.indexes.update(state.get("graph") or {})
        except Exception as exc:  # noqa: BLE001 — un ciclo fallido no tumba el servidor
            _log(f"collector failed: {type(exc).__name__}: {exc}")

    def collect_loop(self, every: float = COLLECT_EVERY_S,
                     debounce: float = COLLECT_DEBOUNCE_S) -> None:
        """Recolecta al arrancar, cada `every` segundos y poco después de cada escritura:
        una nota nueva aparece en el grafo en segundos, no en dos minutos."""
        while not self.stop.is_set():
            self.collect_once()
            woke = self.wake.wait(every)
            if woke and not self.stop.is_set():
                self.stop.wait(debounce)
            self.wake.clear()

    def _loop(self, fn, every: float) -> None:
        while not self.stop.is_set():
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                _log(f"{getattr(fn, '__name__', 'loop')} failed: {type(exc).__name__}: {exc}")
            self.stop.wait(every)

    def follow(self, path_fn, on_line) -> None:
        """Como `tail -F`: empieza al final, sigue las líneas nuevas y reabre si el log rota
        (cambia el inodo) o lo truncan (queda más chico que lo ya leído)."""
        while not self.stop.is_set():
            path = path_fn()
            try:
                fh = open(path, "rb")
            except OSError:
                self.stop.wait(5)
                continue
            with fh:
                fh.seek(0, os.SEEK_END)
                inode = os.fstat(fh.fileno()).st_ino
                rest = b""          # línea a medio escribir: se completa en la próxima lectura
                while not self.stop.is_set():
                    chunk = fh.readline()
                    if chunk:
                        rest += chunk
                        if rest.endswith(b"\n"):
                            try:
                                on_line(rest.decode("utf-8", errors="replace").rstrip("\n"))
                            except Exception:  # noqa: BLE001
                                pass
                            rest = b""
                        continue
                    self.stop.wait(0.25)
                    try:
                        st = os.stat(path)
                    except OSError:
                        break
                    if st.st_ino != inode or st.st_size < fh.tell():
                        break

    def start(self, collect: bool = True) -> None:
        jobs = [(self.follow, (collector.bm_log, self.on_cortex_line)),
                (self.follow, (collector.access_log, self.on_access_line)),
                (self._loop, (self.correlate_once, TICK_S)),
                (self._loop, (self.poll_routines, ROUTINE_POLL_S))]
        if collect:
            jobs.append((self.collect_loop, ()))
        for target, args in jobs:
            threading.Thread(target=target, args=args, daemon=True).start()


# ---------- HTTP ----------
def make_handler(live: Live):
    class Handler(BaseHTTPRequestHandler):
        server_version = "cortex-agentos"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # el panel pide datos cada minuto: sin ruido
            pass

        def _send(self, status: int, body: bytes, ctype: str, cache: str = "no-store") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = self.path.split("?", 1)[0].split("#", 1)[0]
            if path in STATIC:
                name, ctype = STATIC[path]
                return self._send(200, (WEB / name).read_bytes(), ctype, "no-cache")
            if path.startswith("/data/") and path[len("/data/"):] in DATA_FILES:
                target = collector.output_dir() / path[len("/data/"):]
                try:
                    body = target.read_bytes()
                except OSError:
                    return self._send(404, b'{"error":"not collected yet"}', "application/json")
                return self._send(200, body, "application/json; charset=utf-8")
            if path == "/events":
                return self._events()
            return self._send(404, b"not found", "text/plain; charset=utf-8")

        def _events(self):
            q = live.broadcaster.subscribe()
            if q is None:
                return self._send(503, b"too many clients", "text/plain; charset=utf-8")
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(b"retry: 3000\n: connected\n\n")
                # foto de lo que ya corre: un panel recién abierto no espera al próximo inicio
                snap = json.dumps({"type": "routines_running", "routines": running_now()})
                self.wfile.write(f"data: {snap}\n\n".encode("utf-8"))
                self.wfile.flush()
                while not live.stop.is_set():
                    try:
                        data = q.get(timeout=HEARTBEAT_S)
                        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    except queue.Empty:
                        self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                live.broadcaster.unsubscribe(q)

    return Handler


def make_server(port: int, live: Live | None = None, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    live = live or Live()
    server = ThreadingHTTPServer((host, port), make_handler(live))
    server.daemon_threads = True
    server.live = live  # type: ignore[attr-defined]
    return server


def main() -> int:
    port = int(os.environ.get("CORTEX_AGENTOS_PORT", str(DEFAULT_PORT)))
    server = make_server(port)
    server.live.start(collect=True)  # type: ignore[attr-defined]
    _log(f"listening on 127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.live.stop.set()  # type: ignore[attr-defined]
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
