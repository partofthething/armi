"""Small test helpers.

ARMI's own ``TemporaryDirectoryChanger`` expects global state that its test suite sets up, which
is more coupling than these tests need for "give me a scratch directory".
"""

import contextlib
import os
import tempfile


@contextlib.contextmanager
def inTempDir():
    """Run inside a fresh directory, and take it away afterwards."""
    previous = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="armi-webui-test-") as scratch:
        os.chdir(scratch)
        try:
            yield scratch
        finally:
            os.chdir(previous)
