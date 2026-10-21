# Cycling MCP

Personal, self-hosted iGPSPORT China cycling activity synchronizer and MCP server.

## Components

- Python 3.11+, httpx, FastMCP, APScheduler, fitdecode, SQLite.
- Syncs ride lists, summary details, laps and training zones from the domestic iGPSPORT web API. Original FIT downloads are optional and disabled by default.
- Stores data locally; MCP returns ride summaries, NP/TSS/IF, laps, zones and sensor metrics with units and data sources (no GPS trace).
- Runs as a single-process Docker service with a Caddy HTTPS front end.
- **Caddy blocks all public MCP traffic by default** until ChatGPT-compatible authentication is added.

## Local quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
# Edit .env: set IGPSPORT_TOKEN (or username/password) and IGPSPORT_WASM_PATH.
# The WASM may be outside this repository; never commit it.
cycling-sync sync --max-pages 1
cycling-sync list
pytest -q
cycling-server
```

Local Streamable HTTP MCP endpoint: `http://127.0.0.1:8000/mcp`.

Both commands load `.env` from the current working directory. Existing environment variables take precedence. On Windows, use `.venv\Scripts\Activate.ps1` to activate the environment and use forward slashes in the WASM path, for example `D:/workspace/igpsport-analysis/assets/edge_core_wasm_bg-GHC38n8Q.wasm`.

API requests and FIT downloads connect directly and ignore proxy environment variables. The timestamp endpoint uses separate unsigned headers; business requests retain their application headers and HMAC signatures.

Default synchronization saves API data even when a ride has no FIT URL. Set `SYNC_DOWNLOAD_FIT=true` in `.env`, or run `cycling-sync sync --download-fit`, to archive FIT files as well. `--no-download-fit` overrides the environment setting for one run. Optional resource failures produce `partial` status and preserve successfully fetched metadata and previously saved resources. Repeated syncs refresh existing ride metadata instead of skipping rides with an existing FIT file.

`list_activities(limit=20, offset=0)` supports paging through the local archive. `get_activity(activity_id)` returns `summary`, `sensors`, `sensor_sources`, `laps`, `zones` and `sync_errors`. Summary fields retain upstream names and units: distance is in meters, times are in seconds, and sensor speed is in meters per second. Heart rate uses bpm, power uses W, and cadence uses rpm. Zone records preserve upstream thresholds, percentages and optional durations; missing durations are not synthesized.

## Vultr deployment

1. Configure a DNS A record pointing to Vultr.
2. Copy `.env.example` to `.env`; set `MCP_DOMAIN`, `IGPSPORT_TOKEN` and the host's `IGPSPORT_WASM_PATH` locally. Compose mounts the existing WASM file read-only at `/run/igpsport.wasm`; a missing source file causes startup to fail. Never commit tokens or the WASM.
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
- GET `/service/sportg/sporth/activity/queryActivityLapV2?activityId={rideId}` -> lap groups and statistics.
- GET `/service/web-gateway/web-analyze/webActivityAnalyze/queryActivityDetail/{rideId}` -> training zones. If empty, use `hrZoom`, `powerZoom`, `cadZoom` and `spZoom` from the main detail, matching the official page's fallback.
- Optionally download FIT from `fitUrl`. The host allowlist permits igpsport.com domains and the verified `igp-zh.oss-cn-hangzhou.aliyuncs.com` bucket. Downloads are limited to 32 MiB and do not receive account credentials.

## Signed iGPSPORT requests

The request signing flow is implemented in `src/cycling_mcp/signing.py`: load and SHA256-pin the locally provided WASM asset, extract the RSA unwrapping key, fetch the public session key and timestamp, then sign the exact request path/body with HMAC-SHA256. Login and activity API calls use the same signer.

**Required local asset:** `edge_core_wasm_bg-GHC38n8Q.wasm`, SHA256 `7b9d7936b75afc21d0cca11a3eddddea09ecdd782c6064dd1f65d6e674313bfb`. The WASM is not bundled in this repository. Set its local path in `IGPSPORT_WASM_PATH`; Compose handles the read-only mount and container path. Do not commit the binary, credentials, or response data.

Real-account validation covers activity listing, details, laps, zones, API-only SQLite synchronization and local Streamable HTTP MCP queries. Token and username/password authentication paths are also covered by synthetic HTTP integration tests. These checks do not establish production hosting or ChatGPT-facing OAuth support.

## Security / known limitations

- Never commit `.env`, FIT files, SQLite databases, credentials or GPS data.
- Signed requests use the user-provided WASM-derived key exchange and HMAC-SHA256. Configure IGPSPORT_TOKEN or IGPSPORT_USERNAME/IGPSPORT_PASSWORD locally. Tokens can expire; there is no automatic refresh after expiry.
- The upstream web API is unofficial and may change.
- ChatGPT-facing OAuth authentication is **not yet implemented**.
- API fields vary by activity and sensor availability. Missing lap or zone data is returned as unavailable rather than fabricated; per-resource errors remain visible in `sync_errors`.
- Run one application instance only; scheduler and SQLite are not designed for horizontal scaling.
- No automatic user notification for token expiry yet.

## Roadmap

1. Add automatic token renewal and bounded retries for transient failures.
2. Extend optional FIT analysis with time-series tools.
3. Implement ChatGPT-compatible OAuth authentication.
4. Add heart-rate zone analysis, Z2 drift and indoor/outdoor comparison.
