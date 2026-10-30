Data Recipes
============

OceanVal provides built-in recipes for many popular observational datasets.
Downloading and storing data can be annoying and tedious, and recipes take
care of it for you.

Recipes are available for gridded data. Use the ``recipe`` argument with
:func:`oceanval.add_gridded_comparison` and provide the model variable that
should be compared with the observation. There is also a point data recipe,
see `Point data recipes`_.

We can illustrate how they work with an example.  
The following call asks OceanVal to compare the model's ``thetao`` variable with the COBE2 temperature dataset. The recipe dictionary
``{"temperature": "cobe2"}`` tells OceanVal to use the COBE2 recipe for the temperature variable.

.. code-block:: text

   oceanval.add_gridded_comparison(
       name="temperature",                 # <--- report name
       model_variable="thetao",           # <--- model NetCDF variable
       recipe={"temperature": "cobe2"},    # <--- observation recipe
       climatology=True,                # <--- climatological comparison
   )

The parts of the call mean:

- ``name`` is the short name OceanVal uses in reports.
- ``model_variable`` is the variable name in the model NetCDF output.
- ``recipe`` is a dictionary that must contain one variable and source
  identifier. For example, ``{"temperature": "woa23"}`` selects temperature
  from WOA23.
- ``climatology`` is a boolean that sets whether to compare climatological
  means or all available years. Set ``climatology=True`` for a climatological
  comparison, or ``climatology=False`` to compare all available years.

How are recipes processed?
--------------------------

OceanVal will automatically download the observational data when the
:func:`oceanval.matchup` function is called. In most cases this happens via
THREDDS servers, which makes things efficient: OceanVal only downloads what
is needed. The server for COBE-SST 2 limits how many requests it answers
(HTTP 429), and when it turns them away OceanVal downloads the whole file
instead. Once the data is downloaded, the model and observations are
regridded to a common spatial grid, and model and observational data are
averaged per month and year, where appropriate.

What recipes are available?
---------------------------


Global datasets
~~~~~~~~~~~~~~~
.. csv-table:: Global built-in recipes
   :header: "Region", "Variable", "Recipe", "Dataset", "Water-column", "Units", "Example"
   :widths: 10, 14, 12, 28, 10, 14, 12

   "Global", "Alkalinity", "``glodap``", "`GLODAPv2.2016b <https://www.glodap.info/>`_", "No", "``micro-mol kg-1``", ":doc:`Full details <recipe_examples/alkalinity_glodap>`"
   "Global", "Chlorophyll", "``occci``", "`Ocean Colour CCI <https://esa-oceancolour-cci.org/>`_", "No", "``milligram m-3``", ":doc:`Full details <recipe_examples/chlorophyll_occci>`"
   "Global", "KD490", "``occci``", "`Ocean Colour CCI <https://esa-oceancolour-cci.org/>`_", "No", "``m-1``", ":doc:`Full details <recipe_examples/kd490_occci>`"
   "Global", "Nitrate", "``woa23``", "`World Ocean Atlas 2023 <https://www.ncei.noaa.gov/products/world-ocean-atlas>`_", "Yes", "``micromoles_per_kilogram``", ":doc:`Full details <recipe_examples/nitrate_woa23>`"
   "Global", "Oxygen", "``woa23``", "`World Ocean Atlas 2023 <https://www.ncei.noaa.gov/products/world-ocean-atlas>`_", "Yes", "``micromoles_per_kilogram``", ":doc:`Full details <recipe_examples/oxygen_woa23>`"
   "Global", "pH", "``glodap``", "`GLODAPv2.2016b <https://www.glodap.info/>`_", "No", "``total scale``", ":doc:`Full details <recipe_examples/ph_glodap>`"
   "Global", "Phosphate", "``woa23``", "`World Ocean Atlas 2023 <https://www.ncei.noaa.gov/products/world-ocean-atlas>`_", "Yes", "``micromoles_per_kilogram``", ":doc:`Full details <recipe_examples/phosphate_woa23>`"
   "Global", "Salinity", "``woa23``", "`World Ocean Atlas 2023 <https://www.ncei.noaa.gov/products/world-ocean-atlas>`_", "Yes", "``1``", ":doc:`Full details <recipe_examples/salinity_woa23>`"
   "Global", "Silicate", "``woa23``", "`World Ocean Atlas 2023 <https://www.ncei.noaa.gov/products/world-ocean-atlas>`_", "Yes", "``micromoles_per_kilogram``", ":doc:`Full details <recipe_examples/silicate_woa23>`"
   "Global", "Temperature", "``cobe2``", "`COBE-SST 2 <https://psl.noaa.gov/data/gridded/data.cobe2.html>`_", "No", "``degC``", ":doc:`Full details <recipe_examples/temperature_cobe2>`"
   "Global", "Temperature", "``woa23``", "`World Ocean Atlas 2023 <https://www.ncei.noaa.gov/products/world-ocean-atlas>`_", "Yes", "``degC``", ":doc:`Full details <recipe_examples/temperature_woa23>`"


.. toctree::
   :hidden:

   recipe_examples/temperature_cobe2
   recipe_examples/nitrate_woa23
   recipe_examples/phosphate_woa23
   recipe_examples/oxygen_woa23
   recipe_examples/silicate_woa23
   recipe_examples/temperature_woa23
   recipe_examples/salinity_woa23
   recipe_examples/chlorophyll_occci
   recipe_examples/kd490_occci
   recipe_examples/ph_glodap
   recipe_examples/alkalinity_glodap
   recipe_examples/oxygen_nsbc
   recipe_examples/ammonium_nsbc
   recipe_examples/chlorophyll_nsbc
   recipe_examples/nitrate_nsbc
   recipe_examples/phosphate_nsbc
   recipe_examples/salinity_nsbc
   recipe_examples/silicate_nsbc
   recipe_examples/temperature_nsbc

The recipe dictionary must contain one variable and source identifier. For
example, ``{"temperature": "woa23"}`` selects temperature from WOA23.



Northwest European Shelf datasets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``nsbc`` recipe provides North Sea Biogeochemical Climatology data for
chlorophyll, nitrate, phosphate, silicate, oxygen, temperature, and salinity.

.. csv-table:: Northwest European Shelf built-in recipes
   :header: "Region", "Variable", "Recipe", "Dataset", "Water-column", "Units", "Example"
   :widths: 20, 14, 12, 28, 10, 14, 12

   "Northwest European Shelf", "Ammonium", "``nsbc``", "North Sea Biogeochemical Climatology", "Yes", "``mmol/m^3``", ":doc:`Full details <recipe_examples/ammonium_nsbc>`"
   "Northwest European Shelf", "Chlorophyll", "``nsbc``", "North Sea Biogeochemical Climatology", "Yes", "``mg/m^3``", ":doc:`Full details <recipe_examples/chlorophyll_nsbc>`"
   "Northwest European Shelf", "Nitrate", "``nsbc``", "North Sea Biogeochemical Climatology", "Yes", "``mmol/m^3``", ":doc:`Full details <recipe_examples/nitrate_nsbc>`"
   "Northwest European Shelf", "Oxygen", "``nsbc``", "North Sea Biogeochemical Climatology", "Yes", "``mmol/m^3``", ":doc:`Full details <recipe_examples/oxygen_nsbc>`"
   "Northwest European Shelf", "Phosphate", "``nsbc``", "North Sea Biogeochemical Climatology", "Yes", "``mmol/m^3``", ":doc:`Full details <recipe_examples/phosphate_nsbc>`"
   "Northwest European Shelf", "Salinity", "``nsbc``", "North Sea Biogeochemical Climatology", "Yes", "``1``", ":doc:`Full details <recipe_examples/salinity_nsbc>`"
   "Northwest European Shelf", "Silicate", "``nsbc``", "North Sea Biogeochemical Climatology", "Yes", "``mmol/m^3``", ":doc:`Full details <recipe_examples/silicate_nsbc>`"
   "Northwest European Shelf", "Temperature", "``nsbc``", "North Sea Biogeochemical Climatology", "Yes", "``degC``", ":doc:`Full details <recipe_examples/temperature_nsbc>`"

Each Example link opens a separate page containing the corresponding call.

Point data recipes
~~~~~~~~~~~~~~~~~~

.. warning::

   Point recipes are on the ``main`` branch but not yet in a tagged
   release (see the `version history <https://plymouthmarinelaboratory.github.io/OceanVal/version-history.html>`_).
   Install from GitHub to use them: ``pip install git+https://github.com/PlymouthMarineLaboratory/oceanval.git``.

Point recipes are used with :func:`oceanval.add_point_comparison` in place of
``obs_path``. Instead of reading csv files from disk, OceanVal downloads the
observations when :func:`oceanval.matchup` runs, for the years being matched
and the ``lon_lim``/``lat_lim`` area given to ``matchup``. If no ``lon_lim`` and
``lat_lim`` are given, the model's own longitude/latitude extent is used.

.. code-block:: text

   oceanval.add_point_comparison(
       model_variable="votemper",         # <--- model NetCDF variable
       recipe={"temperature": "ices"},    # <--- observation recipe
       vertical=True,                     # <--- also validate below the surface
   )

.. csv-table:: Point data built-in recipes
   :header: "Region", "Variable", "Recipe", "Dataset", "Units", "Water-column"
   :widths: 18, 14, 12, 28, 10, 12

   "Northeast Atlantic", "Temperature", "``ices``", "`ICES Oceanographic database <https://ocean.ices.dk>`_, CTD profiles and bottle data", "°C", "Yes"
   "Northeast Atlantic", "Salinity", "``ices``", "`ICES Oceanographic database <https://ocean.ices.dk>`_, CTD profiles and bottle data", "dimensionless", "Yes"
   "Northeast Atlantic", "Total Alkalinity", "``ices``", "`ICES Oceanographic database <https://ocean.ices.dk>`_, bottle and low resolution CTD data", "mEq/l", "Yes"
   "Northeast Atlantic", "Ammonium", "``ices``", "`ICES Oceanographic database <https://ocean.ices.dk>`_, bottle and low resolution CTD data", "µmol/l", "Yes"
   "Northeast Atlantic", "Chlorophyll", "``ices``", "`ICES Oceanographic database <https://ocean.ices.dk>`_, bottle and low resolution CTD data", "µg/l", "Yes"
   "Northeast Atlantic", "Nitrate", "``ices``", "`ICES Oceanographic database <https://ocean.ices.dk>`_, bottle and low resolution CTD data", "µmol/l", "Yes"
   "Northeast Atlantic", "Oxygen", "``ices``", "`ICES Oceanographic database <https://ocean.ices.dk>`_, bottle and low resolution CTD data", "ml/l", "Yes"
   "Northeast Atlantic", "pH", "``ices``", "`ICES Oceanographic database <https://ocean.ices.dk>`_, bottle and low resolution CTD data", "pH units", "Yes"
   "Northeast Atlantic", "Phosphate", "``ices``", "`ICES Oceanographic database <https://ocean.ices.dk>`_, bottle and low resolution CTD data", "µmol/l", "Yes"
   "Northeast Atlantic", "Silicate", "``ices``", "`ICES Oceanographic database <https://ocean.ices.dk>`_, bottle and low resolution CTD data", "µmol/l", "Yes"

.. _ices-data:

How ICES data is treated
^^^^^^^^^^^^^^^^^^^^^^^^

**What is downloaded.** For each comparison, OceanVal asks ICES for an
export of every profile in the ``lon_lim``/``lat_lim`` area (or the model's own
extent) in the years where the simulation and the comparison's ``start`` and
``end`` overlap. Everything but temperature and salinity comes from the bottle
and low resolution CTD data. Temperature and salinity take two exports, one of
the high resolution CTD profiles and one of the bottle and low resolution CTD
data, which are joined (see *Profiles in both datasets* below). An export holds
every variable measured at its profiles, so large areas or long periods make for
a large download.

**Which values are used.** Each variable is read from one ICES parameter code.
ICES splits some analytes over several codes, by method, and only the code
below is used: nitrate is nitrate alone, for example, not nitrate+nitrite.

.. csv-table:: ICES parameter codes
   :header: "Variable", "ICES parameter code", "ICES dataset"
   :widths: 30, 35, 35

   "Temperature", "``TEMPPR01``", "CTD and bottle"
   "Salinity", "``PSALPR01``", "CTD and bottle"
   "Total Alkalinity", "``ALKYZZXX``", "Bottle"
   "Ammonium", "``AMONZZXX``", "Bottle"
   "Chlorophyll", "``CPHLZZXX``", "Bottle"
   "Nitrate", "``NTRAZZXX``", "Bottle"
   "Oxygen", "``DOXYZZXX``", "Bottle"
   "pH", "``PHXXZZXX``", "Bottle"
   "Phosphate", "``PHOSZZXX``", "Bottle"
   "Silicate", "``SLCAZZXX``", "Bottle"

**Profiles in both datasets.** Bottles are fired on CTD casts, so a cast can be
in both the CTD and the bottle data, with the same values. For temperature and
salinity, bottle values at a station the CTD data already has (the same date,
and the same position to 0.01°) are left out, so each cast is counted once, and
``matchup`` says how many were. The bottle data adds the stations without a CTD
profile, which are many in older years.

**Quality flags.** A value is kept only if its own quality flag and the flag of
its depth are both 0 (good). Values flagged 1 (unknown quality), 4
(questionable) or 8 (bad) are left out. Recent years have many values flagged
1, so they can have few observations, or none.

**Depth.** The depth is ICES's adopted depth (``ADEPZZ01``), in metres.
Observations at 5 m or less make up the surface layer. Through the water
column, an observation above the centre of the model's top cell, as many
bottle values at 0 m are, is compared with the top cell.

**Time.** Only the date of an observation is used, not the time of day. Times
without a time zone are taken as UTC. A time written as hour 24
(``1981-04-27T24``) is 00:00 of the next day, as ISO 8601 has it. An
observation dated only to the month or the year, or whose time cannot be read,
is left out rather than given a day, and ``matchup`` says how many were.

**Values.** Values are used as ICES gives them, in the units in the table
above. OceanVal does not screen them for range or remove duplicates, beyond the
quality flags and the repeated stations above. ICES revises its data, so a later download can differ.

Dataset notes
-------------


The Units column of the gridded and point recipes tables gives the units of the
observations as the dataset's files state them. If your model's units differ,
pass ``obs_multiplier`` and ``obs_adder`` to ``add_gridded_comparison`` or
``add_point_comparison`` (the observations become *observations × obs_multiplier + obs_adder*); the
``oceanval`` window's Units step lists both sets of units, fills these in where
it can tell how to convert one into the other, and writes them for you.

Always check the dataset units and climatology period before comparing the
result with model output. See :doc:`how_to_use` for matching and time-resolution
guidance.


Your own recipes
----------------

.. warning::

   Recipes of your own are on the ``main`` branch but not yet in a tagged
   release (see the `version history <https://plymouthmarinelaboratory.github.io/OceanVal/version-history.html>`_).

A recipe describes a set of observations once, so that you can use it by name
in any matchup, like OceanVal's own. Your recipes are kept in ``.oceanvalrc``
files, which are JSON:

- ``.oceanvalrc`` in the directory you run OceanVal from holds the recipes for
  that directory only;
- ``.oceanvalrc`` in your home directory holds the recipes for everywhere. The
  ``OCEANVALRC`` environment variable names another file to use in its place.

Where the same recipe is in both, the one in the directory is used. A recipe
is used by the name of its source, in lower case: a source called ``MySat``,
for chlorophyll, is ``recipe={"chlorophyll": "mysat"}``, with
:func:`oceanval.add_gridded_comparison` or :func:`oceanval.add_point_comparison`
as for any other recipe. The variable can be one of OceanVal's, or a new one.

Register them in the ``oceanval`` window: **Add your own validation
data for future use** on its first page. Choose point or gridded data and describe it:

- **Gridded data** is netCDF data, on this machine (a file, a directory of
  files, or a pattern ending in ``.nc`` such as ``obs/chl_*.nc``), on a THREDDS
  server (OPeNDAP addresses, with ``dodsC`` in them) or at a web address, which
  is downloaded when it is matched up.
- **Point data** is a csv file, or a directory of csv files, on this machine, with ``lon``,
  ``lat`` and ``observation`` columns and any of ``year``, ``month``, ``day``,
  ``depth`` and ``source``.

OceanVal opens the data as you describe it, as a matchup will, and checks it:
that the variable is in the netCDF files, that the years fit what you say about
a climatology, that the csv columns can be used, and that a server answers.
What it works out, such as units, is filled in **in red and bold**. A recipe
is saved only once its data has been checked, so one on a server that cannot
be reached is not saved. You choose whether to save it for this directory only
or for everywhere, and OceanVal refuses a source name that is one of its own
recipes, or is already in the file, and warns if the other file has a recipe of
the same name.

In the **Own data** step the **Simulation variable** is chosen as in the recipes
window: clicking in the box opens the list of the variables in the
simulation's output, with their ``long_name``, and what you type in the box
searches it. Clicking a variable ticks it and adds it to the box, and ticking
several sums them (``a+b``); a name typed in the box is ticked too. Any name can
still be typed, but one that is not in the simulation's output is refused. For
gridded data on this machine (a netCDF file, a directory of them or a pattern
such as ``obs/*.nc``), the **Observation variable** box lists the variables in
the first file in the same way (one only), once its path has been typed. Data on
a server is not looked at.

Data you add in the **Own data** step of a matchup can be saved as a recipe
there too: **Add this data for use now and in future** adds it to the matchup,
as **Add this data for use now only** does, and saves it as a recipe. A page
first asks for what a recipe needs that the form did not: the units, where to
save it and, if you left them out, the source information and the variable's
names in the report. What OceanVal will use unless you change it is filled in
**in amber**: the units in the netCDF file, ``Source for`` and the source's
name, the variable's name, and everywhere (your home directory). The units of
csv files, which do not say, and of a netCDF variable without them, are marked
in red, and have to be given. Data on a server is checked first, as above. In
the matchup it is saved from, it is registered as your own data, so that
matchup's recipes window shows it in its variable's row, ticked and tagged
**Your data**, in place of its recipe.

Once saved, a recipe is offered in the recipes window beside OceanVal's own,
with a **Yours** tag, ticked wherever a model variable is found for it. For a
variable that OceanVal has no recipes for, it can only identify the model
variable if exactly one is named for it, or has its description in its
``long_name``. Otherwise the model variable is left blank for you to fill in.
The matchup script registers it with a ``recipe=`` argument, and the units step
lists its units like those of any other recipe.

A recipe in a file looks like this:

.. code-block:: json

   {
     "version": 1,
     "recipes": {
       "chlorophyll": {
         "mysat": {
           "kind": "gridded",
           "source": "MySat",
           "source_info": "My satellite chlorophyll, v2",
           "location": "thredds",
           "obs_path": ["https://example.org/thredds/dodsC/chl/2010.nc"],
           "obs_variable": "chl",
           "climatology": false,
           "depth_resolved": false,
           "units": "mg m-3"
         }
       }
     }
   }

A point recipe has ``"kind": "point"``, with ``obs_path`` a csv file or the directory of csv
files and no ``location``, ``obs_variable`` or ``climatology``. A recipe for a
variable of your own also has ``long_name``, ``short_name`` and ``short_title``,
which name it in the report. You can edit the files by hand: OceanVal ignores,
and says so, any file or recipe in them that it cannot use. The file in your
home directory also keeps the ``oceanval`` window's settings (its colours, and
whether it offers the demo), under ``"settings"`` beside ``"recipes"``; saving
either keeps the other.
