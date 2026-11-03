"""
Explore results through time
============================

Open a run's database and colour the core by any block, assembly or component parameter. A slider moves through
every time node, another through height or across the core for side-on sections, and values are printed on every
cell. Click a block to chart it through the whole history, following its assembly through every shuffle. Sixty years
of a 313-assembly core scrub at a millisecond or two a frame. (The values here are synthetic, from
``armi.webui.syntheticResults``.)

.. image:: /.static/inputEditor-results.png
   :width: 100%
   :alt: Power density across a full core 40 years into a 60-year history

Start it with::

    $ python -m armi gui my-case.h5

More in :ref:`results-view`.
"""

# This page is a screenshot of the ARMI UI rather than a plot made when the docs are built, so there is nothing to run.
# sphinx_gallery_thumbnail_path = '.static/inputEditor-results.png'
