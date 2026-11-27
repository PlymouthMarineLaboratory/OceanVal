OSTIA temperature recipe
========================

Dataset: `Met Office OSTIA reprocessed, Copernicus Marine <https://doi.org/10.48670/moi-00168>`_
(product ``SST_GLO_SST_L4_REP_OBSERVATIONS_010_011``).

The Met Office's OSTIA foundation sea surface temperature, from satellites and in situ
data without gaps, on a 0.05 degree (about 5 km) grid from 1982. It is daily: Copernicus
Marine cuts out the model's area a month at a time, and OceanVal averages each month's
days into a monthly mean, in degC, before the daily fields are deleted. It comes at the
full resolution, so it suits regional models: validate a global model against ``oisst``
or ``cobe2`` instead. The app offers it only for a model it identified as the northwest
European shelf (others see it under "Show hidden datasets"). You must be logged in to the
Copernicus Marine store.

It needs Copernicus Marine's toolbox, which OceanVal does not install, and a free
`Copernicus Marine account <https://data.marine.copernicus.eu/register>`_:

.. code-block:: sh

   conda install -c conda-forge copernicusmarine   # or: pip install copernicusmarine
   copernicusmarine login                           # once: saves the login in ~/.copernicusmarine

or set ``COPERNICUSMARINE_SERVICE_USERNAME`` and ``COPERNICUSMARINE_SERVICE_PASSWORD``.
OceanVal uses that login and never asks for or keeps your password. Without the toolbox
or a login the OSTIA matchup fails, saying which is missing, and the other matchups
carry on.

.. code-block:: python

   import oceanval

   oceanval.add_gridded_comparison(
       name="temperature",
       model_variable="thetao",
       recipe={"temperature": "ostia"},
       climatology=False,
   )

``model_variable`` is the variable name in the model NetCDF output.
Set ``climatology=True`` for a climatological comparison, or
``climatology=False`` to compare all available years. ``name`` is the short
name OceanVal uses in reports.
