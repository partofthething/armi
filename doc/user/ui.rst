***********
The ARMI UI
***********

ARMI comes with a user interface that runs in your browser. It shows and edits a case's inputs: see the core you have
described, paint assemblies onto the map, draw block cross sections to scale, stack assemblies and change settings,
all without reading YAML. It runs the case. And it shows what the run wrote: any parameter across the core and through
time, down to individual pins, and what the fuel cost. It is the fastest way to get a feel for an ARMI model, to check
that the inputs say what you meant, and to see what a run did.

.. figure:: /.static/inputEditor-core.png
   :width: 100%
   :alt: The core map view of the ARMI UI

   The core map of the ANL-AFCI-177 test reactor, a one-third-symmetric hexagonal core, with the palette of
   assembly designs on the left and their counts on the right.

For inputs, the UI is a front end to the same text files described in :doc:`inputs`. Anything it writes reads like
hand-written input, and anything written by hand opens in it, so you can move between the UI and a text editor
freely. ARMI itself neither knows nor cares which one you used. For results, it reads the database a run writes
(:ref:`results-view`).


Starting it
===========
Open a case settings file, which brings its blueprints along (and its results, once it has run), a blueprints file on
its own, or a run's database::

    $ python -m armi gui my-case.yaml
    $ python -m armi gui my-case.h5

Applications built on ARMI get the same command, e.g. ``myapp gui my-case.yaml``, and their own materials,
settings and plugins appear in the UI without any extra wiring.

The server is part of ARMI and uses only the Python standard library, and the page is plain JavaScript, so there
is nothing else to install. It binds to your machine's loopback interface and holds one case at a time; nothing
is sent anywhere else. A few options cover other setups:

``--no-browser``
    Print the address instead of opening a browser. On a remote machine, forward the port over SSH (for example
    ``ssh -L 8000:localhost:8000 cluster``, with ``--port 8000``) and open the address locally.
``--port``
    Serve on a particular port instead of any free one.
``--host``
    Serve on another interface, e.g. ``0.0.0.0`` to reach the UI from another machine. The address carries a
    session token, so only someone with the full link can use it.


Inputs
======
The rail on the left switches views; the first ones are for the inputs, and the last two for results. Each view keeps
its selection in the address bar, so a reload comes back to the same place and a link points at the thing you were
looking at. Undo and Redo cover every input change, and the UI marks the case as unsaved until you press Save.

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
   :alt: The pin lattice view of the ARMI UI

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
   :alt: The block view of the ARMI UI

   The C5G7 MOX block drawn from its component definitions, with the area of each component and its share of the
   block.

The cross section is drawn to scale from the component definitions, at cold input dimensions, and redraws as you
type, even while a block is too incomplete to build. Next to it is the area of every component and its share of
the block. A wrong radius, pitch or multiplicity is usually obvious here long before it would show up in a run.
Click a component in the drawing or the table to edit it. Ctrl-wheel (or pinch) zooms in on the point under
the cursor, so a clad or a gap a fraction of a millimetre thick can be checked; drag to pan, and double-click or
*Fit* to see the whole block again.

Assemblies
----------
.. figure:: /.static/inputEditor-assembly.png
   :width: 100%
   :alt: The assembly view of the ARMI UI

   An ANL-AFCI-177 inner fuel assembly: its axial stack, and the heights, cross section types, mesh and enrichment
   of each position.

An assembly is a set of parallel lists: blocks, heights, cross section types, mesh points and material
modifications, all read by position. The UI never lets them get out of step. Positions are inserted above or
below, dragged to reorder and deleted everywhere at once, and modifications such as enrichment by elevation are
shown as a column you can read at a glance.

Parts, Nuclides and File
------------------------
.. figure:: /.static/inputEditor-nuclides.png
   :width: 100%
   :alt: The nuclide flags view of the ARMI UI

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
   :alt: The settings view of the ARMI UI

   The ANL-AFCI-177 case settings, filtered to the twelve that differ from their defaults.

A case has a few hundred settings and you usually came to change a couple of them. Settings are grouped by the
plugin that defines them, filtered by default to the ones that differ from their defaults, and searchable by name
and description. Every setting shows its own description. Saving writes only the settings that differ from their
defaults, which keeps a settings file short enough to read.

Running the case
----------------
**Run** (or Ctrl+Enter) runs the open case, exactly as ``python -m <app> run case.yaml`` would from a terminal:
the application the UI was started with runs it, so ``python -m myapp gui`` runs it with ``myapp``'s plugins.
A run reads the files on disk, so any unsaved blueprints or settings are saved first. It runs in its own process in
the settings file's folder, where its outputs land, and its screen output goes to ``<case>.gui-run.log`` beside them.

A console docked under the workspace follows the log as it grows, with how long the run has taken and a Stop button
that ends the run and anything it started. Hide the console and the run carries on; shift-click Run to bring it
back, which also works after reloading the page. One run at a time.


.. _results-view:

Results
=======
.. figure:: /.static/inputEditor-results.png
   :width: 100%
   :alt: The results view of the ARMI UI

   Power density in a 313-assembly core, 40 years into a 60-year history, with one assembly followed through its
   shuffles in the chart on the right. The values come from :py:mod:`armi.webui.syntheticResults`, which makes up a
   database of this size to test against; they are plausible, not physical.

The UI also reads the database a run writes. Open a ``.h5`` file directly, or a case settings file whose run has
written ``<caseTitle>.h5`` beside it, and the *Results* view shows the core coloured by any block, assembly or
component parameter, number densities included::

    $ python -m armi gui my-case.h5

Pick a parameter on the left. The slider along the bottom moves through time (or use the arrow keys, and Space to
play); the one on the right moves the cut. *XY* is a plan view at one height, and *XZ* and *YZ* are sections through a
row or column of assemblies, with a small plan in the corner showing where the cut is. Values are printed on every
cell that has room for them, at the size and number of significant figures set in the toolbar. Colour maps, a log
scale and the range (the whole history, this time node only, or fixed limits) are there too. A partial core can be
unfolded to the full core with *Full core*.

.. figure:: /.static/inputEditor-results-components.png
   :width: 100%
   :alt: Component temperatures in the results view

   Zoomed in on component ``temperatureInC``: pins, bond, clad, ducts and coolant each coloured by their own
   temperature, with every block's components listed. The selected block's fuel temperature is charted over the
   whole history on the right.

Scroll to zoom and drag to pan; double-click fits the core again. Zoom in far enough and blocks open up into their
pins, ducts and coolant, drawn from the component dimensions and pin positions stored in the database. With a
component parameter chosen, each component is coloured by its own value, and once the pins are large enough each one
carries its value. Zoomed out, a component parameter shows each block's largest (or mean, or smallest) component value.

Click a block to select it. The inspector then charts its value over the whole history, and lists every parameter
of that block at the current time node; click one to show it. The chart can follow the assembly wherever fuel
management moves it, or stay at a core position and show whatever passes through.

.. figure:: /.static/inputEditor-results-section.png
   :width: 100%
   :alt: A burnup section in the results view

   An XZ section through the core centre, coloured by burnup. The synthetic history recharges the most-burnt fuel
   fresh every cycle, so the selected assembly's burnup climbs through each residence and resets, a sawtooth over
   sixty years.

A component parameter travels one time node at a time, so as soon as one is chosen the rest of its history is
fetched in the background, nearest the current node first (the footer counts it down). After that, scrubbing never
waits for the server.

The viewer never rebuilds a reactor. It reads the parameter datasets straight out of the file, sends a parameter's
whole history to the browser in one go, and draws on a canvas, so moving through sixty years of a full core takes a
millisecond or two per frame. To try it on a large case without running one, make a synthetic database::

    $ python -m armi.webui.syntheticResults big.h5 --years 60 --nodes-per-year 4


Fuel cycle cost
---------------
.. figure:: /.static/inputEditor-fuelcost.png
   :width: 100%
   :alt: The fuel cycle cost view

   The fuel cycle cost of the same synthetic 60-year history: every reload the run's fuel management loaded, weighed
   from the database and priced at the case's settings.

*Fuel $* turns the open database into the economics plugin's fuel cycle cost report: the initial core from the mine to
spent fuel, where each $/MWh goes, the fuel bought for every load, cycle by cycle costs, cumulative spend, what moves
the cost, and the basis behind it. It works whether or not the plugin was on during the run.

The fuel is weighed from what the run stored (each component's number densities and volume), exactly as the plugin
weighs a live core, and the reloads are the assemblies that first appear in the core at the start of a cycle with no
burnup, as the plugin finds them during a run. Everything after that is the plugin's own costing.

The prices, enrichment plant assumptions, efficiency, discount rate, lead times and where the reloads come from are on
the right, set from the case. Change one and the whole page follows; nothing is written to the case, and the reset
arrows put a value back. *Download PDF report* writes the plugin's four-page PDF at whatever prices are showing.


Teaching the UI about your settings
===================================
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
