"""
Zoom down to the pins
=====================

Zoom in far enough and blocks open up into their pins, bond, clad, ducts and coolant, drawn from the dimensions
and pin positions stored in the database. With a component parameter such as ``temperatureInC`` each one is coloured
by its own value, and the selected block's fuel temperature is charted over the whole history. (Synthetic values.)

.. image:: /.static/inputEditor-results-components.png
   :width: 100%
   :alt: Component temperatures, pin by pin

Start it with::

    $ python -m armi gui my-case.h5

More in :ref:`results-view`.
"""

# This page is a screenshot of the ARMI UI rather than a plot made when the docs are built, so there is nothing to run.
# sphinx_gallery_thumbnail_path = '.static/inputEditor-results-components.png'
