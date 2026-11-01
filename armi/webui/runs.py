"""
Running the open case from the editor.

The editor sets a case up; this lets it also start one, the same way ``python -m <app> run case.yaml``
would from a terminal, and follow its log. The case runs in a child process of its own:

- it is the application the editor was started with that runs it, so a plugin's settings and
  physics are there (``python -m myapp gui`` runs ``python -m myapp run``);
- it runs in the settings file's folder, where its outputs land, with its screen output going to
  ``<case title>.gui-run.log`` beside them;
- it outlives nothing: stopping it, or a second run, is a deliberate act.

Only one run at a time per editor. Nothing here waits on the child; the browser polls for news.
"""

import os
import pathlib
import signal
import subprocess
import sys
import threading
import time

from armi import getApp

#: Lines of log handed back when the browser has not seen any yet.
TAIL_LINES = 400


def launchCommand(settingsPath) -> list:
    """The command line that runs a case with the application this process is running.

    An application started as a module (``python -m myapp gui``) is run the same way. Otherwise the
    configured :py:class:`~armi.apps.App` class is rebuilt in a fresh interpreter, which is what
    every ARMI entry point does first.
    """
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    if spec is not None and spec.name.endswith(".__main__"):
        return [sys.executable, "-m", spec.name[: -len(".__main__")], "run", str(settingsPath)]
    app = type(getApp())
    bootstrap = (
        "import sys, armi, importlib; "
        f"armi.configure(getattr(importlib.import_module({app.__module__!r}), {app.__qualname__!r})()); "
        "from armi.cli import ArmiCLI; sys.exit(ArmiCLI().run())"
    )
    return [sys.executable, "-c", bootstrap, "run", str(settingsPath)]


class CaseRun:
    """One run of a case in a child process.

    Parameters
    ----------
    settingsPath : str or pathlib.Path
        The case settings file to run.
    command : list, optional
        What to run; by default :py:func:`launchCommand`.
    """

    def __init__(self, settingsPath, command=None):
        self.settingsPath = pathlib.Path(settingsPath).resolve()
        self.workdir = self.settingsPath.parent
        self.command = command or launchCommand(self.settingsPath)
        self.logPath = self.workdir / f"{self.settingsPath.stem}.gui-run.log"
        self.started = time.time()
        self.finished = None
        self.stopped = False
        self._lock = threading.Lock()
        self._log = open(self.logPath, "wb")
        self._process = subprocess.Popen(
            self.command,
            cwd=self.workdir,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            # Its own process group, so that stopping it reaches whatever it started too.
            start_new_session=(os.name == "posix"),
        )

    @property
    def returncode(self):
        code = self._process.poll()
        if code is not None and self.finished is None:
            with self._lock:
                if self.finished is None:
                    self.finished = time.time()
                    self._log.close()
        return code

    @property
    def running(self) -> bool:
        return self.returncode is None

    @property
    def state(self) -> str:
        code = self.returncode
        if code is None:
            return "running"
        if self.stopped:
            return "stopped"
        return "finished" if code == 0 else "failed"

    def stop(self):
        """Ask the run (and everything it started) to end; kill it if it will not."""
        if not self.running:
            return
        self.stopped = True
        if os.name == "posix":
            os.killpg(self._process.pid, signal.SIGTERM)
        else:
            self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(self._process.pid, signal.SIGKILL)
            else:
                self._process.kill()

    def read(self, offset=None) -> dict:
        """Status, plus the log from ``offset`` bytes on (or its tail, when offset is None)."""
        state = self.state
        try:
            data = self.logPath.read_bytes()
        except OSError:
            data = b""
        if offset is None or offset > len(data):
            lines = data.splitlines(keepends=True)
            text = b"".join(lines[-TAIL_LINES:])
        else:
            text = data[offset:]
        end = self.finished or time.time()
        return {
            "state": state,
            "returncode": self._process.poll(),
            "command": " ".join(self.command),
            "settings": str(self.settingsPath),
            "log": str(self.logPath),
            "text": text.decode("utf-8", errors="replace"),
            "offset": len(data),
            "elapsed": end - self.started,
        }
