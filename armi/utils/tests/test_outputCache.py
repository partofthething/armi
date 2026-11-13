# Copyright 2022 TerraPower, LLC
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
"""Tests of the cache of external code runs."""

import os
import unittest
from unittest import mock

from armi import settings
from armi.utils import outputCache
from armi.utils.directoryChangers import TemporaryDirectoryChanger
from armi.utils.outputCache import NAME, OutputCache, denormalize, normalize


def write(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return os.path.abspath(path)


class TestOutputCache(unittest.TestCase):
    def setUp(self):
        self.td = TemporaryDirectoryChanger()
        self.td.__enter__()
        self.cache = OutputCache("cache")
        self.code = write("code.exe", "a stand-in executable")
        self.library = write("library.dat", "cross sections")

    def tearDown(self):
        self.td.__exit__(None, None, None)

    def run_(self, directory, name, deck="lattice\n", produce=True):
        """A run of ``name`` in ``directory``: key it, restore it, or 'run' it (write its output) and file it."""
        deckPath = write(f"{directory}/{name}.inp", f"* {name}\n{deck}FILE './{name}.out'\n")
        inputs = {f"{name}.inp": deckPath, "library": self.library}
        key = self.cache.key(self.code, inputs, outputs=[f"{name}.out"], name=name)
        restored = self.cache.retrieve(key, directory, name=name)
        if restored is not None:
            return key, "restored"
        if produce:
            output = write(f"{directory}/{name}.out", f"results of {name}\n")
            self.cache.store(key, [output, f"{directory}/never-made.txt"], name=name)
        return key, "ran"

    def test_aRunIsRestoredTheSecondTime(self):
        key, first = self.run_("a", "p24")
        _, second = self.run_("b", "p24")
        self.assertEqual((first, second), ("ran", "restored"))
        with open("b/p24.out") as f:
            self.assertEqual(f.read(), "results of p24\n")
        self.assertEqual(list(self.cache.manifest(key)["outputs"]), [f"{NAME}.out"])

    def test_theSameRunUnderAnotherNameIsTheSameEntry(self):
        self.run_("a", "p24")
        _, how = self.run_("b", "e450-b24x2-r")
        self.assertEqual(how, "restored")
        with open("b/e450-b24x2-r.out") as f:
            self.assertEqual(f.read(), "results of e450-b24x2-r\n")

    def test_anyInputChangeIsANewEntry(self):
        first, _ = self.run_("a", "p24")
        second, how = self.run_("b", "p24", deck="another lattice\n")
        self.assertNotEqual(first, second)
        self.assertEqual(how, "ran")
        write("library.dat", "other cross sections")
        third, _ = self.run_("c", "p24")
        self.assertNotIn(third, (first, second))

    def test_aDamagedEntryIsRemoved(self):
        key, _ = self.run_("a", "p24")
        write(os.path.join(self.cache.entry(key), f"{NAME}.out"), "tampered")
        self.assertIsNone(self.cache.retrieve(key, "b", name="p24"))
        self.assertFalse(os.path.exists(self.cache.entry(key)))

    def test_anEntryWithoutAManifestIsNotAnEntry(self):
        key, _ = self.run_("a", "p24", produce=False)
        os.makedirs(self.cache.entry(key))
        self.assertIsNone(self.cache.manifest(key))
        _, how = self.run_("b", "p24")
        self.assertEqual(how, "ran")
        self.assertIsNotNone(self.cache.manifest(key))

    def test_binaryOutputsAreCopiedAsTheyAre(self):
        deck = write("a/p24.inp", "deck")
        key = self.cache.key(self.code, [deck], name="p24")
        blob = os.path.abspath("a/p24.bin")
        with open(blob, "wb") as f:
            f.write(b"\xff\xfe p24 \x00")
        self.cache.store(key, [blob], name="p24")
        (restored,) = self.cache.retrieve(key, ".", name="b12")
        self.assertEqual(os.path.basename(restored), "b12.bin")
        with open(restored, "rb") as f:
            self.assertEqual(f.read(), b"\xff\xfe p24 \x00")

    def test_theNameIsAWholeWord(self):
        text = "refl reflector ./refl.out refl-2 REFL"
        self.assertEqual(normalize(text, "refl"), f"{NAME} reflector ./{NAME}.out refl-2 REFL")
        self.assertEqual(denormalize(normalize(text, "refl"), "r1"), "r1 reflector ./r1.out refl-2 REFL")
        self.assertEqual(normalize(text, None), text)

    def test_fromSettings(self):
        cs = settings.Settings()
        with mock.patch.dict(os.environ, {outputCache.ENVIRONMENT_VARIABLE: ""}):
            self.assertIsNone(outputCache.fromSettings(cs))
        with mock.patch.dict(os.environ, {outputCache.ENVIRONMENT_VARIABLE: "/machine/cache"}):
            self.assertEqual(outputCache.fromSettings(cs).root, "/machine/cache")
            here = cs.modified(newSettings={"outputCacheLocation": "/case/cache"})
            self.assertEqual(outputCache.fromSettings(here).root, "/case/cache")
