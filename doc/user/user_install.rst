************
Installation
************

This section will guide you through installing the ARMI Framework on your machine.

Prerequisites
=============
These instructions target users with some software development knowledge. In particular, we assume familiarity with
`Python <https://www.python.org/>`__, `virtual environments <https://docs.python.org/3/tutorial/venv.html>`_, and
`Git <https://git-scm.com/>`_.

You must have the following installed before proceeding:

* `Python <https://www.python.org/downloads/>`__ version 3.11 or newer.

  .. admonition:: The right Python command

     Python 2 and Python 3 often co-exist on the same system. Whether the ``python`` command refers to Python 2 or 3
     depends on operating system and configuration. Under some circumstances ``python3`` or ``pip3`` will need to be
     used in place of ``python`` or ``pip`` to target the correct version. You can verify your version by running
     ``python -VV``. You can also refer to the Python executable with a full path.

You also likely need the following for interacting with the source code repository:

* `Git <https://git-scm.com/>`_

There are two ways to install ARMI: with `uv <https://docs.astral.sh/uv/>`_ (described next), or with the standard
``venv`` and ``pip`` tools (described after that). uv is faster and creates and manages the virtual environment for you.

Installing with uv
==================
First, `install uv <https://docs.astral.sh/uv/getting-started/installation/>`_. uv can download a suitable Python
interpreter for you, so a separate Python installation is not strictly required.

To use ARMI as a dependency of your own project, add it from within that project::

    $ uv add git+https://github.com/partofthething/armi

To work on the ARMI source code, clone the repository and sync a development environment that includes the test
dependencies::

    $ git clone https://github.com/partofthething/armi
    $ cd armi
    $ uv sync --extra test

This creates a virtual environment in ``.venv`` and installs ARMI into it in editable mode. Other optional dependency
groups can be added with more ``--extra`` flags (e.g. ``--extra mpi --extra memprof``), or all of them with
``--all-extras``. Run commands inside the environment with ``uv run``::

    $ uv run armi --help
    $ uv run pytest -n 4 armi

Alternatively, activate the environment with ``source .venv/bin/activate`` (Linux/macOS) or
``.venv\Scripts\activate`` (Windows) and run commands directly.

.. note:: ARMI is a library, so this repository does not commit a ``uv.lock`` file. ``uv sync`` still writes one
    locally (it is git-ignored); run ``uv lock --upgrade`` to pick up newer versions of the dependencies.

If you will be running ARMI in parallel over MPI, the ``mpi`` extra requires some MPI development libraries
(e.g. ``sudo apt install libopenmpi-dev`` on Linux).

Installing with pip
===================

Preparing a Virtual Environment
-------------------------------
While not *technically* required, we highly recommend installing ARMI into a
`virtual environment <https://docs.python.org/3/library/venv.html>`_  to assist in dependency management. In short,
virtual environments are a mechanism by which a Python user can maintain separate sets of Python packages for various
applications on the same machine. This prevents dependencies from various tools conflicting with one another. ARMI has a
lot of requirements and may conflict with other libraries on your system unless you do this step.

Start a terminal and navigate to the directory you'd like to install ARMI into. To create a new virtual environment, use
a command like::

    $ python -m venv armi-venv

The result is a folder named ``armi-venv``, which contains a minimal set of Python packages, and a set of scripts for
activating and deactivating that environment. To activate the environment, invoke the appropriate script. On Windows::

    $ armi-venv\Scripts\activate.bat

Or on Linux::

    $ source armi-venv/bin/activate

.. note:: You'll have to activate the venv every time you open a new command line.
	Many people set up scripts to activate this automatically.

If you will be running ARMI in parallel over MPI, you must also install the ``mpi4py`` Python library. On Linux, doing
so will require some MPI development libraries (e.g. ``sudo apt install libopenmpi-dev``).

Getting the code
----------------
Choose one of the following two installation methods depending on your needs.

Option 1: Install as a library
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
If you plan on running ARMI without viewing or modifying source code, you may install it with ``pip``, which will
automatically discover and install the dependencies. This is useful for quick evaluations or to use it as a dependency
in another project::

   	(armi-venv) $ pip install https://github.com/partofthething/armi/archive/main.zip


Option 2: Install as a repository (for developers)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
If you'd like to view or change the ARMI source code (common!), you need to clone the ARMI source and then install its
dependencies. Clone the ARMI source code from the git repository with::

   (armi-venv) $ git clone https://github.com/partofthething/armi

.. tip:: If you plan to contribute to ARMI (please do!), you may want to use
	SSH keys and use ``git clone git@github.com:partofthething/armi.git``.

Now install ARMI with all its dependencies::

    (armi-venv) $ cd armi
    (armi-venv) $ pip install -e ".[test]"

.. tip:: If you don't want to install ARMI into your venv, you will need to add the ARMI source
	location to your system's ``PYTHONPATH`` environment variable so that
	Python will be able to find the code when you import it from other directories.

	In Windows, click *Start* and type ``Edit Environmental Variable`` to adjust ``PYTHONPATH``.
	In Linux, add ``export PYTHONPATH=/path/to/armi/source`` in a  user profile script (like ``.bashrc``).


Verifying installation
======================
Check the installation status by running::

    (armi-venv) $ armi

or, equivalently (prefix either with ``uv run`` if you installed with uv and have not activated the environment)::

    (armi-venv) $ python -m armi

If it worked, you should see the ARMI splash screen and no errors::

                       ---------------------------------------------------
                      |             _      ____     __  __    ___         |
                      |            / \    |  _ \   |  \/  |  |_ _|        |
                      |           / _ \   | |_) |  | |\/| |   | |         |
                      |          / ___ \  |  _ <   | |  | |   | |         |
                      |         /_/   \_\ |_| \_\  |_|  |_|  |___|        |
                      |         Advanced  Reactor  Modeling Interface     |
                       ---------------------------------------------------


If it works, congrats! So far so good.


Optional Setup
==============
This subsection provides setup for optional items.

GUI input
---------
To use the :py:mod:`graphical core-map editor <armi.utils.gridEditor>` you will need to also install
`wxPython <https://wxpython.org/pages/downloads/index.html>`_. This is not installed by default during armi installation
because it can cause installation complexities on some platforms.

In any case, all GUI dependencies can be installed by::

    (armi-venv) $ pip install armi[grids]

GUI output
----------
ARMI can write VTK and XDMF output files which can be viewed in tools such as `ParaView <https://www.paraview.org/>`_
and `VisIT <https://wci.llnl.gov/simulation/computer-codes/visit>`_. Download and install those tools from their
websites.
