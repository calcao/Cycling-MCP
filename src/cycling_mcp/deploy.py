"""容器内的首次配置引导和部署前校验。"""

import argparse
import getpass
import ipaddress
import os
from pathlib import Path
import re
import sys

from cryptography.fernet import Fernet
from dotenv import dotenv_values, set_key

from .security import hash_password, normalize_public_url, validate_password_hash

WASM_NAME = "edge_core_wasm_bg-GHC38n8Q.wasm"
CONFIG_NAMES = (
    "MCP_DOMAIN", "MCP_PUBLIC_URL", "MCP_ADMIN_USERNAME", "MCP_ADMIN_PASSWORD_HASH", "MCP_OAUTH_STORAGE_KEY",
    "IGPSPORT_TOKEN", "IGPSPORT_USERNAME", "IGPSPORT_PASSWORD", "IGPSPORT_WASM_PATH",
    "SYNC_INTERVAL_MINUTES", "SYNC_PAGES", "SYNC_DOWNLOAD_FIT",
)


class DeploymentError(RuntimeError):
    """可直接展示且不包含凭据的部署错误。"""


def validate_domain(value):
    """仅接受可用于公开 HTTPS 证书的域名，不接受 URL、IP 或 Caddy 配置片段。"""
    if not isinstance(value, str) or not value.strip():
        raise DeploymentError("Set MCP_DOMAIN to the hostname pointing to your VPS")
    try:
        value = value.strip().encode("idna").decode().lower()
    except UnicodeError as exc:
        raise DeploymentError("Invalid MCP_DOMAIN hostname") from exc
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise DeploymentError("MCP_DOMAIN must be a DNS hostname, not an IP address")
    if (len(value) > 253 or "." not in value or value == "cycling.example.com"
            or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                       for label in value.split("."))):
        raise DeploymentError("Set MCP_DOMAIN to the hostname pointing to your VPS")
    return value


def configure(root, *, interactive=True, reset_password=False):
    """保留已有配置，首次安全采集凭据并生成不会重复覆盖的 OAuth 密钥。"""
    path = root / ".env"
    if path.is_symlink():
        raise DeploymentError("Refusing to modify a symbolic-link .env")
    values = dict(dotenv_values(path, interpolate=False, encoding="utf-8-sig")) if path.exists() else {}
    values.update({name: os.environ[name] for name in CONFIG_NAMES if os.environ.get(name)})
    domain = values.get("MCP_DOMAIN", "")
    if not domain or domain == "cycling.example.com":
        if not interactive:
            raise DeploymentError("MCP_DOMAIN is required in .env for non-interactive setup")
        domain = input("VPS domain (for example cycling.your-domain.com): ").strip()
    values["MCP_DOMAIN"] = validate_domain(domain)
    values["MCP_PUBLIC_URL"] = "https://" + values["MCP_DOMAIN"]
    if not values.get("IGPSPORT_TOKEN") and not (values.get("IGPSPORT_USERNAME") and values.get("IGPSPORT_PASSWORD")):
        if not interactive:
            raise DeploymentError("Configure IGPSPORT_TOKEN or IGPSPORT_USERNAME/IGPSPORT_PASSWORD in .env")
        token = getpass.getpass("iGPSPORT Token (hidden; Enter to use username/password): ").strip()
        if token:
            values["IGPSPORT_TOKEN"] = token
        else:
            values["IGPSPORT_USERNAME"] = input("iGPSPORT username: ").strip()
            values["IGPSPORT_PASSWORD"] = getpass.getpass("iGPSPORT password (hidden): ")
            if not values["IGPSPORT_USERNAME"] or not values["IGPSPORT_PASSWORD"]:
                raise DeploymentError("iGPSPORT username and password are required")
    if not values.get("MCP_ADMIN_USERNAME"):
        values["MCP_ADMIN_USERNAME"] = "owner"
    password = os.getenv("MCP_ADMIN_PASSWORD")
    if password or reset_password or not values.get("MCP_ADMIN_PASSWORD_HASH"):
        if not password:
            if not interactive:
                raise DeploymentError("Configure MCP_ADMIN_PASSWORD_HASH or supply MCP_ADMIN_PASSWORD for initial setup")
            password = getpass.getpass("Authorization management password (at least 16 characters): ")
            if password != getpass.getpass("Confirm authorization management password: "):
                raise DeploymentError("Authorization password confirmation does not match")
        try:
            values["MCP_ADMIN_PASSWORD_HASH"] = hash_password(password)
        except ValueError as exc:
            raise DeploymentError("Authorization management password must contain 16 to 1024 characters") from exc
    validate_password_hash(values["MCP_ADMIN_PASSWORD_HASH"])
    if not values.get("MCP_OAUTH_STORAGE_KEY"):
        if (root / "data" / "oauth.db").exists():
            raise DeploymentError("OAuth data already exists; restore MCP_OAUTH_STORAGE_KEY from your .env backup")
        values["MCP_OAUTH_STORAGE_KEY"] = Fernet.generate_key().decode()
    Fernet(values["MCP_OAUTH_STORAGE_KEY"].encode())
    defaults = {"IGPSPORT_WASM_PATH": "assets/" + WASM_NAME,
                "SYNC_INTERVAL_MINUTES": "60", "SYNC_PAGES": "0", "SYNC_DOWNLOAD_FIT": "false"}
    for name, value in defaults.items():
        if not values.get(name):
            values[name] = value
    if any("\n" in str(value) or "\r" in str(value) for value in values.values() if value is not None):
        raise DeploymentError("Configuration values must not contain line breaks")
    if not path.exists():
        path.touch(mode=0o600, exist_ok=False)
    path.chmod(0o600)
    for name in CONFIG_NAMES:
        if values.get(name) is not None:
            set_key(path, name, values[name], quote_mode="always", encoding="utf-8")
    path.chmod(0o600)
    return values


def validate_configuration():
    """校验部署配置、WASM 版本、数据目录写入及本人账号认证。"""
    from .config import Settings
    from .igpsport import IgpsportClient
    from .signing import SigningError, load_unwrapping_key
    from .storage import ActivityStore

    settings = Settings.from_env()
    if not settings.public_url:
        raise DeploymentError("Public deployment requires MCP_PUBLIC_URL and OAuth setup")
    domain = validate_domain(os.getenv("MCP_DOMAIN", ""))
    if normalize_public_url(settings.public_url) != "https://" + domain:
        raise DeploymentError("MCP_DOMAIN and MCP_PUBLIC_URL must identify the same origin")
    validate_password_hash(settings.admin_password_hash)
    Fernet(settings.oauth_storage_key.encode())
    ActivityStore(settings.data_dir)
    load_unwrapping_key(Path(os.environ["IGPSPORT_WASM_PATH"]))
    try:
        with IgpsportClient(settings.token, settings.username, settings.password) as client:
            client.activity_page(1, 1)
    except SigningError as exc:
        raise DeploymentError("iGPSPORT authentication check failed: " + str(exc)) from None
    print("Configuration, WASM and iGPSPORT authentication verified")


def main():
    """供一键部署脚本调用，不将 .env 当作 Shell 代码执行。"""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("configure")
    setup.add_argument("--non-interactive", action="store_true")
    setup.add_argument("--reset-password", action="store_true")
    sub.add_parser("validate")
    sub.add_parser("domain")
    sub.add_parser("wasm-path")
    args = parser.parse_args()
    try:
        if args.command == "configure":
            configure(Path.cwd(), interactive=not args.non_interactive, reset_password=args.reset_password)
            print("Configuration saved to .env; existing credentials and OAuth key retained")
        elif args.command == "validate":
            validate_configuration()
        else:
            values = dotenv_values(Path.cwd() / ".env", interpolate=False, encoding="utf-8-sig")
            if args.command == "domain":
                print(validate_domain(values.get("MCP_DOMAIN", "")))
            else:
                value = values.get("IGPSPORT_WASM_PATH", "assets/" + WASM_NAME)
                if not value or any(character in value for character in ("\n", "\r", "\0")):
                    raise DeploymentError("Invalid IGPSPORT_WASM_PATH")
                print(value)
    except DeploymentError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except (ValueError, OSError) as exc:
        print("Deployment setup failed (" + type(exc).__name__ + "); check .env and filesystem permissions", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
