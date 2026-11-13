# Copyright 2019 TerraPower, LLC
# Copyright 2026 Nick Touran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Running physics calculations, and the external codes many of them are.

Two layers:

* **Code runs.** A :py:class:`Code` says what an external code is: its executable, how to tell a run of it went well,
  how often a failed run is worth trying again. A :py:class:`CodeRun` is one run of a code on one input, in a run
  directory of its own, so the working directory is never changed and any number of runs can go at once. It runs on any
  :py:class:`~armi.cases.runners.SuiteRunner` (this machine, another over SSH, a Slurm cluster), with a thread count, a
  time limit and retries, brings its outputs back beside its input, and goes through the case's
  :py:class:`~armi.utils.outputCache.OutputCache`: a run already made anywhere the cache is shared is restored instead
  of made again. :py:func:`runAll` runs many at once, in dependency order.
* **Executers.** An :py:class:`Executer` turns the reactor into a calculation and the answer back into the reactor.
  :py:class:`DefaultExecuter` is the usual sequence: transform the geometry, write the input, run the code (a
  :py:class:`CodeRun` of ``options.code``), read the output, apply it, undo the transformation. It is split into
  :py:meth:`~DefaultExecuter.prepare`, :py:meth:`~DefaultExecuter.codeRun` and :py:meth:`~DefaultExecuter.finish`, so
  :py:func:`runExecuters` can run the codes of many executers side by side.
"""

import glob
import hashlib
import os
import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from armi import runLog
from armi.context import MPI_RANK, getFastPath

#: Data files larger than this are symlinked into a run directory rather than copied: libraries are hundreds of MB.
SYMLINK_ABOVE_BYTES = 10_000_000

#: How many of a listing's last lines are searched for a code's success marker, and shown when a run fails.
LISTING_TAIL = 40


# ---------------------------------------------------------------------------
# Codes and their runs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Code:
    """
    An external code: how to run it, and how to tell that a run of it went well.

    Parameters
    ----------
    name : str
        What the code is called, for the log.
    executable : str
        Path to the binary or script.
    successMarker : str, optional
        Text a successful run writes near the end of its listing. Without one, a run succeeded if it exited with 0.
    retries : int
        How many times to rerun an input that did not succeed: a code that occasionally fails on an input it then
        runs fine, as a threaded solver can, deserves one.
    inputOnStdin : bool
        Feed the input on standard input (``code < input``) rather than naming it on the command line.
    """

    name: str
    executable: str
    successMarker: Optional[str] = None
    retries: int = 0
    inputOnStdin: bool = True

    def command(self, inputName: str, threads: int) -> list:
        """The command line of a run on ``inputName`` with ``threads`` OpenMP threads."""
        argv = ["env", f"OMP_NUM_THREADS={threads}", self.executable]
        return argv if self.inputOnStdin else argv + [inputName]

    def succeeded(self, result: "RunResult") -> bool:
        if self.successMarker is None:
            return result.returnCode == 0
        return any(self.successMarker in line for line in result.listing[-LISTING_TAIL:])


class RunResult:
    """What a code run produced: its exit code, its listing, how long it took, and whether it came from the cache."""

    def __init__(
        self,
        code: Code,
        returnCode: int,
        listingPath: str,
        runDir: str,
        wallSeconds: float,
        cached: bool = False,
    ):
        self.code = code
        self.returnCode = returnCode
        self.listingPath = listingPath
        self.runDir = runDir
        self.wallSeconds = wallSeconds
        self.cached = cached
        self._listing: Optional[list] = None

    def __repr__(self):
        how = "cached" if self.cached else f"rc={self.returnCode} {self.wallSeconds:.1f}s"
        return f"<RunResult {self.code.name} {how} {self.listingPath}>"

    @property
    def listing(self) -> list:
        if self._listing is None:
            with open(self.listingPath, errors="replace") as f:
                self._listing = f.read().splitlines()
        return self._listing

    @property
    def succeeded(self) -> bool:
        return self.code.succeeded(self)

    def failureReason(self) -> str:
        """The end of the listing, where a code says what went wrong."""
        return "\n".join(self.listing[-LISTING_TAIL:])


class CodeRun:
    """
    One run of a code on one input, in a clean run directory of its own.

    A code that writes scratch files with fixed names corrupts any other run sharing its directory, and a file left by
    a crashed run can stop the next one, so every run gets a fresh directory with its input and data files in it, and
    only the listing and the outputs asked for come back beside the input. The working directory is never changed.

    Parameters
    ----------
    code : Code
    inputPath : str
        The input. Its directory is where the listing and outputs come back to, and its name without the extension
        (the run's *stem*) names the run.
    dataFiles : dict, optional
        ``{name in the run directory: path}`` of files the input reads: libraries, databases. Large ones are symlinked.
    outputs : sequence of str or (str, str)
        What the run produces that should come back: names or glob patterns, or ``(name in the run directory, name
        back)`` pairs to rename on the way (a code that always writes the same file name, run for many inputs).
        Missing ones are skipped.
    threads : int
        OpenMP threads.
    runDir : str, optional
        Where the run executes; by default ``<stem>.run`` beside the input.
    listingPath : str, optional
        Where the code's standard output goes; by default ``<stem>.out`` beside the input.
    timeoutSeconds : float, optional
        Stop a run that has not finished in this long, and count it as failed.
    retries : int, optional
        Reruns of a failed run; the code's own number by default.
    runner : armi.cases.runners.SuiteRunner, optional
        Where the code runs; this machine by default. A remote runner must see the data files at the same paths.
    cache : armi.utils.outputCache.OutputCache, optional
        Where finished runs are filed under the hash of what determines them (the input, with the run's stem left out,
        the data files, the outputs asked for, the executable): a run already there is restored, a new one that succeeds
        is filed.
    """

    def __init__(
        self,
        code: Code,
        inputPath: str,
        dataFiles: Optional[dict] = None,
        outputs: Sequence = (),
        threads: int = 1,
        runDir: Optional[str] = None,
        listingPath: Optional[str] = None,
        timeoutSeconds: Optional[float] = None,
        retries: Optional[int] = None,
        runner=None,
        cache=None,
    ):
        self.code = code
        self.inputPath = os.path.abspath(inputPath)
        self.dataFiles = dict(dataFiles or {})
        self.outputs = list(outputs)
        self.threads = threads
        base = os.path.dirname(self.inputPath)
        self.runDir = os.path.abspath(runDir) if runDir else os.path.join(base, f"{self.stem}.run")
        self.listingPath = os.path.abspath(listingPath) if listingPath else os.path.join(base, f"{self.stem}.out")
        self.timeoutSeconds = timeoutSeconds
        self.retries = code.retries if retries is None else retries
        self.runner = runner
        self.cache = cache
        self.retrieved: list = []

    def __repr__(self):
        return f"<CodeRun {self.code.name} {os.path.basename(self.inputPath)}>"

    @property
    def stem(self) -> str:
        """The input's name without its extension, which names the run."""
        return os.path.splitext(os.path.basename(self.inputPath))[0]

    @property
    def directory(self) -> str:
        """Where the listing and outputs come back to: the input's directory."""
        return os.path.dirname(self.inputPath)

    def run(self) -> RunResult:
        """Restore the run from the cache, or make it (with retries), bring back its outputs and file it."""
        key = self.cacheKey() if self.cache else None
        if key:
            cached = self.restore(key)
            if cached is not None:
                return cached
        result = self._runOnce()
        for attempt in range(self.retries):
            if result.succeeded:
                break
            runLog.warning(f"Retrying {os.path.basename(self.inputPath)} (attempt {attempt + 2})")
            result = self._runOnce()
        if key and result.succeeded:
            self.cache.store(
                key,
                [*self.retrieved, self.listingPath],
                name=self.stem,
                metadata={"input": os.path.basename(self.inputPath), "wallSeconds": result.wallSeconds},
            )
        return result

    def prepare(self) -> None:
        """A clean run directory holding the input and its data files."""
        if os.path.isdir(self.runDir):
            shutil.rmtree(self.runDir)
        os.makedirs(self.runDir)
        shutil.copy(self.inputPath, self.runDir)
        for name, source in self.dataFiles.items():
            target = os.path.join(self.runDir, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.getsize(source) > SYMLINK_ABOVE_BYTES:
                os.symlink(os.path.abspath(source), target)
            else:
                shutil.copy(source, target)

    def _runOnce(self) -> RunResult:
        from armi.cases.runners import TIMED_OUT, LocalRunner

        self.prepare()
        inputName = os.path.basename(self.inputPath)
        listingName = os.path.basename(self.listingPath)
        runLog.extra(f"Running {self.code.name} on {inputName} with {self.threads} threads")
        runner = self.runner or LocalRunner(maxWorkers=1)
        start = time.time()
        returnCode = runner.runCommand(
            self.runDir,
            self.code.command(inputName, self.threads),
            timeout=self.timeoutSeconds,
            stdin=inputName if self.code.inputOnStdin else None,
            stdout=listingName,
        )
        produced = os.path.join(self.runDir, listingName)
        if os.path.exists(produced):
            shutil.move(produced, self.listingPath)
        else:  # a batch job cancelled before it started, say
            with open(self.listingPath, "w") as listing:
                listing.write(f"The run left no listing (exit code {returnCode}).\n")
        if returnCode == TIMED_OUT:
            with open(self.listingPath, "a") as listing:
                listing.write(f"\nStopped after {self.timeoutSeconds:.0f} s, the time limit.\n")
        wall = time.time() - start
        self.retrieved = self.retrieve()
        result = RunResult(self.code, returnCode, self.listingPath, self.runDir, wall)
        if result.succeeded:
            runLog.info(f"{inputName} finished in {wall:.1f} s")
        else:
            runLog.error(f"{self.code.name} did not finish {inputName} normally:\n{result.failureReason()}")
        return result

    def retrieve(self) -> list:
        """Move the outputs asked for back beside the input; return where they went."""
        moved = []
        for output in self.outputs:
            if isinstance(output, str):
                pairs = [(path, os.path.basename(path)) for path in glob.glob(os.path.join(self.runDir, output))]
            else:
                pairs = [(os.path.join(self.runDir, output[0]), output[1])]
            for source, name in pairs:
                if os.path.exists(source):
                    shutil.move(source, os.path.join(self.directory, name))
                    moved.append(os.path.join(self.directory, name))
        return moved

    def cleanup(self) -> None:
        """Delete the run directory."""
        if os.path.isdir(self.runDir):
            shutil.rmtree(self.runDir)

    # -- the output cache ---------------------------------------------------------

    def cacheKey(self) -> str:
        """What determines the run's outputs: the input, the data files, the outputs asked for, the executable."""
        inputs = {os.path.basename(self.inputPath): self.inputPath, **self.dataFiles}
        outputs = [*self.outputs, os.path.basename(self.listingPath)]
        return self.cache.key(self.code.executable, inputs, outputs=outputs, name=self.stem)

    def restore(self, key: str) -> Optional[RunResult]:
        """The run filed under ``key``, its listing and outputs put where a run would leave them; or None."""
        if self.cache.retrieve(key, self.directory, name=self.stem) is None:
            return None
        listing = os.path.join(self.directory, os.path.basename(self.listingPath))
        if listing != self.listingPath:
            shutil.move(listing, self.listingPath)
        return RunResult(self.code, 0, self.listingPath, self.runDir, 0.0, cached=True)

    def fileInCache(self) -> Optional[str]:
        """
        File a run made without the cache -- its listing and outputs beside its input -- and return its key.

        For seeding a shared cache from the runs a study already made. Only a run whose listing shows it succeeded and
        whose outputs are there is filed; None otherwise.
        """
        if not os.path.exists(self.listingPath):
            return None
        if not RunResult(self.code, 0, self.listingPath, self.runDir, 0.0).succeeded:
            return None
        outputs = []
        for output in self.outputs:
            found = glob.glob(os.path.join(self.directory, output if isinstance(output, str) else output[1]))
            if not found and not isinstance(output, str):
                return None
            outputs.extend(found)
        if self.outputs and not outputs:
            return None
        key = self.cacheKey()
        self.cache.store(key, [*outputs, self.listingPath], name=self.stem)
        return key


def runAll(
    runs: Sequence[CodeRun],
    maxWorkers: Optional[int] = None,
    after: Optional[Sequence[Sequence[int]]] = None,
    runner=None,
) -> list:
    """
    Run many code runs at once, ``maxWorkers`` at a time, and return their results in order.

    Codes are rarely threaded well enough to use a machine on one input, so running many inputs side by side is the
    efficient use of it.

    Parameters
    ----------
    after : list of lists of int, optional
        For each run, the positions of runs it must follow (a branch calculation after the reference it restarts from,
        say). It starts once they have all succeeded and is skipped, with ``None`` as its result, if any did not.
    runner : armi.cases.runners.SuiteRunner, optional
        Where the runs that do not name a runner of their own go. Its ``maxWorkers`` is how many run at once when
        ``maxWorkers`` is not given.
    """
    from armi.cases.runners import runGraph

    if not runs:
        return []
    workers = maxWorkers or (runner.maxWorkers if runner else None)
    workers = min(len(runs), workers or os.cpu_count() or 1)
    runLog.info(f"Launching {len(runs)} code runs, {workers} at a time")
    for run in runs:
        run.runner = run.runner or runner
    return runGraph(
        len(runs),
        after or [[] for _ in runs],
        lambda i: runs[i].run(),
        lambda result: result.succeeded,
        workers,
    )


# ---------------------------------------------------------------------------
# Executers
# ---------------------------------------------------------------------------


class ExecutionOptions:
    """
    Everything an executer needs to know to run a calculation.

    .. impl:: Options for executing external calculations.
        :id: I_ARMI_EX0
        :implements: R_ARMI_EX

        Implements a basic container to hold and report options to be used in the execution of an external code (see
        :need:`I_ARMI_EX1`). Options are stored as instance attributes and can be dumped as a string using
        :py:meth:`~armi.physics.executers.ExecutionOptions.describe`, which includes the name and value of every public
        attribute.

        Parallel instances of a code get distinct run directories from
        :py:meth:`~armi.physics.executers.ExecutionOptions.setRunDirFromCaseTitle`, which hashes the case title with
        the label and appends the MPI rank.

    Attributes
    ----------
    code : Code
        The external code the calculation runs, if it runs one.
    inputFile : str
        Name of the main input, written into the working directory.
    outputFile : str
        Name of the code's listing (its standard output), which comes back to the working directory.
    extraInputFiles : list of (str, str)
        ``(source, name in the run directory)`` of further files the code reads: libraries, databases. Sources are
        relative to the case directory (where the run is) or absolute.
    extraOutputFiles : list of str or (str, str)
        Further files to bring back to the working directory: names or glob patterns, or ``(name in the run
        directory, name back)`` pairs.
    runDir : str
        Where the code runs, often on a local disk rather than a shared one (see :py:meth:`setRunDirFromCaseTitle`);
        by default beside the input.
    workingDir : str
        Where the input is written and the results come back to; set by :py:meth:`DefaultExecuter.prepare`.
    threads : int
        OpenMP threads of the code.
    timeoutSeconds : float
        Time limit of a run of the code.
    runner : armi.cases.runners.SuiteRunner
        Where the code runs; this machine if None.
    cache : armi.utils.outputCache.OutputCache
        The output cache runs are restored from and filed in; none if None.
    label : str
        A name for the run, the prefix of the files it writes.
    interfaceName : str
        The interface the executer runs for, which organizes saved physics files.
    savePhysicsFiles : bool
        Keep the inputs and outputs in a directory of their own per time node instead of the case directory.
    applyResultsToReactor : bool
        Update the reactor with the results. Off when a run is wanted for something other than moving the reactor on.
    """

    def __init__(self, label=None):
        self.code: Optional[Code] = None
        self.inputFile = None
        self.outputFile = None
        self.extraInputFiles = []
        self.extraOutputFiles = []
        self.runDir = None
        self.workingDir = None
        self.threads = 1
        self.timeoutSeconds = None
        self.runner = None
        self.cache = None
        self.label = label
        self.interfaceName = None
        self.applyResultsToReactor = True
        self.paramsToScaleSubset = None
        self.savePhysicsFiles = False

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.label}>"

    def fromUserSettings(self, cs):
        """Set options from a particular Settings object."""
        raise NotImplementedError()

    def fromReactor(self, reactor):
        """Set options from a particular reactor object."""
        raise NotImplementedError()

    def resolveDerivedOptions(self):
        """Called by executers right before executing."""

    def setRunDirFromCaseTitle(self, caseTitle: str) -> None:
        """
        A run directory on the fast path named from the case title, the label and the MPI rank.

        Optional: ``runDir`` can be anything. This gives a consistent, short name per case, label and rank.
        """
        caseString = f"{caseTitle}-{str(self.label)}".encode("utf-8")
        caseTitleHash = str(hashlib.sha1(caseString).hexdigest())[:8]
        self.runDir = os.path.join(getFastPath(), f"{caseTitleHash}-{MPI_RANK}")

    def describe(self) -> str:
        """Make a string summary of all options."""
        lines = ["Options summary:", "----------------"]
        for key, val in sorted(self.__dict__.items()):
            if not key.startswith("_"):
                lines.append(f"  {key:40s}{str(val)[:80]:80s}")
        return "\n".join(lines)


class Executer:
    """
    Short-lived object that coordinates a calculation step and updates a reactor.

    Notes
    -----
    This is deliberately **not** a :py:class:`~mpiActions.MpiAction`. Thus, Executers can run as
    potentially multiple steps in a parent (parallelizable ) MpiAction or in other flexible
    ways. This is intended to maximize reusability.
    """

    def __init__(self, options, reactor):
        self.options = options
        self.r = reactor

    def run(self):
        """
        Run the executer steps.

        This should use the current state of the reactor as input, perform some kind of calculation, and update the
        reactor with the output.
        """
        raise NotImplementedError()


class DefaultExecuter(Executer):
    """
    An Executer with the run sequence common to externally executed physics codes.

    * :py:meth:`prepare`: resolve the options, transform the geometry, choose the working directory and write the input
      into it;
    * :py:meth:`codeRun`: the run of ``options.code`` on that input, in its own run directory, through the case's
      runner and output cache;
    * :py:meth:`finish`: read the output, apply it to the reactor, undo the geometry transformation.

    :py:meth:`run` does the three in turn; :py:func:`runExecuters` does them for many executers with their codes running
    side by side. Nothing changes the working directory: :py:meth:`writeInput` writes :py:attr:`inputPath` and
    :py:meth:`_readOutput` reads what came back beside it.

    .. impl:: Default tool for executing external calculations.
        :id: I_ARMI_EX1
        :implements: R_ARMI_EX

        Facilitates the execution of external calculations by accepting ``options`` (an
        :py:class:`~armi.physics.executers.ExecutionOptions` object). :py:meth:`run` resolves the derived options,
        checks that the code's executable exists, performs any geometry transformations, writes the input, runs the
        code in its own run directory (:py:class:`CodeRun`), reads the output and applies it to the reactor, and
        finally undoes the geometry transformations.
    """

    def run(self):
        """Prepare, run the code (if there is one) and finish; return the output."""
        self.prepare()
        return self.finish(self._execute())

    def prepare(self) -> None:
        """Everything before the code runs: options, geometry, working directory, input."""
        self.options.resolveDerivedOptions()
        runLog.debug(self.options.describe())
        code = self.options.code
        if code and not (os.path.exists(code.executable) or shutil.which(code.executable)):
            raise IOError(f"Required executable `{code.executable}` not found for {self}")
        self._performGeometryTransformations()
        self.options.workingDir = self._workingDirectory()
        os.makedirs(self.options.workingDir, exist_ok=True)
        self.writeInput()

    def _workingDirectory(self) -> str:
        """The case directory, or a directory per time node and interface when physics files are kept."""
        if not self.options.savePhysicsFiles or self.r is None:
            return os.getcwd()
        state = f"c{self.r.p.cycle}n{self.r.p.timeNode}"
        return os.path.join(os.getcwd(), state, self.options.interfaceName or self.options.label or "run")

    @property
    def inputPath(self) -> str:
        return os.path.join(self.options.workingDir, self.options.inputFile)

    @property
    def outputPath(self) -> str:
        return os.path.join(self.options.workingDir, self.options.outputFile)

    def codeRun(self) -> Optional[CodeRun]:
        """The run of the options' code on the input, or None for a calculation that runs no code."""
        options = self.options
        if options.code is None:
            return None
        dataFiles = {name: os.path.abspath(source) for source, name in options.extraInputFiles}
        return CodeRun(
            options.code,
            self.inputPath,
            dataFiles=dataFiles,
            outputs=[o if isinstance(o, str) else tuple(o) for o in options.extraOutputFiles],
            threads=options.threads,
            runDir=options.runDir,
            listingPath=self.outputPath if options.outputFile else None,
            timeoutSeconds=options.timeoutSeconds,
            runner=options.runner,
            cache=options.cache,
        )

    def _execute(self) -> Optional[RunResult]:
        """Run the code; an executer whose calculation is not an external code overrides this."""
        run = self.codeRun()
        return run.run() if run is not None else None

    def finish(self, result: Optional[RunResult]):
        """Everything after the code ran: check it, read and apply the output, undo the geometry; return the output."""
        if result is not None:
            if not result.succeeded:
                raise RuntimeError(f"{result.code.name} failed on {self.inputPath}:\n{result.failureReason()}")
            if not result.cached and os.path.isdir(result.runDir):
                shutil.rmtree(result.runDir)
        output = self._readOutput()
        if self.options.applyResultsToReactor:
            output.apply(self.r)
        self._undoGeometryTransformations()
        self._updateAdditionalParameters()
        return output

    def writeInput(self):
        """Write the code's input to :py:attr:`inputPath`."""

    def _readOutput(self):
        raise NotImplementedError()

    def _performGeometryTransformations(self):
        pass

    def _undoGeometryTransformations(self):
        pass

    def _updateAdditionalParameters(self):
        pass


def runExecuters(executers: Sequence[DefaultExecuter], maxWorkers: Optional[int] = None, runner=None) -> list:
    """
    Run many executers with their codes side by side, and return their outputs in order.

    Every executer is prepared (its input written) first, then the codes run together through :py:func:`runAll`, then
    every executer is finished. Executers that transform the same reactor's geometry cannot be interleaved like this;
    run those one at a time.
    """
    for executer in executers:
        executer.prepare()
    runs = [executer.codeRun() for executer in executers]
    pending = [i for i, run in enumerate(runs) if run is not None]
    results = [None] * len(executers)
    for i, result in zip(pending, runAll([runs[i] for i in pending], maxWorkers, runner=runner)):
        results[i] = result
    return [executer.finish(result) for executer, result in zip(executers, results)]
