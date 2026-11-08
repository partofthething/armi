*****************************
First Time Contributors Guide
*****************************

Contributions are welcome. ARMI is open source, so whatever you contribute will be too.

Help Wanted
===========

There are a lot of places you can get started to help the ARMI project and team:

* Better :ref:`armi-docing`
* Better test coverage
* Many more type annotations are desired. Type issues cause lots of bugs.
* Targeted speedups (e.g. informed by a profiler)
* Additional relevance to thermal reactors

Naturally, you can also look at the open `ARMI issues <https://github.com/partofthething/armi/issues>`_ to see what work needs to be done. In particular, check out the `help wanted tickets <https://github.com/partofthething/armi/issues?q=is%3Aopen+is%3Aissue+label%3A%22help+wanted%22>`_ and `good first issue tickets <https://github.com/partofthething/armi/issues?q=is%3Aopen+is%3Aissue+label%3A%22good+first+issue%22>`_.

Testing
=======

Contributions need to pass the unit tests, and new code should come with tests of its own.

The ARMI tests are meant to be run using `pytest <https://docs.pytest.org/en/8.0.x/>`_
locally ::

    $ pip install -e .[test]
    $ pytest -n 4 armi

Submitting Changes
==================

To submit a change to ARMI, you will have to open a Pull Request (PR) on GitHub.com.

The process for opening a PR against ARMI goes something like this:

1. `Fork the ARMI repo <https://docs.github.com/en/get-started/quickstart/fork-a-repo>`_
2. `Create a new branch <https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/creating-and-deleting-branches-within-your-repository>`_ in your repo
3. Make your code changes to your new branch
4. Submit a Pull Request against `ARMIs main branch <https://github.com/partofthething/armi/pull/new/main>`_
    a. See `GitHubs general guidance on Pull Requests <https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/creating-a-pull-request>`_
    b. See ARMIs specific guidance on what makes a "good" Pull Request: :ref:`armi-tooling`.
5. Actively engage with your PR reviewer's questions and comments.

> Note that by submitting a pull request you agree to license your contribution under the Apache License 2.0, as described in `CONTRIBUTING.md <https://github.com/partofthething/armi/blob/main/CONTRIBUTING.md>`_.

See :ref:`armi-stds` for the coding standards and :ref:`armi-tooling` for commit messages and PRs.

Licensing of Tools
==================

New dependencies need licenses compatible with ARMI's Apache 2.0 license; see :ref:`third-party-licensing`.
