# Copyright 2026 TerraPower, LLC
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
"""Tests of the runLog tooling with MPI."""

import logging
import tempfile
import unittest
from logging import handlers
from unittest import mock

from armi import context, runLog


class TestRunLoggerMPI(unittest.TestCase):
    @unittest.skipIf(context.MPI_SIZE <= 1, "Parallel test only")
    def test_handlerType(self):
        log = runLog._RunLog(context.MPI_RANK, logger=logging.Logger("test_handlerType"))
        with tempfile.TemporaryDirectory() as logDir, mock.patch.object(runLog, "_LOG_DIR", logDir):
            log.startLog("things_and_stuff")
            handlerType = type(log.logger.handlers[0])
            log.close()

        # check the handler type
        if context.MPI_RANK == 0:
            self.assertEqual(handlerType, runLog._StdoutHandler)
        elif context.PLATFORM == context.Platform.WINDOWS:
            self.assertEqual(handlerType, logging.FileHandler)
        else:
            self.assertEqual(handlerType, handlers.WatchedFileHandler)
