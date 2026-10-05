# Contributing

Contributions to this fork of ARMI are welcome. For a walkthrough of the contribution process and the project's coding
standards, see `doc/developer/first_time_contributors.rst` and `doc/developer/standards_and_practices.rst`.

## Licensing of contributions

This project is licensed under the [Apache License 2.0](LICENSE.md). Per Section 5 of that license, any contribution
you intentionally submit for inclusion in this project is licensed under the same Apache 2.0 terms, without any
additional terms or conditions. There is no separate Contributor License Agreement to sign.

By submitting a contribution, you confirm that you have the right to submit it under the Apache License 2.0. If your
contribution includes third-party material, keep its original copyright and license notices and note it in your pull
request description.

## Use of LLMs

Judicious use of LLMs and AI coding assistants is welcome for any part of a contribution, including tests. You are
responsible for what you submit, however it was produced. See the "Use of Large Language Models (LLMs)" section of
`doc/developer/standards_and_practices.rst` for details.

## Before submitting a pull request

1. Format and lint your code with `ruff format .` and `ruff check .`.
2. Run the test suite with `pytest -n 4 armi`.
3. Write a pull request description that explains what changed and why.
