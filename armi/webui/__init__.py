"""
A browser-based editor for ARMI inputs, and a viewer for the results of running them.

This serves a small single-page app from a local HTTP server so that blueprints and settings can be
edited visually: core maps, pin lattices, block cross sections, assembly axial stacks, nuclide flags
and custom isotopics, all against the real :py:mod:`armi.reactor.blueprints` object model. It also
shows what a run wrote to its database, through time. Every application gets it as the ``gui``
entry point::

    $ python -m armi gui my-case.yaml
    $ python -m armi gui my-case.h5

Nothing leaves the machine. The server binds to loopback, holds one document at a time, and writes
only where you tell it to. The server is standard library and the front end is plain ES modules
with no build step, so there is nothing to compile.

The pieces
----------
:py:mod:`armi.webui.document`
    Blueprints in, JSON out, and back again. Grids get a normalized cell dictionary so the browser
    never has to understand ascii maps.
:py:mod:`armi.webui.preview`
    Turns a block definition into flat shapes a browser can draw, at cold input dimensions.
:py:mod:`armi.webui.catalog`
    What the editor offers as choices: materials, component shapes, flags, symmetries.
:py:mod:`armi.webui.settingsdoc`
    The same treatment for a settings file.
:py:mod:`armi.webui.settingseditors`
    How a plugin says its own settings should be edited.
:py:mod:`armi.webui.results`
    Reads a results database straight from HDF5, fast enough to scrub through decades of it.
:py:mod:`armi.webui.syntheticResults`
    Writes a made-up database of any size, for trying the results view on a big case.
:py:mod:`armi.webui.server`
    Routing, static files, and the loopback server itself.
"""
