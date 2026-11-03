"""
The local server behind the editor.

Deliberately built on nothing but the standard library. The editor is a tool you reach for in the
middle of setting up a case, often on a cluster login node, and it should not be the reason an
install needs a web framework. There is no template engine and no build step either: the browser
loads the files in ``static/`` exactly as they sit in the package.

Because this process can write files anywhere the user can, it does not simply trust whatever
connects to it:

- It binds to loopback unless told otherwise.
- Every API call must carry the session token, which is handed out in the URL the browser opens.
  Without it, a page you happen to have open on some other site cannot drive your editor.
- The ``Host`` header must be a loopback name, which keeps a DNS rebinding attack from reaching a
  port that is otherwise only yours. Bound beyond loopback, the editor has to answer to whatever
  name other machines know it by, so the token is left to stand guard alone.
"""

import http.server
import json
import mimetypes
import posixpath
import secrets
import socket
import threading
import urllib.parse
import webbrowser
from pathlib import Path

from armi import runLog
from armi.webui.api import Download, Session

STATIC_ROOT = Path(__file__).parent / "static"
TOKEN_HEADER = "X-ARMI-Token"
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})


class GuiServer:
    """A loopback HTTP server wrapping one editing :py:class:`~armi.webui.api.Session`.

    Parameters
    ----------
    session : armi.webui.api.Session
        The document being edited.
    port : int, optional
        Port to bind. Zero, the default, takes whatever is free.
    host : str, optional
        Interface to bind. Loopback by default; anything else exposes the editor to whoever can
        reach this machine and learn the token.
    """

    def __init__(self, session, port=0, host="127.0.0.1"):
        self.session = session
        self.token = secrets.token_urlsafe(24)
        self.host = host
        self._server = _ThreadingServer((host, port), _Handler)
        self._server.gui = self
        self._thread = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def loopbackOnly(self) -> bool:
        return self.host in _ALLOWED_HOSTS

    @property
    def url(self) -> str:
        """The address to open, token and all."""
        host = self.host if not self.loopbackOnly else "127.0.0.1"
        if host in ("0.0.0.0", ""):
            host = socket.gethostname()
        return f"http://{host}:{self.port}/?t={self.token}"

    def start(self):
        """Serve in the background."""
        self._thread = threading.Thread(target=self._server.serve_forever, name="armi-gui", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        """Stop serving and release the port."""
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def serveForever(self):
        """Serve on this thread until interrupted."""
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            runLog.info("Shutting down the ARMI editor.")
        finally:
            self._server.server_close()


def serve(path=None, bpPath=None, csPath=None, port=0, host="127.0.0.1", openBrowser=True, block=True) -> GuiServer:
    """Start the editor.

    Parameters
    ----------
    path : str, optional
        A case settings file or a blueprints file. A settings file also brings in the blueprints it
        loads, which is usually what you want.
    bpPath : str, optional
        Blueprints file to open, if you would rather be explicit.
    csPath : str, optional
        Settings file to open, if you would rather be explicit.
    port : int, optional
        Port to bind, or zero for any free one.
    host : str, optional
        Interface to bind. Use ``0.0.0.0`` to reach the editor from another machine.
    openBrowser : bool, optional
        Point the default browser at it once it is up.
    block : bool, optional
        Serve on this thread. Set False to get the server back and keep going, which is how the
        tests drive it.
    """
    session = Session.forPath(path) if path else Session(bpPath, csPath)
    server = GuiServer(session, port=port, host=host)
    runLog.important(f"ARMI editor is at {server.url}")
    if openBrowser:
        # Threaded so that a machine with no browser, or a slow one, does not stall the server.
        threading.Thread(target=webbrowser.open, args=(server.url,), daemon=True).start()
    if not block:
        return server.start()
    server.serveForever()
    return server


class _ThreadingServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    address_family = socket.AF_INET
    gui: GuiServer = None


class _Handler(http.server.BaseHTTPRequestHandler):
    """Routes API calls to the session and everything else to ``static/``."""

    server_version = "ARMI-GUI"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------------------------------

    def log_message(self, fmt, *args):
        runLog.debug("gui: " + fmt % args)

    def _send(self, status, body: bytes, contentType: str, extraHeaders=()):
        self.send_response(status)
        self.send_header("Content-Type", contentType)
        self.send_header("Content-Length", str(len(body)))
        # Nothing here should ever be cached; the files change as the package is edited.
        self.send_header("Cache-Control", "no-store")
        for key, value in extraHeaders:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _sendJson(self, status, payload):
        try:
            body = json.dumps(payload)
        except TypeError as ee:
            # Dropping the connection here would leave the editor hanging with no idea why, so
            # report the encoding failure as the error it is.
            runLog.error(f"Could not encode the response to {self.path}: {ee}")
            status, body = 500, json.dumps({"error": f"Response could not be encoded: {ee}"})
        self._send(status, body.encode("utf-8"), "application/json; charset=utf-8")

    def _hostIsLocal(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in _ALLOWED_HOSTS

    def _authorized(self, query) -> bool:
        given = self.headers.get(TOKEN_HEADER) or (query.get("t") or [None])[0]
        return bool(given) and secrets.compare_digest(given, self.server.gui.token)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except ValueError as ee:
            raise ValueError(f"Request body is not valid JSON: {ee}") from ee
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    # -- verbs ------------------------------------------------------------------------------------

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def _dispatch(self, method):
        if self.server.gui.loopbackOnly and not self._hostIsLocal():
            self._sendJson(421, {"error": "This editor only answers to localhost."})
            return

        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        path = posixpath.normpath(urllib.parse.unquote(parsed.path))

        if not path.startswith("/api/"):
            self._serveStatic(path)
            return

        if not self._authorized(query):
            self._sendJson(403, {"error": "Missing or wrong session token."})
            return

        try:
            body = self._body() if method in ("POST", "PUT") else {}
            status, payload = self._route(method, path, body, {key: value[0] for key, value in query.items()})
        except ValueError as ee:
            status, payload = 400, {"error": str(ee)}
        except Exception as ee:  # pragma: no cover - last resort so the editor stays up
            runLog.error(f"Unhandled error serving {method} {path}: {ee}")
            status, payload = 500, {"error": f"{type(ee).__name__}: {ee}"}
        if isinstance(payload, Download):
            disposition = f'attachment; filename="{payload.filename}"'
            self._send(status, payload.body, payload.contentType, [("Content-Disposition", disposition)])
            return
        if isinstance(payload, (bytes, bytearray)):
            # Result arrays go as raw float32. As JSON, a parameter's whole history would be several
            # times larger and take the browser longer to parse than to draw.
            self._send(status, bytes(payload), "application/octet-stream")
            return
        self._sendJson(status, payload)

    def _route(self, method, path, body, query):
        session: Session = self.server.gui.session
        route = (method, path)
        routes = {
            ("GET", "/api/state"): lambda: (200, session.state()),
            ("GET", "/api/catalog"): lambda: (200, session.catalog),
            ("GET", "/api/files"): lambda: session.listFiles(query),
            ("GET", "/api/blueprints"): session.getBlueprints,
            ("PUT", "/api/blueprints"): lambda: session.putBlueprints(body),
            ("POST", "/api/open"): lambda: session.open(body),
            ("POST", "/api/blueprints/open"): lambda: session.openBlueprints(body),
            ("POST", "/api/blueprints/new"): lambda: session.newBlueprints(body),
            ("POST", "/api/blueprints/save"): lambda: session.saveBlueprints(body),
            ("GET", "/api/blueprints/yaml"): session.blueprintsYaml,
            ("POST", "/api/grids/expand"): lambda: session.expandGrid(body),
            ("POST", "/api/preview/block"): lambda: session.previewBlock(body),
            ("GET", "/api/settings"): session.getSettings,
            ("PUT", "/api/settings"): lambda: session.putSettings(body),
            ("POST", "/api/settings/save"): lambda: session.saveSettings(body),
            ("POST", "/api/run"): lambda: session.startRun(body),
            ("GET", "/api/run"): lambda: session.runStatus(query),
            ("POST", "/api/run/stop"): lambda: session.stopRun(body),
            ("GET", "/api/results"): session.resultsSummary,
            ("POST", "/api/results/open"): lambda: session.openResults(body),
            ("GET", "/api/results/positions"): lambda: session.resultsPositions(query),
            ("GET", "/api/results/series"): lambda: session.resultsSeries(query),
            ("GET", "/api/results/components"): lambda: session.resultsComponents(query),
            ("POST", "/api/results/componentHistory"): lambda: session.resultsComponentHistory(body),
            ("GET", "/api/results/componentRange"): lambda: session.resultsComponentRange(query),
            ("POST", "/api/fuelcost"): lambda: session.fuelCost(body),
            ("POST", "/api/fuelcost/pdf"): lambda: session.fuelCostPdf(body),
            ("GET", "/api/results/names"): lambda: session.resultsNames(query),
            ("GET", "/api/results/designs"): lambda: session.resultsDesigns(query),
            ("GET", "/api/results/block"): lambda: session.resultsBlock(query),
        }
        handler = routes.get(route)
        if handler is None:
            return 404, {"error": f"No route for {method} {path}."}
        return handler()

    # -- static -----------------------------------------------------------------------------------

    def _serveStatic(self, path):
        relative = "index.html" if path in ("/", "/.", "") else path.lstrip("/")
        target = (STATIC_ROOT / relative).resolve()
        try:
            target.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self._send(403, b"Forbidden", "text/plain; charset=utf-8")
            return
        if not target.is_file():
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return

        contentType, _encoding = mimetypes.guess_type(target.name)
        if target.suffix == ".js":
            # Some platforms still map .js to text/plain, which breaks ES module loading.
            contentType = "text/javascript"
        self._send(200, target.read_bytes(), f"{contentType or 'application/octet-stream'}; charset=utf-8")
