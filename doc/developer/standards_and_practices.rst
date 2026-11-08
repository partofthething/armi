.. _armi-stds:

**********************************
Standards and Practices for Coding
**********************************

These guidelines keep the code base consistent, so that someone familiar with one module can read
and work on another without adjusting to a new style. The overall theme is: **balance clarity with
conciseness**. Be as clear as possible, using as few words as possible.

Most of the guidelines can be broken when there is a good reason; say why in the PR.


Use of Large Language Models (LLMs)
===================================
This fork of ARMI welcomes the judicious use of LLMs and AI coding assistants for any part of development, including
writing code, tests, documentation, and PR or Issue descriptions. There are no hard restrictions on how or where they
are used.

ARMI is used to perform nuclear reactor simulation and analysis, so quality matters regardless of how a change was
produced. Contributors using LLMs are encouraged to:

1. Understand and stand behind everything they submit. The person opening a PR is responsible for it, whether it was
   written by hand or with the help of a tool.
2. Review generated code, tests, and documentation for correctness and conciseness. Watch in particular for tests that
   merely restate the implementation, assert on trivial behavior, or pass for the wrong reasons.
3. Run the test suite and ``ruff`` locally before submitting.

Reviewers should hold LLM-assisted changes to the same standard as any other contribution.

Code formatting with ruff
=========================
ARMI is formatted with `ruff <https://docs.astral.sh/ruff/>`_, and CI checks that every change
is formatted.

The ``ruff`` formatter provides 100% consistency in ARMI for: whitespace, line length, trailing
commas, and string formatting. And it is easy to run on the command line:

.. code-block:: bash

    ruff format .

Code linting with ruff
======================
ARMI is also linted with ``ruff``, and new code should have no ``ruff`` warnings or errors:

.. code-block:: bash

    ruff check . --fix

Remove commented-out code
=========================
If you were testing code and you commented out a block, delete it before sending it in for code
review/production. If you want to see the old code later, it will still be in the Git history.

Avoid hard-coding run parameters
================================
Use the global settings object ``self.cs`` for most user-setable parameters that determine the run
environment, etc. This will help keep the amount of repeated code down.

Do not put user names, passwords, or paths on your own computer in the code. Use environmental variables where possible and user-configurable settings
elsewhere. You can also use the ``armi.ROOT`` variable (for the active code directory) or
``armi.RES``, and some other useful root-level variables.

Avoid the global keyword
========================
Module-level state changed through ``global`` makes code hard to follow, test, and debug. Pass state
explicitly or keep it on an object instead. A few older modules still use ``global``; please don't
add new uses.

Naming conventions
==================

ARMI uses ``camelCase`` rather than PEP 8's ``snake_case``, for historical reasons. Follow it so the code stays
consistent.

Use meaningful names
--------------------
Use descriptive names for variables, functions, methods, classes, and files. This might mean using a longer name like
``correlationMatrix`` instead of a shorter one like ``cm``.

General conventions
-------------------

Here are some general naming guidelines that are always applicable, but particularly applicable to public classes,
functions, and methods and their signatures (the signature includes the parameters):

* Variables that you designate as unused should be prefaced with an underscore (``_``).
* Do not use Python `reserved keywords <https://realpython.com/lessons/reserved-keywords/>`_ as variable names.
* Try to use names that are pronounceable. (Well-established variable names from equations are acceptable.)
* Keep names concise and expressive. (An exception is test method names, which may be longer and more
  descriptive.)
* Avoid abbreviations and acronyms, unless they are well understood by subject-matter experts (e.g. DB for database,
  XS for cross-sections, BU for burn up). When using acronyms or abbreviations with ``camelCase`` or ``PascalCase``:

  * Use the same case for two-letter acronyms/abbreviations (e.g. ``diskIO``, ``ioOperation``)
  * Use different case for acronyms/abbreviations with more than two characters (e.g. ``renderHtml()``, ``path``)

For consistency, use the following naming conventions:

package names
    Python packages, i.e. folders with an ``__init__.py``, **shall** use ``camelCase``.

module names
    Python modules, i.e. python files, **shall** use ``camelCase``.

    **Caveat:** Test modules are prefixed with ``test_``.

module constants
    Module-level "constants" **shall** be all capitals with an underscore separating words.

function names
    Functions **shall** use ``camelCase``. If the function is only intended to be used within that module, prefix
    it with a single leading underscore to indicate it is "module protected."

variable names
    Use ``camelCase``. In the odd scenario that the variable is not used (e.g. a method returns a
    tuple and you only want the first item), prefix it with a single leading underscore to indicate it is
    "module protected."

class names
    Classes **shall** use ``PascalCase``. If the class is only intended to be inherited by other classes within
    the module, prefix the class name with an underscore to indicate it is "module protected."

class attribute, instance attribute and method names
    Use ``camelCase``. If the method is only intended to be used within that module, prefix it with a single
    leading underscore to indicate it is "class protected."

test class and test function names
    See :ref:`unit-tests` for test function and class naming conventions.

Naming quick-reference
----------------------

.. list-table::
    :widths: 40 30 30
    :header-rows: 1

    * - Item to be named
      - Public
      - Private
    * - package

        (folder with an ``__init__.py``)
      - ``packageName``
      - N/A
    * - module

        (a ``.py`` file)
      - ``moduleName``
      - N/A
    * - module constant
      - ``SPEED_OF_LIGHT_IN_METERS_PER_SECOND``
      - ``_ONE_OVER_PI``
    * - method or function
      - ``doSomeAction()``
      - ``_doSomeAction()``
    * - class or instance attribute
      - ``assemblies``
      - ``_assemblies``
    * - variable names
      - ``linearHeatGenerationRate``
      - ``_unusedDescription``

        There are not "private" variables, use this for an unused variable.

Common naming conventions within ARMI
-------------------------------------
Single character variable names are not usually "clear" or "concise"; however, the following variables are a
well-established convention within ARMI and should be used by developers:

    * ``r`` when referring to a reactor, and
    * ``o`` when referring to a operator

Other names are also consistently used throughout ARMI for specific objects:

* ``cs`` when referring to a :py:class:``armi.settings.Settings`` class; this should not be confused with
  the ``.settings`` attribute of ``ArmiObject``.
* ``lib`` when referring to a cross section library


Prefer shorter methods
======================
A method should have one clear purpose. If you are writing a method that does one thing after the other,
break it up into multiple methods and have a primary method call them in order. If your method is longer
than 100 lines, see if you can't break it up. This does a few things:

1. It makes the code easier to read.
2. It makes the code chunks more reusable.
3. It makes the code easier to test.
4. It makes the code easier to profile, for performance.

Avoid repeating code
====================
In other words, don't repeat yourself. (`D. R. Y. <https://en.wikipedia.org/wiki/Don't_repeat_yourself>`_).
Repetitious code is harder to read, and harder to update. If you ever find yourself copying and pasting
code, consider pulling the repeated code out into its own function, or using a loop.

Public methods should have docstrings
=====================================
Always create the `proper docstrings <https://numpydoc.readthedocs.io/en/latest/example.html>`_ for all public
functions and public classes.

.. _unit-tests:

Unit tests
==========
New code needs unit tests that cover it.

ARMI uses the ``pytest`` library to drive tests, therefore tests need to be runnable from the commandline by
``python -m pytest armi``. Furthermore, for consistency:

* Each individual unit test should take under 10 seconds, on a modern laptop.
* All unit tests should be placed into a separate module from production code that is prefixed with ``test_``.
* All unit tests should be written in object-oriented fashion, inheriting from ``unittest.TestCase``.
* All test method names should start with ``test_``.
* All test clases should be of the form ``TestXYZ``.
* All test method names should be descriptive. If the test method is not descriptive enough, add a docstring.
* Unit tests should have at least one assertion.

Import statements
=================
Python allows many variations on the import statement, including relative imports, renaming and others. We prefer:

#. one import per line,
#. no relative imports
#. no periods
#. explicit module/namespace usage

Import ordering
---------------
For consistency, import packages in this order:

1. Python built-in packages
2. External third-party packages
3. ARMI modules

Place a single line between each of these groups, for example:

.. code-block:: python
    :linenos:

    import os
    import math

    import numpy as np
    from matplotlib import pyplot

    from armi import runLog

Don't create naked exceptions.
==============================
When creating ``try``/``except`` blocks, a naked exception is when the ``except`` command is not
followed by a specific exception type. Naked exceptions hide unexpected bugs. `This article <http://www.wilfred.me.uk/blog/2013/11/03/no-naked-excepts/>`_ explains the concept well,
as well as a few exceptions to this general rule.

Examples:

Bad

::

    >>> try:
    >>>     stuff()
    >>> except:
    >>>     runLog.warning('Some error occurred in stuff().')

Good (for one exception type)

::

    >>> try:
    >>>     stuff()
    >>> except AttributeError:
    >>>     runLog.warning('Some error occurred in stuff().')

Good (for multiple exception types)

::

    >>> try:
    >>>     stuff()
    >>> except (ZeroDivisionError, FloatingPointError):
    >>>     runLog.warning('Some error occurred in stuff().')


Data model
==========
Any reactor state information that is created by an ``Interface`` should be stored in the ARMI data model. The goal
is that given minimal information (i.e. case settings and blueprints) ARMI should be able to load an entire reactor
simulation from a given database. State kept outside the reactor, or in new input files, breaks that.

Input files
===========
ARMI developers **shall** use one of the following well-defined, Python-supported, input file formats.

.json
    JSON files are used for a variety of data-object representations. There are some limitations of
    JSON, in that it does not easily support comments. JSON is also very strict.

.yaml
    YAML files are like JSON files but can have comments in them.

General do's and don'ts
=======================

Do not use ``print``
    ARMI code should not use the ``print`` function; use one of the methods within ``armi.runLog``.

Do not add new ``TODO`` statements to the repo.
    If it is worth doing, open a GitHub Issue instead. The same goes for ``FIXME`` and ``XXX``.

Do not link GitHub tickets or PRs in code.
    Explain the point in the docstring or the docs instead; links rot and readers can't follow them
    offline.
