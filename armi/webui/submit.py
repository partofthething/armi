"""
Submitting the open case to run somewhere, and following it as it goes.

The editor sets a case up; this runs it, on this machine, on another one over SSH, or on a Slurm cluster, through the
same :py:mod:`armi.cases.runners` a case suite uses. A submission is a suite of one case, so it gets what suites get:

- the case runs in its own folder, where its outputs land, wherever the runner puts it;
- the application the editor was started with runs it, so a plugin's settings and physics are there;
- a case whose inputs have not changed since it last succeeded is not run again, unless asked to be.

Everything there is to know about the run is in files in the case folder: the job, the worker's log, the result, and
the database the run writes. That is what is followed here, so a run on another machine is followed the same way once
its runner copies the folder back as it goes (:py:class:`~armi.cases.runners.SshRunner` ``syncEvery``). Progress is
read off the log: ARMI says when it writes each time node to the database, and the case settings say how many there will
be.

One submission at a time per editor. Nothing here waits on the run; the browser polls for news.
"""

import pathlib
import re
import shlex
import threading
import time
import traceback

from armi import runLog
from armi.utils import caseWorker as worker

#: Lines of log handed back when the browser has not seen any yet.
TAIL_LINES = 400

#: Seconds between copies of a remote case folder back here while it runs.
SYNC_SECONDS = 20

#: Where a submission can go, and what it needs to know to get there.
TARGETS = ("local", "ssh", "slurm")

_STATEPOINT = re.compile(r"Writing to database for statepoint: /c(\d+)n(\d+)(\w*)")
_IMPORTANT = re.compile(r"^\[impt\]\s*(.+)$", re.MULTILINE)


def makeRunner(target: dict, rerun: bool = False):
    """The :py:class:`~armi.cases.runners.SuiteRunner` a target describes.

    A target is a dict with ``"kind"`` one of :py:data:`TARGETS` and, for the remote ones, where and how to run:

    - ``ssh``: ``host``, ``remoteRoot`` (the folder there that holds the case folder), and optionally ``python`` and
      ``setup`` (bash commands run first, e.g. activating an environment);
    - ``slurm``: optionally ``setup``, ``cpus``, ``walltimeHours`` and ``sbatchArgs`` (a string, split like a shell
      would). The cluster has to see this file system.

    ``timeoutHours`` applies to any of them.
    """
    from armi.cases import runners

    kind = target.get("kind", "local")
    hours = target.get("timeoutHours")
    timeout = float(hours) * 3600.0 if hours else None
    if kind == "local":
        return runners.LocalRunner(maxWorkers=1, timeout=timeout, rerun=rerun)
    if kind == "ssh":
        host = (target.get("host") or "").strip()
        remoteRoot = (target.get("remoteRoot") or "").strip()
        if not host or not remoteRoot:
            raise ValueError("Say which host to run on and which folder there to run in.")
        return runners.SshRunner(
            host,
            remoteRoot,
            python=(target.get("python") or "python3").strip(),
            setup=(target.get("setup") or "").strip() or None,
            maxWorkers=1,
            timeout=timeout,
            rerun=rerun,
            syncEvery=SYNC_SECONDS,
        )
    if kind == "slurm":
        walltime = target.get("walltimeHours")
        return runners.SlurmRunner(
            maxWorkers=1,
            timeout=timeout,
            rerun=rerun,
            setup=(target.get("setup") or "").strip() or None,
            cpusPerJob=int(target.get("cpus") or 1),
            walltime=float(walltime) * 3600.0 if walltime else None,
            sbatchArgs=shlex.split(target.get("sbatchArgs") or ""),
        )
    raise ValueError(f"A run can go to {', '.join(TARGETS)}, not {kind!r}.")


def describe(target: dict) -> str:
    """Where a target runs, in a few words."""
    kind = target.get("kind", "local")
    if kind == "ssh":
        return f"{target.get('host')}:{target.get('remoteRoot')}"
    if kind == "slurm":
        return "Slurm"
    return "this machine"


def expectedTimeNodes(cs) -> int:
    """How many time nodes the case will write to its database."""
    from armi.utils import getBurnSteps

    steps = getBurnSteps(cs)
    return sum(n + 1 for n in steps) if steps else 1


class Submission:
    """One case submitted to a runner, run on a thread of its own and followed through its folder.

    Parameters
    ----------
    settingsPath : str or pathlib.Path
        The case settings file. The case runs in its folder.
    target : dict
        Where to run it; see :py:func:`makeRunner`.
    rerun : bool
        Run it even if its folder holds a successful result for the same inputs.
    """

    def __init__(self, settingsPath, target: dict, rerun: bool = False):
        from armi import settings
        from armi.cases import case as armicase
        from armi.cases import suite as armisuite

        self.settingsPath = pathlib.Path(settingsPath).resolve()
        self.directory = self.settingsPath.parent
        self.target = dict(target)
        self.runner = makeRunner(self.target, rerun)
        cs = settings.Settings(str(self.settingsPath))
        self.title = cs.caseTitle
        self.totalNodes = expectedTimeNodes(cs)
        self.databasePath = self.directory / f"{self.title}.h5"
        self.suite = armisuite.CaseSuite(cs)
        self.suite.add(armicase.Case(cs))
        self.started = time.time()
        self.finished = None
        self.outcome = None  # the runner's status for the case once it is done: ok, cached, failed, ...
        self.error = None
        self.stopRequested = False
        self._logPath = self.directory / worker.LOG_FILE
        # A run that does start rewrites the log, so anything older belongs to an earlier one.
        self._staleLog = self._logStamp()
        self._thread = threading.Thread(target=self._run, name=f"armi-submit-{self.title}", daemon=True)
        self._thread.start()

    def _logStamp(self):
        try:
            stat = self._logPath.stat()
        except OSError:
            return None
        return (stat.st_ino, stat.st_mtime_ns)

    def _run(self):
        try:
            results = self.suite.run(self.runner)
            self.outcome = results.get(self.title, {}).get("outcome", "failed")
        except Exception as error:
            self.outcome = "failed"
            self.error = f"{type(error).__name__}: {error}"
            runLog.error(traceback.format_exc())
        finally:
            self.finished = time.time()

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    @property
    def state(self) -> str:
        """``running`` until the runner returns, then ``finished``, ``cached``, ``stopped`` or ``failed``."""
        if self.running:
            return "running"
        if self.outcome == "ok":
            return "finished"
        if self.outcome == "cached":
            return "cached"
        if self.stopRequested or self.outcome == "stopped":
            return "stopped"
        return "failed"

    def stop(self):
        """Ask the run to stop. The worker notices within a few seconds, ends everything it started, and says so."""
        if not self.running:
            return
        self.stopRequested = True
        try:
            self.runner.requestStop(str(self.directory))
        except Exception as error:
            raise RuntimeError(f"Could not ask the run to stop: {error}") from error

    def _log(self) -> bytes:
        if self._staleLog is not None and self._logStamp() == self._staleLog:
            return b""
        try:
            return self._logPath.read_bytes()
        except OSError:
            return b""

    def progress(self, text: str) -> dict:
        """The time nodes written so far, and the latest thing ARMI thought important enough to say."""
        written = []
        for match in _STATEPOINT.finditer(text):
            cycle, node, label = int(match.group(1)), int(match.group(2)), match.group(3)
            if not label:
                written.append([cycle, node])
        notes = _IMPORTANT.findall(text)
        return {
            "nodes": len(written),
            "totalNodes": self.totalNodes,
            "lastNode": written[-1] if written else None,
            "latest": notes[-1].strip() if notes else None,
        }

    def read(self, offset=None) -> dict:
        """Status and progress, plus the log from ``offset`` bytes on (or its tail, when offset is None)."""
        state = self.state
        data = self._log()
        if offset is None or offset > len(data):
            text = b"".join(data.splitlines(keepends=True)[-TAIL_LINES:])
        else:
            text = data[offset:]
        full = data.decode("utf-8", errors="replace")
        end = self.finished or time.time()
        return {
            "state": state,
            "title": self.title,
            "target": self.target,
            "where": describe(self.target),
            "directory": str(self.directory),
            "settings": str(self.settingsPath),
            "log": str(self._logPath),
            "text": text.decode("utf-8", errors="replace"),
            "offset": len(data),
            "elapsed": end - self.started,
            "progress": self.progress(full),
            "database": str(self.databasePath) if self.databasePath.is_file() else None,
            "databaseVersion": self._databaseVersion(),
            "error": self.error or self._resultError(state),
        }

    def _databaseVersion(self):
        """Changes whenever the run puts a new copy of its database in the case folder."""
        try:
            return self.databasePath.stat().st_mtime_ns
        except OSError:
            return None

    def _resultError(self, state):
        """What the worker said went wrong, from its result file."""
        if state != "failed":
            return None
        result = worker.readJson(self.directory / worker.RESULT_FILE)
        if result and result.get("error"):
            return result["error"].strip().splitlines()[-1]
        return None
