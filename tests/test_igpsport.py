import pytest
from cycling_mcp.igpsport import IgpsportClient, IgpsportError

def test_invalid_ride_id():
    with IgpsportClient("dummy") as client:
        with pytest.raises(ValueError):
            client.activity_detail("../../etc/passwd")

def test_untrusted_fit_url():
    with IgpsportClient("dummy") as client:
        with pytest.raises(IgpsportError):
            client.download_fit("http://127.0.0.1/secret")
