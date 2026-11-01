****************
The input editor
****************

ARMI comes with a browser-based editor for its inputs. Point it at a case and you can see the core you have
described, paint assemblies onto the map, draw block cross sections to scale, stack assemblies, and change
settings, all without reading YAML. It is the fastest way to get a feel for an ARMI model, and the easiest way to
check that the inputs say what you meant.

.. figure:: /.static/inputEditor-core.png
   :width: 100%
   :alt: The core map view of the input editor

   The core map of the ANL-AFCI-177 test reactor, a one-third-symmetric hexagonal core, with the palette of
   assembly designs on the left and their counts on the right.

The editor is a front end to the same text files described in :doc:`inputs`. Anything it writes reads like
hand-written input, and anything written by hand opens in it, so you can move between the editor and a text
editor freely. ARMI itself neither knows nor cares which one you used.


Starting it
===========
Open a case settings file, which brings its blueprints along, or a blueprints file on its own::

    $ python -m armi gui my-case.yaml

Applications built on ARMI get the same command, e.g. ``myapp gui my-case.yaml``, and their own materials,
settings and plugins appear in the editor without any extra wiring.

The server is part of ARMI and uses only the Python standard library, and the page is plain JavaScript, so there
is nothing else to install. It binds to your machine's loopback interface and holds one case at a time; nothing
is sent anywhere else. A few options cover other setups:

``--no-browser``
    Print the address instead of opening a browser. On a remote machine, forward the port over SSH (for example
    ``ssh -L 8000:localhost:8000 cluster``, with ``--port 8000``) and open the address locally.
``--port``
    Serve on a particular port instead of any free one.
``--host``
    Serve on another interface, e.g. ``0.0.0.0`` to reach the editor from another machine. The address carries a
    session token, so only someone with the full link can use it.


A tour of the views
===================
The rail on the left switches views. Each one keeps its selection in the address bar, so a reload comes back to
the same place and a link points at the thing you were looking at. Undo and Redo cover every change, and the
editor marks the case as unsaved until you press Save.

Core
----
The core map, shown above. Pick an assembly design from the palette and click or drag to place it; Erase,
Pick and Inspect do what they say. Right-click a position for ring actions, alt-drag to pan and ctrl-scroll to
zoom. *Expand to full* turns a symmetric partial core into a full one. The panel on the right sets the grid's
geometry, symmetry and pitch, and keeps a running count of every assembly type.

Lattices
--------
.. figure:: /.static/inputEditor-lattice.png
   :width: 100%
   :alt: The pin lattice view of the input editor

   The MOX assembly lattice of the C5G7 benchmark: low, medium and high enrichment MOX pins, guide tubes and a
   central fission chamber.

Pin maps and every other grid in the file are edited position by position the same way. A lattice ID is only a
label: what a position holds is whatever components claim that label, in every block drawn on the lattice. The
inspector follows that chain for you. Pick a position to see which components claim it, what they are made of
and which blocks use the lattice.

Blocks
------
.. figure:: /.static/inputEditor-block.png
   :width: 100%
   :alt: The block view of the input editor

   The C5G7 MOX block drawn from its component definitions, with the area of each component and its share of the
   block.

The cross section is drawn to scale from the component definitions, at cold input dimensions, and redraws as you
type, even while a block is too incomplete to build. Next to it is the area of every component and its share of
the block. A wrong radius, pitch or multiplicity is usually obvious here long before it would show up in a run.
Click a component in the drawing or the table to edit it.

Assemblies
----------
.. figure:: /.static/inputEditor-assembly.png
   :width: 100%
   :alt: The assembly view of the input editor

   An ANL-AFCI-177 inner fuel assembly: its axial stack, and the heights, cross section types, mesh and enrichment
   of each position.

An assembly is a set of parallel lists: blocks, heights, cross section types, mesh points and material
modifications, all read by position. The editor never lets them get out of step. Positions are inserted above or
below, dragged to reorder and deleted everywhere at once, and modifications such as enrichment by elevation are
shown as a column you can read at a glance.

Parts, Nuclides and File
------------------------
.. figure:: /.static/inputEditor-nuclides.png
   :width: 100%
   :alt: The nuclide flags view of the input editor

   Nuclide flags for C5G7: which nuclides get cross sections, which deplete, and which elements expand into
   isotopes.

*Parts* edits the component definitions that are shared between blocks, and component groups. *Nuclides* holds
the nuclide flags, with filters for depleting, cross section and expanded nuclides, and the custom isotopics that
materials can refer to. *File* shows exactly what Save would write, so it doubles as a preview of a save and a
quick way to copy a section into another case.

Settings
--------
.. figure:: /.static/inputEditor-settings.png
   :width: 100%
   :alt: The settings view of the input editor

   The ANL-AFCI-177 case settings, filtered to the twelve that differ from their defaults.

A case has a few hundred settings and you usually came to change a couple of them. Settings are grouped by the
plugin that defines them, filtered by default to the ones that differ from their defaults, and searchable by name
and description. Every setting shows its own description. Saving writes only the settings that differ from their
defaults, which keeps a settings file short enough to read.


Teaching the editor about your settings
=======================================
Most settings need nothing more than their type, default and options to get a sensible control. A plugin with many
settings, or settings that only make sense together, can say more. Give the plugin class a
``defineSettingEditors(cs)`` static method returning, for each setting it wants to present, a description that may:

* ask for a better control: one choice from a list (each option with a line of help), several choices, a table with
  typed columns, or a file or folder path with a Browse button;
* put the setting in a section of the plugin's own, give that section a short description, and order the settings
  within it so the one that matters most comes first;
* show the setting only while another has a given value (or any of several), so options that belong to one method
  stay out of the way until that method is chosen;
* add a hint under the control.

The method gets the settings as they stand and is asked again after every change, so the controls can follow the
case: the banks a core actually has, or a note on what a chosen preset will really use.
:py:mod:`armi.webui.settingseditors` documents the vocabulary.

.. code-block:: python

    class MyPlugin(plugins.ArmiPlugin):
        @staticmethod
        def defineSettingEditors(cs):
            return {
                "mySolver": {
                    "widget": "choice",
                    "options": [
                        {"value": "fast", "help": "Minutes per case."},
                        {"value": "exact", "help": "Benchmark quality; hours."},
                    ],
                    "group": "My solver",
                    "groupHint": "How the solver trades speed for accuracy.",
                    "order": 1,
                },
                "myExactTolerance": {
                    "group": "My solver",
                    "order": 2,
                    "showWhen": {"setting": "mySolver", "equals": "exact"},
                },
                "myLibrary": {"widget": "path", "group": "My solver", "order": 3},
            }
