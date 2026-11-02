"""Tests for sweeps, design-built suites and suite runners."""

import json
import os
import subprocess
import time
import unittest
from unittest import mock

from armi.cases.inputModifiers.inputModifiers import (
    DesignFunctionModifier,
    DesignModifier,
    FunctionModifier,
    SettingsModifier,
)
from armi.cases.runners import LOST, SKIPPED, TIMED_OUT, LocalRunner, SlurmRunner, SshRunner, linkProducts
from armi.cases.suiteBuilder import SuiteBuilder
from armi.cases.sweeps import Sweep
from armi.utils import caseWorker as worker
from armi.utils.directoryChangers import TemporaryDirectoryChanger

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BLUEPRINTS = os.path.join(THIS_DIR, "..", "..", "testing", "reactors", "anl-afci-177", "anl-afci-177-blueprints.yaml")


def renderToy(design: dict, directory: str) -> dict:
    """Write a toy design's settings: the power and cycle length it holds, and the AFCI blueprints."""
    settingsPath = os.path.join(directory, "toy.yaml")
    with open(settingsPath, "w") as f:
        f.write("settings:\n")
        f.write(f"  power: {design['power']}\n")
        f.write(f"  cycleLength: {design['cycleLength']}\n")
        f.write(f"  loadingFile: {os.path.abspath(BLUEPRINTS)}\n")
    designPath = os.path.join(directory, "design.json")
    with open(designPath, "w") as f:
        json.dump(design, f)
    return {"settings": settingsPath, "design": designPath}


class Power(DesignModifier):
    def __init__(self, power):
        DesignModifier.__init__(self, {"power": power})
        self.power = power

    def modifyDesign(self, design):
        design["power"] = self.power
        return design


def reportPower(case, dependencies):
    with open(os.path.join(case.directory, "product.txt"), "w") as f:
        f.write(str(case.cs["power"]))
    return {"power": case.cs["power"], "cycleLength": case.cs["cycleLength"]}


def sumDependencies(case, dependencies):
    total = 0.0
    for directory in dependencies.values():
        linkProducts(directory, os.path.join(case.directory, os.path.basename(directory)), ["product.txt"])
        with open(os.path.join(directory, "product.txt")) as f:
            total += float(f.read())
    return {"total": total}


def failing(case, dependencies):
    raise RuntimeError("deliberate failure")


def sleepy(case, dependencies):
    with open(os.path.join(case.directory, "grandchild.pid"), "w") as f:
        f.write(str(subprocess.Popen(["sleep", "60"]).pid))
    time.sleep(60)
    return {}


def toyBuilder():
    return SuiteBuilder(design={"name": "toy", "power": 1.0e6, "cycleLength": 100.0}, renderDesign=renderToy)


def nameByIndex(index, _case, _mods):
    return os.path.join(os.getcwd(), f"case{index}", f"toy{index}.yaml")


class TestSweep(unittest.TestCase):
    def test_algebra(self):
        clean = Sweep.point(SettingsModifier("comment", "clean"))
        power = Sweep.axis(SettingsModifier("power", p) for p in (1, 2))
        cycle = Sweep.axis(SettingsModifier("cycleLength", c) for c in (10, 20, 30))
        sweep = clean + power * cycle
        self.assertEqual(len(sweep), 7)
        points = list(sweep)
        self.assertEqual([m.value for m in points[1]], [1, 10])
        self.assertEqual([m.value for m in points[3]], [1, 30])
        self.assertEqual(len(sweep.where(lambda v: v.get("cycleLength") != 20)), 5)
        self.assertEqual(len(power.zip(Sweep.axis(SettingsModifier("comment", c) for c in "ab"))), 2)
        with self.assertRaises(ValueError):
            power.zip(cycle)


class TestDesignSuite(unittest.TestCase):
    def test_buildFromDesign(self):
        with TemporaryDirectoryChanger():
            builder = toyBuilder()
            longCycle = FunctionModifier(lambda cs, bp: (cs.modified(newSettings={"cycleLength": 500.0}), bp))
            builder.addSweep(
                Sweep.axis(Power(p) for p in (1.0e6, 2.0e6))
                * Sweep.axis([DesignFunctionModifier(lambda d: d, cycle="design"), longCycle])
            )
            suite = builder.buildSuite(namingFunc=nameByIndex, task=f"{__name__}:reportPower")
            cases = list(suite)
            self.assertEqual(len(cases), 4)
            self.assertEqual(cases[2].design["power"], 2.0e6)
            self.assertEqual(cases[2].cs["power"], 2.0e6)
            self.assertEqual(cases[3].cs["cycleLength"], 500.0)
            self.assertEqual(cases[2].independentVariables, {"power": 2.0e6, "cycle": "design"})
            self.assertTrue(any(p.endswith("design.json") for p in cases[0].extraInputFiles))
            self.assertEqual(cases[0].task, f"{__name__}:reportPower")

    def test_designModifierNeedsDesign(self):
        with self.assertRaises(TypeError):
            Power(1.0)(None, None)
        with self.assertRaises(ValueError):
            SuiteBuilder(design={"power": 1})


class TestLocalRunner(unittest.TestCase):
    def test_runCacheAndTable(self):
        with TemporaryDirectoryChanger():
            builder = toyBuilder()
            builder.addSweep(Sweep.axis(Power(p) for p in (1.0e6, 2.0e6, 3.0e6)))
            suite = builder.buildSuite(namingFunc=nameByIndex, task=f"{__name__}:reportPower")
            results = suite.run(LocalRunner(maxWorkers=3, timeout=120))
            self.assertEqual([r["status"] for r in results.values()], ["ok"] * 3)
            self.assertEqual(results["toy0"]["results"]["power"], 1.0e6)
            table = suite.resultsTable()
            self.assertEqual([row["power"] for row in table], [1.0e6, 2.0e6, 3.0e6])
            self.assertEqual(table[0]["cycleLength"], 100.0)
            suite.writeResultsTable("table.csv")
            with open("table.csv") as f:
                self.assertTrue(f.readline().startswith("case,status,wallSeconds,power"))

            # nothing changed: everything is cached and the results stay put
            stamp = os.path.getmtime(os.path.join(list(suite)[0].directory, worker.RESULT_FILE))
            suite.run(LocalRunner(maxWorkers=3))
            self.assertEqual(stamp, os.path.getmtime(os.path.join(list(suite)[0].directory, worker.RESULT_FILE)))

            # change one design: only that case reruns
            builder.modifierSets[1] = (Power(2.5e6),)
            suite = builder.buildSuite(namingFunc=nameByIndex, task=f"{__name__}:reportPower")
            suite.run(LocalRunner(maxWorkers=3))
            self.assertEqual(stamp, os.path.getmtime(os.path.join(list(suite)[0].directory, worker.RESULT_FILE)))
            self.assertEqual(suite.resultsTable()[1]["power"], 2.5e6)

    def test_dependenciesAndFailures(self):
        with TemporaryDirectoryChanger():
            builder = toyBuilder()
            builder.addSweep(Sweep.axis(Power(p) for p in (1.0e6, 2.0e6, 3.0e6)))
            suite = builder.buildSuite(namingFunc=nameByIndex, task=f"{__name__}:reportPower")
            first, second, total = list(suite)
            total.task = f"{__name__}:sumDependencies"
            total.addExplicitDependency(first)
            total.addExplicitDependency(second)
            results = suite.run(LocalRunner(maxWorkers=3, timeout=120))
            self.assertEqual(results[total.title]["results"]["total"], 3.0e6)
            self.assertTrue(os.path.islink(os.path.join(total.directory, "case0", "product.txt")))

            # a failing dependency skips its dependents
            second.task = f"{__name__}:failing"
            results = suite.run(LocalRunner(maxWorkers=3, timeout=120))
            self.assertEqual(results[second.title]["status"], "failed")
            self.assertIn("deliberate failure", results[second.title]["error"])
            self.assertEqual(results[total.title]["status"], "skipped")
            statuses = {row["case"]: row["status"] for row in suite.resultsTable()}
            self.assertEqual(statuses[first.title], "ok")

    def test_timeoutKillsTheWholeTask(self):
        with TemporaryDirectoryChanger():
            builder = toyBuilder()
            builder.addModifierSet(())
            suite = builder.buildSuite(namingFunc=nameByIndex, task=f"{__name__}:sleepy")
            (case,) = list(suite)
            results = suite.run(LocalRunner(maxWorkers=1, timeout=8))
            self.assertEqual(results[case.title]["status"], "timeout")
            with open(os.path.join(case.directory, "grandchild.pid")) as f:
                pid = int(f.read())
            time.sleep(0.5)
            self.assertFalse(os.path.exists(f"/proc/{pid}") and "sleep" in open(f"/proc/{pid}/cmdline").read())


class TestRunCommands(unittest.TestCase):
    def test_runCommandsLocally(self):
        with TemporaryDirectoryChanger():
            for name in ("a", "b"):
                os.makedirs(name)
                with open(os.path.join(name, "deck.in"), "w") as f:
                    f.write(f"deck {name}\n")
            os.makedirs("slow")
            runner = LocalRunner(maxWorkers=3)
            codes = runner.runCommands(
                [
                    {"directory": "a", "argv": ["cat"], "stdin": "deck.in", "stdout": "deck.out"},
                    {"directory": "b", "argv": ["cat"], "stdin": "deck.in", "stdout": "deck.out"},
                    {"directory": "slow", "argv": ["sleep", "30"], "timeout": 1},
                ]
            )
            self.assertEqual(codes, [0, 0, TIMED_OUT])
            with open(os.path.join("b", "deck.out")) as f:
                self.assertEqual(f.read(), "deck b\n")

    def test_orderedCommands(self):
        with TemporaryDirectoryChanger():
            for name in "abcd":
                os.makedirs(name)
            runner = LocalRunner(maxWorkers=4)
            codes = runner.runCommands(
                [
                    {"directory": "a", "argv": ["sh", "-c", "sleep 0.5; echo ref > ref.out"]},
                    {"directory": "b", "argv": ["false"]},
                    {"directory": "c", "argv": ["true"], "after": [1]},
                    {"directory": "d", "argv": ["cp", "../a/ref.out", "."], "after": [0]},
                ]
            )
            self.assertEqual(codes, [0, 1, SKIPPED, 0])
            self.assertTrue(os.path.exists(os.path.join("d", "ref.out")))
            with self.assertRaises(RuntimeError):
                runner.runCommands([{"directory": "a", "argv": ["true"], "after": [0]}])

    def test_threadsPerJob(self):
        runner = LocalRunner(threadsPerJob=4)
        self.assertEqual(runner.env["OMP_NUM_THREADS"], "4")
        self.assertEqual(runner.maxWorkers, max(1, (os.cpu_count() or 1) // 4))


def fakeSbatch(submitted):
    """Stand in for ``sbatch --wait``: run the script (each array element in turn) with bash."""

    def sbatch(self, script, extra, timeout):
        submitted.append((script, extra, timeout))
        array = [e for e in extra if e.startswith("--array=")]
        indices = range(int(array[0].split("=")[1].split("-")[1].split("%")[0]) + 1) if array else [None]
        for index in indices:
            env = {**os.environ, **({"SLURM_ARRAY_TASK_ID": str(index)} if index is not None else {})}
            subprocess.run(["bash", script], env=env)
        return 0

    return sbatch


class TestSlurmRunner(unittest.TestCase):
    def test_singleJobsAndArrays(self):
        with TemporaryDirectoryChanger():
            for name in "abc":
                os.makedirs(os.path.join("run", name))
                with open(os.path.join("run", name, "deck.in"), "w") as f:
                    f.write(f"deck {name}\n")
            runner = SlurmRunner(maxWorkers=2, setup="export GREETING=hi", cpusPerJob=3, pythonPath=["/somewhere"])
            submitted = []
            with mock.patch.object(SlurmRunner, "_sbatch", fakeSbatch(submitted)):
                code = runner.runCommand(
                    "run/a", ["sh", "-c", "cat; echo $GREETING $OMP_NUM_THREADS"], 60, "deck.in", "o"
                )
                self.assertEqual(code, 0)
                with open(os.path.join("run", "a", "o")) as f:
                    self.assertEqual(f.read(), "deck a\nhi 3\n")
                with open(os.path.join("run", "a", SlurmRunner.SCRIPT_FILE)) as f:
                    script = f.read()
                self.assertIn("timeout -k 10 60 sh -c", script)
                self.assertIn("export PYTHONPATH=/somewhere", script)

                codes = runner.runCommands(
                    [
                        {"directory": "run/a", "argv": ["cat"], "stdin": "deck.in", "stdout": "deck.out"},
                        {"directory": "run/b", "argv": ["sh", "-c", "exit 4"]},
                        {"directory": "run/c", "argv": ["sleep", "5"], "timeout": 0.2},
                    ]
                )
            self.assertEqual(codes, [0, 4, TIMED_OUT])
            arrayScript, extra, _timeout = submitted[-1]
            self.assertEqual(os.path.dirname(arrayScript), os.path.abspath("run"))
            self.assertIn("--array=0-2%2", extra)
            with open(os.path.join("run", "a", "deck.out")) as f:
                self.assertEqual(f.read(), "deck a\n")

            # a job that never recorded an exit code (cancelled, say) is lost
            with mock.patch.object(SlurmRunner, "_sbatch", lambda self, script, extra, timeout: 0):
                self.assertEqual(runner.runCommand("run/b", ["true"]), LOST)

    def test_sbatchArguments(self):
        runner = SlurmRunner(cpusPerJob=2, sbatchArgs=["--partition=short"])
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            runner._sbatch("/x/job.sh", ["--job-name=x"], timeout=600)
        args = run.call_args.args[0]
        self.assertEqual(args[:4], ["sbatch", "--wait", "--parsable", "--cpus-per-task=2"])
        self.assertEqual(args[4], "--time=12")  # 600 s plus grace, in minutes
        self.assertEqual(args[-3:], ["--partition=short", "--job-name=x", "/x/job.sh"])


class TestSshRunner(unittest.TestCase):
    def test_commandsItSends(self):
        runner = SshRunner("me@host", "/remote/root", python="/venv/bin/python", setup="source env.sh")
        runner.localRoot = "/local/root"
        sent = []
        with (
            mock.patch.object(SshRunner, "_ssh", side_effect=lambda cmd, check=True: sent.append(cmd) or 0),
            mock.patch.object(SshRunner, "_rsync", side_effect=lambda a, b: sent.append(("rsync", a, b))),
        ):
            code = runner.runCommand("/local/root/run/c1", ["solver"], timeout=60, stdin="case.inp", stdout="case.out")
        self.assertEqual(code, 0)
        self.assertEqual(sent[0], "mkdir -p /remote/root/run/c1")
        self.assertEqual(sent[1], ("rsync", "/local/root/run/c1/", "me@host:/remote/root/run/c1/"))
        self.assertEqual(
            sent[2],
            "bash -c 'cd /remote/root/run/c1 && source env.sh && timeout -k 10 60 solver < case.inp > case.out 2>&1'",
        )
        self.assertEqual(sent[3], ("rsync", "me@host:/remote/root/run/c1/", "/local/root/run/c1/"))
