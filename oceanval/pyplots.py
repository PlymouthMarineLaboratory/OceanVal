"""The world's borders bundled with OceanVal (data/TM_WORLD_BORDERS-0.3.shp), for the
transect maps, and the land of the report's plotnine maps when Natural Earth's
cannot be read (oceanval.plotnine_figures)."""
import importlib.resources

_WORLD_MAP = None


def world_map():
    """Return a cached GeoDataFrame of the bundled world borders shapefile."""
    global _WORLD_MAP
    if _WORLD_MAP is None:
        import geopandas as gpd

        shp_path = importlib.resources.files("oceanval").joinpath(
            "data/TM_WORLD_BORDERS-0.3.shp"
        )
        _WORLD_MAP = gpd.read_file(str(shp_path))
    return _WORLD_MAP
