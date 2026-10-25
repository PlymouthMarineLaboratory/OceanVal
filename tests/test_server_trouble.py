import io
import urllib.error

import pytest
import requests

from oceanval import data_store, prompts, server_trouble

# as NOAA's thredds server answered, read by CDO (oceanval_matchups/matchup_failures.log)
UNAVAILABLE = (
    "syntax error, unexpected WORD_WORD, expecting SCAN_ATTR or SCAN_DATASET or SCAN_ERRORcontext: "
    '<!DOCTYPE^ HTML PUBLIC "-//IETF//DTD HTML 2.0//EN"><html><head><title>503 Service Unavailable'
    "</title></head><body><h1>Service Unavailable</h1><p>The server is temporarily unable to service "
    "yourrequest due to maintenance downtime or capacityproblems. Please try again later.</p><p>Additionall"
)
PROXY = (
    "syntax error, unexpected WORD_WORD, expecting SCAN_ATTR or SCAN_DATASET or SCAN_ERRORcontext: "
    '<!DOCTYPE^ HTML PUBLIC "-//IETF//DTD HTML 2.0//EN"><html><head><title>502 Proxy Error</title>'
    "</head><body><h1>Proxy Error</h1><p>The proxy server received an invalidresponse from an upstream ser"
)
WOA23 = "https://www.ncei.noaa.gov/thredds-ocean/dodsC/woa23/DATA/nitrate/netcdf/all/1.00/woa23_all_n01_01.nc"


def raised_as_nctoolkit_does(text):
    """The error nctoolkit raises: ValueError(e), while handling run_cdo's."""
    try:
        try:
            raise ValueError(text)
        except ValueError as error:
            raise ValueError(error)
    except ValueError as error:
        return error


class TestAnswerOf:
    def test_noaas_page_saying_it_is_unavailable(self):
        assert server_trouble.answer_of(ValueError(UNAVAILABLE)) == {
            "status": 503,
            "reason": "Service Unavailable",
        }

    def test_a_proxy_error(self):
        assert server_trouble.answer_of(ValueError(PROXY)) == {"status": 502, "reason": "Proxy Error"}

    def test_as_nctoolkit_raises_it(self):
        assert server_trouble.answer_of(raised_as_nctoolkit_does(UNAVAILABLE))["status"] == 503

    def test_the_words_alone_of_the_page(self):
        text = "context: <h1>Service Unavailable</h1><p>The server is temporarily unable to service"
        assert server_trouble.answer_of(RuntimeError(text))["status"] == 503

    def test_too_many_requests(self):
        text = "context: <html><head><title>429 Too Many Requests</title></head>"
        assert server_trouble.answer_of(RuntimeError(text)) == {"status": 429, "reason": "Too Many Requests"}

    def test_urllibs_error(self):
        error = urllib.error.HTTPError(WOA23, 503, "Service Unavailable", {}, io.BytesIO())
        assert server_trouble.answer_of(error) == {"status": 503, "reason": "Service Unavailable"}

    def test_requests_error(self):
        response = requests.Response()
        response.status_code = 504
        error = requests.HTTPError("504 Server Error", response=response)
        assert server_trouble.answer_of(error) == {"status": 504, "reason": "Gateway Timeout"}

    @pytest.mark.parametrize(
        "error",
        [
            urllib.error.HTTPError(WOA23, 404, "Not Found", {}, io.BytesIO()),
            ValueError("<html><head><title>404 Not Found</title></head>"),
            FileNotFoundError("no such file: woa23_all_n01_01.nc"),
            ValueError("cdo    merge (Abort): Input streams have different number of levels!"),
            ConnectionError("Connection timed out: www.ncei.noaa.gov"),
        ],
    )
    def test_anything_else_is_not_the_servers_trouble(self, error):
        assert server_trouble.answer_of(error) is None


class TestServers:
    def test_the_host_of_a_url_or_a_list_of_them(self):
        assert server_trouble.host_of(WOA23) == "www.ncei.noaa.gov"
        assert server_trouble.host_of([WOA23, "x"]) == "www.ncei.noaa.gov"
        assert server_trouble.host_of("/data/woa23/n01.nc") is None
        assert server_trouble.host_of([]) is None

    def test_every_server_of_oceanvals_own_data_is_named(self):
        assert set(data_store.BUILTIN_HOSTS) <= set(server_trouble.SERVERS)

    def test_another_is_named_by_its_address(self):
        assert server_trouble.server_of("thredds.example.org") == "thredds.example.org"

    def test_what_is_said_at_a_terminal(self):
        lines = server_trouble.explain({"status": 503, "reason": "Service Unavailable"}, "www.ncei.noaa.gov")
        assert lines[0] == "NOAA's server (www.ncei.noaa.gov) answered HTTP 503 Service Unavailable."
        assert "at NOAA's end, not a problem with your simulation" in " ".join(lines)


class TestTell:
    def test_the_window_is_told(self):
        told = []
        with prompts.told_by(told.append):
            answer = server_trouble.tell(ValueError(UNAVAILABLE), [WOA23], "gridded|nitrate|WOA23|", "nitrate (WOA23)")
        assert answer["status"] == 503
        assert told == [
            {
                "kind": "server_trouble",
                "key": "gridded|nitrate|WOA23|",
                "title": "nitrate (WOA23)",
                "host": "www.ncei.noaa.gov",
                "server": "NOAA",
                "status": 503,
                "reason": "Service Unavailable",
            }
        ]

    def test_nothing_is_told_of_another_error(self):
        told = []
        with prompts.told_by(told.append):
            assert server_trouble.tell(FileNotFoundError("x"), WOA23, "k", "t") is None
        assert told == []
