"""
Run the cases of a suite in parallel, locally, on another machine over SSH, or (by subclassing) on a batch system.

A :py:class:`SuiteRunner` turns each case into a job:

* **Cached.** A job's hash covers its input files, its task and the hashes of the jobs it depends on. A case whose
  directory already holds a successful result with the same hash is not run again, so a suite can be rerun after
  adding cases, after a crash or after editing one input, and only what changed runs. Pass ``rerun=True`` to force it.
* **Ordered.** Jobs run as soon as the cases they depend on (``Case.addExplicitDependency`` or plugin-defined
  dependencies) have succeeded, up to ``maxWorkers`` at once. Dependents of a failed case are skipped. A task receives
  its dependencies' directories, to reuse what they produced (see :py:func:`linkProducts`).
* **Self-contained.** The job file, the inputs and the results all live in the case directory, and the worker
  (:py:mod:`armi.utils.caseWorker`) only needs that directory. Paths between dependent cases are stored relative to each
  other, so a tree of cases can be run anywhere it is copied.

Backends implement one primitive, :py:meth:`SuiteRunner.runCommand`: run a command in a directory, wherever the backend
runs things, and leave the directory holding what it wrote. A case job is just the worker command run in the case
directory. :py:class:`LocalRunner` runs commands as subprocesses; :py:class:`SshRunner` copies the directory to a
remote host with rsync, runs the command there and copies it back. A PBS or Slurm backend would write a job script
that runs the command in the directory, submit it, wait for it and (if the batch system does not share the file
system) copy the directory back, e.g.::

    class PbsRunner(SuiteRunner):
        def runCommand(self, directory, argv, timeout=None, stdin=None, stdout=None):
            script = self.writeScript(directory, argv, timeout, stdin, stdout)  # cd, environment, walltime
            jobId = subprocess.check_output(["qsub", script], text=True).strip()
            return self.waitFor(jobId)  # poll qstat, return the exit status

Because the primitive knows nothing about cases, the same runners serve work in the middle of a run:
:py:meth:`SuiteRunner.runCommands` runs many external code inputs (one directory each) in parallel on whichever
backend it is given. Work that is pure Python on shared reactor state is a different shape and stays with
:py:mod:`armi.mpiActions` (broadcast the state, scatter actions, gather results).

Typical use::

    suite = builder.buildSuite(namingFunc=..., task="mypackage.tasks:screen")
    results = suite.run(LocalRunner(maxWorkers=10, timeout=600))
    suite.writeResultsTable("screen.csv")
"""

import concurrent.futures
import contextlib
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

from armi import runLog
from armi.cases import tasks
from armi.utils import caseWorker as worker

#: What :py:meth:`SuiteRunner.runCommand` returns for a command killed at its timeout.
TIMED_OUT = -9

if TYPE_CHECKING:
    from armi.cases.case import Case


@dataclass(eq=False)
class Job:
    """One case, ready to run: where it runs, what it runs and what it waits for."""

    case: "Case"
    directory: str
    settingsFile: str
    inputFiles: List[str]
    dependencies: List["Job"] = field(default_factory=list)
    hash: str = ""

    @property
    def title(self) -> str:
        return self.case.title

    @property
    def task(self) -> str:
        return self.case.task or tasks.DEFAULT_TASK

    def result(self) -> Optional[dict]:
        """The result in the case directory, if any."""
        return worker.readJson(os.path.join(self.directory, worker.RESULT_FILE))

    def isDone(self) -> bool:
        """Whether the directory already holds a successful result for exactly these inputs."""
        result = self.result()
        return bool(result) and result["status"] == "ok" and result["hash"] == self.hash

    def spec(self, app: Optional[str], timeout: Optional[float]) -> dict:
        return {
            "title": self.title,
            "hash": self.hash,
            "settings": self.settingsFile,
            "task": self.task,
            "app": app,
            "timeout": timeout,
            "dependencies": {d.title: os.path.relpath(d.directory, self.directory) for d in self.dependencies},
            "independentVariables": _jsonable(self.case.independentVariables),
        }


class SuiteRunner:
    """
    Runs a suite's cases as cached, dependency-ordered jobs, ``maxWorkers`` at a time.

    Parameters
    ----------
    maxWorkers : int
        Jobs running at once.
    timeout : float, optional
        Seconds a job may run before it and everything it started is killed.
    rerun : bool
        Run every job even if its directory holds a result for the same inputs.
    """

    #: The interpreter that runs case workers, wherever this runner runs them.
    python = "python3"

    def __init__(self, maxWorkers: int = 1, timeout: Optional[float] = None, rerun: bool = False):
        self.maxWorkers = maxWorkers
        self.timeout = timeout
        self.rerun = rerun

    def run(self, suite) -> Dict[str, dict]:
        """Run the suite and return each case's result, by title."""
        jobs = self.plan(suite)
        app = _appSpec()
        statuses: Dict[str, str] = {}
        pending = []
        for job in jobs:
            if job.isDone() and not self.rerun:
                statuses[job.title] = "cached"
            else:
                pending.append(job)
        runLog.important(
            f"{type(self).__name__}: {len(pending)} of {len(jobs)} cases to run, {len(jobs) - len(pending)} cached"
        )
        self.prepare([j for j in jobs if j in pending])

        running = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.maxWorkers) as pool:
            while pending or running:
                for job in list(pending):
                    depStatuses = [statuses.get(d.title) for d in job.dependencies]
                    if any(s in ("failed", "timeout", "skipped") for s in depStatuses):
                        statuses[job.title] = "skipped"
                        pending.remove(job)
                        runLog.warning(f"Skipping {job.title}: a case it depends on did not succeed")
                    elif all(s in ("ok", "cached") for s in depStatuses) and len(running) < self.maxWorkers:
                        with open(os.path.join(job.directory, worker.JOB_FILE), "w") as f:
                            json.dump(job.spec(app, self.timeout), f, indent=1)
                        running[pool.submit(self._executeTimed, job)] = job
                        pending.remove(job)
                if not running:
                    continue
                done, _ = concurrent.futures.wait(running, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    job = running.pop(future)
                    statuses[job.title] = self._status(job, future)

        return {job.title: self._finalResult(job, statuses[job.title]) for job in jobs}

    @staticmethod
    def _finalResult(job, status) -> dict:
        """The job's result file if it belongs to these inputs, else just its status."""
        result = job.result()
        if result and result["hash"] == job.hash:
            return result
        return {"title": job.title, "hash": job.hash, "status": status}

    def plan(self, suite) -> List[Job]:
        """Jobs for every enabled case, in dependency order, with their hashes."""
        cases = [c for c in suite if c.enabled]
        jobs = {}
        for case in cases:
            if not os.path.exists(case.cs.path):
                case.writeInputs(sourceDir=suite.cs.inputDirectory)
            directory = os.path.abspath(case.directory)
            jobs[case] = Job(
                case=case,
                directory=directory,
                settingsFile=os.path.relpath(os.path.abspath(case.cs.path), directory),
                inputFiles=_inputFiles(case),
            )
        for case, job in jobs.items():
            job.dependencies = [jobs[d] for d in case.dependencies if d in jobs]

        ordered: List[Job] = []
        visiting = set()

        def visit(job):
            if job in ordered:
                return
            if job in visiting:
                raise RuntimeError(f"Circular dependency through {job.title}")
            visiting.add(job)
            for dep in job.dependencies:
                visit(dep)
            visiting.discard(job)
            job.hash = _hashJob(job)
            ordered.append(job)

        for job in jobs.values():
            visit(job)
        return ordered

    def prepare(self, jobs: Sequence[Job]):
        """Hook before any job runs, e.g. to check a remote host or copy shared files."""

    def execute(self, job: Job):
        """Run the worker for ``job``, which leaves its result in ``job.directory``."""
        self.runCommand(job.directory, [self.python, "-m", "armi.utils.caseWorker", "."])

    def runCommand(
        self,
        directory: str,
        argv: Sequence[str],
        timeout: Optional[float] = None,
        stdin: Optional[str] = None,
        stdout: Optional[str] = None,
    ) -> int:
        """
        Run ``argv`` in ``directory`` (wherever this runner runs things) and return its exit code.

        This is the one primitive a backend implements. The directory is the unit of work: everything the command
        reads must be in it, and everything it writes must be there afterwards. ``stdin`` and ``stdout`` are file
        names in the directory. A command still running after ``timeout`` seconds is killed with everything it
        started, and :py:data:`TIMED_OUT` is returned.
        """
        raise NotImplementedError

    def runCommands(self, commands: Sequence[dict]) -> List[int]:
        """
        Run many independent commands, ``maxWorkers`` at a time, and return their exit codes in order.

        Each command is a dict of :py:meth:`runCommand` arguments. This serves the middle of a run as well as a
        suite: an interface that needs a hundred external code runs (code inputs, say) writes each into its own
        directory and hands them here, and they run locally, on another machine or on a batch system depending only
        on the runner it was given.
        """
        commands = [{"timeout": self.timeout, **c} for c in commands]
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.maxWorkers) as pool:
            return list(pool.map(lambda c: self.runCommand(**c), commands))

    def _executeTimed(self, job):
        start = time.time()
        self.execute(job)
        return time.time() - start

    def _status(self, job, future) -> str:
        try:
            future.result()
        except Exception as error:
            runLog.error(f"{job.title}: the runner failed: {error}")
            return "failed"
        result = job.result()
        status = result["status"] if result and result["hash"] == job.hash else "failed"
        wall = result.get("wallSeconds") if result else None
        runLog.important(f"{job.title}: {status}" + (f" in {wall:.1f} s" if wall is not None else ""))
        return status


class LocalRunner(SuiteRunner):
    """
    Run jobs and commands as subprocesses on this machine.

    Case workers get this interpreter and its ``sys.path``, so tasks and applications importable here (including
    modules found through ``sys.path`` changes made by a driver script) import the same way in the workers.

    Parameters
    ----------
    env : dict, optional
        Extra environment variables for every command, e.g. ``{"OMP_NUM_THREADS": "1"}``.
    """

    def __init__(self, maxWorkers: int = os.cpu_count() or 1, timeout=None, rerun=False, env=None):
        super().__init__(maxWorkers, timeout, rerun)
        self.python = sys.executable
        self.env = env or {}

    def runCommand(self, directory, argv, timeout=None, stdin=None, stdout=None) -> int:
        env = {**os.environ, **self.env, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
        with contextlib.ExitStack() as files:
            inFile = files.enter_context(open(os.path.join(directory, stdin))) if stdin else subprocess.DEVNULL
            outFile = files.enter_context(open(os.path.join(directory, stdout), "w")) if stdout else subprocess.DEVNULL
            process = subprocess.Popen(
                argv,
                cwd=directory,
                env=env,
                stdin=inFile,
                stdout=outFile,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                return process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                worker.killGroup(process)
                return TIMED_OUT


class SshRunner(SuiteRunner):
    """
    Run jobs and commands on another machine: copy each directory there with rsync, run, copy it back.

    The local tree under ``localRoot`` is mirrored under ``remoteRoot``, so dependent cases find each other there by
    the same relative paths. For case jobs, the remote host needs ARMI, the application and the task's module
    importable by ``python`` after ``setup`` runs.

    Parameters
    ----------
    host : str
        ``[user@]host`` as ``ssh`` takes it.
    remoteRoot : str
        Directory on the host that mirrors ``localRoot``.
    python : str
        The interpreter to run on the host.
    setup : str, optional
        Bash commands run before each command, e.g. ``"source ~/project/env.sh"``.
    pythonPath : list of str, optional
        Remote directories to put on ``PYTHONPATH`` (e.g. where the task's module lives).
    localRoot : str, optional
        Local directory mirrored to ``remoteRoot``. For a suite it defaults to the deepest directory holding every
        case; for bare commands, to each directory's parent.
    """

    def __init__(
        self,
        host: str,
        remoteRoot: str,
        python: str = "python3",
        setup: Optional[str] = None,
        pythonPath: Optional[List[str]] = None,
        localRoot: Optional[str] = None,
        maxWorkers: int = 1,
        timeout=None,
        rerun=False,
    ):
        super().__init__(maxWorkers, timeout, rerun)
        self.host = host
        self.remoteRoot = remoteRoot
        self.python = python
        self.setup = setup
        self.pythonPath = pythonPath or []
        self.localRoot = localRoot

    def plan(self, suite):
        jobs = super().plan(suite)
        if self.localRoot is None and jobs:
            self.localRoot = os.path.commonpath([j.directory for j in jobs] + [os.path.dirname(jobs[0].directory)])
        return jobs

    def remoteDirectory(self, directory: str) -> str:
        root = self.localRoot or os.path.dirname(os.path.abspath(directory))
        return os.path.join(self.remoteRoot, os.path.relpath(os.path.abspath(directory), root))

    def runCommand(self, directory, argv, timeout=None, stdin=None, stdout=None) -> int:
        directory = os.path.abspath(directory)
        remote = self.remoteDirectory(directory)
        command = shlex.join(argv)
        if timeout:
            command = f"timeout -k {worker.GRACE_SECONDS} {timeout} {command}"
        if stdin:
            command += f" < {shlex.quote(stdin)}"
        command += f" > {shlex.quote(stdout)} 2>&1" if stdout else " > /dev/null 2>&1"
        steps = [f"cd {shlex.quote(remote)}"]
        if self.setup:
            steps.append(self.setup)
        if self.pythonPath:
            steps.append(
                "export PYTHONPATH=" + shlex.quote(os.pathsep.join(self.pythonPath)) + '"${PYTHONPATH:+:$PYTHONPATH}"'
            )
        steps.append(command)
        self._ssh(f"mkdir -p {shlex.quote(remote)}")
        self._rsync(directory + "/", f"{self.host}:{remote}/")
        # the remote login shell may not be bash (fish, say), so run the steps through bash explicitly
        returnCode = self._ssh("bash -c " + shlex.quote(" && ".join(steps)), check=False)
        self._rsync(f"{self.host}:{remote}/", directory + "/")
        return TIMED_OUT if timeout and returnCode == 124 else returnCode

    def _ssh(self, command, check=True) -> int:
        return subprocess.run(
            ["ssh", "-o", "BatchMode=yes", self.host, command], check=check, stdin=subprocess.DEVNULL
        ).returncode

    @staticmethod
    def _rsync(source, destination):
        subprocess.run(["rsync", "-a", source, destination], check=True, stdin=subprocess.DEVNULL)


def linkProducts(source: str, destination: str, patterns: Sequence[str]) -> List[str]:
    """
    Symlink the files in ``source`` matching ``patterns`` into ``destination``, for a task to reuse a dependency's
    products (e.g. cross section libraries) instead of recomputing them. Returns the links made.
    """
    import glob

    os.makedirs(destination, exist_ok=True)
    links = []
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.join(source, pattern))):
            link = os.path.join(destination, os.path.basename(path))
            if os.path.lexists(link):
                os.remove(link)
            os.symlink(os.path.relpath(path, destination), link)
            links.append(link)
    return links


def _inputFiles(case) -> List[str]:
    """The settings, blueprints and any extra input files of a case, as absolute paths."""
    from armi.settings.fwSettings.globalSettings import CONF_LOADING_FILE

    paths = {os.path.abspath(case.cs.path)}
    loadingFile = case.cs[CONF_LOADING_FILE]
    if loadingFile:
        paths.add(os.path.abspath(os.path.join(case.cs.inputDirectory, loadingFile)))
    paths.update(os.path.abspath(p) for p in case.extraInputFiles)
    return sorted(p for p in paths if os.path.isfile(p))


def _hashJob(job: Job) -> str:
    """
    A hash of what determines a job's results: its task, its input files (by name and content, so the tree can move)
    and the hashes of its dependencies.
    """
    digest = hashlib.sha256(job.task.encode())
    for path in job.inputFiles:
        digest.update(os.path.basename(path).encode())
        with open(path, "rb") as f:
            digest.update(f.read())
    for dep in sorted(job.dependencies, key=lambda d: d.title):
        digest.update(dep.hash.encode())
    return digest.hexdigest()


def _appSpec() -> Optional[str]:
    """``module:Class`` of the configured application, for the workers to configure it too."""
    import armi

    if not armi.isConfigured():
        return None
    app = armi.getApp()
    return f"{type(app).__module__}:{type(app).__qualname__}"


def _jsonable(values: dict) -> dict:
    return {k: v if isinstance(v, (str, int, float, bool, type(None))) else repr(v) for k, v in values.items()}
