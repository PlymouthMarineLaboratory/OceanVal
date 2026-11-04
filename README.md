<a href="https://PlymouthMarineLaboratory.github.io/OceanVal/"><img src="docs-site/assets/img/oceanval_wordmark.svg" width="240" alt="OceanVal" align="left" /></a>
<p align="right">
  <a href="https://oceanval.readthedocs.io/en/latest/?badge=latest"><img src="https://readthedocs.org/projects/oceanval/badge/?version=latest" alt="Documentation Status" /></a>
  <a href="https://anaconda.org/channels/conda-forge/packages/oceanval/overview"><img src="https://anaconda.org/conda-forge/oceanval/badges/version.svg" alt="Conda Badge" /></a>
  <img src="https://github.com/PlymouthMarineLaboratory/oceanVal/actions/workflows/python-app.yml/badge.svg" alt="GitHub Testing" />
  <img src="https://github.com/PlymouthMarineLaboratory/oceanVal/actions/workflows/macos-app.yml/badge.svg" alt="GitHub Testing on macOS" />
</p>
<br clear="left" />

Ocean model analysis and  validation made easy in Python.

To learn more about the package, visit the [OceanVal website](https://PlymouthMarineLaboratory.github.io/OceanVal/). The

OceanVal is designed for the automated analyis and validation of ocean models. You provide the model and validation data. OceanVal does the rest. It can also analyse a simulation on its own, with no observations: the climatology and the phenology of its variables, in an HTML report. Raw FVCOM output is analysed on the FVCOM mesh itself: the climatology, the phenology, the long-term and seasonal series, map temporal changes and transects, with spatial means weighted by the areas of the mesh's cells.

# Installation 

OceanVal should be used with Python versions 3.10-3.13.

You can install the latest release OceanVal from conda-forge as follows:

```sh
conda install conda-forge::oceanval
```

You can install the development version of OceanVal from GitHub using the following steps.

First, clone this directory:

```sh
git clone https://github.com/PlymouthMarineLaboratory/oceanval.git
```

Then move to this directory.

```sh
cd oceanval
```


Second, set up a conda environment. If you want the envionment to called something other than `oceanval`, you can change the name in the oceanval.yml file. 

```sh
conda env create -f oceanval.yml
```



Activate this environment.

```sh
conda activate oceanval
```

Now, sometimes R package installs go wrong in conda. Run the following command to ensure Rcpp is installed correctly.

```sh
Rscript -e "install.packages('Rcpp', repos = 'https://cloud.r-project.org/')"
```

Now, install the package.

```sh
pip install .
```

OceanVal needs jupyter-book 2 or later, which `pip install .` installs.

Alternatively, install the conda environment and package using the following commands:

```sh
    conda env create --name oceanval -f https://raw.githubusercontent.com/PlymouthMarineLaboratory/oceanval/main/oceanval.yml
    conda activate oceanval
    pip install git+https://github.com/PlymouthMarineLaboratory/oceanval.git
```

## Updating the development version

To update an installed development version to the latest code on GitHub, activate the same conda environment and run:

```sh
pip install --force-reinstall git+https://github.com/PlymouthMarineLaboratory/oceanval.git
```

`--force-reinstall` installs the latest code even when pip thinks the installed version is up to date. It also reinstalls the dependencies, so the update can take a few minutes.



# Running OceanVal

To run OceanVal, just do the following on the command line:

```sh
oceanval

```

This opens the OceanVal app in your web browser. Its first page offers four boxes: **View simulation output** (search a simulation's netCDF files and map any variable in them, at the time, depth and years you choose, or play it as an animation), **Analyze simulations**, **Compare simulations** and **Validate simulations**.




