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
"""Tests for MPI actions."""

import unittest

from armi import context
from armi.mpiActions import MpiAction
from armi.utils import iterables


class MockMpiAction(MpiAction):
    """Mock MPI Action, to simplify tests."""

    def __init__(self, broadcastResult: int = 3, invokeResult: int = 7):
        self.broadcastResult = broadcastResult
        self.invokeResult = invokeResult

    def broadcast(self, obj=None):
        return self.broadcastResult

    def invoke(self, o, r, cs):
        return self.invokeResult


@unittest.skipUnless(context.MPI_RANK == 0, "test only on root node")
class TestMpiIter(unittest.TestCase):
    def setUp(self):
        """Save MPI size on entry."""
        self._mpiSize = context.MPI_SIZE
        self.action = MpiAction()

    def tearDown(self):
        """Restore MPI rank and size on exit."""
        context.MPI_SIZE = self._mpiSize
        context.MPI_RANK = 0

    def test_gatherWithoutMpi(self):
        context.MPI_SIZE = 1
        self.assertEqual(self.action.gather(), [self.action])
        self.assertEqual(self.action.gather(3), [3])

    def test_mpiIter(self):
        allObjs = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
        distObjs = [[0, 1, 2], [3, 4, 5], [6, 7], [8, 9], [10, 11]]

        context.MPI_SIZE = 5
        for rank in range(context.MPI_SIZE):
            context.MPI_RANK = rank
            myObjs = list(self.action.mpiIter(allObjs))
            self.assertEqual(myObjs, distObjs[rank])

    def _distributeObjects(self, allObjs, numProcs):
        context.MPI_SIZE = numProcs
        objs = []
        for context.MPI_RANK in range(context.MPI_SIZE):
            objs.append(list(self.action.mpiIter(allObjs)))
        return objs

    def test_perfectBalancing(self):
        """Test load balancing when numProcs divides numObjects.

        In this case, all processes should get the same number of objects.
        """
        numObjs, numProcs = 25, 5
        allObjs = list(range(numObjs))
        objs = self._distributeObjects(allObjs, numProcs)
        counts = [len(o) for o in objs]
        imbalance = max(counts) - min(counts)

        # ensure we haven't missed any objects
        self.assertEqual(iterables.flatten(objs), allObjs)

        # check imbalance
        self.assertEqual(imbalance, 0)

    def test_excessProcesses(self):
        """Test load balancing when numProcs exceeds numObjects.

        In this case, some processes should receive a single object and the rest should receive no objects.
        """
        numObjs, numProcs = 5, 25
        allObjs = list(range(numObjs))
        objs = self._distributeObjects(allObjs, numProcs)
        counts = [len(o) for o in objs]
        imbalance = max(counts) - min(counts)

        # ensure we haven't missed any objects
        self.assertEqual(iterables.flatten(objs), allObjs)

        # check imbalance
        self.assertLessEqual(imbalance, 1)

    def test_typicalBalancing(self):
        """Test load balancing for typical case (numProcs < numObjs).

        In this case, the total imbalance should be 1 (except for the perfectly balanced case).
        """
        numObjs, numProcs = 25, 6
        allObjs = list(range(numObjs))
        objs = self._distributeObjects(allObjs, numProcs)

        # typical case (more objects than processes)
        counts = [len(o) for o in objs]
        imbalance = max(counts) - min(counts)
        self.assertLessEqual(imbalance, 1)
        self.assertEqual(iterables.flatten(objs), allObjs)

    def test_invokeAsMaster(self):
        """Verify that calling invokeAsMaster calls invoke."""
        self.assertEqual(7, MockMpiAction.invokeAsMaster(1, 2, 3))
