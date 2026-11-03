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
remote host with rsync, runs the command there and copies it back; :py:class:`SlurmRunner` submits each command as a
batch job on a cluster that shares the file system, and many at once as a job array. Another batch system (PBS, say)
follows the Slurm runner: write a script that runs the command in the directory, submit it and wait for it.

Because the primitive knows nothing about cases, the same runners serve work in the middle of a run:
:py:meth:`SuiteRunner.runCommands` runs many external code inputs (one directory each, optionally ordered) in parallel
on whichever backend it is given, and :py:func:`armi.cases.branches.runBranches` can hand perturbed copies of the
live reactor to a runner as snapshots. Collective operations within one calculation (scatter blocks, gather
results) stay with :py:mod:`armi.mpiActions`.

Typical use::

    suite = builder.buildSuite(namingFunc=..., task="mypackage.tasks:screen")
    results = suite.run(LocalRunner(maxWorkers=10, timeout=600))
    suite.writeResultsTable("screen.csv")
"""

import concurrent.futures
import contextlib
import hashlib
import json
import math
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence

from armi import runLog
from armi.cases import tasks
from armi.utils import caseWorker as worker

#: What :py:meth:`SuiteRunner.runCommand` returns for a command killed at its timeout.
TIMED_OUT = -9

#: What :py:meth:`SuiteRunner.runCommands` returns for a command not run because one it runs after failed.
SKIPPED = -2

#: What :py:class:`SlurmRunner` returns for a job that ended without recording an exit code (cancelled, say, or
#: killed at the batch system's time limit).
LOST = -1

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
        pending = [job for job in jobs if self.rerun or not job.isDone()]
        statuses = {job.title: "cached" for job in jobs if job not in pending}
        runLog.important(f"{type(self).__name__}: {len(pending)} of {len(jobs)} cases to run, {len(statuses)} cached")
        self.prepare(pending)

        position = {id(job): i for i, job in enumerate(pending)}
        waitsOn = [[position[id(d)] for d in job.dependencies if id(d) in position] for job in pending]
        outcomes = runGraph(
            len(pending), waitsOn, lambda i: self._runJob(pending[i], app), lambda s: s == "ok", self.maxWorkers
        )
        for job, status in zip(pending, outcomes):
            if status is None:
                runLog.warning(f"Skipped {job.title}: a case it depends on did not succeed")
            statuses[job.title] = status or "skipped"

        return {job.title: self._finalResult(job, statuses[job.title]) for job in jobs}

    def _runJob(self, job: Job, app: Optional[str]) -> str:
        """Write the job file, run the job and return its status."""
        with open(os.path.join(job.directory, worker.JOB_FILE), "w") as f:
            json.dump(job.spec(app, self.timeout), f, indent=1)
        try:
            self.execute(job)
        except Exception as error:
            runLog.error(f"{job.title}: the runner failed: {error}")
            return "failed"
        result = job.result()
        status = result["status"] if result and result["hash"] == job.hash else "failed"
        wall = result.get("wallSeconds") if result else None
        runLog.important(f"{job.title}: {status}" + (f" in {wall:.1f} s" if wall is not None else ""))
        return status

    @staticmethod
    def _finalResult(job, status) -> dict:
        """
        The job's result file if it belongs to these inputs, else just its status.

        Either way ``"outcome"`` says what happened this time: ``"cached"`` for a case whose earlier result stood,
        otherwise the status it ran to (or ``"skipped"``).
        """
        result = job.result()
        if result and result["hash"] == job.hash:
            return dict(result, outcome=status)
        return {"title": job.title, "hash": job.hash, "status": status, "outcome": status}

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

    def requestStop(self, directory: str):
        """Ask the case job running in ``directory`` to stop; its result then records ``"stopped"``."""
        with open(os.path.join(directory, worker.STOP_FILE), "w") as f:
            f.write("stop requested\n")

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
        Run many commands, ``maxWorkers`` at a time, and return their exit codes in order.

        Each command is a dict of :py:meth:`runCommand` arguments. This serves the middle of a run as well as a
        suite: an interface that needs a hundred external code runs (code inputs, say) writes each into its own
        directory and hands them here, and they run locally, on another machine or on a batch system depending only
        on the runner it was given.

        A command may also list, under ``"after"``, the positions of commands it must follow (a branch calculation
        after the reference it restarts from, say). It starts once they have all exited with 0, and is not run at all
        (:py:data:`SKIPPED`) if any of them did not.
        """
        commands = [{"timeout": self.timeout, **c} for c in commands]
        waitsOn = [list(c.pop("after", ())) for c in commands]
        codes = runGraph(
            len(commands), waitsOn, lambda i: self.runCommand(**commands[i]), lambda code: code == 0, self.maxWorkers
        )
        return [SKIPPED if code is None else code for code in codes]


class LocalRunner(SuiteRunner):
    """
    Run jobs and commands as subprocesses on this machine.

    Case workers get this interpreter and its ``sys.path``, so tasks and applications importable here (including
    modules found through ``sys.path`` changes made by a driver script) import the same way in the workers.

    Parameters
    ----------
    maxWorkers : int, optional
        Commands running at once. Defaults to as many as fit on this machine's cores at ``threadsPerJob`` each.
    env : dict, optional
        Extra environment variables for every command.
    threadsPerJob : int, optional
        Threads each command may use, passed to the codes it runs as ``OMP_NUM_THREADS``. Workers times threads should
        not exceed the cores, or the codes compete for them.
    """

    def __init__(self, maxWorkers: Optional[int] = None, timeout=None, rerun=False, env=None, threadsPerJob=None):
        cores = os.cpu_count() or 1
        super().__init__(maxWorkers or max(1, cores // (threadsPerJob or 1)), timeout, rerun)
        self.python = sys.executable
        self.env = dict(env or {})
        if threadsPerJob:
            self.env.setdefault("OMP_NUM_THREADS", str(threadsPerJob))

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
    syncEvery : float, optional
        Seconds between copies of the directory back here while its command runs, so its progress (logs, a database
        written as it goes) can be followed locally. By default it is copied back only when the command ends.
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
        syncEvery: Optional[float] = None,
    ):
        super().__init__(maxWorkers, timeout, rerun)
        self.host = host
        self.remoteRoot = remoteRoot
        self.python = python
        self.setup = setup
        self.pythonPath = pythonPath or []
        self.localRoot = localRoot
        self.syncEvery = syncEvery

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
        process = self._sshStart("bash -c " + shlex.quote(" && ".join(steps)))
        while True:
            try:
                returnCode = process.wait(timeout=self.syncEvery)
                break
            except subprocess.TimeoutExpired:
                # a copy taken mid-write is replaced by the next one, so a failure here is not worth stopping for
                subprocess.run(
                    ["rsync", "-a", f"{self.host}:{remote}/", directory + "/"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        self._rsync(f"{self.host}:{remote}/", directory + "/")
        return TIMED_OUT if timeout and returnCode == 124 else returnCode

    def requestStop(self, directory: str):
        remote = os.path.join(self.remoteDirectory(directory), worker.STOP_FILE)
        self._ssh(f"touch {shlex.quote(remote)}")

    def _sshStart(self, command) -> subprocess.Popen:
        return subprocess.Popen(["ssh", "-o", "BatchMode=yes", self.host, command], stdin=subprocess.DEVNULL)

    def _ssh(self, command, check=True) -> int:
        return subprocess.run(
            ["ssh", "-o", "BatchMode=yes", self.host, command], check=check, stdin=subprocess.DEVNULL
        ).returncode

    @staticmethod
    def _rsync(source, destination):
        subprocess.run(["rsync", "-a", source, destination], check=True, stdin=subprocess.DEVNULL)


class SlurmRunner(SuiteRunner):
    """
    Run jobs and commands as Slurm batch jobs, on a cluster whose compute nodes share this file system.

    Each command becomes a script in its directory (:py:attr:`SCRIPT_FILE`), submitted with ``sbatch --wait``, which
    returns when the job ends; up to ``maxWorkers`` are in the queue at once. :py:meth:`runCommands` submits all its
    commands as one job array instead, throttled to ``maxWorkers`` running at once, which suits a hundred branch
    calculations in the middle of a run far better than a hundred separate submissions. Each script records its
    command's exit code in its directory, so every array element reports its own.

    Nothing is copied: the directories must be on a file system the compute nodes see at the same path (as cluster
    home and project directories usually are).

    Parameters
    ----------
    python : str, optional
        The interpreter on the compute nodes. Defaults to this one, which a shared file system usually makes
        available there.
    setup : str, optional
        Bash commands run before each command, e.g. ``"module load openmpi && source ~/venv/bin/activate"``.
    pythonPath : list of str, optional
        Directories to put on ``PYTHONPATH`` for each command. Defaults to this ``sys.path``, as for
        :py:class:`LocalRunner`.
    cpusPerJob : int
        ``--cpus-per-task`` for each job, also passed to its codes as ``OMP_NUM_THREADS``.
    walltime : float, optional
        Seconds to request per job (``--time``). Defaults to a little over the command's timeout, or the
        cluster's default if there is none.
    sbatchArgs : list of str, optional
        More ``sbatch`` options, e.g. ``["--partition=short", "--account=myproject", "--mem=8G"]``.
    useArrays : bool
        Whether :py:meth:`runCommands` submits job arrays.
    """

    SCRIPT_FILE = "slurm-job.sh"
    EXIT_FILE = "slurm-job.exit"

    def __init__(
        self,
        maxWorkers: int = 100,
        timeout=None,
        rerun=False,
        python: Optional[str] = None,
        setup: Optional[str] = None,
        pythonPath: Optional[List[str]] = None,
        cpusPerJob: int = 1,
        walltime: Optional[float] = None,
        sbatchArgs: Sequence[str] = (),
        useArrays: bool = True,
    ):
        super().__init__(maxWorkers, timeout, rerun)
        self.python = python or sys.executable
        self.setup = setup
        self.pythonPath = [p for p in sys.path if p] if pythonPath is None else list(pythonPath)
        self.cpusPerJob = cpusPerJob
        self.walltime = walltime
        self.sbatchArgs = list(sbatchArgs)
        self.useArrays = useArrays

    def writeScript(self, directory, argv, timeout=None, stdin=None, stdout=None) -> str:
        """Write the script that runs ``argv`` in ``directory`` and records its exit code there; return its path."""
        directory = os.path.abspath(directory)
        command = shlex.join(argv)
        if timeout:
            command = f"timeout -k {worker.GRACE_SECONDS} {timeout} {command}"
        if stdin:
            command += f" < {shlex.quote(stdin)}"
        command += f" > {shlex.quote(stdout)} 2>&1" if stdout else ""
        lines = ["#!/bin/bash", f"cd {shlex.quote(directory)} || exit 1"]
        if self.setup:
            lines.append(self.setup)
        lines.append(f"export OMP_NUM_THREADS={self.cpusPerJob}")
        if self.pythonPath:
            lines.append(
                "export PYTHONPATH=" + shlex.quote(os.pathsep.join(self.pythonPath)) + '"${PYTHONPATH:+:$PYTHONPATH}"'
            )
        lines += [command, "code=$?", f"echo $code > {self.EXIT_FILE}", "exit $code"]
        exitFile = os.path.join(directory, self.EXIT_FILE)
        if os.path.exists(exitFile):
            os.remove(exitFile)
        path = os.path.join(directory, self.SCRIPT_FILE)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(path, 0o755)
        return path

    def runCommand(self, directory, argv, timeout=None, stdin=None, stdout=None) -> int:
        directory = os.path.abspath(directory)
        script = self.writeScript(directory, argv, timeout, stdin, stdout)
        name = os.path.basename(directory)
        self._sbatch(script, [f"--job-name={name}", f"--output={os.path.join(directory, 'slurm-%j.out')}"], timeout)
        return self._exitCode(directory)

    def runCommands(self, commands: Sequence[dict]) -> List[int]:
        """Run the commands as one job array, unless they are ordered (``"after"``) or arrays are off."""
        if len(commands) < 2 or not self.useArrays or any(c.get("after") for c in commands):
            return super().runCommands(commands)
        commands = [{"timeout": self.timeout, **c} for c in commands]
        directories = [os.path.abspath(c["directory"]) for c in commands]
        scripts = [self.writeScript(**c) for c in commands]
        arrayDir = os.path.commonpath(directories)
        handle, arrayScript = tempfile.mkstemp(prefix="slurm-array-", suffix=".sh", dir=arrayDir)
        with os.fdopen(handle, "w") as f:
            f.write("#!/bin/bash\n")
            f.write("scripts=(" + " ".join(shlex.quote(s) for s in scripts) + ")\n")
            f.write('exec bash "${scripts[$SLURM_ARRAY_TASK_ID]}"\n')
        timeouts = [c["timeout"] for c in commands if c["timeout"]]
        runLog.important(f"Submitting {len(commands)} commands as a Slurm job array, {self.maxWorkers} at a time")
        self._sbatch(
            arrayScript,
            [
                f"--array=0-{len(commands) - 1}%{self.maxWorkers}",
                f"--job-name={os.path.basename(arrayDir)}",
                f"--output={os.path.join(arrayDir, 'slurm-%A_%a.out')}",
            ],
            max(timeouts) if len(timeouts) == len(commands) else None,
        )
        return [self._exitCode(d) for d in directories]

    def _sbatch(self, script: str, extra: List[str], timeout: Optional[float]) -> int:
        """Submit ``script`` and wait for it (or, for an array, all of it) to end."""
        args = ["sbatch", "--wait", "--parsable", f"--cpus-per-task={self.cpusPerJob}"]
        walltime = self.walltime or (timeout + 2 * worker.GRACE_SECONDS + 60 if timeout else None)
        if walltime:
            args.append(f"--time={math.ceil(walltime / 60)}")
        args += self.sbatchArgs + extra + [script]
        return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL).returncode

    def _exitCode(self, directory: str) -> int:
        try:
            with open(os.path.join(directory, self.EXIT_FILE)) as f:
                code = int(f.read().strip())
        except (OSError, ValueError):
            return LOST
        return TIMED_OUT if code == 124 else code


def runGraph(
    count: int,
    waitsOn: Sequence[Sequence[int]],
    execute: Callable[[int], object],
    succeeded: Callable[[object], bool],
    maxWorkers: int,
) -> list:
    """
    Run ``execute(i)`` for ``i`` in ``range(count)``, ``maxWorkers`` at a time, each once those it waits on succeeded.

    ``waitsOn[i]`` lists the items ``i`` waits on, and ``succeeded`` judges what ``execute`` returned. An item is not
    run if something it waits on did not succeed (or was itself not run). Returns what ``execute`` returned for each
    item, in order, with ``None`` for those not run.
    """
    outcomes: list = [None] * count
    finished = [False] * count
    ok = [False] * count
    pending = list(range(count))
    running = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, maxWorkers)) as pool:
        while pending or running:
            progressed = False
            for i in list(pending):
                if any(finished[d] and not ok[d] for d in waitsOn[i]):
                    finished[i] = True
                elif all(ok[d] for d in waitsOn[i]) and len(running) < maxWorkers:
                    running[pool.submit(execute, i)] = i
                else:
                    continue
                pending.remove(i)
                progressed = True
            if not running:
                if pending and not progressed:
                    raise RuntimeError(f"Items {pending} wait on each other in a cycle")
                continue
            done, _ = concurrent.futures.wait(running, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                i = running.pop(future)
                outcomes[i] = future.result()
                finished[i] = True
                ok[i] = succeeded(outcomes[i])
    return outcomes


def resultRow(title: str, result: Optional[dict], independentVariables: Optional[dict] = None) -> dict:
    """
    A table row for one result: its title, status and wall time, independent variables, then scalar results.

    Non-scalar results (lists, nested dicts) stay in the result file.
    """
    result = result or {}
    row = {"case": title, "status": result.get("status", "not run"), "wallSeconds": result.get("wallSeconds")}
    row.update(independentVariables or result.get("independentVariables", {}))
    row.update(
        {k: v for k, v in result.get("results", {}).items() if isinstance(v, (str, int, float, bool, type(None)))}
    )
    return row


def writeTable(rows: Sequence[dict], path: str) -> str:
    """Write rows as CSV, with a column for every key any row has."""
    import csv

    columns = list(dict.fromkeys(k for row in rows for k in row))
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


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
