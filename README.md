# Cycling MCP

Personal, self-hosted iGPSPORT China cycling activity synchronizer and MCP server.

## Components

- Python 3.11+, httpx, FastMCP, APScheduler, fitdecode, SQLite.
- Syncs ride lists, summary details, laps and training zones from the domestic iGPSPORT web API. Original FIT downloads are optional and disabled by default.
- Stores data locally; MCP returns ride summaries, NP/TSS/IF, laps, zones and sensor metrics with units and data sources (no GPS trace).
- Runs as a single-process Docker service with a Caddy HTTPS front end.
- Public MCP requests require OAuth authorization, with a separate owner login and persistent encrypted tokens.

## Debian 12 VPS: one-command startup

Use a Debian 12 VPS with a DNS hostname pointing to it. Allow incoming TCP ports **80 and 443** in the VPS firewall and provider security group. Check both A and AAAA records: remove an AAAA record if the VPS has no working public IPv6. Ports 80/443 must be available for this project's Caddy service.

```bash
git clone https://github.com/calcao/Cycling-MCP.git
cd Cycling-MCP
sudo bash deploy.sh
```

The first run prompts for your domain, iGPSPORT Token or username/password, and an **authorization management password of at least 16 characters**. The authorization account defaults to `owner`; this login is separate from your iGPSPORT account. You do not need to install Python or copy a WASM file manually.

The script installs Docker Engine and Compose from Docker's official Debian repository if Docker is absent, builds the application image, writes a private `.env`, downloads and SHA256-verifies the pinned WASM, checks iGPSPORT authentication, and starts the application and Caddy. Caddy obtains and renews HTTPS certificates automatically. The script verifies OAuth discovery and an unauthenticated `401` from `/mcp` before reporting success. Existing incompatible Docker packages are reported for manual resolution rather than removed.

Re-running `sudo bash deploy.sh` rebuilds and starts the service while retaining credentials, OAuth client registrations, ride data and certificates. Initial ride synchronization runs in the background; by default it fetches all activity pages and repeats every 60 minutes. FIT export remains optional.

### Connect ChatGPT

In a ChatGPT account/workspace that supports custom remote MCP apps, add this connector URL and choose **OAuth**:

```text
https://your-domain/mcp
```

Leave client ID and client secret blank when using dynamic client registration. During authorization, sign in with `owner` and the authorization management password set by the installer, review the callback address, and allow access. The server supports OAuth authorization code with PKCE S256, protected-resource discovery, dynamic client registration, refresh-token rotation and token revocation. These match the [ChatGPT MCP authentication flow](https://developers.openai.com/plugins/build/auth); a real ChatGPT connection still needs validation against your deployed hostname.

Public clients (`none`) and confidential clients (`client_secret_post`) are supported. A manually registered client can use the same flow. Access tokens last one hour; refresh tokens last 30 days and rotate on use. Restarting the service retains authorization. Reusing a rotated refresh token revokes that authorization's token family. iGPSPORT Tokens cannot authenticate MCP requests.

### Operations and backups

Run these commands from the repository directory:

```bash
sudo docker compose ps
sudo docker compose logs --tail 100 cycling-server caddy
sudo docker compose stop
sudo docker compose start
```

To update, pull the desired revision and run `sudo bash deploy.sh` again. The installer requires a local Docker daemon and uses its `default` context. Existing services sharing this VPS should keep their own ports and Compose projects.

Back up `.env`, `data/`, and the `caddy_data`/`caddy_config` Docker volumes. Stop the services while copying SQLite files for a consistent backup. Treat backups as private: `.env` contains your iGPSPORT credentials and the key used to decrypt `data/oauth.db`. Losing that key loses existing OAuth authorizations. Do not delete volumes with `docker compose down -v` when retaining certificates.

To change the authorization password, run this from the repository directory and enter the new password when prompted:

```bash
sudo bash deploy.sh --reset-password
```

Changing the authorization password revokes existing grants and requires clients to authorize again; their registrations are preserved. Keep `MCP_OAUTH_STORAGE_KEY` unchanged. To switch domains, edit `MCP_DOMAIN` in `.env`, update DNS and rerun the installer; clients must reconnect at the new URL.

For unattended installation, prepare `.env` with a real `MCP_DOMAIN`, iGPSPORT credentials, and either `MCP_ADMIN_PASSWORD_HASH` or an initial `MCP_ADMIN_PASSWORD` supplied to the installer environment, then use `sudo bash deploy.sh --non-interactive`. For the latter, use `sudo --preserve-env=MCP_ADMIN_PASSWORD` with a securely supplied environment variable. The raw authorization password is never written to `.env`; only its scrypt hash is saved. Dollar signs and quotes in credentials remain literal.

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

**Required asset:** `edge_core_wasm_bg-GHC38n8Q.wasm`, SHA256 `7b9d7936b75afc21d0cca11a3eddddea09ecdd782c6064dd1f65d6e674313bfb`. The WASM is not bundled in this repository. The Debian installer downloads the fixed version from the official iGPSPORT site and rejects unexpected hashes. For local use or if that URL becomes unavailable, supply the matching file through `IGPSPORT_WASM_PATH`; Compose handles the read-only mount and container path. Do not commit the binary, credentials, or response data.

Real-account validation covers activity listing, details, laps, zones, API-only SQLite synchronization and local Streamable HTTP MCP queries. Token and username/password authentication paths are covered by synthetic HTTP integration tests. OAuth has HTTP integration coverage for discovery, login approval, PKCE, MCP calls, revocation, restart persistence and refresh-token replay. These checks do not establish successful VPS hosting or a completed ChatGPT connection.

## Security / known limitations

- Never commit `.env`, FIT files, SQLite databases, credentials or GPS data.
- Signed requests use the user-provided WASM-derived key exchange and HMAC-SHA256. Configure IGPSPORT_TOKEN or IGPSPORT_USERNAME/IGPSPORT_PASSWORD locally. Tokens can expire; there is no automatic refresh after expiry.
- The upstream web API is unofficial and may change.
- Public deployment requires HTTPS and a complete OAuth configuration; binding to a non-loopback address without OAuth fails at startup. Port 8000 is only exposed inside the Compose network.
- API fields vary by activity and sensor availability. Missing lap or zone data is returned as unavailable rather than fabricated; per-resource errors remain visible in `sync_errors`.
- Run one application instance only; scheduler and SQLite are not designed for horizontal scaling.
- No automatic user notification for token expiry yet.

## Roadmap

1. Add automatic token renewal and bounded retries for transient failures.
2. Extend optional FIT analysis with time-series tools.
3. Add heart-rate zone analysis, Z2 drift and indoor/outdoor comparison.
