# Notes for AI coding assistants

## Copyright and license headers

- Do not add a copyright or license header to new files. The repository-wide `LICENSE.md` covers them.
- Never remove or edit an existing copyright or license notice in a file that came from upstream ARMI (for example
  `# Copyright 2019 TerraPower, LLC`). Apache 2.0 Section 4(c) requires keeping them.
- If a new file copies a substantial amount of code from an existing file, keep that file's notice in the new one.
- When making substantial changes to a file with an existing copyright notice, add a line for the committer directly
  below the existing ones, using the name from `git config user.name` and the current year, for example
  `# Copyright 2026 Jane Doe`. Leave the existing lines unchanged, and skip this for minor edits or if the committer
  already has a line.
