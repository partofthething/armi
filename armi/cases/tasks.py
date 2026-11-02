"""
Tasks: what a case does when a :py:class:`~armi.cases.runners.SuiteRunner` runs it.

A task is a module-level function named ``"module:function"`` so a worker process (local, over SSH, or on a batch
system) can import it. It receives the case, loaded from its directory with the working directory set there, and the
directories of the cases it depends on::

    def summarize(case, dependencies: dict[str, str]) -> dict:
        ...
        return {"keff": 1.0012}

and returns a JSON-serializable dict of results. Scalars there land in
:py:meth:`~armi.cases.suite.CaseSuite.resultsTable`.

A task need not run the whole operator loop. Screening sweeps usually want one cheap calculation per case (a single
state at zero burnup, say), and a task can do exactly that while reusing the case's settings and blueprints.
"""

import importlib
from typing import Any, Callable, Dict

#: The task of a case with no task of its own: the full ARMI operator loop.
DEFAULT_TASK = "armi.cases.tasks:runOperator"


def runOperator(case, dependencies: Dict[str, str]) -> dict:
    """Run the case's operator loop, as ``armi run`` would."""
    case.run()
    return {}


def resolve(spec: str) -> Callable:
    """The function a ``"module:function"`` spec names."""
    moduleName, _, functionName = spec.partition(":")
    if not functionName:
        raise ValueError(f"A task is named as 'module:function', not {spec!r}")
    target: Any = importlib.import_module(moduleName)
    for part in functionName.split("."):
        target = getattr(target, part)
    return target
