import pytest
import httpx
from unittest.mock import Mock

from cycling_mcp import igpsport
from cycling_mcp.igpsport import IgpsportClient, IgpsportError

def test_invalid_ride_id():
    with IgpsportClient("dummy") as client:
        with pytest.raises(ValueError):
            client.activity_detail("../../etc/passwd")

def test_untrusted_fit_url():
    with IgpsportClient("dummy") as client:
        with pytest.raises(IgpsportError):
            client.download_fit("http://127.0.0.1/secret")


def test_laps_and_zones_use_observed_api_paths():
    with IgpsportClient("dummy") as client:
        client.get = Mock(side_effect=[[{"items": []}], {"hrZone": []}])
        assert client.activity_laps("123") == [{"items": []}]
        assert client.activity_zones("123") == {"hrZone": []}
        assert client.get.call_args_list[0].args == (
            "/service/sportg/sporth/activity/queryActivityLapV2", {"activityId": "123"})
        assert client.get.call_args_list[1].args == (
            "/service/web-gateway/web-analyze/webActivityAnalyze/queryActivityDetail/123",)


@pytest.mark.parametrize("method", ["activity_laps", "activity_zones"])
def test_analysis_rejects_invalid_id_before_authentication(method):
    with IgpsportClient() as client:
        with pytest.raises(ValueError):
            getattr(client, method)("../123")
        assert client.transport is None


def test_fit_download_accepts_observed_igpsport_oss_host(monkeypatch):
    content = b"\x0e\x10\x00\x00\x00\x00\x00\x00.FIT\x00\x00"
    real_client = httpx.Client
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, content=content)

    monkeypatch.setattr(igpsport.httpx, "Client", lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(respond)))
    with IgpsportClient() as client:
        assert client.download_fit(
            "https://igp-zh.oss-cn-hangzhou.aliyuncs.com/test-fit") == content
    assert "Authorization" not in seen[0].headers


def test_other_oss_bucket_is_not_trusted():
    with IgpsportClient() as client:
        with pytest.raises(IgpsportError):
            client.download_fit("https://other.oss-cn-hangzhou.aliyuncs.com/file")


def test_malformed_list_response_is_not_treated_as_empty():
    with IgpsportClient() as client:
        client.get = Mock(return_value={"totalRows": 82})
        with pytest.raises(IgpsportError, match="Invalid activity list"):
            client.activity_page(1)


def test_fit_download_is_bounded(monkeypatch):
    real_client = httpx.Client
    monkeypatch.setattr(igpsport, "MAX_FIT_BYTES", 8)
    monkeypatch.setattr(igpsport.httpx, "Client", lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"a" * 16))))
    with IgpsportClient() as client:
        with pytest.raises(IgpsportError, match="size limit"):
            client.download_fit("https://igp-zh.oss-cn-hangzhou.aliyuncs.com/test")
