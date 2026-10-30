Quickstart
==========

This guide shows the shortest path from a model output directory to an HTML
validation report: three function calls. It assumes your model files are
CF-compliant NetCDF files stored either in one directory or in numeric
``YYYY/MM`` subdirectories.

Install
-------

Install the released package from conda-forge:

.. code-block:: console

   conda install -c conda-forge oceanval

For full installation options, see :doc:`installing`.

The OceanVal App
----------------

The quickest way needs no Python at all. In a terminal, in the directory to
work in, run:

.. code-block:: console

   oceanval

A page opens in your web browser that takes you from a folder of model
output to a validation report:

1. **Choose** what to do: match up and validate, match up only, or validate
   matchups made earlier.
   Or register recipes of your own for observations you use again, which are
   saved in a ``.oceanvalrc`` file (see :doc:`recipes`).
2. **Simulation**: where the model output is, and which files to use,
   including how many directories down they are: type a number, or pick 0 to
   5 from the list the arrow in the box opens. If you leave it empty, OceanVal
   fills it in, in amber, when it recognises the folders: ``YYYY/MM/**.nc``
   (a year with the months 01 to 12) is 2, ``MM/*.nc`` (the months 01 to 12) is
   1, and files with no subdirectories is 0. A number you type is never changed.
   OceanVal reads the years the output covers from the times in its files
   (never their names) while you carry on, in a few seconds, for the next
   steps.
3. **Own data**: add observations of your own, if you have any, for this
   matchup only or saved as a recipe for later ones too. The simulation
   variable is picked from the simulation's variables, which OceanVal reads
   while you answer, and the observation variable of gridded data from those
   in the first netCDF file of the observations.
4. **Recipes**: check the model variables found, give the first and last
   years to validate (type them, or pick them from the list of the years the
   output covers that the arrow in each box opens), choose how many cores to
   use, which has no default, and choose the observations to validate against.
5. **Units**: check and confirm the units, with conversions suggested where
   they differ.
6. **Files** and **Report**: check the matchups (the files of each variable
   in the years it is matched up for, and any dates missing from them), then
   choose the report's options.
7. **Run**: the matchups and the report. A grid has a row for each variable
   and data source: its **Matchup** column turns from empty to a turning
   circle while that matchup is made, then to a tick (or a cross if it could
   not be made). Its **Interim report** column goes the same way as the
   matchup's page is added to the interim validation report, whose link is at
   the top. A tick there opens that page. The **Model extraction** column
   shows how many of the simulation's files the matchup has read so far, such
   as 37/120, and then a tick. The **Observation extraction** column has a
   turning circle while the observations are read or downloaded, and then a
   tick. OceanVal's output is shown only if the run fails, with its errors.
   If a data server is down or overloaded (NOAA's for WOA23, say, answering
   **503 Service Unavailable**), a notice at the bottom of the window says so
   as soon as a matchup fails because of it: which server, what it answered
   and when, and the matchups it stopped. This is a problem at the server's
   end, not with your simulation, so try those matchups again later.

**Back**, on every step after the first, keeps what you have entered, so you
can go back to fix a mistake without starting again.

Simulations you have validated before (remembered as soon as you confirm the
matchups) are listed by the arrow at the right of the simulation directory
box, so you can pick one instead of typing it. They are remembered in a ``.oceanvalcache`` JSON file in your home directory (the
``OCEANVALCACHE`` environment variable names another file to use in its
place). A new version keeps it. The **x** at the right of a simulation in the list
takes it out of the history (the Delete key does the same for the one marked).

The years a simulation's output covers are read from the time axes of only
some of its files: the first and last of each kind of output, by name and by
when they were written, and some others chosen at random as a check (every file
of a kind is read if the check finds one out of order). Only the simulation's
main output counts: the kinds of file with at least a tenth as many files as
the most numerous kind, and at least two, unless every kind is a single file.
Files beside it, such as a post-processed time series or the grids an earlier
validation saved, are left out. The years are kept in ``.oceanvalcache`` too,
and read again only when the files change.

Over SSH, including MobaXterm, whose X11 forwarding would otherwise open a
browser on the remote machine, ``oceanval`` does not open a browser. It prints
the link and the command that forwards the port, such as
``ssh -L 8765:127.0.0.1:8765 you@server``: run it in a new terminal on your own
computer (or add the forward to MobaXterm's Tunneling settings once), leave it
open, and open the link in your browser. VS Code forwards the port for you.
``oceanval --no-browser`` does the same anywhere, and ``OCEANVAL_BROWSER=1``
opens a browser over SSH anyway.

Closing the browser window quits ``oceanval`` in the terminal, within about
15 seconds, whichever browser you use. A run that is going is never stopped
by it, nor by moving to another tab (which some browsers, such as Safari,
freeze): it carries on, the terminal prints the link to open the window
again, and ``oceanval`` quits once the run is over. **Stop**, **Quit** and
Ctrl+C stop a run at once.

The cog beside the OceanVal name at the top left of every page of the window
(**Preferences**) sets whether the first
page offers **Try a demo**, and the **data store** (below). Nothing changes until you press **Apply**. They are kept under ``"settings"`` in the ``.oceanvalrc`` file in
your home directory (or the file ``OCEANVALRC`` names), and read each time the
window opens. The **i** beside it shows the version of OceanVal, links to the website and
GitHub, and the built-in datasets.

The circular arrow beside the **i** (**Restart**) goes back to the first page from any step. It asks first,
as everything you entered in the window is then discarded (a run in progress is stopped), so that you start
from a clean slate. Your Preferences, your own recipes in ``.oceanvalrc`` and the simulations OceanVal remembers are kept.

The **data store** is a folder for keeping the built-in observational data that is downloaded
(WOA23, OC-CCI, NSBC, COBE2, GLODAP and ICES), so later matchups need not download it again.
Your own data is never kept in it. The folder must already exist: OceanVal keeps everything in an
``oceanval_datastore`` folder it makes inside it, and never writes or removes anything outside
that. Choose a disk with a reasonable amount of free space, as the store can come to several GB.
Choose **Always use data from your data store, if available** to download only what it does not
have, or **Only use the data store when download fails** to download everything each time (and
keep it), using what is kept only for what cannot be downloaded. The store keeps track of the
area and years of what it has: gridded data is kept for the model's area, rounded out to whole
degrees (the whole globe for a model covering 90% of it), ICES data a year at a time, and a
matchup inside what is kept takes it from the store. The setting is used by every matchup,
including those run from Python; ``matchup(..., data_store=False)`` leaves it out for one run.
**Run a check on the data store** looks for data held twice, files that are missing or not the
size they were, and anything in the store that is not OceanVal's built-in data (files of yours,
for one), and says what it finds. It only looks: nothing is changed, removed or fixed.

To see how it works first, choose **Try a demo**, below the four choices. It
asks you to choose one of three CMIP6 climate models, each from a different
ESGF server (NorESM2-LM, MPI-ESM1-2-LR and UKESM1-0-LL), and one to three of
its variables, sea surface temperature, surface nitrate and sea surface
salinity, in a grid. Only those files are downloaded (up to 50 MB, 26 MB and
460 MB for the three models) and it takes you through the same steps with the
options filled in, including the model's name as the only file names to use.
A short page of instructions comes first (press Continue on each page, and tick
the agreement when asked to run the validation), and asks whether you want the
validation concise and fast (the default, with results in a couple of minutes) or
detailed but slower (4 or 5 minutes). The step for your own data is left out, as only OceanVal's own datasets are used.
Temperature is matched up with COBE-SST 2, and nitrate and salinity with the
World Ocean Atlas 2023. The units step suggests
converting the observed nitrate, which you check as you would for your own
model. It shows how OceanVal works, not how to validate a climate model, which needs many years
of output rather than one. It creates a directory called ``oceanval_demo``,
with the data, matchups and report in it: remove it when you have finished.

The script it runs is written first, to ``matchup.py`` (``matchup1.py``, ``matchup2.py`` and so on if that file exists already), so you can read it
and run it again later. Every step is described on the website, in
`Validating from your browser
<https://plymouthmarinelaboratory.github.io/OceanVal/browser.html>`_, including how to
use it on a remote machine.

The sections below do the same in Python.

Match model output with observations
------------------------------------

Work from a fresh, empty directory: OceanVal writes its matchup files and
report there. Register the observations you need, then run ``matchup``:

.. code-block:: python

   import oceanval

   # 1. Register: compare the model variable "thetao" with WOA23 temperature
   oceanval.add_gridded_comparison(
       name="temperature",
       model_variable="thetao",
       recipe={"temperature": "woa23"},
       start=2005,
       end=2014,
       climatology=True,
   )

   # 2. Match: pair model output with the observations
   oceanval.matchup(
       sim_dir="/path/to/model/output",
       start=2005,
       end=2014,
       cores=4,
   )

Replace ``thetao`` with the temperature variable name used in your model's
NetCDF files. OceanVal will scan ``sim_dir``, report the file pattern it has
identified, and ask you to confirm before matching. The matched data is
written to an ``oceanval_matchups`` directory.

To use other variables or your own observation files, see :doc:`recipes` and
:doc:`how_to_use`.

Build the report
----------------

Run validation from the same directory:

.. code-block:: python

   # 3. Report: compute statistics and build the HTML report
   oceanval.validate()

The report is written below ``oceanval_report`` and opens in your browser
when the build completes. It includes climatology maps, bias maps,
seasonality analysis, spatial correlation tables, and full documentation of
the methods used.

Next steps
----------

* Add more variables: each :doc:`recipe <recipes>` is one extra
  ``add_gridded_comparison`` call.
* Validate against your own gridded or in-situ data: see :doc:`how_to_use`.
* Compare several simulations side by side with :func:`oceanval.compare`.

Troubleshooting
---------------

If no matchups are produced, check the model directory structure, variable
names, time resolution, units, and climatology setting. The most common issue
with monthly model output and in-situ observations is using daily matching
precision; see the time resolution guidance in :doc:`how_to_use`, or browse
the :doc:`Q&A <q_a>`.
