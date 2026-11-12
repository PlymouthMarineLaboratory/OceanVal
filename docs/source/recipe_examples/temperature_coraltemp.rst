CoralTemp temperature recipe
============================

Dataset: `NOAA Coral Reef Watch CoralTemp v3.1 <https://coralreefwatch.noaa.gov/product/5km/index_5km_sst.php>`_.

Monthly satellite sea surface temperature on a 0.05 degree (about 5 km) grid from 1985,
without gaps. NOAA CoastWatch's ERDDAP server cuts out the model's area and years
before sending them, a year at a time, at the full resolution, so it suits regional
models. A year of the whole globe is over a gigabyte: validate a global model against
``oisst`` or ``cobe2`` instead.

.. code-block:: python

   import oceanval

   oceanval.add_gridded_comparison(
       name="temperature",
       model_variable="thetao",
       recipe={"temperature": "coraltemp"},
       climatology=False,
   )

``model_variable`` is the variable name in the model NetCDF output.
Set ``climatology=True`` for a climatological comparison, or
``climatology=False`` to compare all available years. ``name`` is the short
name OceanVal uses in reports.
