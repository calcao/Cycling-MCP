"""单用户授权所需的密码哈希与公开地址校验。"""

import base64
import hashlib
import hmac
import secrets
from urllib.parse import urlsplit


def _derive(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**17, r=8, p=1,
                          maxmem=256 * 1024 * 1024, dklen=32)


def hash_password(password):
    """使用独立随机盐和 scrypt 保存密码，限制密码长度。"""
    if not 16 <= len(password) <= 1024:
        raise ValueError("Authorization password must contain 16 to 1024 characters")
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt)
    return "scrypt$17$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def validate_password_hash(encoded):
    """校验哈希格式，不接受可改变内存消耗的自定义参数。"""
    try:
        algorithm, cost, salt, digest = encoded.split("$")
        if algorithm != "scrypt" or cost != "17":
            raise ValueError
        salt, digest = base64.b64decode(salt, altchars=b"-_", validate=True), base64.b64decode(digest, altchars=b"-_", validate=True)
        if len(salt) != 16 or len(digest) != 32:
            raise ValueError
        return salt, digest
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid MCP_ADMIN_PASSWORD_HASH; run the deployment setup") from exc


def verify_password(password, encoded):
    """使用恒定时间比较验证密码，不记录密码或派生值。"""
    if not isinstance(password, str) or len(password) > 1024:
        return False
    salt, expected = validate_password_hash(encoded)
    return hmac.compare_digest(_derive(password, salt), expected)


def normalize_public_url(value):
    """要求固定的 HTTPS 站点根地址，禁止凭据、查询参数和子路径。"""
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError("MCP_PUBLIC_URL must be an HTTPS origin, for example https://cycling.example.com")
    return "https://" + parsed.hostname.lower()
