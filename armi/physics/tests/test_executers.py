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

"""Provide tests for the generic Executers."""

import os
import stat
import unittest

from armi.physics import executers
from armi.reactor import geometry
from armi.utils import directoryChangers
from armi.utils.outputCache import OutputCache


class MockParams:
    def __init__(self):
        self.cycle = 1
        self.timeNode = 2


class MockCore:
    def __init__(self):
        # just pick a random geomType
        self.geomType = geometry.GeomType.CARTESIAN
        self.symmetry = "full"
        self.p = MockParams()


class MockReactor:
    def __init__(self):
        self.core = MockCore()
        self.o = None
        self.p = MockParams()


class TestExecutionOptions(unittest.TestCase):
    def test_runningDirectoryPath(self):
        """Test that the running directory path is set up correctly based on the case title and label provided."""
        e = executers.ExecutionOptions(label=None)
        e.setRunDirFromCaseTitle(caseTitle="test")
        self.assertEqual(os.path.basename(e.runDir), "508bc04f-0")

        e = executers.ExecutionOptions(label="label")
        e.setRunDirFromCaseTitle(caseTitle="test")
        self.assertEqual(os.path.basename(e.runDir), "b07da087-0")

        e = executers.ExecutionOptions(label="label2")
        e.setRunDirFromCaseTitle(caseTitle="test")
        self.assertEqual(os.path.basename(e.runDir), "9c1c83cb-0")


#: A stand-in code: fails until it has run ``$FAILURES`` times, sleeps ``$SLEEP`` seconds, counts its runs in the
#: directory above its run directories, writes ``<input stem>.res`` from its input and its data file, and copies a
#: ``take`` file it is given to ``taken.txt``.
CODE = """#!/bin/sh
count=$(cat ../../runs 2>/dev/null || echo 0)
echo $((count + 1)) > ../../runs
input=$(ls *.inp)
stem=${input%.inp}
sleep ${SLEEP:-0}
cat "$input" data.txt > "$stem.res" 2>/dev/null
if [ -e take ]; then cp take taken.txt; fi
echo "case $stem"
if [ "$count" -lt "${FAILURES:-0}" ] || grep -q fail "$input"; then echo "it went wrong"; exit 1; fi
echo "normal end of execution"
"""


class CodeTestCase(unittest.TestCase):
    """A temporary directory holding the stand-in code, its data file and an output cache."""

    def setUp(self):
        self.td = directoryChangers.TemporaryDirectoryChanger()
        self.td.__enter__()
        self.root = os.getcwd()
        with open("code.sh", "w") as f:
            f.write(CODE)
        os.chmod("code.sh", os.stat("code.sh").st_mode | stat.S_IEXEC)
        with open("data.txt", "w") as f:
            f.write("cross sections\n")
        self.code = executers.Code("stand-in", os.path.abspath("code.sh"), successMarker="normal end")
        self.cache = OutputCache("cache")

    def tearDown(self):
        os.environ.pop("FAILURES", None)
        os.environ.pop("SLEEP", None)
        self.td.__exit__(None, None, None)

    def runs(self) -> int:
        with open(os.path.join(self.root, "runs")) as f:
            return int(f.read())

    def codeRun(self, directory, stem, body="lattice", **kwargs) -> executers.CodeRun:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{stem}.inp")
        with open(path, "w") as f:
            f.write(f"* {stem}\n{body}\nFILE './{stem}.res'\n")
        options = {"dataFiles": {"data.txt": "data.txt"}, "outputs": [f"{stem}.res"], **kwargs}
        return executers.CodeRun(self.code, path, **options)


class TestCode(unittest.TestCase):
    def test_successByMarkerOrExitCode(self):
        marked = executers.Code("c", "c", successMarker="all done")
        bare = executers.Code("c", "c")
        with directoryChangers.TemporaryDirectoryChanger():
            with open("listing", "w") as f:
                f.write("work\nall done\n")
            self.assertTrue(executers.RunResult(marked, 1, "listing", ".", 0.0).succeeded)
            self.assertTrue(executers.RunResult(bare, 0, "listing", ".", 0.0).succeeded)
            self.assertFalse(executers.RunResult(bare, 2, "listing", ".", 0.0).succeeded)

    def test_command(self):
        self.assertEqual(executers.Code("c", "/bin/c").command("in.inp", 4), ["env", "OMP_NUM_THREADS=4", "/bin/c"])
        argument = executers.Code("c", "/bin/c", inputOnStdin=False)
        self.assertEqual(argument.command("in.inp", 1)[-1], "in.inp")


class TestCodeRun(CodeTestCase):
    def test_runsInItsOwnDirectoryAndBringsBackTheOutputs(self):
        run = self.codeRun("a", "p24")
        result = run.run()
        self.assertTrue(result.succeeded)
        self.assertEqual(os.getcwd(), self.root)
        self.assertTrue(os.path.exists("a/p24.res"))
        self.assertIn("case p24", "\n".join(result.listing))
        self.assertEqual(result.listingPath, os.path.abspath("a/p24.out"))

    def test_renamesAnOutputOnTheWay(self):
        run = self.codeRun("a", "p24", outputs=[("p24.res", "renamed.res")])
        run.run()
        self.assertTrue(os.path.exists("a/renamed.res"))

    def test_retriesAFailedRun(self):
        os.environ["FAILURES"] = "1"
        self.assertTrue(self.codeRun("a", "p24", retries=1).run().succeeded)
        self.assertEqual(self.runs(), 2)

    def test_givesUpAfterRetries(self):
        os.environ["FAILURES"] = "2"
        result = self.codeRun("a", "p24", retries=1).run()
        self.assertFalse(result.succeeded)
        self.assertIn("it went wrong", result.failureReason())

    def test_stopsARunPastItsTimeLimit(self):
        os.environ["SLEEP"] = "30"
        result = self.codeRun("a", "p24", timeoutSeconds=1).run()
        self.assertFalse(result.succeeded)
        self.assertIn("time limit", result.failureReason())


class TestRunAll(CodeTestCase):
    def test_ordersAndSkips(self):
        """A run after one that failed is skipped; a run after one that succeeded gets its outputs."""
        with open("take", "w") as f:
            f.write("burnup\n")
        reference = self.codeRun("a", "reference")
        branch = self.codeRun("b", "branch", dataFiles={"data.txt": "data.txt", "take": "take"})
        bad = self.codeRun("c", "bad", body="fail")
        orphan = self.codeRun("d", "orphan")
        results = executers.runAll([reference, branch, bad, orphan], 4, after=[[], [0], [], [2]])
        self.assertTrue(results[0].succeeded and results[1].succeeded)
        self.assertFalse(results[2].succeeded)
        self.assertIsNone(results[3])
        self.assertTrue(os.path.exists("b/branch.run/taken.txt"))

    def test_runnerIsPassedOn(self):
        from armi.cases.runners import LocalRunner

        runner = LocalRunner(maxWorkers=1, threadsPerJob=1)
        own = LocalRunner(maxWorkers=1)
        runs = [self.codeRun("a", "a"), self.codeRun("b", "b", runner=own)]
        self.assertTrue(all(r.succeeded for r in executers.runAll(runs, runner=runner)))
        self.assertIs(runs[0].runner, runner)
        self.assertIs(runs[1].runner, own)


class TestCachedRuns(CodeTestCase):
    """Code runs through an output cache: restored, shared under another name, never a failure."""

    def test_restoresTheSameRun(self):
        first = self.codeRun("a", "p24", cache=self.cache).run()
        second = self.codeRun("b", "p24", cache=self.cache).run()
        self.assertEqual((first.cached, second.cached), (False, True))
        self.assertEqual(self.runs(), 1)
        self.assertIn("case p24", "\n".join(second.listing))

    def test_sharesARunUnderAnotherName(self):
        self.codeRun("a", "p24", cache=self.cache).run()
        result = self.codeRun("b", "e450-b24x2-r", cache=self.cache).run()
        self.assertTrue(result.cached)
        with open("b/e450-b24x2-r.res") as f:
            text = f.read()
        self.assertIn("* e450-b24x2-r", text)
        self.assertNotIn("p24", text)

    def test_runsAgainWhenAnyInputDiffers(self):
        self.codeRun("a", "p24", cache=self.cache).run()
        self.codeRun("b", "p24", body="another lattice", cache=self.cache).run()
        self.assertEqual(self.runs(), 2)

    def test_doesNotKeepAFailedRun(self):
        self.codeRun("a", "p24", body="fail", cache=self.cache).run()
        self.codeRun("b", "p24", body="fail", cache=self.cache).run()
        self.assertEqual(self.runs(), 2)

    def test_filesARunMadeWithoutTheCache(self):
        self.codeRun("a", "p24").run()
        self.assertIsNotNone(self.codeRun("a", "p24", cache=self.cache).fileInCache())
        self.assertTrue(self.codeRun("b", "p12", cache=self.cache).run().cached)
        self.assertEqual(self.runs(), 1)


class EchoOutput:
    """What the stand-in executer reads: the result file beside its input."""

    def __init__(self, path):
        with open(path) as f:
            self.text = f.read()

    def apply(self, reactor):
        reactor.applied.append(self.text)


class EchoExecuter(executers.DefaultExecuter):
    """Writes ``<label>.inp``, runs the stand-in code on it and reads the ``.res`` it writes."""

    def writeInput(self):
        with open(self.inputPath, "w") as f:
            f.write(f"* {self.options.label}\n")

    def _readOutput(self):
        return EchoOutput(os.path.join(self.options.workingDir, f"{self.options.label}.res"))


class TestDefaultExecuter(CodeTestCase):
    def executer(self, label, **options):
        opts = executers.ExecutionOptions(label=label)
        opts.code = self.code
        opts.inputFile = f"{label}.inp"
        opts.outputFile = f"{label}.out"
        opts.extraInputFiles = [("data.txt", "data.txt")]
        opts.extraOutputFiles = [(f"{label}.res", f"{label}.res")]
        # Two levels below the test root, where the stand-in code counts its runs.
        opts.runDir = os.path.join(self.root, "rundirs", label)
        for name, value in options.items():
            setattr(opts, name, value)
        reactor = MockReactor()
        reactor.applied = []
        return EchoExecuter(opts, reactor)

    def test_runExternalExecutable(self):
        """Run an external executable with an Executer, without changing the working directory.

        .. test:: Run an external executable with an Executer.
            :id: T_ARMI_EX
            :tests: R_ARMI_EX
        """
        executer = self.executer("block")
        output = executer.run()
        self.assertEqual(os.getcwd(), self.root)
        self.assertIn("* block", output.text)
        self.assertEqual(executer.r.applied, [output.text])
        self.assertTrue(os.path.exists("block.out"))
        self.assertFalse(os.path.exists("rundirs/block"), "the run directory of a successful run is removed")

    def test_aFailedRunRaises(self):
        os.environ["FAILURES"] = "5"
        with self.assertRaisesRegex(RuntimeError, "stand-in failed"):
            self.executer("block").run()

    def test_physicsFilesKeptPerTimeNode(self):
        executer = self.executer("block", savePhysicsFiles=True, interfaceName="flux")
        executer.run()
        self.assertTrue(os.path.exists(os.path.join("c1n2", "flux", "block.res")))

    def test_aMissingExecutableIsAnError(self):
        executer = self.executer("block", code=executers.Code("missing", "/no/such/code"))
        with self.assertRaises(IOError):
            executer.run()

    def test_manyExecutersSideBySide(self):
        executers_ = [self.executer(f"block{i}", cache=self.cache) for i in range(3)]
        outputs = executers.runExecuters(executers_, maxWorkers=3)
        self.assertEqual([o.text.splitlines()[0] for o in outputs], ["* block0", "* block1", "* block2"])
        self.assertEqual(self.runs(), 1, "the three inputs differ only in their names, so one run serves all")
