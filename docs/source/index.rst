OceanVal: automated ocean model validation
===========================================

.. important::

   **This documentation is archived.** OceanVal's documentation has moved to
   `plymouthmarinelaboratory.github.io/OceanVal <https://plymouthmarinelaboratory.github.io/OceanVal/>`_ —
   please update your bookmarks. This Read the Docs site is no longer updated.

**Point OceanVal at your model output. Get back a complete validation report.**

OceanVal is a Python package that automates the slow and tedious parts of
ocean model validation. It matches your simulation output against gridded and
in-situ observations, computes validation statistics, and builds a polished,
shareable HTML report, all with a few lines of code.

.. code-block:: python

   import oceanval

   oceanval.add_gridded_comparison(
       name="temperature",
       model_variable="thetao",
       recipe={"temperature": "woa23"},
       start=2005,
       end=2014,
       climatology=True,
   )

   oceanval.matchup(sim_dir="/path/to/model/output", start=2005, end=2014)
   oceanval.validate()

See a `real example report <https://plymouthmarinelaboratory.github.io/oceanval_example/index.html>`_
built by OceanVal.

Why OceanVal?
-------------

* **Fast to first result**: install from conda-forge and validate a
  simulation the same day, not after weeks of scripting.
* **Built-in observation recipes**: WOA23, COBE2, OCC-CI, GLODAP, and NSBC
   datasets are ready to use, with data downloaded and processed for you.
  See :doc:`recipes`.
* **Rigorous statistics**: bias, RMSD, correlation, seasonality, spatial
  patterns, and vertical profiles, computed like-for-like on matched data.
* **Publication-quality reports**: an HTML report you can send to
  collaborators, with figures, tables, and methods documented automatically.
* **Works with your model**: NEMO, CMEMS, CMIP-style output, and any
  CF-compliant NetCDF files. FVCOM output is supported with ``matchup(fvcom=True)``.
* **Reproducible and hackable**: every report is generated from Jupyter
  notebooks you can inspect, edit, and rebuild with :func:`oceanval.rebuild`.
* **Compare simulations**: build side-by-side comparison reports for multiple
  runs with :func:`oceanval.compare`.

How it works
------------

.. code-block:: text

   1. REGISTER        2. MATCH               3. REPORT
   observations  -->  model + observations  -->  HTML validation report
   (built-in           (oceanval.matchup)        (oceanval.validate)
   recipes or
   your own data)

1. **Register** the observations to validate against, either with a
   built-in :doc:`recipe <recipes>` or your own gridded/in-situ data.
2. **Match** your model output with those observations using
   :func:`oceanval.matchup`. OceanVal finds your files, regrids, and pairs
   model values with observed values.
3. **Report** with :func:`oceanval.validate`, which computes the statistics
   and builds the HTML report in your working directory.

Start here
----------

.. csv-table::
   :header: "I want to...", "Go to"
   :widths: 55, 45

   "Install OceanVal", ":doc:`installing`"
   "Run my first validation", ":doc:`quickstart`"
   "Understand the full workflow", ":doc:`how_to_use`"
   "Use built-in observation datasets", ":doc:`recipes`"
   "Check my data is compatible", ":doc:`obs_data`"
   "Fix a problem", ":doc:`q_a`"
   "Look up a function", ":doc:`api`"

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: Getting Started

   installing
   quickstart
   how_to_use
   q_a

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Data handling

   obs_data
   recipes

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: Help & reference

   api
   info

About
-----

OceanVal is developed by the Marine Systems Modelling group at
`Plymouth Marine Laboratory <https://www.pml.ac.uk/>`_. It is open source and
`developed on GitHub <https://github.com/PlymouthMarineLaboratory/oceanVal>`_, where bug
reports and feature requests are welcome.
