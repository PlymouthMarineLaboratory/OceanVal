Installation and a quick example
============

**System requirements**: OceanVal runs on Linux and macOS with Python 3.10-3.13, and needs jupyter-book 2 or later.

The recommended way to install OceanVal is via conda, which handles the
scientific dependencies (including CDO and R) for you. If you do not have
conda installed, follow the
`conda installation guide <https://docs.conda.io/projects/conda/en/latest/user-guide/install/index.html>`_.

Install the released package from conda-forge:

.. code-block:: console

   conda install -c conda-forge oceanval

To keep things clean, install into a dedicated environment:

.. code-block:: console

   conda create -n oceanval -c conda-forge oceanval
   conda activate oceanval

Development version
-------------------

To use the latest development version, clone the repository and follow the
instructions in the
`README <https://github.com/PlymouthMarineLaboratory/oceanval#installation>`_.


A two-minute example
--------------------

The example below downloads one year of CMIP6 sea surface temperature output
and validates it against the COBE2 observational dataset. It takes a couple
of minutes to run and produces `a report like this one
<https://plymouthmarinelaboratory.github.io/oceanval_example/index.html>`_.

Run it from an empty directory, in a Python script or Jupyter notebook:


.. code:: ipython3

   import os
   import oceanval

   url = "http://noresg.nird.sigma2.no/thredds/fileServer/esg_dataroot/cmor/CMIP6/CMIP/NCC/NorESM2-LM/historical/r3i1p1f1/Omon/tos/gn/v20190920/tos_Omon_NorESM2-LM_historical_r3i1p1f1_gn_201001-201412.nc"

   # download this file

   out = os.path.basename(url)

   os.system(f"wget {url} -O {out}")

   oceanval.add_gridded_comparison(
      recipe = {"temperature":"cobe2"},
       model_variable = "tos"
   )

   oceanval.matchup(sim_dir = ".",
                  start = 2014, end = 2014,
                  n_dirs_down = 0,
                  cores = 1,
                  lon_lim = [-180, 180], lat_lim = [-90, 90],
                  ask = False
                  )
   
   oceanval.validate(concise = False, region = "global")



When it finishes, an HTML report opens in your browser showing how the model
and observations compare. The built-in ``cobe2`` :doc:`recipe <recipes>`
handles downloading the observational data automatically.

.. note::

   This is a demonstration of the workflow, not a rigorous way to validate a
   climate model. For a real validation you would use more years of output
   and more variables. Continue with the :doc:`quickstart`.
