# Cycling MCP

Personal, self-hosted iGPSPORT China cycling activity synchronizer and MCP server.

## Components

- Python 3.11+, httpx, FastMCP, APScheduler, fitdecode, SQLite.
- Syncs ride lists, details and original FIT files from the domestic iGPSPORT web API.
- Stores data locally; MCP returns ride metadata and aggregated sensor metrics (no GPS trace).
- Runs as a single-process Docker service with a Caddy HTTPS front end.
- **Caddy blocks all public MCP traffic by default** until ChatGPT-compatible authentication is added.

## Local quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
export IGPSPORT_TOKEN='your-domestic-web-bearer-token'
cycling-sync sync --max-pages 1
cycling-sync list
pytest -q
cycling-server
```

Local Streamable HTTP MCP endpoint: `http://127.0.0.1:8000/mcp`.

## Vultr deployment

1. Configure a DNS A record pointing to Vultr.
2. Copy `.env.example` to `.env`; set `MCP_DOMAIN` and `IGPSPORT_TOKEN` locally. Never commit tokens.
3. `mkdir -p data && sudo chown -R 10001:10001 data && chmod 700 data`
4. `docker compose up -d --build`
5. Configure a **ChatGPT-compatible authentication mechanism** before replacing the Caddy 403 response with an authenticated reverse proxy to `cycling-server:8000`.
6. Then use `https://your-domain/mcp` in a ChatGPT account that supports custom remote MCP connections.

The public endpoint is deliberately disabled. Do not expose personal ride data without authentication.

## iGPSPORT API reference

Unofficial endpoints, based on [FitSync](https://github.com/Dunky-Z/FitSync/blob/main/src/igpsport_client.py):

- POST `https://prod.zh.igpsport.com/service/auth/account/login`, JSON username/password/appId=`igpsport-web` -> access_token.
- GET `https://prod.zh.igpsport.com/service/web-gateway/web-analyze/activity/queryMyActivity?pageNo=1&pageSize=20&reqType=0&sort=1` -> data.rows.
- GET `https://prod.zh.igpsport.com/service/web-gateway/web-analyze/activity/queryActivityDetail/{rideId}` -> data.fitUrl.
- Download FIT from returned URL. The current host allowlist only permits igpsport.com domains; actual signed download host may differ. Review and explicitly extend allowlist after verifying the real response.

## Security / known limitations

- Never commit `.env`, FIT files, SQLite databases, credentials or GPS data.
- Bearer tokens can expire; optionally configure username/password locally for re-login.
- The upstream web API is unofficial and may change.
- Current FIT download is not streamed and lacks a hard byte limit; do not use against untrusted endpoints.
- ChatGPT-facing OAuth authentication is **not yet implemented**.
- FIT parsing and API schema need real-account validation.
- Run one application instance only; scheduler and SQLite are not designed for horizontal scaling.
- No automatic user notification for token expiry yet.

## Roadmap

1. Validate iGPSPORT response JSON and signed FIT URL host with sanitized browser requests.
2. Add HTTP fixture tests and bounded FIT downloads.
3. Implement ChatGPT-compatible OAuth authentication.
4. Add heart-rate zone analysis, Z2 drift and indoor/outdoor comparison.
