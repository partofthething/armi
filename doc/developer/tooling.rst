.. _armi-tooling:

**************************
Tooling and Infrastructure
**************************

Good Commit Messages
====================
The ARMI project follows a few basic rules for "good" commit messages:

* The purpose of the message is to explain to the changes you made to a stranger 5 years from now.
* Keep your writing short and to the point.
* The first line of each commit must be shorter than 50 characters.
* Commit messages should be active voice, present tense.
* Multi-line comments are allowed, but make sure the second line of the commit is blank:

.. code-block::

    Adding this commit for REASONS.

    Here is some super important extra info.
    Oh, there is so much extra info.
    This section
    * is
    * totally
    * optional.

Good Pull Requests
==================
A good commit is like a sentence: it expresses one complete thought. A good Pull Request (PR) is
like a paragraph: a few commits that add up to one larger idea.

One Idea = One PR
-----------------
Keep each PR to one idea. Unrelated changes are easier to review, test, and revert as separate PRs.

Open it when it is ready
------------------------
Open a PR when you think it is ready for review: the code works, the tests are written, and the
docs are updated. Use a Draft PR if you want early feedback on something unfinished.

Test It
-------
Unless a PR only touches documentation or formatting, it needs tests:

* New code needs tests showing it works.
* Changed code needs tests showing it still works.
* A bug fix needs a test that fails without the fix.

Document It
-----------
The API reference is generated from docstrings, so write good ones: say what a function does and
why, for someone who is new to that part of the code. If your change makes something in ``doc/``
out of date, update it in the same PR.

Watch for Requirements
----------------------
When you are touching code in ARMI, watch out for the docstrings in the methods, classes, or
modules you are editing. These docstrings might have bread crumbs that link back to requirements.
Such breadcrumbs will look like:

.. code-block::

    """
    .. test: This is a requirement test breadcrumb.

    .. impl: This is an requirement implementation breadcrumb.

    """

If you touch any code that has such a docstring, even at the top of the file, you are going to be
responsible for not breaking that code/functionality. And you will be required to explicitly call
out that you touch such a code in your PR.

Your PR reviewer will take an extra look at any PR that touches a requirement test or implementation.
And you will need to add a special note in your PR description, under a field called "One-line Impact on Requirements". This note can be as long as it needs to be, but can only be on one line.


Packaging and dependency management
===================================
There are many ways to manage and package a Python project. We try to centralize as much of this as possible in a ``pyproject.toml``, following existing conventions. In particular, we follow `the official Python packaging guidance <https://packaging.python.org/en/latest/>`_.

pyproject.toml
--------------
As much as possible, the ARMI team will try to centralize our installation and build systems through
the top-level ``pyproject.toml`` file. The only exception will be our documentation, which has much
customization done through the Sphinx ``doc/conf.py`` file.

The packages listed in the ``install_requires`` argument to ``setup()`` are meant to express, as
abstractly as possible, the packages that need to be installed **somehow** for the package to work.
In addition, ``extras_require`` are used to specify other packages that are not strictly required,
but if installed enable extra functionality, like unit testing or building documentation.

.. _third-party-licensing:

Third-Party Licensing
---------------------
ARMI is Apache 2.0 licensed, and people build private and commercial applications on it. New
dependencies should have permissive licenses that are compatible with that, such as MIT, BSD, or
Apache 2.0. Avoid copyleft licenses like the GPL, which would put conditions on how ARMI and the
applications built on it can be distributed.

Releasing a New Version of ARMI
===============================
We use the common ``major.minor.bump`` version scheme where a version string might look like
``0.1.7``, ``1.0.0``, or ``12.3.123``. Each number has a specific meaning:

* ``major`` - Revved for major milestones of the ARMI project.
* ``minor`` - Revved for the usual release, real feature work completed.
* ``bump`` - Revved for very small releases, but still well-tested with a stable API.

**NOTE**: Changes to documentation or testing probably do not deserve a version bump.

**Any change to a major or minor version is considered a release.**

Releases are made by the maintainers of this repository. The only tags in the repository are for
released versions.

Every release should follow this process:

1. Ensure all unit tests pass and the documentation is building correctly.
2. Create a release PR:

    - Bump the ``version`` string in ``pyproject.toml``.
    - Now that the release is done, hard-copy the SCR information into the last releases RST file, so we don't keep regenerating it: ``doc/qa_docs/scr/x.y.rst``.
    - Update the commit in ``doc/qa_docs/scr/latest_scr.rst`` to the release commit.

3. Tag the commit after it goes into the repo:

    - From this commit: ``git tag -a 1.0.0 -m "Release v1.0.0"``
    - Or from another commit: ``git tag -a 1.0.0 <commit-hash> -m "Release v1.0.0"``
    - Pushing to the repo: ``git push origin 1.0.0``
    - **NOTE** - The ONLY tags in the ARMI repo are for official version releases.

4. Make sure the release has its page in ``doc/release`` (this fork keeps its release notes in the docs).
5. Tell everyone!

Logging with runLog
===================
ARMI provides a logging tool, ``runLog``, to be used in place of ``print`` for all logging during a
simulation. It is very easy to use:

.. code-block:: python

    from armi import runLog

    runLog.debug("This will only be seen if you run in debug mode.")
    runLog.info("Default log level.")
    runLog.error("The run will die, or the results are invalid.")

.. note::
    Calling ``runLog.error()`` is not the same as calling Python's ``raise error``; a log statement
    does not kill a run, or raise an error, it just puts some text in the log.

When an ARMI simulation is run, it will be run at a particular log level. All log messages that are
at or above that log level will be seen during the simulation and in the final log files. To control
the log level of an ARMI run, you use the setting ``verbosity`` in your settings file. You will
probably be running ARMI in a parallel mode, and if you want the child processes to have a different
log level than the main process, you can set ``branchVerbosity`` to the desired verbosity of all the
child processes.

For reference, here are the log levels that ARMI supports:

.. list-table::
    :widths: 20 20 60
    :header-rows: 1

    * - Level
      - Value
      - When to Use
    * - debug
      - 10
      - This will only be seen if the simulation is run in debug mode.
    * - extra
      - 15
      - More detailed than will normally be seen in a usual simulation.
    * - info
      - 20
      - Use only for things that important enough to be visible during every normal simulation.
    * - important
      - 25
      - More important than the default log level, but not a problem or issue.
    * - prompt
      - 27
      - RESERVED for the ARMI CLI.
    * - warning
      - 30
      - Use ONLY for issues that may or may not invalidate the simulation results.
    * - error
      - 40
      - Use ONLY for problems that halt the program or invalidate the simulation results.
    * - header
      - 100
      - Use ONLY to define major sections in the log files.


Blocking Duplicate Logs
-----------------------
Sometimes you want to add a log message, but based on program logic it might pop up in the final log
file multiple times, even thousands of times. And probably you do not want that. Happily, the
``runLog`` tool provides a simple argument that will stop a single log line from being logged more
than once.

Here is a (silly) example of a heavily duplicate log message:

.. code-block:: python

    for _i in range(1000):
        runLog.warning("Something wicked this way comes.")

That log message gets printed 1,000 times, but we can ensure it is only printed once:

.. code-block:: python

    for _i in range(1000):
        runLog.warning("Something wicked this way comes.", single=True)

This is handy for keeping log files readable.


Module-Level Logging
--------------------
The ``runLog`` tool also allows for you to log one module differently from the rest of the code
base. For instance, you could set the log level to "debug" in just one Python file, to help testing
during development.

That functionality is provided by a standard library logger that lives underneath the ``armi`` logger, so its messages
are formatted and written by the ARMI run log like any other:

.. code-block:: python

    from armi import runLog
    log = runLog.getLogger(__name__)

You can then log using the standard ``logging`` interface. The ARMI-specific levels are available as constants, and
the ``single`` and ``label`` options are passed through ``extra``:

.. code-block:: python

    log.info('Normal stuff.')
    log.error('Oh no!')
    log.log(runLog.IMPORTANT, 'Worth calling out.')
    log.warning('Only once, please.', extra={'single': True})

Finally, you can change the logging level of just that module, either with the ``moduleVerbosity`` setting or by
doing:

.. code-block:: python

    log.setLevel(logging.DEBUG)

Inside the ``armi`` package, ``logging.getLogger(__name__)`` gives the same logger as ``runLog.getLogger(__name__)``.
In a downstream application, use ``runLog.getLogger(__name__)``: it puts your module's logger underneath the ``armi``
logger (e.g. ``armi.myApp.physics``), which is what sends its messages to the ARMI run log. The ``moduleVerbosity``
setting uses the same names, so ``myApp.physics: debug`` works as written.
