****************
Included plugins
****************

Most of what ARMI does comes from :doc:`plugins </developer/making_armi_based_apps>`, and a default ARMI application
already registers several. They supply the reactor data model, the database and reports, the command line, fuel
management, fuel cycle economics and the parameters and settings the physics plugins share. Applications add their
own plugins next to these, usually the physics codes, and can drop or replace any of them.

This page says what each included plugin does and how to turn on the parts that are off by default.

.. list-table::
   :header-rows: 1
   :widths: 24 76

   * - Plugin
     - What it provides
   * - Reactor
     - The reactor data model (:py:mod:`armi.reactor`): the built-in block and assembly types, the systems a
       reactor is built from, and axial expansion.
   * - Bookkeeping
     - The database, history tracking, reports, memory profiling and snapshots (:py:mod:`armi.bookkeeping`), and the
       ``extract-inputs``, ``inject-inputs`` and ``vis-file`` entry points that work with databases.
   * - Entry points
     - The command line verbs: ``run`` and ``run-suite``, ``gui`` (the :doc:`input editor <input_editor>`), ``clone``,
       ``compare``, ``migrate-inputs``, ``modify``, ``report`` and ``clean-temps`` (:py:mod:`armi.cli`).
   * - Fuel handler
     - Fuel management (:py:mod:`armi.physics.fuelCycle`): moves, discharges and loads fuel between cycles with
       the fuel handler named by ``fuelHandlerName`` from the ``shuffleLogic`` module.
   * - Economics
     - Fuel cycle cost: the initial core, every reload and spent fuel, levelized against the electricity the plant
       makes, with an optional PDF report (:py:mod:`armi.physics.economics`). Off by default; see below.
   * - Neutronics
     - Cross section group management, the fission product model and the neutronics settings and parameters that
       physics plugins share (:py:mod:`armi.physics.neutronics`), and the ``diff-isotxs`` entry point. The flux solvers
       themselves come from other plugins.
   * - Fuel performance
     - The settings and block parameters fuel performance plugins share (:py:mod:`armi.physics.fuelPerformance`),
       such as fuel pin temperatures and gas pressure.
   * - Thermal hydraulics
     - The block parameters thermal hydraulics plugins write, such as coolant temperatures and density
       (:py:mod:`armi.physics.thermalHydraulics`).
   * - Safety
     - A home for safety-related code (:py:mod:`armi.physics.safety`). It defines nothing yet.

The :doc:`settings_report` lists every setting these plugins define, and the :doc:`params_report` every parameter.


Fuel cycle economics
====================
The economics plugin answers what a reactor's fuel will cost and what that adds to the price of its electricity.
It costs every step from the mine to spent fuel, for the initial core and every reload, and levelizes the lot
against the plant's generation.

.. figure:: /.static/economicsFuelCycleReport.png
   :width: 100%
   :alt: The first two pages of the fuel cycle cost report for the ARMI test reactor

   The first two of the report's four pages, for the ARMI test reactor at 1,000 MWt with quarter-core reloads
   (illustrative inputs).

Turning it on
-------------
It is off by default. Turn it on in the case settings::

    settings:
      econFuelCost: true
      econFuelCostReport: true   # also write <caseTitle>-fuel-cost.pdf

The plugin reads the fuel straight off the reactor model, so there is nothing else to describe: every
heavy-metal component is grouped by fuel form and enrichment, and masses are scaled to the full core when the
model is a symmetric part of it. The fuel form, which sets the fabrication price, is worked out from the
materials (UO\ :sub:`2`, U-metal or TRISO) unless ``econFuelForm`` names one.

What it computes
----------------
For the initial core, each front-end step with its quantity and cost:

* **Natural uranium** bought as U\ :sub:`3`\ O\ :sub:`8` (lb and tU), including conversion losses.
* **Conversion** to UF\ :sub:`6` (tU).
* **Enrichment**: separative work (kg-SWU) and the depleted tails it leaves, at the tails assay given or the one
  that makes the product cheapest at these prices (``econOptimizeTails``).
* **Fabrication** (kgHM), priced by fuel form.

Over the plant's life:

* **Reloads.** By default (``econReloads: auto``), a run with a fuel handler and more than one cycle costs the
  fresh assemblies the fuel management actually loads at the start of each cycle, and reports at the end of the
  run. Otherwise each later cycle is projected to buy ``econReloadFraction`` of the initial core: 1 for whole-core
  refueling, about 1/n for an n-batch core.
* **Spent fuel**, as a fee on every MWh generated.
* **Timing.** Each front-end step is paid its lead time before the fuel it makes is loaded, and electricity is
  credited at each cycle's midpoint.
* **The levelized fuel cycle cost** in $/MWh, discounted at ``econDiscountRate``, alongside the undiscounted
  cost, the cost of each cycle against its own electricity, and the sensitivity of the levelized cost to every
  price, the discount rate and the capacity factor.
* **Fuel utilization:** discharge burnup, specific power, natural uranium and separative work per GWh, and energy
  per kilogram of natural uranium.

The results go to the run log, to the core parameters ``fuelCostInitialCore`` and ``fuelCostLcoe``, and, with
``econFuelCostReport``, to a four-page PDF:

1. **Summary:** the headline numbers, the initial core traced from the mine to spent fuel, where each $/MWh goes,
   every step's cost and computed key points.
2. **Reloads and fuel utilization:** the utilization metrics, the fuel bought for each load and each cycle's fuel
   against its electricity.
3. **Cash flow and sensitivity:** cumulative spend as paid, what moves the cost, and the step from undiscounted to
   levelized cost.
4. **Basis and method:** the initial core by fuel group, every price and assumption, and the equations.

Prices and assumptions
----------------------
The defaults are rough mid-2020s values in US dollars, good for a first estimate and meant to be replaced with
your own.

.. list-table::
   :header-rows: 1
   :widths: 34 16 50

   * - Setting
     - Default
     - Meaning
   * - ``econOrePrice``
     - 80
     - Natural uranium, $/lb U\ :sub:`3`\ O\ :sub:`8`
   * - ``econConversionCost``, ``econConversionLoss``
     - 60, 0.005
     - Conversion to UF\ :sub:`6`, $/kgU, and the fraction of uranium lost doing it
   * - ``econSwuCost``
     - 190
     - Enrichment, $/kg-SWU
   * - ``econFeedAssay``, ``econTailsAssay``
     - 0.00711, 0.0025
     - U-235 fraction of the enrichment feed and tails
   * - ``econFabCostUO2``, ``econFabCostUMetal``, ``econFabCostTRISO``
     - 400, 1500, 10000
     - Fabrication, $/kgHM, for each fuel form
   * - ``econBackEndCost``
     - 1.0
     - Spent fuel storage and disposal, $/MWh generated
   * - ``econLeadTimeOre``, ``…Conversion``, ``…Enrichment``, ``…Fabrication``
     - 2, 1.5, 1, 0.5
     - Years before loading that each step is paid
   * - ``econThermalEfficiency``
     - 0.33
     - Net electric over thermal power
   * - ``econDiscountRate``
     - 0.07
     - Real annual discount rate

The plant's power, cycle lengths and capacity factors come from the usual case settings (``power``,
``cycleLength`` or ``cycleLengths``, ``availabilityFactor`` and ``powerFractions``).

Using it from Python
--------------------
The same calculation works on any reactor model, for studies that compare designs or prices without a run:

.. code-block:: python

    from armi.physics.economics import economicsInterface, report

    cost = economicsInterface.costCore(o.r.core, o.cs)
    print(economicsInterface.summarize(cost))
    print(f"{cost.lcoe:.2f} $/MWh")
    report.writeFuelCostReport(cost, "fuel-cost.pdf", title="My design")

    # re-price without rebuilding anything
    cheaper = cost.repriced(cost.unitCosts.scaled("enrichment", 0.8))
