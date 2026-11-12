OISST temperature recipe
========================

Dataset: `NOAA OISST v2.1 <https://psl.noaa.gov/data/gridded/data.noaa.oisst.v2.highres.html>`_.

Monthly sea surface temperature on a 0.25 degree grid from September 1981, an optimum
interpolation of satellite (AVHRR) and in situ measurements without gaps. It is read
over NOAA PSL's THREDDS server, like COBE2; when that server turns requests away
(HTTP 429), OceanVal downloads the whole file instead.

.. code-block:: python

   import oceanval

   oceanval.add_gridded_comparison(
       name="temperature",
       model_variable="thetao",
       recipe={"temperature": "oisst"},
       climatology=False,
   )

``model_variable`` is the variable name in the model NetCDF output.
Set ``climatology=True`` for a climatological comparison, or
``climatology=False`` to compare all available years. ``name`` is the short
name OceanVal uses in reports.
