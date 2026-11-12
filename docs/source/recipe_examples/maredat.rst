MAREDAT plankton biomass recipes
================================

Dataset: `MAREDAT <https://doi.org/10.5194/essd-5-227-2013>`_, the world atlas of
marine ecosystem data (Buitenhuis et al. 2013).

.. code-block:: python

   import oceanval

   oceanval.add_gridded_comparison(
       name="diatoms",
       model_variable="P1_c",
       recipe={"diatoms": "maredat"},
       climatology=True,
       vertical=False,  # set True to validate the full water column
   )

The ``maredat`` recipe has five plankton groups, each a monthly climatology of
carbon biomass in milligrams of carbon per cubic metre, on a 1 degree grid of
33 depths to 5500 m, with nothing where nothing was sampled:

* ``diatoms`` (`Leblanc et al. 2012 <https://doi.org/10.1594/PANGAEA.777384>`_), ERSEM's ``P1_c``
* ``picophytoplankton`` (`Buitenhuis et al. 2012 <https://doi.org/10.1594/PANGAEA.777385>`_), ERSEM's ``P3_c``
* ``bacteria``, heterotrophic bacteria and archaea
  (`Buitenhuis et al. 2012 <https://doi.org/10.1594/PANGAEA.779142>`_), ERSEM's ``B1_c``
* ``microzooplankton``, ciliates and heterotrophic dinoflagellates
  (`Buitenhuis et al. 2010 <https://doi.org/10.1594/PANGAEA.779970>`_), ERSEM's ``Z5_c``
* ``mesozooplankton`` (`Moriarty & O'Brien 2013 <https://doi.org/10.1594/PANGAEA.785501>`_),
  ERSEM's ``Z4_c``; the ``copepod`` recipe is a second source, a 0-200 m mean

Each group's zip is downloaded from PANGAEA and its netCDF file rewritten as a
small CF file of the biomass, with the months dated in 2000. The samples are
sparse and span orders of magnitude, so read the comparison in log10. With
``vertical=False`` the observations' first level, 0 m, is compared with the
model's surface.
