"""
Branch calculations: perturb copies of the live reactor and run a task on each, in parallel, in the middle of a run.

A case suite varies a case's *inputs* and builds each reactor from scratch. A branch calculation varies the *state* of
a reactor that already exists: a reactivity coefficient perturbs the fuel temperature at this time node, a rod worth
moves a bank, a depletion branch changes the boron. Both are the same shape (a base state, a perturbation, a task
that returns a dict of results), so branches reuse the suite's pieces:

* A :py:class:`StateModifier` perturbs a reactor in memory, as an
  :py:class:`~armi.cases.inputModifiers.inputModifiers.InputModifier` perturbs inputs. It carries independent
  variables the same way, so :py:class:`~armi.cases.sweeps.Sweep` builds branch sweeps with the same algebra.
* A task is ``task(r, cs) -> dict``, run in the branch's own directory (so it may write code inputs there).
* Each branch leaves a result file in its directory in the same format as a suite case, and
  :py:class:`BranchResults` tabulates them the same way.

Where the branches run is the executor's business, so the same call scales from a laptop to a cluster::

    from armi.cases.branches import FunctionStateModifier, runBranches
    from armi.cases.sweeps import Sweep


    def setFuelTemperature(r, temperature): ...


    sweep = Sweep.axis(FunctionStateModifier(setFuelTemperature, fuelTemp=t) for t in (600, 900, 1200))
    results = runBranches(r, cs, sweep, "mypackage.tasks:keff", ForkExecutor(maxWorkers=32))
    results.writeTable("fuelTemperature.csv")

* :py:class:`SerialExecutor` runs each branch on a copy, one after another.
* :py:class:`ForkExecutor` forks a process per branch on this machine. A forked child shares the parent's memory
  copy-on-write, so the state is never serialized and only small result files come back: the way to use every core
  of a big machine without MPI.
* :py:class:`MpiExecutor` distributes the state to the MPI ranks once and splits the branches between them.
* :py:class:`SnapshotExecutor` writes the state to a snapshot once and runs each branch as a job of any
  :py:class:`~armi.cases.runners.SuiteRunner`: subprocesses, another machine over SSH, or a Slurm job array on a
  cluster. Passing a runner to :py:func:`runBranches` uses this executor.

Each branch starts from the state as it was when the branches started, and no branch changes the reactor the run
goes on with: results come back as data for the caller to apply. Tasks should use only the reactor and settings they
are given. The reactor's operator and interfaces are there only in a forked child, and a child must not write to the
run's database.
"""

import json
import multiprocessing
import multiprocessing.connection
import os
import pickle
import shutil
import signal
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Union

from armi import context, mpiActions, runLog
from armi.cases import runners, sweeps
from armi.utils import caseWorker as worker

#: The state a :py:class:`SnapshotExecutor` hands to its jobs: a pickle of ``(reactor, settings)``.
SNAPSHOT_FILE = "branch-snapshot.pkl"

#: A branch's modifiers and task, pickled for its job.
POINT_FILE = "branch-point.pkl"

Task = Union[str, Callable]


class StateModifier:
    """
    Perturbs a reactor in memory, as an :py:class:`~armi.cases.inputModifiers.inputModifiers.InputModifier` perturbs
    the inputs of a case.

    Subclasses implement :py:meth:`__call__`, changing the reactor in place. Modifiers sent to other processes by
    :py:class:`MpiExecutor` or :py:class:`SnapshotExecutor` must pickle, so define them (and any functions they hold)
    at module level.

    Parameters
    ----------
    independentVariable : dict, optional
        Name/value pairs describing this perturbation, which become columns of the results table.
    """

    def __init__(self, independentVariable: Optional[dict] = None):
        self.independentVariable = independentVariable or {}

    def __call__(self, r) -> None:
        raise NotImplementedError

    def __repr__(self):
        return f"<{type(self).__name__} {self.independentVariable}>"


class FunctionStateModifier(StateModifier):
    """
    Perturb the reactor with ``function(r, **independentVariable)``, for changes that do not deserve a class.

    Examples
    --------
    >>> FunctionStateModifier(setBoron, ppm=600)  # calls setBoron(r, ppm=600)
    """

    def __init__(self, function: Callable, **independentVariable):
        StateModifier.__init__(self, independentVariable)
        self.function = function

    def __call__(self, r) -> None:
        self.function(r, **self.independentVariable)


@dataclass
class Branch:
    """One perturbed state to run: its title, the modifiers that make it and the directory it runs in."""

    title: str
    point: tuple
    directory: str

    @property
    def independentVariables(self) -> dict:
        return sweeps.independentVariables(self.point)


class BranchResults(dict):
    """The result of each branch, by title, in the format of a suite case's result file."""

    def table(self) -> List[dict]:
        """One row per branch: its title, status, wall time, independent variables and scalar results."""
        return [runners.resultRow(title, result) for title, result in self.items()]

    def writeTable(self, path: str) -> str:
        """Write :py:meth:`table` as CSV."""
        return runners.writeTable(self.table(), path)

    def toDataFrame(self):
        """:py:meth:`table` as a pandas DataFrame (pandas must be installed)."""
        import pandas as pd

        return pd.DataFrame(self.table()).set_index("case")

    def failed(self) -> List[str]:
        """Titles of the branches that did not succeed."""
        return [title for title, result in self.items() if result.get("status") != "ok"]


def runBranches(
    r,
    cs,
    sweep: Iterable,
    task: Task,
    executor=None,
    directory: str = "branches",
    names: Optional[Callable[[int, tuple], str]] = None,
) -> BranchResults:
    """
    Run ``task`` on a perturbed copy of the reactor for each point of ``sweep`` and return the results.

    Parameters
    ----------
    r : Reactor
        The state every branch starts from. It is not changed.
    cs : Settings
        The settings handed to each task.
    sweep : Sweep or iterable of points
        Each point is a tuple of :py:class:`StateModifier` objects (a bare modifier is a point of one).
    task : callable or str
        ``task(r, cs) -> dict``, or its ``"module:function"`` name. It runs in its branch's directory, after the
        modifiers, and returns JSON-serializable results.
    executor : Executor or SuiteRunner, optional
        Where the branches run. A :py:class:`~armi.cases.runners.SuiteRunner` runs them as snapshot jobs. Defaults to
        :py:class:`SerialExecutor`.
    directory : str
        Where the branch directories go.
    names : callable, optional
        ``names(index, point) -> title``. Defaults to ``branch000``, ``branch001``, and so on.
    """
    if isinstance(executor, runners.SuiteRunner):
        executor = SnapshotExecutor(executor)
    executor = executor or SerialExecutor()
    root = os.path.abspath(directory)
    names = names or (lambda index, _point: f"branch{index:03d}")
    branches = []
    for index, point in enumerate(sweep):
        point = tuple(point) if isinstance(point, (tuple, list)) else (point,)
        for modifier in point:
            if not isinstance(modifier, StateModifier):
                raise TypeError(f"{modifier!r} is not a StateModifier; branches perturb the reactor, not its inputs")
        branches.append(Branch(names(index, point), point, os.path.join(root, names(index, point))))
    titles = [b.title for b in branches]
    if len(set(titles)) != len(titles):
        raise ValueError("Branch titles must be unique")
    os.makedirs(root, exist_ok=True)
    runLog.important(f"Running {len(branches)} branches with {type(executor).__name__} in {root}")
    results = executor.run(r, cs, branches, task, root)
    return BranchResults((b.title, results[b.title]) for b in branches)


class Executor:
    """Runs branches somewhere. Subclasses implement :py:meth:`run`."""

    def run(self, r, cs, branches: Sequence[Branch], task: Task, root: str) -> Dict[str, dict]:
        """Run every branch and return each one's result, by title."""
        raise NotImplementedError


class SerialExecutor(Executor):
    """Run each branch on its own copy of the state, one after another, in this process."""

    def run(self, r, cs, branches, task, root):
        results = {}
        for branch in branches:
            rCopy, csCopy = copyState(r, cs)
            results[branch.title] = _logged(runBranch(rCopy, csCopy, branch, task))
        return results


class ForkExecutor(Executor):
    """
    Run each branch in a forked child process on this machine, ``maxWorkers`` at a time.

    A child starts with the parent's memory, shared copy-on-write, so the state costs nothing to hand over and the
    child may change it freely. Each child gets its own process group, so a branch that runs past ``timeout`` is
    killed with any codes it started.

    Forking needs a POSIX system and cannot be mixed with MPI. Forking while other threads hold locks can deadlock the
    child, so start branches from the main thread of the run, not from inside a thread pool.

    Parameters
    ----------
    maxWorkers : int, optional
        Branches at once. Defaults to as many as fit on the cores at ``threadsPerWorker`` each.
    timeout : float, optional
        Seconds a branch may run.
    threadsPerWorker : int, optional
        Threads each branch's codes may use, set as ``OMP_NUM_THREADS`` in the child.
    """

    def __init__(self, maxWorkers: Optional[int] = None, timeout: Optional[float] = None, threadsPerWorker=None):
        self.maxWorkers = maxWorkers or max(1, (os.cpu_count() or 1) // (threadsPerWorker or 1))
        self.timeout = timeout
        self.threadsPerWorker = threadsPerWorker

    def run(self, r, cs, branches, task, root):
        if context.MPI_SIZE > 1:
            raise RuntimeError("ForkExecutor cannot run under MPI; use MpiExecutor instead")
        if "fork" not in multiprocessing.get_all_start_methods():
            raise RuntimeError("ForkExecutor needs a system that can fork; use SnapshotExecutor instead")
        # Python 3.14 defaults to forkserver on POSIX; only fork shares the parent's state
        forking = multiprocessing.get_context("fork")
        task = _resolve(task)
        pending = list(branches)
        running = {}  # sentinel -> (process, branch, deadline)
        try:
            while pending or running:
                while pending and len(running) < self.maxWorkers:
                    branch = pending.pop(0)
                    _prepare(branch)
                    process = forking.Process(
                        target=_forkedBranch, args=(r, cs, branch, task, self.threadsPerWorker), name=branch.title
                    )
                    process.start()
                    deadline = time.monotonic() + self.timeout if self.timeout else None
                    running[process.sentinel] = (process, branch, deadline)
                deadlines = [d for _p, _b, d in running.values() if d is not None]
                wait = max(0.0, min(deadlines) - time.monotonic()) if deadlines else None
                for sentinel in multiprocessing.connection.wait(list(running), timeout=wait):
                    process, branch, _deadline = running.pop(sentinel)
                    process.join()
                    _logged(_resultOf(branch, f"the branch process ended with exit code {process.exitcode}"))
                now = time.monotonic()
                for sentinel, (process, branch, deadline) in list(running.items()):
                    if deadline is not None and now >= deadline:
                        _killGroup(process)
                        del running[sentinel]
                        _logged(_writeResult(branch, "timeout", wallSeconds=self.timeout))
        finally:
            # interrupted (Ctrl-C, say): leave no branch running
            for process, _branch, _deadline in running.values():
                _killGroup(process)
        return {branch.title: _resultOf(branch) for branch in branches}


class MpiExecutor(Executor):
    """
    Split the branches between the MPI ranks of an ARMI run, each running its share on copies of the state.

    The workers must be waiting in their worker loop, as they are during an ``OperatorMPI`` run. Without MPI, the
    branches run serially. Tasks and modifiers are broadcast, so they must pickle (define them at module level).

    Parameters
    ----------
    distributeState : bool
        Distribute the primary's state to the workers first. Pass False if it has just been distributed.
    """

    def __init__(self, distributeState: bool = True):
        self.distributeState = distributeState

    def run(self, r, cs, branches, task, root):
        if not context.MPI_DISTRIBUTABLE or context.MPI_SIZE < 2:
            return SerialExecutor().run(r, cs, branches, task, root)
        if r.o is None:
            raise RuntimeError("MpiExecutor needs the reactor's operator, to reach the MPI workers")
        if self.distributeState:
            mpiActions.DistributeStateAction.invokeAsMaster(r.o, r, cs)
        action = _BranchAction(list(branches), task)
        action.broadcast()
        results = action.invoke(r.o, r, cs)
        return {branch.title: result for branch, result in zip(branches, results)}


class _BranchAction(mpiActions.MpiAction):
    """Run this rank's share of the branches and gather every result on the primary."""

    def __init__(self, branches: Sequence[Branch] = (), task: Task = ""):
        mpiActions.MpiAction.__init__(self)
        self.branches = list(branches)
        self.task = task

    def invokeHook(self):
        task = _resolve(self.task)
        mine = []
        for branch in self.mpiIter(self.branches):
            rCopy, csCopy = copyState(self.r, self.cs)
            mine.append(_logged(runBranch(rCopy, csCopy, branch, task)))
        gathered = self.gather(mine)
        return self.mpiFlatten(gathered) if context.MPI_RANK == 0 else None


class SnapshotExecutor(Executor):
    """
    Write the state to a snapshot once and run each branch as a job of a :py:class:`~armi.cases.runners.SuiteRunner`.

    Each branch directory gets the snapshot (hard-linked, so it costs no space locally), its pickled modifiers and
    task, and a job file for :py:mod:`armi.utils.caseWorker`, which loads the snapshot, applies the modifiers and runs
    the task wherever the runner puts it. A ``LocalRunner`` gives each branch a fresh process; an ``SshRunner`` copies
    each directory to another machine; a ``SlurmRunner`` submits all the branches as one job array and waits.

    The snapshot is a pickle, so the workers must run the same code (ARMI, the application and the task's module) as
    this process. Tasks and modifiers must pickle, so define them at module level in an importable module (not in
    the script being run).

    Parameters
    ----------
    runner : SuiteRunner
        Where the jobs run.
    timeout : float, optional
        Seconds a branch may run, enforced by the worker.
    """

    def __init__(self, runner: "runners.SuiteRunner", timeout: Optional[float] = None):
        self.runner = runner
        self.timeout = timeout

    def run(self, r, cs, branches, task, root):
        snapshot = os.path.join(root, SNAPSHOT_FILE)
        with open(snapshot, "wb") as f:
            pickle.dump((r, cs), f, protocol=pickle.HIGHEST_PROTOCOL)
        app = runners._appSpec()
        for branch in branches:
            _prepare(branch)
            _linkOrCopy(snapshot, os.path.join(branch.directory, SNAPSHOT_FILE))
            try:
                with open(os.path.join(branch.directory, POINT_FILE), "wb") as f:
                    pickle.dump((branch.point, task), f, protocol=pickle.HIGHEST_PROTOCOL)
            except (pickle.PicklingError, AttributeError, TypeError) as error:
                raise TypeError(
                    f"The modifiers and task of {branch.title} must pickle to run in another process; define them "
                    f"at module level in an importable module ({error})"
                ) from error
            job = {
                "title": branch.title,
                "hash": "",
                "kind": "branch",
                "snapshot": SNAPSHOT_FILE,
                "point": POINT_FILE,
                "app": app,
                "timeout": self.timeout,
                "dependencies": {},
                "independentVariables": runners._jsonable(branch.independentVariables),
            }
            with open(os.path.join(branch.directory, worker.JOB_FILE), "w") as f:
                json.dump(job, f, indent=1)
        argv = [self.runner.python, "-m", "armi.utils.caseWorker", "."]
        self.runner.runCommands([{"directory": b.directory, "argv": argv, "timeout": None} for b in branches])
        return {b.title: _logged(_resultOf(b, "the branch job left no result")) for b in branches}


def runSnapshotBranch(directory: str, job: dict) -> dict:
    """Load a :py:class:`SnapshotExecutor` job's state, modifiers and task, and run it (in the worker)."""
    with open(os.path.join(directory, job["snapshot"]), "rb") as f:
        r, cs = pickle.load(f)
    with open(os.path.join(directory, job["point"]), "rb") as f:
        point, task = pickle.load(f)
    _imposeBurnChain(cs)
    return runBranch(r, cs, Branch(job["title"], point, directory), _resolve(task))


def runBranch(r, cs, branch: Branch, task: Task) -> dict:
    """Apply the branch's modifiers to ``r`` (in place), run the task in the branch directory and write its result."""
    os.makedirs(branch.directory, exist_ok=True)
    start = time.time()
    cwd = os.getcwd()
    os.chdir(branch.directory)
    try:
        for modifier in branch.point:
            modifier(r)
        results = _resolve(task)(r, cs) or {}
        return _writeResult(branch, "ok", results, wallSeconds=round(time.time() - start, 1))
    except Exception:
        traceback.print_exc()
        return _writeResult(branch, "failed", wallSeconds=round(time.time() - start, 1), error=traceback.format_exc())
    finally:
        os.chdir(cwd)


def copyState(r, cs):
    """An independent copy of the reactor and settings, made as MPI makes one: by pickling them."""
    return pickle.loads(pickle.dumps((r, cs), protocol=pickle.HIGHEST_PROTOCOL))


def _forkedBranch(r, cs, branch, task, threads):
    """The body of a forked child: its own process group, then the branch."""
    os.setsid()
    if threads:
        os.environ["OMP_NUM_THREADS"] = str(threads)
    result = runBranch(r, cs, branch, task)
    sys.stdout.flush()
    sys.exit(0 if result["status"] == "ok" else 1)


def _killGroup(process):
    """Stop a forked child and everything it started: politely, then not."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        process.join(worker.GRACE_SECONDS if sig == signal.SIGTERM else None)
        if process.exitcode is not None:
            break


def _resolve(task: Task) -> Callable:
    if isinstance(task, str):
        from armi.cases import tasks

        return tasks.resolve(task)
    return task


def _job(branch: Branch) -> dict:
    return {"title": branch.title, "hash": "", "independentVariables": runners._jsonable(branch.independentVariables)}


def _writeResult(branch: Branch, status: str, results=None, wallSeconds=None, error=None) -> dict:
    try:
        return worker.writeResult(branch.directory, _job(branch), status, results, wallSeconds, error)
    except (TypeError, ValueError) as jsonError:
        message = f"The task's results are not JSON-serializable: {jsonError}"
        return worker.writeResult(branch.directory, _job(branch), "failed", None, wallSeconds, message)


def _resultOf(branch: Branch, whyMissing: str = "the branch has not run") -> dict:
    """The branch's result file, or a failed result if it left none."""
    result = worker.readJson(os.path.join(branch.directory, worker.RESULT_FILE))
    if result is None:
        result = _writeResult(branch, "failed", error=whyMissing)
    return result


def _prepare(branch: Branch):
    os.makedirs(branch.directory, exist_ok=True)
    resultPath = os.path.join(branch.directory, worker.RESULT_FILE)
    if os.path.exists(resultPath):
        os.remove(resultPath)


def _logged(result: dict) -> dict:
    wall = result.get("wallSeconds")
    message = f"Branch {result['title']}: {result['status']}" + (f" in {wall:.1f} s" if wall is not None else "")
    (runLog.important if result["status"] == "ok" else runLog.warning)(message)
    return result


def _linkOrCopy(source: str, destination: str):
    if os.path.lexists(destination):
        os.remove(destination)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def _imposeBurnChain(cs):
    """Impose the run's burn chain in a fresh worker process, as the run did when it started."""
    if not cs["initializeBurnChain"]:
        return
    if not os.path.exists(cs["burnChainFileName"]):
        runLog.warning(f"Burn chain {cs['burnChainFileName']} not found here; branches will use the default chain")
        return
    from armi.nucDirectory import nuclideBases

    with open(cs["burnChainFileName"]) as stream:
        nuclideBases.imposeBurnChain(stream)
