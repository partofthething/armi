# Copyright 2026 Ocean Atomics, LLC
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
