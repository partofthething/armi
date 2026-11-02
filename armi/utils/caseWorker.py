"""
Run one case of a suite in its directory: ``python -m armi.utils.caseWorker <caseDirectory>``.

A :py:class:`~armi.cases.runners.SuiteRunner` writes a job file (:py:data:`JOB_FILE`) into the case directory and
starts this module there, on whatever machine runs the case. The worker reads the job, configures the application,
loads the case from its settings, runs its task and writes :py:data:`RESULT_FILE`. Everything it needs is in the case
directory, so a runner only has to get the directory to the machine and back.

The worker supervises the task in a child process with its own process group. On a timeout the whole group is
killed, including any codes the task started, and the result records ``"timeout"``.

A branch job (``"kind": "branch"``, written by :py:class:`~armi.cases.branches.SnapshotExecutor`) loads a snapshot of
a reactor from the middle of a run instead of a case, applies the branch's modifiers and runs its task.
"""

import json
import os
import signal
import subprocess
import sys
import time
import traceback

JOB_FILE = "suite-job.json"
RESULT_FILE = "suite-result.json"
LOG_FILE = "suite-worker.log"

#: Seconds between asking a timed-out task to stop and killing it.
GRACE_SECONDS = 10


def readJson(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def readJob(directory) -> dict:
    with open(os.path.join(directory, JOB_FILE)) as f:
        return json.load(f)


def writeResult(directory, job, status, results=None, wallSeconds=None, error=None):
    result = {
        "title": job["title"],
        "hash": job["hash"],
        "status": status,
        "wallSeconds": wallSeconds,
        "independentVariables": job.get("independentVariables", {}),
        "results": results or {},
    }
    if error:
        result["error"] = error
    path = os.path.join(directory, RESULT_FILE)
    with open(path + ".tmp", "w") as f:
        json.dump(result, f, indent=1)
    os.replace(path + ".tmp", path)
    return result


def supervise(directory: str) -> int:
    """Run the task in a child process group, enforcing the job's timeout."""
    directory = os.path.abspath(directory)
    job = readJob(directory)
    resultPath = os.path.join(directory, RESULT_FILE)
    if os.path.exists(resultPath):
        os.remove(resultPath)
    start = time.time()
    with open(os.path.join(directory, LOG_FILE), "w") as log:
        child = subprocess.Popen(
            [sys.executable, "-m", "armi.utils.caseWorker", "--child", directory],
            cwd=directory,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            child.wait(timeout=job.get("timeout"))
        except subprocess.TimeoutExpired:
            killGroup(child)
            writeResult(directory, job, "timeout", wallSeconds=round(time.time() - start, 1))
            return 1
    result = readJson(resultPath)
    if result is None:
        with open(os.path.join(directory, LOG_FILE), errors="replace") as log:
            tail = log.read()[-3000:]
        result = writeResult(directory, job, "failed", wallSeconds=round(time.time() - start, 1), error=tail)
    return 0 if result["status"] == "ok" else 1


def killGroup(child):
    """Stop a process started with ``start_new_session`` and everything it started: politely, then not."""
    for sig, wait in ((signal.SIGTERM, GRACE_SECONDS), (signal.SIGKILL, None)):
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            return
        try:
            child.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def runTask(directory: str) -> int:
    """Configure the application, load the case and run its task (in the child process)."""
    import importlib

    import armi

    directory = os.path.abspath(directory)
    os.chdir(directory)
    job = readJob(directory)
    start = time.time()
    try:
        # configure before importing armi.cases, which needs a configured application
        if not armi.isConfigured():
            app = None
            if job.get("app"):
                moduleName, _, className = job["app"].partition(":")
                app = getattr(importlib.import_module(moduleName), className)()
            armi.configure(app)
        if job.get("kind") == "branch":
            from armi.cases import branches

            # the branch writes its own result, ok or failed
            return 0 if branches.runSnapshotBranch(directory, job)["status"] == "ok" else 1

        from armi import settings
        from armi.cases import case as armicase
        from armi.cases import tasks

        case = armicase.Case(settings.Settings(os.path.join(directory, job["settings"])))
        dependencies = {
            title: os.path.normpath(os.path.join(directory, path)) for title, path in job["dependencies"].items()
        }
        results = tasks.resolve(job["task"])(case, dependencies)
    except Exception:
        traceback.print_exc()
        writeResult(directory, job, "failed", wallSeconds=round(time.time() - start, 1), error=traceback.format_exc())
        return 1
    writeResult(directory, job, "ok", results, wallSeconds=round(time.time() - start, 1))
    return 0


def main(argv):
    if argv[:1] == ["--child"]:
        return runTask(argv[1])
    if len(argv) != 1:
        print(__doc__)
        return 2
    return supervise(argv[0])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
