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

"""
Handle logging of console during a simulation.

The default way of calling and the global armi logger is to just import it:

.. code-block::

    from armi import runLog

You may want a logger specific to a single module, say to provide debug logging for only one module. That is a standard
library logger underneath the ``armi`` logger, so its messages are formatted and written by the ARMI run log:

.. code-block::

    from armi import runLog
    log = runLog.getLogger(__name__)

In either case, you can then log things the same way:

.. code-block::

    runLog.info('information here')
    runLog.error('extra error info here')
    raise SomeException  # runLog.error() implies that the code will crash!

Change the global log level with ``runLog.setVerbosity('debug')``. A module-level logger is a plain
``logging.Logger``, so its level is set with ``log.setLevel(runLog.LOG.logLevels['debug'][0])``, or with the
``moduleVerbosity`` setting.

Everything is built on the standard library ``logging`` package: ARMI logs to the ``armi`` logger, which has one handler
(stdout for the lead process, a file for the others) with a :class:`DeduplicationFilter` and an ARMI formatter. ARMI
does not change how any other logger in the process behaves.
"""

import logging
import operator
import os
import sys
import time
from glob import glob
from logging import handlers

from armi import context

OS_SECONDS_TIMEOUT = 2 * 60
LOGGER_NAME = "armi"
STDOUT_LOGGER_NAME = "ARMI"

# custom log levels, in addition to the standard library's DEBUG, INFO, WARNING, and ERROR
EXTRA = 15
IMPORTANT = 25
PROMPT = 27
HEADER = 100

for _level, _name in ((EXTRA, "EXTRA"), (IMPORTANT, "IMPORTANT"), (PROMPT, "PROMPT"), (HEADER, "HEADER")):
    logging.addLevelName(_level, _name)


def getLogDir():
    """Return a file path for the `logs` directory, first checking if the user set the ARMI_TEMP_ROOT_PATH environment
    variable.
    """
    if os.environ.get("ARMI_TEMP_ROOT_PATH"):
        return os.path.join(os.environ["ARMI_TEMP_ROOT_PATH"], "logs")
    else:
        return os.path.join(os.getcwd(), "logs")


# The worker log files go to the log directory as it was when ARMI was imported. For some bespoke MPI use cases, the
# working directory has changed by the time the logs are started, which would scatter the logs.
_LOG_DIR = getLogDir()


def getLogger(name):
    """
    Return a module-level logger that is written to the ARMI run log.

    This is a standard library logger underneath the ``armi`` logger, so it shares the ARMI handlers and formatting but
    can have its own level. Names already in the ``armi`` namespace are used as-is, anything else (like a downstream
    app's ``__name__``) is put underneath it.

    Parameters
    ----------
    name : str
        Usually the ``__name__`` of the calling module.
    """
    if name == LOGGER_NAME or name.startswith(LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


class _StdoutHandler(logging.StreamHandler):
    """Write to whatever ``sys.stdout`` is when the message is emitted, so redirecting stdout also redirects the log."""

    def __init__(self):
        logging.Handler.__init__(self)

    @property
    def stream(self):
        return sys.stdout


class _RunLogFormatter(logging.Formatter):
    """
    Format ARMI log lines.

    Each line is prefixed with the short level name (and the MPI rank, for worker processes), and the continuation lines
    of a multi-line message are indented so they line up under the first line.
    """

    def __init__(self, mpiRank, showLevel=True):
        logging.Formatter.__init__(self, "%(message)s")
        self._mpiRank = mpiRank
        self._prefixes = {level: prefix for level, prefix in _RunLog.getLogLevels(mpiRank).values()}
        self._whiteSpace = _RunLog.getWhiteSpace(mpiRank)
        self._showLevel = showLevel

    def format(self, record):
        text = logging.Formatter.format(self, record).rstrip().replace("\n", "\n" + self._whiteSpace)
        if not self._showLevel:
            return text

        prefix = self._prefixes.get(record.levelno)
        if prefix is None:
            rank = "" if self._mpiRank == 0 else f"-{self._mpiRank:>03d}"
            prefix = f"[{record.levelname}{rank}] "

        return prefix + text


class _RunLog:
    """
    Handles all the logging.

    For the parent process, things are allowed to print to stdout and stderr, but the stdout prints are formatted like
    log statements. For the child processes, everything is piped to log files.

    .. impl:: A simulation-wide log, with user-specified verbosity.
        :id: I_ARMI_LOG
        :implements: R_ARMI_LOG

        Log statements are any text a user wants to record during a run. For instance, basic notifications of what is
        happening in the run, simple warnings, or hard errors. Every log message has an associated log level, controlled
        by the "verbosity" of the logging statement in the code. In the ARMI codebase, you can see many examples of
        logging:

        .. code-block:: python

            runLog.error("This sort of error might usually terminate the run.")
            runLog.warning("Users probably want to know.")
            runLog.info("This is the usual verbosity.")
            runLog.debug("This is only logged during a debug run.")

        The full list of logging levels is defined in ``_RunLog.getLogLevels()``, and the developer specifies the
        verbosity of a run via ``_RunLog.setVerbosity()``.

        A message can be logged only once per run (``single=True``), and every warning is counted, so that at the end
        of the ARMI-based simulation the analyst has a summary of the warnings in the run as well as a full record of
        potentially interesting information they can use to understand their run.
    """

    def __init__(self, mpiRank=0, logger=None):
        """
        Build a log object.

        Parameters
        ----------
        mpiRank : int
            If this is zero, we are in the parent process, otherwise child process. This should not be adjusted after
            instantiation.
        logger : logging.Logger, optional
            The logger to write to. By default this is the ``armi`` logger. Pass a separate logger to keep this run log
            from changing the global logging setup (e.g. in testing).
        """
        self._mpiRank = mpiRank
        self._verbosity = logging.INFO if mpiRank == 0 else logging.WARNING
        self.initialErr = None
        self._stderrFile = None
        self.logLevels = self.getLogLevels(mpiRank)
        self._logLevelNumbers = sorted([ll[0] for ll in self.logLevels.values()])
        self.logger = logging.getLogger(LOGGER_NAME) if logger is None else logger

        self._deduplicationFilter = DeduplicationFilter()
        self.setDefaultHandlers()
        self.logger.setLevel(self._verbosity)

    @staticmethod
    def getLogLevels(mpiRank):
        """Helper method to build an important data object this class needs.

        Parameters
        ----------
        mpiRank : int
            If this is zero, we are in the parent process, otherwise child process. This should not be adjusted after
            instantiation.
        """
        rank = "" if mpiRank == 0 else f"-{mpiRank:>03d}"

        # NOTE: these are in order of increasing level, so the GUI shows the options in the right order
        return {
            "debug": (logging.DEBUG, f"[dbug{rank}] "),
            "extra": (EXTRA, f"[xtra{rank}] "),
            "info": (logging.INFO, f"[info{rank}] "),
            "important": (IMPORTANT, f"[impt{rank}] "),
            "prompt": (PROMPT, f"[prmt{rank}] "),
            "warning": (logging.WARNING, f"[warn{rank}] "),
            "error": (logging.ERROR, f"[err {rank}] "),
            "header": (HEADER, f"{rank}"),
        }

    @staticmethod
    def getWhiteSpace(mpiRank):
        """Helper method to build the white space used to left-adjust the log lines.

        Parameters
        ----------
        mpiRank : int
            If this is zero, we are in the parent process, otherwise child process. This should not be adjusted after
            instantiation.
        """
        logLevels = _RunLog.getLogLevels(mpiRank)
        return " " * len(max([ll[1] for ll in logLevels.values()]))

    def _setHandler(self, handler):
        """
        Replace the handlers on our logger with the given one.

        The de-duplication filter goes on the handler, not the logger, so that it also sees the messages from the
        module-level loggers underneath ours.
        """
        for h in self.logger.handlers:
            h.close()
        handler.addFilter(self._deduplicationFilter)
        self.logger.handlers = [handler]

    def setDefaultHandlers(self):
        """Log to stdout without level prefixes, as before a run starts or after it ends."""
        handler = _StdoutHandler()
        handler.setFormatter(_RunLogFormatter(self._mpiRank, showLevel=False))
        self._setHandler(handler)

    def log(self, msgType, msg, single=False, label=None, **kwargs):
        """
        Log a message at the given level, which is a level name (like "debug") or a level number.

        This is used by all message passers (e.g. info, warning, etc.).

        Parameters
        ----------
        msgType : str or int
            The log level.
        msg : object
            The message, which is converted to a string as-is (it is not %-formatted).
        single : bool, optional
            If True, this message is only logged the first time it (or its label) is seen.
        label : str, optional
            The label used to de-duplicate this message and count it in the warning report. Defaults to the message.
        """
        msgLevel = msgType if isinstance(msgType, int) else self.logLevels[msgType][0]
        self.logger.log(msgLevel, str(msg), extra={"single": single, "label": label})

    def getDuplicatesFilter(self):
        """Return the filter that de-duplicates messages and counts warnings."""
        return self._deduplicationFilter

    def clearSingleLogs(self):
        """Reset the list of de-duplicated warnings, so users can see those warnings again."""
        self._deduplicationFilter.singleMessageLabels.clear()

    def warningReport(self):
        """Summarize all warnings for the run."""
        self.logger.info("----- Final Warning Count --------")
        self.logger.info("  {0:^10s}   {1:^25s}".format("COUNT", "LABEL"))

        warningCounts = self._deduplicationFilter.warningCounts
        if not warningCounts:
            self.logger.info("  {0:^10s}   {1:^25s}".format(str(0), str("None Found")))
            self.logger.info("------------------------------------")
            return

        total = 0
        for label, count in sorted(warningCounts.items(), key=operator.itemgetter(1), reverse=True):
            self.logger.info(f"  {str(count):^10s}   {str(label):^25s}")
            total += count
        self.logger.info("------------------------------------")

        # add a totals line
        self.logger.info(f"  {str(total):^10s}   Total Number of Warnings")
        self.logger.info("------------------------------------")

    def getLogVerbosityRank(self, level):
        """Return integer verbosity rank given the string verbosity name."""
        try:
            return self.logLevels[level][0]
        except KeyError:
            log_strs = list(self.logLevels.keys())
            raise KeyError(f"{level} is not a valid verbosity level: {log_strs}")

    def setVerbosity(self, level):
        """
        Sets the minimum output verbosity for the logger.

        Any message with a higher verbosity than this will be emitted.

        Parameters
        ----------
        level : int or str
            The level to set the log output verbosity to. Valid numbers are 0-50 and valid strings are keys of logLevels

        Examples
        --------
        >>> setVerbosity('debug') -> sets to 0
        >>> setVerbosity(0) -> sets to 0

        """
        # first, we have to get a valid integer from the input level
        if isinstance(level, str):
            self._verbosity = self.getLogVerbosityRank(level)
        elif isinstance(level, int):
            # Snap the level down to one of our named levels, so that the verbosity always means one of them.
            if level in self._logLevelNumbers:
                self._verbosity = level
            elif level < self._logLevelNumbers[0]:
                self._verbosity = self._logLevelNumbers[0]
            else:
                for i in range(len(self._logLevelNumbers) - 1, -1, -1):
                    if level >= self._logLevelNumbers[i]:
                        self._verbosity = self._logLevelNumbers[i]
                        break
        else:
            raise TypeError(f"Invalid verbosity rank {level}.")

        self.logger.setLevel(self._verbosity)

    def getVerbosity(self):
        """Return the global runLog verbosity."""
        return self._verbosity

    def restoreStandardStreams(self):
        """Set the system stderr back to its default (as it was when the run started)."""
        if self.initialErr is not None:
            sys.stderr = self.initialErr
            self.initialErr = None

        if self._stderrFile is not None:
            self._stderrFile.close()
            self._stderrFile = None

    def _logFilePath(self, name, extension):
        return os.path.join(_LOG_DIR, f"{STDOUT_LOGGER_NAME}.{name}.{self._mpiRank:04d}.{extension}")

    def startLog(self, name):
        """
        Start the run log, formatting the output and (for worker processes) sending it to files.

        The lead process logs to stdout. Each worker process logs to ``logs/ARMI.<name>.<rank>.stdout`` and sends its
        stderr to ``logs/ARMI.<name>.<rank>.stderr``. These are combined into one file by :func:`concatenateLogs` at
        the end of the run.

        .. impl:: Logging is done to the screen and to file.
            :id: I_ARMI_LOG_IO
            :implements: R_ARMI_LOG_IO

            This logger makes it easy for users to add log statements to an ARMI application, and ARMI will control the
            flow of those log statements. In particular, ARMI routes the standard library ``armi`` logger, and any
            module-level loggers underneath it, to both screen and file. This works for stdout and stderr.

            At any place in the ARMI application, developers can interject a plain text logging message, and when that
            code is hit during an ARMI simulation, the text will be piped to screen (for the lead process) or to a log
            file (for the worker processes), which are combined at the end of the run.
        """
        if self._mpiRank == 0:
            handler = _StdoutHandler()
        else:
            createLogDir(_LOG_DIR)
            filePath = self._logFilePath(name, "stdout")
            if context.PLATFORM == context.Platform.WINDOWS:
                handler = logging.FileHandler(filePath, delay=True)
            else:
                handler = handlers.WatchedFileHandler(filePath, delay=True)

        handler.setFormatter(_RunLogFormatter(self._mpiRank))
        self._setHandler(handler)
        self.logger.setLevel(self._verbosity)

        if self._mpiRank != 0:
            # send anything written to stderr (like tracebacks) to a file
            self.restoreStandardStreams()
            self._stderrFile = open(self._logFilePath(name, "stderr"), "a", buffering=1)
            self.initialErr = sys.stderr
            sys.stderr = self._stderrFile

    def close(self):
        """Stop logging to files, restore stderr, and go back to the default stdout handler."""
        self.setDefaultHandlers()
        self.restoreStandardStreams()


def close(mpiRank=None):
    """End use of the log. Concatenate if needed and restore defaults."""
    mpiRank = context.MPI_RANK if mpiRank is None else mpiRank

    if mpiRank == 0:
        try:
            concatenateLogs()
        except IOError as ee:
            warning("Failed to concatenate logs due to IOError.")
            error(ee)

    LOG.close()


def concatenateLogs(logDir=None):
    """
    Concatenate the armi run logs and delete them.

    Should only ever be called by parent.

    .. impl:: Log files from different processes are combined.
        :id: I_ARMI_LOG_MPI
        :implements: R_ARMI_LOG_MPI

        The log files are plain text files. Since ARMI is frequently run in parallel, the situation arises where each
        ARMI process generates its own plain text log file. This function combines the separate log files, per process,
        into one log file.

        The files are written in numerical order, with the lead process stdout first then the lead process stderr. Then
        each other process is written to the combined file, in order, stdout then stderr. Finally, the original stdout
        and stderr files are deleted.
    """
    if logDir is None:
        logDir = getLogDir()

    # find all the logging-module-based log files
    stdoutFiles = sorted(glob(os.path.join(logDir, "*.stdout")))
    if not len(stdoutFiles):
        info("No log files found to concatenate.")
        return

    info(f"Concatenating {len(stdoutFiles)} log files")

    # default worker log name if none is found
    caseTitle = "armi-workers"
    for stdoutPath in stdoutFiles:
        stdoutFile = os.path.normpath(stdoutPath).split(os.sep)[-1]
        prefix = STDOUT_LOGGER_NAME + "."
        if stdoutFile[0 : len(prefix)] == prefix:
            candidate = stdoutFile.split(".")[-3]
            if len(candidate) > 0:
                caseTitle = candidate
                break

    combinedLogName = os.path.join(logDir, f"{caseTitle}-mpi.log")
    with open(combinedLogName, "w") as workerLog:
        workerLog.write("\n{0} CONCATENATED WORKER LOG FILES {1}\n".format("-" * 10, "-" * 10))

        for stdoutName in stdoutFiles:
            # NOTE: If the log file name format changes, this will need to change.
            rank = int(stdoutName.split(".")[-2])
            with open(stdoutName, "r") as logFile:
                data = logFile.read()
                # only write if there's something to write
                if data:
                    rankId = "\n{0} RANK {1:03d} STDOUT {2}\n".format("-" * 10, rank, "-" * 60)
                    if rank == 0:
                        print(rankId, file=sys.stdout)
                        print(data, file=sys.stdout)
                    else:
                        workerLog.write(rankId)
                        workerLog.write(data)
            try:
                os.remove(stdoutName)
            except OSError:
                warning(f"Could not delete {stdoutName}")

            # then print the stderr messages for that child process
            stderrName = stdoutName[:-3] + "err"
            if os.path.exists(stderrName):
                with open(stderrName) as logFile:
                    data = logFile.read()
                    if data:
                        # only write if there's something to write.
                        rankId = "\n{0} RANK {1:03d} STDERR {2}\n".format("-" * 10, rank, "-" * 60)
                        print(rankId, file=sys.stderr)
                        print(data, file=sys.stderr)
                try:
                    os.remove(stderrName)
                except OSError:
                    warning(f"Could not delete {stderrName}")


# Here are all the module-level functions that should be used for most outputs. They use the Log
# object behind the scenes.
def raw(msg):
    """Print raw text without any special functionality."""
    LOG.log("header", msg, single=False)


def extra(msg, single=False, label=None):
    LOG.log("extra", msg, single=single, label=label)


def debug(msg, single=False, label=None):
    LOG.log("debug", msg, single=single, label=label)


def info(msg, single=False, label=None):
    LOG.log("info", msg, single=single, label=label)


def important(msg, single=False, label=None):
    LOG.log("important", msg, single=single, label=label)


def warning(msg, single=False, label=None):
    LOG.log("warning", msg, single=single, label=label)


def error(msg, single=False, label=None):
    LOG.log("error", msg, single=single, label=label)


def header(msg, single=False, label=None):
    LOG.log("header", msg, single=single, label=label)


def warningReport():
    LOG.warningReport()


def setVerbosity(level):
    LOG.setVerbosity(level)


def getVerbosity():
    return LOG.getVerbosity()


class DeduplicationFilter(logging.Filter):
    """
    Allow users to log a message only once, and count all warnings for the warning report.

    A record is de-duplicated by its ``label`` attribute (falling back to the message itself) when its ``single``
    attribute is True. Both are set through the ``extra`` argument of a logging call.
    """

    def __init__(self, *args, **kwargs):
        logging.Filter.__init__(self, *args, **kwargs)
        self.singleMessageLabels = set()
        self.warningCounts = {}

    def filter(self, record):
        # determine if this is a "do not duplicate" message
        single = getattr(record, "single", False)

        # grab the label if it exist, otherwise use the message itself as the label
        label = getattr(record, "label", None)
        label = record.getMessage() if label is None else label

        # Track all warnings, for warning report
        if record.levelno in (logging.WARNING, logging.CRITICAL):
            if label not in self.warningCounts:
                self.warningCounts[label] = 1
            else:
                self.warningCounts[label] += 1
                if single:
                    return False

        # If the message is set to "do not duplicate" we may filter it out
        if single:
            if label in self.singleMessageLabels:
                return False
            self.singleMessageLabels.add(label)

        return True


def createLogDir(logDir: str = None) -> None:
    """A helper method to create the log directory."""
    # the usual case is the user does not pass in a log dir path, so we use the global one
    if logDir is None:
        logDir = getLogDir()

    # create the directory
    if not os.path.exists(logDir):
        try:
            os.makedirs(logDir)
        except FileExistsError:
            # If we hit this race condition, we still win.
            return

    # potentially, wait for directory to be created
    secondsWait = 0.5
    loopCounter = 0
    while not os.path.exists(logDir):
        loopCounter += 1
        if loopCounter > (OS_SECONDS_TIMEOUT / secondsWait):
            raise OSError(f"Was unable to create the log directory: {logDir}")

        time.sleep(secondsWait)


def logFactory():
    """Create the default logging object."""
    return _RunLog(int(context.MPI_RANK))


LOG = logFactory()
