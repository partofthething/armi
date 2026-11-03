"""
Cost the fuel cycle
===================

The economics plugin's fuel cycle cost report, from any run's database: the initial core from the mine to spent
fuel, every reload the run's fuel management loaded, cumulative spend, and what moves the levelized cost. Every price
and assumption can be changed on the page, and the PDF report downloaded at any of them. (Synthetic values.)

.. image:: /.static/inputEditor-fuelcost.png
   :width: 100%
   :alt: The fuel cycle cost of a run, worked out from its database

Start it with::

    $ python -m armi gui my-case.h5

More in :ref:`results-view`.
"""

# This page is a screenshot of the ARMI UI rather than a plot made when the docs are built, so there is nothing to run.
# sphinx_gallery_thumbnail_path = '.static/inputEditor-fuelcost.png'
