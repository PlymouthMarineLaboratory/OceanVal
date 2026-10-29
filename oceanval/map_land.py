"""The land of the maps nctoolkit draws, put beneath the data, with the lakes as water.

nctoolkit's pub_plot and panel_plot draw Natural Earth's land over the data
(cartopy gives a feature a zorder of 1.5, a pcolormesh 1), and Natural Earth's
land takes in the lakes, so a model of Lake Erie came out all land. land_below,
called after each pub_plot or panel_plot, puts the land beneath the data and
draws Natural Earth's lakes over it in the colour of the map's background, so
every cell with a value shows, wherever it is, and a lake with none looks like
water. A map nctoolkit drew no land on is left as it is.

The Analyze and Compare simulations figures are scripts of their own, which
write the function out (LAND_CODE), so it must import everything it uses.
"""

import inspect


def land_below(fig=None):
    """Put the land of each map of fig (the current figure) beneath its data,
    and the lakes over the land as water. Never raises: without the lakes'
    shapefile (cartopy downloads it once) the land is still put beneath."""
    import matplotlib.pyplot as plt
    try:
        import cartopy.feature as cfeature
        from cartopy.io import shapereader
        from cartopy.mpl.feature_artist import FeatureArtist
    except Exception:
        return
    fig = plt.gcf() if fig is None else fig
    for ax in fig.axes:
        features = [
            artist for artist in ax.get_children()
            if isinstance(artist, FeatureArtist)
            and isinstance(getattr(artist, "_feature", None), cfeature.NaturalEarthFeature)
        ]
        land = [artist for artist in features if artist._feature.name == "land"]
        # a map drawn without land, or one done already
        if not land or any(artist._feature.name == "lakes" for artist in features):
            continue
        data = [
            artist.get_zorder() for artist in ax.collections + ax.images
            if not isinstance(artist, FeatureArtist)
        ]
        bottom = min(data) if data else 1
        for artist in land:
            artist.set_zorder(bottom - 0.2)
        scale = land[0]._feature.scale
        try:
            shapereader.natural_earth(resolution=scale, category="physical", name="lakes")
        except Exception:
            continue
        ax.add_feature(
            cfeature.NaturalEarthFeature("physical", "lakes", scale),
            facecolor=ax.get_facecolor(),
            edgecolor="none",
            zorder=bottom - 0.1,
        )


# the function as a script writes it out
LAND_CODE = inspect.getsource(land_below)
