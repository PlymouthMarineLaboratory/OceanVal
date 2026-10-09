"""validate()'s variables option: which matchups a report validates."""

import os
import pickle

import pytest

import oceanval
from oceanval import selection


def write_matchups(directory, titles=None):
    """The files of a finished matchup with two gridded temperatures, one of
    them through the water column, and point nitrate at the surface and
    through the water column, as validate() reads their names."""
    matchups = os.path.join(directory, "oceanval_matchups")
    files = [
        "gridded/temperature/COBE2_temperature_surface.nc",
        "gridded/temperature/WOA23_temperature_surface.nc",
        "gridded/temperature/WOA23_temperature_vertical.nc",
        "point/surface/nitrate/ICES/ICES_surface_nitrate.csv",
        "point/all/nitrate/glodap/glodap_all_nitrate.csv",
        # not a matchup of its own
        "point/all/nitrate/glodap/glodap_all_nitrate_unit.csv",
    ]
    for name in files:
        path = os.path.join(matchups, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
    with open(os.path.join(matchups, "short_titles.pkl"), "wb") as saved:
        pickle.dump(titles if titles is not None else {"nitrate": "Nitrate"}, saved)
    with open(os.path.join(matchups, "variables_matched.pkl"), "wb") as saved:
        pickle.dump(["nitrate", "temperature"], saved)


class TestOnDisk:
    def test_each_matchup_validate_makes_a_page_for(self, tmp_path):
        write_matchups(tmp_path)
        found = selection.on_disk(str(tmp_path))
        assert [
            (m["kind"], m["variable"], m["source"], m["layer"], m["vertical"]) for m in found
        ] == [
            ("gridded", "temperature", "COBE2", None, False),
            ("gridded", "temperature", "WOA23", None, True),
            ("point", "nitrate", "glodap", "all", True),
            ("point", "nitrate", "ICES", "surface", False),
        ]
        assert [m["key"] for m in found] == [
            "temperature/COBE2", "temperature/WOA23", "nitrate/glodap", "nitrate/ICES",
        ]

    def test_titles_are_matchups_own_or_the_variables(self, tmp_path):
        write_matchups(tmp_path, titles={"nitrate": "Nitrate (NO3)"})
        titles = {m["variable"]: m["title"] for m in selection.on_disk(str(tmp_path))}
        assert titles == {"temperature": "Temperature", "nitrate": "Nitrate (NO3)"}

    def test_nothing_there(self, tmp_path):
        assert selection.on_disk(str(tmp_path)) == []


class TestCheckVariables:
    @pytest.mark.parametrize(
        "given, checked",
        [
            (None, None),
            ("temperature", ["temperature"]),
            (["temperature", " nitrate / ICES "], ["temperature", "nitrate/ICES"]),
            (("temperature/WOA23",), ["temperature/WOA23"]),
        ],
    )
    def test_the_forms_it_takes(self, given, checked):
        assert selection.check_variables(given) == checked

    @pytest.mark.parametrize(
        "given",
        [[], [""], [3], ["a/b/c"], ["/ICES"], ["nitrate/"], {"nitrate": "ICES"}, 5],
    )
    def test_what_it_does_not_take(self, given):
        with pytest.raises(ValueError, match="variables"):
            selection.check_variables(given)

    def test_entries_pick_matchups_by_variable_or_by_source_too(self, tmp_path):
        write_matchups(tmp_path)
        found = selection.on_disk(str(tmp_path))

        def picked(variables):
            return [m["key"] for m in selection.check_against(variables, found)]

        assert picked(None) == [m["key"] for m in found]
        assert picked(["temperature"]) == ["temperature/COBE2", "temperature/WOA23"]
        # not case sensitive
        assert picked(["Temperature/woa23", "NITRATE/ices"]) == ["temperature/WOA23", "nitrate/ICES"]

    def test_an_entry_must_pick_a_matchup(self, tmp_path):
        write_matchups(tmp_path)
        found = selection.on_disk(str(tmp_path))
        with pytest.raises(ValueError, match="no matchups of 'oxygen'.*temperature/COBE2"):
            selection.check_against(["temperature", "oxygen"], found)
        with pytest.raises(ValueError, match="no matchups of 'nitrate/WOA23'"):
            selection.check_against(["nitrate/WOA23"], found)


class TestValidate:
    def test_variables_are_checked_before_the_old_report_is_removed(self, tmp_path):
        write_matchups(tmp_path)
        os.makedirs(tmp_path / "oceanval_report")
        (tmp_path / "oceanval_report" / "keep.txt").touch()
        with pytest.raises(ValueError, match="no matchups of 'oxygen'"):
            oceanval.validate(
                data_dir=str(tmp_path), out_dir=str(tmp_path), variables=["oxygen"], test=True
            )
        with pytest.raises(ValueError, match="variables"):
            oceanval.validate(data_dir=str(tmp_path), out_dir=str(tmp_path), variables=[], test=True)
        assert (tmp_path / "oceanval_report" / "keep.txt").exists()
