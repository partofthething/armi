# Copyright 2019 TerraPower, LLC
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
"""Tests of the runLog tooling."""

import logging
import os
import sys
import unittest
from io import StringIO
from logging import handlers
from pathlib import Path
from shutil import rmtree
from unittest import mock

from armi import context, runLog
from armi.testing import mockRunLogs
from armi.utils.directoryChangers import TemporaryDirectoryChanger


class RunLogTestCase(unittest.TestCase):
    """
    Base class for tests that build their own run logs.

    Each test runs in a temporary directory that the worker log files are written to, and the global run log, the
    ``armi`` logger, and stderr are put back the way they were afterwards.
    """

    def setUp(self):
        self.td = TemporaryDirectoryChanger()
        self.td.__enter__()
        self.logDir = os.path.join(self.td.destination, "logs")
        self._logDirPatch = mock.patch.object(runLog, "_LOG_DIR", self.logDir)
        self._logDirPatch.start()

        self.originalLog = runLog.LOG
        self.originalErr = sys.stderr
        armiLogger = logging.getLogger(runLog.LOGGER_NAME)
        self.originalHandlers = list(armiLogger.handlers)
        self.originalLevel = armiLogger.level

    def tearDown(self):
        runLog.LOG.restoreStandardStreams()
        runLog.LOG = self.originalLog
        sys.stderr = self.originalErr
        armiLogger = logging.getLogger(runLog.LOGGER_NAME)
        for h in armiLogger.handlers:
            if h not in self.originalHandlers:
                h.close()
        armiLogger.handlers = self.originalHandlers
        armiLogger.setLevel(self.originalLevel)

        self._logDirPatch.stop()
        self.td.__exit__(None, None, None)

    @staticmethod
    def divertToStream(log, mpiRank=0):
        """Send the log output to a stream, formatted the way ARMI formats it, to make testing easier."""
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(runLog._RunLogFormatter(mpiRank))
        log._setHandler(handler)
        return stream


class TestRunLog(RunLogTestCase):
    def test_setVerbosityFromInteger(self):
        """Test that the log verbosity can be set with an integer.

        .. test:: The run log verbosity can be configured with an integer.
            :id: T_ARMI_LOG0
            :tests: R_ARMI_LOG
        """
        log = runLog._RunLog(1)
        expectedStrVerbosity = "debug"
        verbosityRank = log.getLogVerbosityRank(expectedStrVerbosity)
        runLog.setVerbosity(verbosityRank)
        self.assertEqual(verbosityRank, runLog.getVerbosity())
        self.assertEqual(verbosityRank, logging.DEBUG)

    def test_setVerbosityFromString(self):
        """
        Test that the log verbosity can be set with a string.

        .. test:: The run log verbosity can be configured with a string.
            :id: T_ARMI_LOG1
            :tests: R_ARMI_LOG
        """
        log = runLog._RunLog(1)
        expectedStrVerbosity = "error"
        verbosityRank = log.getLogVerbosityRank(expectedStrVerbosity)
        runLog.setVerbosity(expectedStrVerbosity)
        self.assertEqual(verbosityRank, runLog.getVerbosity())
        self.assertEqual(verbosityRank, logging.ERROR)

    def test_verbosityOutOfRange(self):
        """Test that the log verbosity setting resets to a canonical value when it is out of range."""
        runLog.setVerbosity(-50)
        self.assertEqual(runLog.LOG.logger.level, min([v[0] for v in runLog.LOG.logLevels.values()]))

        runLog.setVerbosity(5000)
        self.assertEqual(runLog.LOG.logger.level, max([v[0] for v in runLog.LOG.logLevels.values()]))

    def test_invalidSetVerbosityByString(self):
        """Test that the log verbosity setting fails if the integer is invalid."""
        with self.assertRaises(KeyError):
            runLog.setVerbosity("taco")

        with self.assertRaises(TypeError):
            runLog.setVerbosity(["debug"])

    def test_parentRunLogging(self):
        """A basic test of the logging of the parent runLog."""
        log = runLog.LOG = runLog._RunLog(0)
        log.startLog("test_parentRunLogging")
        log.setVerbosity(logging.INFO)
        stream = self.divertToStream(log)

        log.log("debug", "You shouldn't see this.", single=False, label=None)
        log.log("warning", "Hello, ", single=False, label=None)
        log.log("error", "world!", single=False, label=None)
        log.log("info", "line one\nline two\n", single=False, label=None)
        runLog.close(99)

        self.assertEqual(
            stream.getvalue(),
            "[warn] Hello,\n[err ] world!\n[info] line one\n       line two\n",
        )

    def test_startLogToStdout(self):
        """The lead process logs to stdout, with the level prefixes, once the log is started."""
        log = runLog.LOG = runLog._RunLog(0)
        with mock.patch("sys.stdout", new_callable=StringIO) as stdout:
            runLog.info("before start")
            log.startLog("test_startLogToStdout")
            runLog.info("after start")
            runLog.header("a header")
            runLog.close(99)
            runLog.info("after close")

        self.assertEqual(stdout.getvalue(), "before start\n[info] after start\na header\nafter close\n")
        self.assertFalse(os.path.exists(self.logDir))

    def test_getWhiteSpace(self):
        log = runLog._RunLog(0)
        space0 = len(log.getWhiteSpace(0))
        space1 = len(log.getWhiteSpace(1))
        space9 = len(log.getWhiteSpace(9))

        self.assertGreater(space1, space0)
        self.assertEqual(space1, space9)

    def test_warningReport(self):
        """A simple test of the warning tracking and reporting logic.

        .. test:: Generate a warning report after a simulation is complete.
            :id: T_ARMI_LOG2
            :tests: R_ARMI_LOG
        """
        log = runLog.LOG = runLog._RunLog(321)
        log.startLog("test_warningReport")
        stream = self.divertToStream(log, 321)

        # log some things
        log.setVerbosity(logging.INFO)
        log.log("warning", "test_warningReport", single=True, label=None)
        log.log("debug", "invisible due to log level", single=False, label=None)
        log.log("warning", "test_warningReport", single=True, label=None)
        log.log("warning", "simple_warning", single=False, label=None)
        log.log("error", "high level something", single=False, label=None)

        # test that the logging found some duplicate outputs
        dupsFilter = log.getDuplicatesFilter()
        self.assertIsInstance(dupsFilter, runLog.DeduplicationFilter)
        self.assertEqual(dupsFilter.warningCounts, {"test_warningReport": 2, "simple_warning": 1})

        # run the warning report
        log.warningReport()
        runLog.close(1)

        # test what was logged
        streamVal = stream.getvalue()
        self.assertIn("Final Warning Count", streamVal, msg=streamVal)
        self.assertIn("simple_warning", streamVal, msg=streamVal)
        self.assertIn("test_warningReport", streamVal, msg=streamVal)
        self.assertIn("Total Number of Warnings", streamVal, msg=streamVal)
        self.assertNotIn("invisible", streamVal, msg=streamVal)
        self.assertEqual(streamVal.count("test_warningReport"), 2, msg=streamVal)

    def test_warningReportNoWarnings(self):
        """A test of warningReport when there were no warnings.

        .. test:: Test an important edge case for a warning report.
            :id: T_ARMI_LOG3
            :tests: R_ARMI_LOG
        """
        log = runLog.LOG = runLog._RunLog(323)
        log.startLog("test_warningReportNoWarnings")
        stream = self.divertToStream(log, 323)

        log.setVerbosity(logging.INFO)
        log.log("debug", "invisible due to log level", single=False, label=None)
        log.log("error", "high level something", single=False, label=None)

        log.warningReport()
        runLog.close(1)

        streamVal = stream.getvalue()
        self.assertIn("None Found", streamVal, msg=streamVal)
        self.assertNotIn("Total Number of Warnings", streamVal, msg=streamVal)
        self.assertNotIn("invisible", streamVal, msg=streamVal)

    def test_closeLogging(self):
        """A basic test of the close() functionality."""
        log = runLog.LOG = runLog._RunLog(777)
        self.assertEqual(len(log.logger.handlers), 1)
        self.assertIsInstance(log.logger.handlers[0], runLog._StdoutHandler)
        self.assertIs(sys.stderr, self.originalErr)

        # start the logging for real
        log.startLog("test_closeLogging")
        self.assertEqual(len(log.logger.handlers), 1)
        self.assertNotIsInstance(log.logger.handlers[0], runLog._StdoutHandler)
        self.assertIsNot(sys.stderr, self.originalErr)

        # close() and test that we are back to the default logging
        runLog.close(1)
        self.assertEqual(len(log.logger.handlers), 1)
        self.assertIsInstance(log.logger.handlers[0], runLog._StdoutHandler)
        self.assertIs(sys.stderr, self.originalErr)

    def test_setVerbosity(self):
        """Let's test the setVerbosity() method carefully.

        .. test:: The run log has configurable verbosity.
            :id: T_ARMI_LOG4
            :tests: R_ARMI_LOG

        .. test:: The run log can log to stream.
            :id: T_ARMI_LOG_IO0
            :tests: R_ARMI_LOG_IO
        """
        with mockRunLogs.BufferLog() as mock:
            # we should start with a clean slate
            self.assertEqual("", mock.getStdout())
            runLog.LOG.startLog("test_setVerbosity")
            runLog.LOG.setVerbosity(logging.INFO)

            # we should start at info level, and that should be working correctly
            self.assertEqual(runLog.LOG.getVerbosity(), logging.INFO)
            runLog.info("hi")
            self.assertIn("hi", mock.getStdout())
            mock.emptyStdout()

            runLog.debug("invisible")
            self.assertEqual("", mock.getStdout())

            # setVerbosity() to WARNING, and verify it is working
            runLog.LOG.setVerbosity(logging.WARNING)
            runLog.info("still invisible")
            self.assertEqual("", mock.getStdout())
            runLog.warning("visible")
            self.assertIn("visible", mock.getStdout())
            mock.emptyStdout()

            # setVerbosity() to DEBUG, and verify it is working
            runLog.LOG.setVerbosity(logging.DEBUG)
            runLog.debug("Visible")
            self.assertIn("Visible", mock.getStdout())
            mock.emptyStdout()

            # setVerbosity() to ERROR, and verify it is working
            runLog.LOG.setVerbosity(logging.ERROR)
            runLog.warning("Still Invisible")
            self.assertEqual("", mock.getStdout())
            runLog.error("Visible!")
            self.assertIn("Visible!", mock.getStdout())

            # we shouldn't be able to setVerbosity() to a non-canonical value
            self.assertEqual(runLog.LOG.getVerbosity(), logging.ERROR)
            runLog.LOG.setVerbosity(logging.WARNING + 1)
            self.assertEqual(runLog.LOG.getVerbosity(), logging.WARNING)

    def test_bufferLogLeavesArmiLoggerAlone(self):
        """Using a BufferLog in a test does not change the real ARMI logger."""
        armiLogger = logging.getLogger(runLog.LOGGER_NAME)
        level, handlers = armiLogger.level, list(armiLogger.handlers)
        with mockRunLogs.BufferLog():
            runLog.LOG.startLog("test_bufferLogLeavesArmiLoggerAlone")
            runLog.setVerbosity("error")

        self.assertEqual(armiLogger.level, level)
        self.assertEqual(armiLogger.handlers, handlers)
        self.assertIs(sys.stderr, self.originalErr)

    def test_setVerbosityBeforeStartLog(self):
        """The user/dev may accidentally call ``setVerbosity()`` before ``startLog()``,
        this should be mostly supportable. This is just an edge case.

        .. test:: Test that we support the user setting log verbosity BEFORE the logging starts.
            :id: T_ARMI_LOG5
            :tests: R_ARMI_LOG
        """
        with mockRunLogs.BufferLog() as mock:
            # we should start with a clean slate, before debug logging
            self.assertEqual("", mock.getStdout())
            runLog.LOG.setVerbosity(logging.DEBUG)
            runLog.LOG.startLog("test_setVerbosityBeforeStartLog")

            # we should start at info level, and that should be working correctly
            self.assertEqual(runLog.LOG.getVerbosity(), logging.DEBUG)
            runLog.debug("hi")
            self.assertIn("hi", mock.getStdout())
            mock.emptyStdout()

            # we should start with a clean slate, before info logging
            self.assertEqual("", mock.getStdout())
            runLog.LOG.setVerbosity(logging.INFO)
            runLog.LOG.startLog("test_setVerbosityBeforeStartLog2")

            # we should start at info level, and that should be working correctly
            self.assertEqual(runLog.LOG.getVerbosity(), logging.INFO)
            runLog.debug("nope")
            runLog.info("hi")
            self.assertIn("hi", mock.getStdout())
            self.assertNotIn("nope", mock.getStdout())
            mock.emptyStdout()

    def test_workerVerbosityBeforeStartLog(self):
        """A worker keeps the verbosity it was given before the log started, even if that is INFO."""
        log = runLog.LOG = runLog._RunLog(5)
        self.assertEqual(log.getVerbosity(), logging.WARNING)
        log.setVerbosity("info")
        log.startLog("test_workerVerbosityBeforeStartLog")
        self.assertEqual(log.logger.level, logging.INFO)
        runLog.close(1)

    def test_callingStartLogMultipleTimes(self):
        """Calling startLog() multiple times will lead to multiple output files, but logging should still work."""
        with mockRunLogs.BufferLog() as mock:
            # we should start with a clean slate
            self.assertEqual("", mock.getStdout())
            runLog.LOG.startLog("test_callingStartLogMultipleTimes1")
            runLog.LOG.setVerbosity(logging.INFO)

            # we should start at info level, and that should be working correctly
            self.assertEqual(runLog.LOG.getVerbosity(), logging.INFO)
            runLog.info("hi1")
            self.assertIn("hi1", mock.getStdout())
            mock.emptyStdout()

            # call startLog() again
            runLog.LOG.startLog("test_callingStartLogMultipleTimes2")
            runLog.LOG.setVerbosity(logging.INFO)

            # we should start at info level, and that should be working correctly
            self.assertEqual(runLog.LOG.getVerbosity(), logging.INFO)
            runLog.info("hi2")
            self.assertIn("hi2", mock.getStdout())
            mock.emptyStdout()

            # call startLog() again, with a duplicate logger name
            runLog.LOG.startLog("test_callingStartLogMultipleTimes2")
            runLog.LOG.setVerbosity(logging.INFO)

            # we should start at info level, and that should be working correctly
            self.assertEqual(runLog.LOG.getVerbosity(), logging.INFO)
            runLog.info("hi222")
            self.assertIn("hi222", mock.getStdout())
            mock.emptyStdout()

    def test_workerStartLogMultipleTimes(self):
        """Starting a worker log again moves the output to the new files, and close() still restores stderr."""
        log = runLog.LOG = runLog._RunLog(2)
        log.setVerbosity("info")
        log.startLog("first")
        runLog.info("in first")
        log.startLog("second")
        runLog.info("in second")
        runLog.close(1)

        self.assertIs(sys.stderr, self.originalErr)
        with open(os.path.join(self.logDir, "ARMI.first.0002.stdout")) as f:
            self.assertEqual(f.read(), "[info-002] in first\n")
        with open(os.path.join(self.logDir, "ARMI.second.0002.stdout")) as f:
            self.assertEqual(f.read(), "[info-002] in second\n")

    def test_deduplicationFilter(self):
        """Test that the logic to only print a log message once works correctly."""
        with mockRunLogs.BufferLog() as mock:
            # we should start with a clean slate
            self.assertEqual("", mock.getStdout())
            runLog.LOG.startLog("test_deduplicationFilter")
            runLog.LOG.setVerbosity(logging.INFO)

            msgInfo = "singleInfoMessage"
            for i in range(4):
                runLog.info(f"{msgInfo}: {i}", single=True, label=msgInfo)

            msgWarn = "singleWarnMessage"
            for j in range(4):
                runLog.warning(f"{msgWarn}: {j}", single=True, label=msgWarn)

            logs = mock.getStdout()
            self.assertEqual(logs.count(msgInfo), 1)
            self.assertEqual(logs.count(msgWarn), 1)

            # once cleared, the messages can be seen again
            runLog.LOG.clearSingleLogs()
            runLog.info(f"{msgInfo}: again", single=True, label=msgInfo)
            self.assertEqual(mock.getStdout().count(msgInfo), 2)

    def test_deduplicationFromStandardLogging(self):
        """The ``single`` and ``label`` options can also be passed through standard library logging calls."""
        log = runLog.LOG = runLog._RunLog(0)
        log.startLog("test_deduplicationFromStandardLogging")
        stream = self.divertToStream(log)

        for i in range(3):
            log.logger.warning("count %d", i, extra={"single": True, "label": "count"})
        log.logger.warning("100% sure")
        runLog.warning("100% sure")

        self.assertEqual(stream.getvalue(), "[warn] count 0\n[warn] 100% sure\n[warn] 100% sure\n")
        self.assertEqual(log.getDuplicatesFilter().warningCounts, {"count": 3, "100% sure": 2})
        runLog.close(99)

    def test_workerLogsToFiles(self):
        """Worker processes write their log and their stderr to files, which the lead process combines.

        .. test:: Write logging text to the logging stream and/or file.
            :id: T_ARMI_LOG7
            :tests: R_ARMI_LOG
        """
        log = runLog.LOG = runLog._RunLog(3)
        log.startLog("test_workerLogsToFiles")
        log.setVerbosity("info")
        runLog.info("worker info")
        runLog.warning("worker warning\nsecond line")
        runLog.header("worker header")
        print("worker stderr", file=sys.stderr)
        runLog.close(1)

        stdoutFile = os.path.join(self.logDir, "ARMI.test_workerLogsToFiles.0003.stdout")
        stderrFile = os.path.join(self.logDir, "ARMI.test_workerLogsToFiles.0003.stderr")
        with open(stdoutFile) as f:
            self.assertEqual(
                f.read(),
                "[info-003] worker info\n[warn-003] worker warning\n           second line\n-003worker header\n",
            )
        with open(stderrFile) as f:
            self.assertEqual(f.read(), "worker stderr\n")

        # the lead process puts the stdout and stderr of each worker into one log, and removes the originals
        runLog.LOG = runLog._RunLog(0, logger=logging.Logger("lead"))
        with mock.patch("sys.stderr", new_callable=StringIO) as stderr:
            runLog.concatenateLogs(logDir=self.logDir)
        self.assertIn("worker stderr", stderr.getvalue())
        self.assertFalse(os.path.exists(stdoutFile))
        self.assertFalse(os.path.exists(stderrFile))
        with open(os.path.join(self.logDir, "test_workerLogsToFiles-mpi.log")) as f:
            self.assertIn("[warn-003] worker warning", f.read())

    def test_concatenateLogs(self):
        """
        Simple test of the concat logs function.

        .. test:: The run log combines logs from different processes.
            :id: T_ARMI_LOG_MPI
            :tests: R_ARMI_LOG_MPI

        .. test:: The run log can log to file.
            :id: T_ARMI_LOG_IO1
            :tests: R_ARMI_LOG_IO
        """
        # create the log dir
        logDir = "test_concatenateLogs"
        if os.path.exists(logDir):
            rmtree(logDir)
        runLog.createLogDir(logDir)

        # create as stdout file
        stdoutFile1 = os.path.join(logDir, "{}.runLogTest.0000.stdout".format(runLog.STDOUT_LOGGER_NAME))
        with open(stdoutFile1, "w") as f:
            f.write("hello world\n")

        stdoutFile2 = os.path.join(logDir, "{}.runLogTest.0001.stdout".format(runLog.STDOUT_LOGGER_NAME))
        with open(stdoutFile2, "w") as f:
            f.write("hello other world\n")

        # verify behavior for a corner case
        stdoutFile3 = os.path.join(logDir, "{}..0000.stdout".format(runLog.STDOUT_LOGGER_NAME))
        with open(stdoutFile3, "w") as f:
            f.write("hello world again\n")

        self.assertTrue(os.path.exists(stdoutFile1))
        self.assertTrue(os.path.exists(stdoutFile2))
        self.assertTrue(os.path.exists(stdoutFile3))

        # create a stderr file
        stderrFile = os.path.join(logDir, "{}.runLogTest.0000.stderr".format(runLog.STDOUT_LOGGER_NAME))
        with open(stderrFile, "w") as f:
            f.write("goodbye cruel world\n")

        self.assertTrue(os.path.exists(stderrFile))

        # concat logs
        runLog.concatenateLogs(logDir=logDir)

        # verify output
        combinedLogFile = os.path.join(logDir, "runLogTest-mpi.log")
        self.assertTrue(os.path.exists(combinedLogFile))
        self.assertFalse(os.path.exists(stdoutFile1))
        self.assertFalse(os.path.exists(stdoutFile2))
        self.assertFalse(os.path.exists(stdoutFile3))
        self.assertFalse(os.path.exists(stderrFile))

        # verify behavior for a corner case
        stdoutFile3 = os.path.join(logDir, "{}..0000.stdout".format(runLog.STDOUT_LOGGER_NAME))
        with open(stdoutFile3, "w") as f:
            f.write("hello world again\n")
        # concat logs
        runLog.concatenateLogs(logDir=logDir)
        # verify output
        combinedLogFile = os.path.join(logDir, "armi-workers-mpi.log")
        self.assertTrue(os.path.exists(combinedLogFile))
        self.assertFalse(os.path.exists(stdoutFile3))

    def test_createLogDir(self):
        """Test the createLogDir() method.

        .. test:: Test that log directories can be created for logging output files.
            :id: T_ARMI_LOG6
            :tests: R_ARMI_LOG
        """
        logDir = "test_createLogDir"
        self.assertFalse(os.path.exists(logDir))
        for _ in range(10):
            runLog.createLogDir(logDir)
            self.assertTrue(os.path.exists(logDir))

    def test_handlerType(self):
        log = runLog.LOG = runLog._RunLog(321)
        log.startLog("test_handlerType")
        if context.PLATFORM == context.Platform.WINDOWS:
            self.assertEqual(type(log.logger.handlers[0]), logging.FileHandler)
        else:
            self.assertEqual(type(log.logger.handlers[0]), handlers.WatchedFileHandler)
        runLog.close(1)


class TestModuleLoggers(RunLogTestCase):
    def test_getLogger(self):
        """Module-level loggers live under the ``armi`` logger."""
        self.assertEqual(runLog.getLogger("armi").name, "armi")
        self.assertEqual(runLog.getLogger("armi.reactor.blocks").name, "armi.reactor.blocks")
        self.assertEqual(runLog.getLogger("myApp.physics").name, "armi.myApp.physics")
        self.assertEqual(runLog.getLogger("armiExtras").name, "armi.armiExtras")

    def test_moduleLoggerVerbosity(self):
        """A module-level logger can be more verbose than the rest of ARMI, and is formatted by ARMI."""
        log = runLog.LOG = runLog._RunLog(0)
        log.startLog("test_moduleLoggerVerbosity")
        log.setVerbosity("info")
        stream = self.divertToStream(log)

        moduleLog = runLog.getLogger("myApp.test_moduleLoggerVerbosity")
        quietLog = runLog.getLogger("myApp.test_moduleLoggerVerbosity_quiet")
        moduleLog.setLevel(logging.DEBUG)
        try:
            moduleLog.debug("module debug")
            moduleLog.log(runLog.EXTRA, "module extra")
            quietLog.debug("quiet debug")
            runLog.debug("global debug")
        finally:
            moduleLog.setLevel(logging.NOTSET)

        self.assertEqual(stream.getvalue(), "[dbug] module debug\n[xtra] module extra\n")
        runLog.close(99)

    def test_moduleLoggerDeduplication(self):
        """Messages from module-level loggers are de-duplicated and counted in the warning report."""
        log = runLog.LOG = runLog._RunLog(0)
        log.startLog("test_moduleLoggerDeduplication")
        stream = self.divertToStream(log)

        moduleLog = runLog.getLogger("myApp.test_moduleLoggerDeduplication")
        for _ in range(3):
            moduleLog.warning("module warning", extra={"single": True})

        self.assertEqual(stream.getvalue(), "[warn] module warning\n")
        self.assertEqual(log.getDuplicatesFilter().warningCounts, {"module warning": 3})
        runLog.close(99)

    def test_otherLoggersUntouched(self):
        """ARMI does not change loggers outside of the ``armi`` namespace."""
        log = runLog.LOG = runLog._RunLog(0)
        log.startLog("test_otherLoggersUntouched")

        other = logging.getLogger("notArmi.test_otherLoggersUntouched")
        self.assertIs(type(other), logging.Logger)
        self.assertEqual(other.handlers, [])
        self.assertEqual(other.getEffectiveLevel(), logging.getLogger().getEffectiveLevel())
        self.assertEqual(logging.getLevelName(logging.INFO), "INFO")
        self.assertEqual(logging.getLevelName(runLog.IMPORTANT), "IMPORTANT")
        runLog.close(99)


class TestRunLogFormatter(unittest.TestCase):
    @staticmethod
    def _record(level, msg):
        return logging.LogRecord("armi", level, __file__, 1, msg, (), None)

    def test_prefixes(self):
        fmt = runLog._RunLogFormatter(0)
        self.assertEqual(fmt.format(self._record(logging.INFO, "hi")), "[info] hi")
        self.assertEqual(fmt.format(self._record(runLog.IMPORTANT, "hi")), "[impt] hi")
        self.assertEqual(fmt.format(self._record(runLog.HEADER, "hi")), "hi")
        self.assertEqual(fmt.format(self._record(logging.CRITICAL, "hi")), "[CRITICAL] hi")

        fmt = runLog._RunLogFormatter(12)
        self.assertEqual(fmt.format(self._record(logging.ERROR, "hi")), "[err -012] hi")
        self.assertEqual(fmt.format(self._record(runLog.HEADER, "hi")), "-012hi")
        self.assertEqual(fmt.format(self._record(logging.CRITICAL, "hi")), "[CRITICAL-012] hi")

    def test_multiLine(self):
        fmt = runLog._RunLogFormatter(0)
        self.assertEqual(fmt.format(self._record(logging.INFO, "one\ntwo\n\n")), "[info] one\n       two")

        fmt = runLog._RunLogFormatter(0, showLevel=False)
        self.assertEqual(fmt.format(self._record(logging.INFO, "one\ntwo")), "one\n       two")

    def test_exception(self):
        fmt = runLog._RunLogFormatter(0)
        try:
            raise ValueError("bad value")
        except ValueError:
            record = logging.LogRecord("armi", logging.ERROR, __file__, 1, "oops", (), sys.exc_info())

        text = fmt.format(record)
        self.assertTrue(text.startswith("[err ] oops\n       Traceback"), msg=text)
        self.assertIn("ValueError: bad value", text)


class TestRunLogEnvEdits(unittest.TestCase):
    """Tests that will use monkeypatch to alter an environment variable."""

    def setUp(self):
        # We cannot import pytest at the top of the file right now. The ARMI unit tests are currently imported at
        # runtime, and until that is changed, we don't want pytest to be a runtime dependency. For now, hide the import
        # down here. Once the testing module is complete and ARMI's unit tests aren't all imported, the pytest import
        # can move up to where it belongs.
        import pytest

        self.monkeypatch = pytest.MonkeyPatch()

    def tearDown(self):
        self.monkeypatch.undo()

    def test_createLogDirNonDefault(self):
        """Test the scenario where a user sets the environment variable that edits the log dir location."""
        with TemporaryDirectoryChanger() as td:
            self.monkeypatch.setenv("ARMI_TEMP_ROOT_PATH", str(Path(td.destination) / "logzGoHere"))
            runLog.createLogDir()
            # assert the env variable-edits logs path exists
            p = Path(td.destination) / "logzGoHere" / "logs"
            self.assertTrue(p.exists())
            # assert the default logs path doesn't exist
            p = Path(os.getcwd()) / "logs"
            self.assertFalse(p.exists())

    def test_getLogDir(self):
        """Test getLogDir with and without an environment variable edit."""
        default = Path(runLog.getLogDir())
        self.assertEqual(default, Path(os.getcwd()) / "logs")
        root = Path("somewhere") / "else"
        self.monkeypatch.setenv("ARMI_TEMP_ROOT_PATH", str(root))
        altered = Path(runLog.getLogDir())
        self.assertEqual(altered, root / "logs")
