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

"""
The outputs of external code runs, filed under the hash of what determines them.

A run of an external code is a function of the executable, the files it reads and what is asked of it. Hash those and
the outputs can be filed under the hash and handed back the next time anything asks for the same run -- in a rerun, in
another case that needs the same calculation, or on another person's machine sharing the cache directory -- instead of
running the code again. Nothing has to be named to be shared, and nothing is shared that differs in any input.

* **Keys are content.** Every input file is hashed with SHA-256 (once per version of the file per process, so a large
  nuclear data library costs one read) together with its name in the run, the executable's contents, the outputs asked
  for and anything else the caller says the run depends on. A format version is part of the key, so an entry written
  by another version of this module is never misread.
* **A run's own name can be left out of its key.** A deck ``p24.x2m`` writing ``p24.out`` computes the same thing as
  the same deck called ``b12.x2m``. Given that ``name``, each whole-word occurrence of it is replaced by a placeholder
  in the input files named after the run, in the output names and in the text of the outputs before they are hashed or
  filed, and put back as the asking run's name when they are restored.
* **Safe to share.** An entry is written to a scratch directory and renamed into place with its manifest written last,
  so a reader never sees half an entry and two runs filing the same result do not corrupt each other. A damaged entry
  is removed when it is found, and the next run files a good one.

Use::

    cache = outputCache.fromSettings(cs)  # outputCacheLocation, else ARMI_OUTPUT_CACHE, else None
    key = cache.key(executable, {"deck.inp": deckPath, "library": libraryPath}, outputs=["deck.out"], name="deck")
    if cache.retrieve(key, workDir, name="deck") is None:
        run(...)
        cache.store(key, [os.path.join(workDir, "deck.out")], name="deck")
"""

import functools
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Optional, Union

from armi import runLog

PathLike = Union[str, Path]

#: The manifest of an entry: the outputs and their hashes. Written last, so an entry without one is not an entry.
MANIFEST_NAME = "manifest.json"

#: Bump when the key or the layout changes, so entries of another version are never misread.
VERSION = 2

#: Stands in for a run's own name in everything the cache keeps.
NAME = "{RUN-NAME}"

#: The environment variable naming a machine's shared cache, for cases that do not set ``outputCacheLocation``.
ENVIRONMENT_VARIABLE = "ARMI_OUTPUT_CACHE"


@functools.cache
def _digest(path: str, size: int, mtime: float) -> str:
    """SHA-256 of a file's contents, once per version of the file (its size and mtime) per process."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fileDigest(path: PathLike) -> str:
    """SHA-256 of what ``path`` holds, following symlinks."""
    real = os.path.realpath(path)
    stat = os.stat(real)
    return _digest(real, stat.st_size, stat.st_mtime)


def _pattern(name: str) -> re.Pattern:
    """``name`` as a whole word: not part of a longer name, such as ``reflector`` for ``refl``."""
    return re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])")


def normalize(text: str, name: Optional[str]) -> str:
    """``text`` with the run's name replaced by :py:data:`NAME`; unchanged without a name."""
    return _pattern(name).sub(NAME, text) if name else text


def denormalize(text: str, name: Optional[str]) -> str:
    """``text`` with :py:data:`NAME` replaced by the run's name; unchanged without a name."""
    return text.replace(NAME, name) if name else text


def _text(path: PathLike) -> Optional[str]:
    """A file's contents if it is UTF-8 text, else None."""
    with open(path, "rb") as f:
        data = f.read()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _copy(source: PathLike, destination: PathLike, transform: Callable[[str], str]) -> None:
    """Copy a file, passing its text through ``transform`` if it is text; anything else is copied as it is."""
    text = _text(source)
    if text is None:
        shutil.copyfile(source, destination)
        return
    with open(destination, "w", encoding="utf-8", newline="") as f:
        f.write(transform(text))


class OutputCache:
    """
    Code runs filed under the hash of their inputs, in a directory that may be shared.

    Each entry is a directory named by its key, under a subdirectory of the key's first two characters, holding the
    output files (with the run's name taken out of their names and their text) and the manifest.
    """

    def __init__(self, root: PathLike):
        self.root = os.path.abspath(root)

    def __repr__(self):
        return f"<OutputCache {self.root}>"

    def key(
        self,
        executable: PathLike,
        inputs: Union[Sequence[PathLike], Mapping[str, PathLike]],
        outputs: Sequence = (),
        name: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> str:
        """
        The hash of everything that determines what a run would produce.

        Parameters
        ----------
        executable : path
            The code. Its contents are hashed, so the same binary anywhere is the same code.
        inputs : sequence or mapping
            The files the run reads: paths, named by their base names, or ``{name in the run: path}``.
        outputs : sequence
            What is asked of the run: output names, or anything JSON-serializable that describes them.
        name : str, optional
            The run's own name, left out of the key: out of every name in it, and out of the text of the input files
            whose name holds it as a whole word (the run's deck, not a library it reads).
        extra : dict, optional
            Anything else the outputs depend on that is not in a file. JSON-serializable.
        """
        if not isinstance(inputs, Mapping):
            inputs = {os.path.basename(path): path for path in inputs}
        files = {}
        for runName, path in sorted(inputs.items()):
            text = _text(path) if name and _pattern(name).search(runName) else None
            if text is not None:
                files[normalize(runName, name)] = hashlib.sha256(normalize(text, name).encode()).hexdigest()
            else:
                files[normalize(runName, name)] = fileDigest(path)
        parts = {
            "version": VERSION,
            "executable": fileDigest(executable),
            "inputs": files,
            "outputs": json.loads(normalize(json.dumps(list(outputs)), name)),
            "extra": extra or {},
        }
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()

    def entry(self, key: str) -> str:
        """The directory of the entry filed under ``key``."""
        return os.path.join(self.root, key[:2], key)

    def manifest(self, key: str) -> Optional[dict]:
        """The manifest of the entry under ``key``, or None if there is no complete entry."""
        path = os.path.join(self.entry(key), MANIFEST_NAME)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    def retrieve(self, key: str, destination: PathLike, name: Optional[str] = None) -> Optional[list]:
        """
        Copy the outputs filed under ``key`` into ``destination``, named for the asking run, and return their paths.

        None if there is no such entry, or if it is damaged (an output missing or not matching the manifest), in which
        case it is removed so that the next run files a good one.
        """
        manifest = self.manifest(key)
        if manifest is None:
            return None
        entry = self.entry(key)
        restored = []
        try:
            for stored, digest in manifest["outputs"].items():
                source = os.path.join(entry, stored)
                if fileDigest(source) != digest:
                    raise ValueError(f"{stored} does not match the manifest")
                target = os.path.join(destination, denormalize(stored, name))
                _copy(source, target, lambda text: denormalize(text, name))
                restored.append(target)
        except (OSError, ValueError) as error:
            runLog.warning(f"Removing the damaged cache entry {entry}: {error}")
            shutil.rmtree(entry, ignore_errors=True)
            return None
        runLog.info(f"Restored {len(restored)} outputs from {entry}")
        return restored

    def store(
        self, key: str, outputs: Sequence[PathLike], name: Optional[str] = None, metadata: Optional[dict] = None
    ) -> None:
        """
        File a run's ``outputs`` under ``key``. Outputs that do not exist are skipped, so the list may be greedy.

        An entry already there is kept: whoever filed it ran the same calculation.
        """
        if self.manifest(key) is not None:
            return
        entry = self.entry(key)
        os.makedirs(os.path.dirname(entry), exist_ok=True)
        scratch = tempfile.mkdtemp(prefix=".incoming-", dir=os.path.dirname(entry))
        try:
            digests = {}
            for path in outputs:
                if not os.path.exists(path):
                    continue
                stored = normalize(os.path.basename(path), name)
                _copy(path, os.path.join(scratch, stored), lambda text: normalize(text, name))
                digests[stored] = fileDigest(os.path.join(scratch, stored))
            manifest = {"outputs": digests, "stored": time.strftime("%Y-%m-%d %H:%M:%S"), **(metadata or {})}
            with open(os.path.join(scratch, MANIFEST_NAME), "w") as f:
                json.dump(manifest, f, indent=1)
            if os.path.isdir(entry):
                # What a writer that died before its manifest left behind; this replaces it.
                shutil.rmtree(entry, ignore_errors=True)
            os.rename(scratch, entry)
            runLog.info(f"Filed {len(digests)} outputs in {entry}")
        except OSError:
            # Another process filed the same run first; theirs is as good as this one.
            shutil.rmtree(scratch, ignore_errors=True)


def fromSettings(cs) -> Optional[OutputCache]:
    """The cache a case uses: its ``outputCacheLocation``, else ``ARMI_OUTPUT_CACHE``, else none."""
    root = cs["outputCacheLocation"] or os.environ.get(ENVIRONMENT_VARIABLE, "")
    return OutputCache(root) if root else None
