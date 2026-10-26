#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# 仅在目标 VPS 上执行；配置文件由 Python 解析，不作为 Shell 代码加载。
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
interactive=true
reset_password=false
[[ "$#" -le 1 ]] || { printf 'Use one option at a time.\n' >&2; exit 2; }
case "${1:-}" in
  --non-interactive) interactive=false ;;
  --reset-password) reset_password=true ;;
  --help|-h)
    printf 'Usage: sudo bash deploy.sh [--non-interactive|--reset-password]\nFirst run configures HTTPS, account access and OAuth. Re-running preserves data and credentials.\n'
    exit 0 ;;
  '') ;;
  *) printf 'Unknown argument. Use --help.\n' >&2; exit 2 ;;
esac

die() { printf '%s\n' "$*" >&2; exit 1; }
log() { printf '\n%s\n' "$*"; }
trap 'printf "Deployment failed at line %s. Existing data was retained.\n" "$LINENO" >&2' ERR

if [[ "$EUID" -ne 0 ]]; then
  command -v sudo >/dev/null || die 'Run this script as root or install sudo.'
  exec sudo -- bash "$repo_dir/deploy.sh" "$@"
fi

[[ -r /etc/os-release ]] || die 'Debian 12 is required.'
source /etc/os-release
[[ "${ID:-}" == debian && "${VERSION_ID:-}" == 12 ]] || die 'This installer targets Debian 12 (bookworm).'
cd -- "$repo_dir"
for entry in .env data .deploy.lock; do
  [[ ! -L "$repo_dir/$entry" ]] || die "Refusing symbolic-link deployment entry: $entry"
done
exec 9>"$repo_dir/.deploy.lock"
flock -n 9 || die 'Another deployment is already running.'
owner_uid="$(stat -c %u "$repo_dir")"
owner_gid="$(stat -c %g "$repo_dir")"
docker_cmd=(docker --context default)

log '[1/6] Checking local Docker Engine and Compose'
if ! command -v docker >/dev/null; then
  for package in docker.io docker-compose podman-docker containerd runc; do
    if [[ "$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)" == installed ]]; then
      die "Conflicting package $package is already installed. Resolve it manually; existing services were not removed."
    fi
  done
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
    https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod 0644 /etc/apt/keyrings/docker.asc
  printf 'Types: deb\nURIs: https://download.docker.com/linux/debian\nSuites: bookworm\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
    "$(dpkg --print-architecture)" > /etc/apt/sources.list.d/docker.sources
  apt-get update
  apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
"${docker_cmd[@]}" compose version >/dev/null || die 'Compose v2 is required. Install a compatible Compose plugin, then rerun.'
if ! command -v curl >/dev/null; then
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates curl
fi
systemctl enable --now docker
timeout 30 "${docker_cmd[@]}" info >/dev/null || die 'The local Docker daemon is unavailable.'

log '[2/6] Building the application image'
"${docker_cmd[@]}" build --tag cycling-mcp:local .

log '[3/6] Preparing configuration'
if [[ -f "$repo_dir/.env" ]]; then
  chown "$owner_uid:$owner_gid" "$repo_dir/.env"
  chmod 0600 "$repo_dir/.env"
fi
configure_args=(configure)
if [[ "$reset_password" == true ]]; then
  configure_args+=(--reset-password)
fi
terminal_args=()
if [[ "$interactive" == true ]]; then
  [[ -t 0 ]] || die 'Interactive setup needs a terminal. Preconfigure .env and use --non-interactive for automation.'
  terminal_args=(-i -t)
else
  configure_args+=(--non-interactive)
fi
"${docker_cmd[@]}" run --rm "${terminal_args[@]}" --user "$owner_uid:$owner_gid" --network none --cap-drop ALL \
  --security-opt no-new-privileges:true --mount "type=bind,source=$repo_dir,target=/workspace" \
  --workdir /workspace --env MCP_ADMIN_PASSWORD --entrypoint python cycling-mcp:local \
  -m cycling_mcp.deploy "${configure_args[@]}"
chown "$owner_uid:$owner_gid" "$repo_dir/.env"
chmod 0600 "$repo_dir/.env"

read_setting() {
  "${docker_cmd[@]}" run --rm --user "$owner_uid:$owner_gid" --network none \
    --mount "type=bind,source=$repo_dir,target=/workspace,readonly" --workdir /workspace \
    --entrypoint python cycling-mcp:local -m cycling_mcp.deploy "$1"
}
domain="$(read_setting domain)"
wasm_setting="$(read_setting wasm-path)"
wasm_path="$(realpath -m -- "$wasm_setting")"
wasm_sha='7b9d7936b75afc21d0cca11a3eddddea09ecdd782c6064dd1f65d6e674313bfb'

log '[4/6] Verifying the pinned WASM and persistent data permissions'
if [[ ! -f "$wasm_path" ]]; then
  [[ "$wasm_path" == "$repo_dir/assets/edge_core_wasm_bg-GHC38n8Q.wasm" ]] || \
    die 'The configured external WASM is missing. Copy it to that path or use the default assets path.'
  install -d -m 0755 "$repo_dir/assets"
  wasm_temp="$(mktemp "$repo_dir/assets/.wasm-download.XXXXXX")"
  if ! curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
      --retry 2 --connect-timeout 10 --max-time 120 \
      https://app.igpsport.cn/assets/edge_core_wasm_bg-GHC38n8Q.wasm -o "$wasm_temp"; then
    rm -f -- "$wasm_temp"
    die 'WASM download failed. Supply the pinned file locally and rerun.'
  fi
  if ! printf '%s  %s\n' "$wasm_sha" "$wasm_temp" | sha256sum --check --status; then
    rm -f -- "$wasm_temp"
    die 'Downloaded WASM hash does not match. The file was not installed.'
  fi
  chmod 0644 "$wasm_temp"
  mv -- "$wasm_temp" "$wasm_path"
fi
printf '%s  %s\n' "$wasm_sha" "$wasm_path" | sha256sum --check --status || die 'Existing WASM hash does not match the pinned version.'
install -d -m 0700 "$repo_dir/data"
chown -R --no-dereference 10001:10001 "$repo_dir/data"
find "$repo_dir/data" -type d -exec chmod 0700 {} +
find "$repo_dir/data" -type f -exec chmod 0600 {} +

log '[5/6] Checking configuration and iGPSPORT authentication'
"${docker_cmd[@]}" compose config --quiet
"${docker_cmd[@]}" compose run --rm --no-deps cycling-server python -m cycling_mcp.deploy validate

log '[6/6] Starting HTTPS and the OAuth-protected MCP server'
"${docker_cmd[@]}" compose up --detach --no-build --wait --wait-timeout 120
metadata="$(curl --fail --silent --show-error --retry 12 --retry-all-errors --retry-delay 5 \
  --connect-timeout 3 --max-time 8 "https://$domain/.well-known/oauth-authorization-server")" || \
  die 'HTTPS verification failed. Check DNS A/AAAA records, TCP ports 80/443, and docker compose logs caddy.'
printf '%s' "$metadata" | "${docker_cmd[@]}" run --rm -i --network none --entrypoint python cycling-mcp:local \
  -c 'import json,sys; data=json.load(sys.stdin); assert data["issuer"].rstrip("/")==sys.argv[1]; assert "S256" in data["code_challenge_methods_supported"]; assert data.get("registration_endpoint")' "https://$domain"
status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10 "https://$domain/mcp")"
[[ "$status" == 401 ]] || die 'The MCP endpoint did not return the required OAuth challenge.'
printf '\nReady: https://%s/mcp\nChoose OAuth in ChatGPT and log in with the authorization account configured above.\nInitial ride synchronization runs in the background.\n' "$domain"
