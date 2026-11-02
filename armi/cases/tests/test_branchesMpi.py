"""
Tests for branches split between MPI ranks.

They are skipped unless run under MPI::

    mpiexec -n 2 python -m pytest armi/cases/tests/test_branchesMpi.py
"""

import os
import shutil
import unittest

from armi import context
from armi.cases.branches import MpiExecutor, runBranches
from armi.cases.sweeps import Sweep
from armi.cases.tests.test_branches import SetPower
from armi.operators import OperatorMPI
from armi.testing import TESTING_ROOT, loadTestReactor


def reportRank(r, cs):
    return {"power": r.core.p.power, "rank": context.MPI_RANK, "worldRank": context.MPI_COMM.Get_rank()}


class TestMpiBranches(unittest.TestCase):
    @unittest.skipIf(context.MPI_SIZE <= 1, "Parallel test only")
    def test_branchesSplitBetweenRanks(self):
        oldOperator, r = loadTestReactor(
            TESTING_ROOT, inputFileName="reactors/smallestTestReactor/armiRunSmallest.yaml"
        )
        o = OperatorMPI(cs=oldOperator.cs)
        o.r = r
        r.o = o
        r.core.p.power = 1.0
        # every rank computes the same branch directory, without collectives the workers would take for commands
        directory = os.path.abspath("branchesMpiTest")
        if context.MPI_RANK == 0:
            try:
                sweep = Sweep.axis(SetPower(p) for p in (2.0, 3.0, 4.0, 5.0))
                results = runBranches(r, o.cs, sweep, reportRank, MpiExecutor(), directory=directory)
            finally:
                context.MPI_COMM.bcast("quit", root=0)
                context.MPI_COMM.bcast("finished", root=0)
                shutil.rmtree(directory, ignore_errors=True)
            self.assertEqual([res["status"] for res in results.values()], ["ok"] * 4)
            self.assertEqual([res["results"]["power"] for res in results.values()], [2.0, 3.0, 4.0, 5.0])
            ranks = sorted({res["results"]["worldRank"] for res in results.values()})
            self.assertEqual(ranks, list(range(context.MPI_SIZE)))
            self.assertEqual(r.core.p.power, 1.0)
        else:
            o.workerOperate()
