"""validate()'s variables option: which matchups a report validates."""

import os

import pytest

import oceanval
from oceanval import selection
from simulations import write_matchups


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


class TestLastRun:
    """The record of the matchups the last matchup() run made, which
    validate(previous=False) validates only."""

    def test_the_record_is_read_as_written(self, tmp_path):
        matchup_dir = tmp_path / "oceanval_matchups"
        matchup_dir.mkdir()
        # a gridded matchup is told with no layer, which is what it is kept with whatever
        selection.save_last_run(str(matchup_dir), [
            ("gridded", "temperature", "COBE2", None),
            ("point", "nitrate", "ICES", "surface"),
        ])
        assert selection.last_run(str(tmp_path)) == {
            ("gridded", "temperature", "COBE2", None),
            ("point", "nitrate", "ICES", "surface"),
        }
        assert not (matchup_dir / (selection.LAST_RUN + ".part")).exists()
        selection.forget_last_run(str(matchup_dir))
        # and again, with nothing there
        selection.forget_last_run(str(matchup_dir))
        with pytest.raises(ValueError, match="no record of which matchups the last matchup"):
            selection.last_run(str(tmp_path))

    def test_an_unreadable_record_is_none(self, tmp_path):
        write_matchups(tmp_path)
        (tmp_path / "oceanval_matchups" / selection.LAST_RUN).write_text("{not json")
        with pytest.raises(ValueError, match="previous=True"):
            selection.last_run(str(tmp_path))

    def test_the_last_runs_matchups_are_matched_by_kind_and_layer(self, tmp_path):
        write_matchups(tmp_path)
        found = selection.on_disk(str(tmp_path))
        record = {("point", "nitrate", "ICES", "surface"), ("point", "nitrate", "glodap", "surface")}
        # glodap's matchup here is through the water column, so another run's
        assert [m["key"] for m in selection.of_last_run(found, record)] == ["nitrate/ICES"]
        # with the earlier matchups previous picks
        assert [m["key"] for m in selection.of_last_run(found, record, ["temperature/COBE2"])] == [
            "temperature/COBE2", "nitrate/ICES",
        ]
        assert len(selection.of_last_run(found, record, True)) == len(found)

    def test_picks(self):
        assert selection.picks(True, "temperature", "COBE2")
        assert not selection.picks(False, "temperature", "COBE2")
        assert selection.picks(["temperature/cobe2"], "temperature", "COBE2")
        assert not selection.picks(["temperature/WOA23"], "temperature", "COBE2")
        assert selection.picks(["temperature"], "temperature", "WOA23")


class TestValidate:
    def test_previous_is_checked_before_the_old_report_is_removed(self, tmp_path):
        write_matchups(tmp_path)
        os.makedirs(tmp_path / "oceanval_report")
        (tmp_path / "oceanval_report" / "keep.txt").touch()

        def validate(**kwargs):
            oceanval.validate(data_dir=str(tmp_path), out_dir=str(tmp_path), test=True, **kwargs)

        # no record of the last run
        with pytest.raises(ValueError, match="previous=True"):
            validate(previous=False)
        with pytest.raises(ValueError, match="previous: there are no matchups of 'oxygen'"):
            validate(previous=["oxygen"])
        with pytest.raises(ValueError, match="previous must name at least one"):
            validate(previous=[])
        write_matchups(tmp_path, last_run=[("point", "nitrate", "ICES", "surface")])
        # variables picks among the last run's matchups, and those previous picks
        with pytest.raises(ValueError, match="no matchups of 'temperature'.*nitrate/ICES"):
            validate(previous=False, variables=["temperature"])
        with pytest.raises(ValueError, match="no matchups of 'temperature/COBE2'"):
            validate(previous=["temperature/WOA23"], variables=["temperature/COBE2"])
        write_matchups(tmp_path, last_run=[("point", "oxygen", "ICES", "surface")])
        with pytest.raises(ValueError, match="made no matchups"):
            validate(previous=False)
        assert (tmp_path / "oceanval_report" / "keep.txt").exists()

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
