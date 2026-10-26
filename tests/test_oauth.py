import asyncio
import base64
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.testclient import TestClient

from cycling_mcp.oauth import OAuthProvider, SCOPE, make_http_app
from cycling_mcp.security import hash_password

PASSWORD = "fixture-owner-password-2026"
CALLBACK = "https://client.example/callback"
VERIFIER = "fixture-verifier-" + "a" * 48


@pytest.fixture(scope="module")
def password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def oauth(tmp_path, password_hash):
    key = Fernet.generate_key().decode()
    provider = OAuthProvider("https://cycling.example", "owner", password_hash, key, tmp_path / "oauth.db")
    mcp = FastMCP("OAuth fixture", host="0.0.0.0", stateless_http=True, json_response=True,
                 auth_server_provider=provider, auth=provider.auth_settings(),
                 transport_security=TransportSecuritySettings(allowed_hosts=["cycling.example"],
                                                               allowed_origins=["https://cycling.example"]))

    @mcp.tool()
    def private_data() -> dict:
        """返回测试数据，不包含真实骑行信息。"""
        return {"private": True}

    with TestClient(make_http_app(mcp, provider), base_url=provider.base, follow_redirects=False) as client:
        yield provider, client, key


def register(client, method="none"):
    response = client.post("/register", json={"client_name": "Fixture client", "redirect_uris": [CALLBACK],
        "token_endpoint_auth_method": method, "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "scope": SCOPE})
    assert response.status_code == 201, response.text
    return response.json()


def begin(client, registered):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
    response = client.get("/authorize", params={"response_type": "code", "client_id": registered["client_id"],
        "redirect_uri": CALLBACK, "state": "fixture-state", "scope": SCOPE,
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": "https://cycling.example/mcp"})
    assert response.status_code == 302, response.text
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    return form(page)


def form(page):
    return {name: re.search('name="' + name + '" value="([^"]+)"', page.text).group(1)
            for name in ("request", "csrf")}


def approve(client, values):
    response = client.post("/login", data={**values, "username": "owner", "password": PASSWORD, "decision": "allow"})
    assert response.status_code == 303, response.text
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["state"] == ["fixture-state"]
    return query["code"][0]


def token_data(registered, code):
    result = {"client_id": registered["client_id"], "grant_type": "authorization_code", "code": code,
              "redirect_uri": CALLBACK, "code_verifier": VERIFIER, "resource": "https://cycling.example/mcp"}
    if registered.get("client_secret"):
        result["client_secret"] = registered["client_secret"]
    return result


@pytest.mark.parametrize("method", ["none", "client_secret_post"])
def test_discovery_login_pkce_and_protected_mcp(oauth, method):
    provider, client, _ = oauth
    unauthenticated = client.post("/mcp", json={})
    assert unauthenticated.status_code == 401
    assert "resource_metadata=" in unauthenticated.headers["www-authenticate"]
    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["issuer"] == provider.issuer
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert "none" in metadata["token_endpoint_auth_methods_supported"]
    resource = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert resource["resource"] == provider.resource
    assert resource["authorization_servers"] == [provider.issuer]
    registered = register(client, method)
    code = approve(client, begin(client, registered))
    response = client.post("/token", data=token_data(registered, code))
    assert response.status_code == 200, response.text
    tokens = response.json()
    assert tokens["scope"] == SCOPE
    headers = {"Authorization": "Bearer " + tokens["access_token"], "Accept": "application/json, text/event-stream"}
    response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1,
        "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "fixture", "version": "1"}}})
    assert response.status_code == 200, response.text
    assert "result" in response.json()
    response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2,
        "method": "tools/call", "params": {"name": "private_data", "arguments": {}}})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert not result.get("isError"), result
    assert json.loads(result["content"][0]["text"]) == {"private": True}
    assert client.post("/token", data=token_data(registered, code)).status_code == 400
    persisted = provider.store.path.read_bytes()
    assert tokens["access_token"].encode() not in persisted
    assert tokens["refresh_token"].encode() not in persisted
    assert registered["client_id"].encode() not in persisted


def test_wrong_pkce_redirect_resource_and_scope_cannot_issue_tokens(oauth):
    _, client, _ = oauth
    registered = register(client)
    code = approve(client, begin(client, registered))
    valid = token_data(registered, code)
    for field, value in (("code_verifier", "wrong"), ("redirect_uri", "https://attacker.example/callback"),
                         ("resource", "https://attacker.example/mcp")):
        response = client.post("/token", data={**valid, field: value})
        assert response.status_code == 400, response.text
    assert client.post("/token", data=valid).status_code == 200


def test_authorization_requires_s256_and_supported_scope(oauth):
    _, client, _ = oauth
    registered = register(client)
    params = {"response_type": "code", "client_id": registered["client_id"], "redirect_uri": CALLBACK,
              "state": "fixture-state", "scope": SCOPE, "code_challenge": VERIFIER,
              "code_challenge_method": "S256", "resource": "https://cycling.example/mcp"}
    for changed in ({"code_challenge_method": "plain"}, {"scope": "admin"},
                    {"resource": "https://attacker.example/mcp"}):
        response = client.get("/authorize", params={**params, **changed})
        assert response.status_code in {302, 400}, response.text
        if response.status_code == 302:
            assert "error=" in response.headers["location"]
            assert urlsplit(response.headers["location"]).hostname == "client.example"
    params.pop("code_challenge")
    response = client.get("/authorize", params=params)
    assert response.status_code == 400 or "error=" in response.headers.get("location", "")


def test_csrf_password_and_origin_are_required(oauth):
    _, client, _ = oauth
    values = begin(client, register(client))
    assert client.post("/login", data={**values, "csrf": "wrong", "decision": "deny"}).status_code == 400
    assert client.post("/login", headers={"Origin": "https://attacker.example"},
                       data={**values, "decision": "deny"}).status_code == 400
    assert client.post("/login", data=values).status_code == 400
    failure = client.post("/login", data={**values, "username": "owner", "password": "wrong", "decision": "allow"})
    assert failure.status_code == 401
    code = approve(client, form(failure))
    assert code


def test_login_page_preserves_same_origin_form_submission(oauth):
    provider, client, _ = oauth
    values = begin(client, register(client))
    page = client.get('/login', params={'request': values['request']})
    assert page.headers['referrer-policy'] == 'same-origin'
    assert "form-action 'self' https://client.example;" in page.headers['content-security-policy']
    assert "frame-ancestors 'none'" in page.headers['content-security-policy']
    values = form(page)
    assert client.post('/login', headers={'Origin': 'null'},
                       data={**values, 'decision': 'deny'}).status_code == 400
    response = client.post('/login', headers={'Origin': provider.base},
                           data={**values, 'username': 'owner', 'password': PASSWORD, 'decision': 'allow'})
    assert response.status_code == 303
    assert 'code=' in response.headers['location']
    assert response.headers['referrer-policy'] == 'no-referrer'


def test_deny_does_not_issue_code(oauth):
    _, client, _ = oauth
    response = client.post("/login", data={**begin(client, register(client)), "decision": "deny"})
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query == {"error": ["access_denied"], "state": ["fixture-state"]}


def test_restart_refresh_rotation_and_replay_revocation(oauth, password_hash):
    provider, client, key = oauth
    registered = register(client)
    tokens = client.post("/token", data=token_data(registered, approve(client, begin(client, registered)))).json()
    restarted = OAuthProvider(provider.base, "owner", password_hash, key, provider.store.path)
    assert asyncio.run(restarted.get_client(registered["client_id"])) is not None
    assert asyncio.run(restarted.load_access_token(tokens["access_token"])) is not None
    request = {"client_id": registered["client_id"], "grant_type": "refresh_token",
               "refresh_token": tokens["refresh_token"], "resource": provider.resource}
    refreshed = client.post("/token", data=request)
    assert refreshed.status_code == 200, refreshed.text
    assert asyncio.run(restarted.load_access_token(tokens["access_token"])) is None
    assert client.post("/token", data=request).status_code == 400
    assert asyncio.run(restarted.load_access_token(refreshed.json()["access_token"])) is None


def test_invalid_redirect_and_unregistered_client_are_rejected(oauth):
    _, client, _ = oauth
    assert client.post("/register", json={"redirect_uris": ["http://attacker.example/callback"],
        "grant_types": ["authorization_code", "refresh_token"]}).status_code == 400
    assert client.post("/token", data={"client_id": "missing", "grant_type": "refresh_token", "refresh_token": "missing"}).status_code == 401
    assert client.post("/mcp", headers={"Authorization": "Bearer igpsport-fixture-token"}, json={}).status_code == 401


def test_public_registration_is_rate_limited(oauth):
    _, client, _ = oauth
    for _ in range(10):
        register(client)
    assert client.post("/register", json={}).status_code == 429


def test_revocation_invalidates_both_access_and_refresh(oauth):
    provider, client, _ = oauth
    registered = register(client, "client_secret_post")
    tokens = client.post("/token", data=token_data(registered, approve(client, begin(client, registered)))).json()
    response = client.post("/revoke", data={"client_id": registered["client_id"],
        "client_secret": registered["client_secret"], "token": tokens["access_token"]})
    assert response.status_code == 200, response.text
    assert asyncio.run(provider.load_access_token(tokens["access_token"])) is None
    assert client.post("/token", data={"client_id": registered["client_id"],
        "client_secret": registered["client_secret"], "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"]}).status_code == 400


def test_expired_grants_cannot_be_exchanged(oauth):
    provider, client, _ = oauth
    registered = register(client)
    code = approve(client, begin(client, registered))
    with provider.store.transaction() as db:
        db.execute("UPDATE oauth_records SET expires_at=0 WHERE kind='code'")
    assert client.post("/token", data=token_data(registered, code)).status_code == 400
    values = begin(client, registered)
    with provider.store.transaction() as db:
        db.execute("UPDATE oauth_records SET expires_at=0 WHERE kind='pending'")
    assert client.post("/login", data={**values, "username": "owner", "password": PASSWORD,
        "decision": "allow"}).status_code == 400


def test_password_change_revokes_grants_but_preserves_registered_clients(oauth):
    provider, client, key = oauth
    registered = register(client)
    tokens = client.post("/token", data=token_data(registered, approve(client, begin(client, registered)))).json()
    changed = OAuthProvider(provider.base, "owner", hash_password("replacement-password-2026"), key, provider.store.path)
    assert asyncio.run(changed.get_client(registered["client_id"])) is not None
    assert asyncio.run(changed.load_access_token(tokens["access_token"])) is None
    assert client.post("/token", data={"client_id": registered["client_id"], "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"]}).status_code == 400


def test_wrong_storage_key_fails_without_replacing_data(oauth, password_hash):
    provider, client, _ = oauth
    registered = register(client)
    with pytest.raises(RuntimeError, match="storage key"):
        OAuthProvider(provider.base, "owner", password_hash, Fernet.generate_key().decode(), provider.store.path)
    assert asyncio.run(provider.get_client(registered["client_id"])) is not None
