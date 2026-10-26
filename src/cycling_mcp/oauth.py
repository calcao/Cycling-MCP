"""基于 MCP SDK 的单用户 OAuth 授权、审批与持久化。"""

import asyncio
import hashlib
import html
import ipaddress
import secrets
import time
from urllib.parse import parse_qs, quote, urlsplit

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    RefreshToken, RegistrationError, TokenError, construct_redirect_uri,
)
from mcp.server.auth.routes import build_metadata
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import RequestBodyLimitMiddleware
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from .oauth_store import OAuthStore
from .security import normalize_public_url, validate_password_hash, verify_password

SCOPE = "cycling:access"
COOKIE = "__Host-cycling-oauth"
ACCESS_LIFETIME = 3600
REFRESH_LIFETIME = 30 * 86400


class FamilyAccessToken(AccessToken):
    """携带内部令牌族标识的访问令牌。"""

    family: str


class FamilyRefreshToken(RefreshToken):
    """携带内部令牌族标识的刷新令牌。"""

    family: str


class OAuthProvider:
    """向 SDK 提供持久化账户、审批与令牌生命周期。"""

    def __init__(self, public_url, username, password_hash, storage_key, database):
        self.base = normalize_public_url(public_url)
        self.issuer = self.base + "/"
        self.resource = self.base + "/mcp"
        self.username, self.password_hash = username, password_hash
        validate_password_hash(password_hash)
        self.store = OAuthStore(database, storage_key)
        self._password_slots = asyncio.Semaphore(2)
        fingerprint = hashlib.sha256((self.issuer + username + password_hash).encode()).hexdigest()
        with self.store.transaction() as db:
            previous = self.store.get(db, "config", "owner")
            if previous and previous["fingerprint"] != fingerprint:
                db.execute("DELETE FROM oauth_records WHERE kind NOT IN ('client','config')")
            self.store.put(db, "config", "owner", {"fingerprint": fingerprint})

    def auth_settings(self):
        """发布发现、动态注册与撤销端点，并要求资源和 scope 匹配。"""
        return AuthSettings(
            issuer_url=self.issuer, resource_server_url=self.resource, validate_token_resource=True,
            required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
            revocation_options=RevocationOptions(enabled=True),
        )

    async def get_client(self, client_id):
        """读取持久化客户端，保持重启后的客户端身份不变。"""
        with self.store.transaction() as db:
            data = self.store.get(db, "client", client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info):
        """注册 HTTPS 或回环回调客户端，不授予任何骑行数据访问权。"""
        if client_info.token_endpoint_auth_method not in {"none", "client_secret_post"}:
            raise RegistrationError("invalid_client_metadata", "Unsupported token authentication method")
        for value in client_info.redirect_uris:
            uri = urlsplit(str(value))
            try:
                loopback = uri.hostname == "localhost" or ipaddress.ip_address(uri.hostname or "").is_loopback
            except ValueError:
                loopback = False
            if (not uri.hostname or uri.username or uri.password or uri.fragment
                    or (uri.scheme != "https" and not (uri.scheme == "http" and loopback))):
                raise RegistrationError("invalid_redirect_uri", "HTTPS or loopback redirect required")
        with self.store.transaction() as db:
            db.execute("DELETE FROM oauth_records WHERE expires_at IS NOT NULL AND expires_at<=?", (time.time(),))
            count = db.execute("SELECT count(*) FROM oauth_records WHERE kind='client'").fetchone()[0]
            if count >= 1000:
                raise RegistrationError("invalid_client_metadata", "Client registration limit reached")
            self.store.put(db, "client", client_info.client_id, client_info.model_dump(mode="json"))

    async def authorize(self, client, params):
        """创建短期审批请求，只有所有者登录确认后才签发授权码。"""
        if params.resource not in (None, self.resource):
            raise AuthorizeError("invalid_request", "Unknown resource")
        if params.scopes and set(params.scopes) != {SCOPE}:
            raise AuthorizeError("invalid_scope", "Unsupported scope")
        params = params.model_copy(update={"resource": self.resource, "scopes": [SCOPE]})
        identifier, expiry = secrets.token_urlsafe(32), int(time.time()) + 600
        with self.store.transaction() as db:
            self.store.put(db, "pending", identifier, {
                "client_id": client.client_id, "params": params.model_dump(mode="json"),
                "expires_at": expiry,
            }, expiry)
        return self.base + "/login?request=" + identifier

    async def load_authorization_code(self, client, authorization_code):
        """读取绑定客户端、回调、PKCE 挑战和资源的短期授权码。"""
        with self.store.transaction() as db:
            data = self.store.get(db, "code", authorization_code)
        return AuthorizationCode.model_validate(data) if data and data["client_id"] == client.client_id else None

    def _issue(self, db, client_id, scopes, family):
        now = int(time.time())
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        common = {"client_id": client_id, "scopes": scopes, "family": family,
                  "resource": self.resource, "subject": self.username}
        self.store.put(db, "access", access, {**common, "token": access,
                       "expires_at": now + ACCESS_LIFETIME, "claims": {"iss": self.issuer}},
                       now + ACCESS_LIFETIME, family)
        self.store.put(db, "refresh", refresh, {**common, "token": refresh,
                       "expires_at": now + REFRESH_LIFETIME}, now + REFRESH_LIFETIME, family)
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_LIFETIME,
                          refresh_token=refresh, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, authorization_code):
        """事务内一次性消费授权码，避免并发重放。"""
        with self.store.transaction() as db:
            data = self.store.get(db, "code", authorization_code.code, consume=True)
            if not data or data["client_id"] != client.client_id:
                raise TokenError("invalid_grant", "Authorization code already consumed or expired")
            return self._issue(db, client.client_id, data["scopes"], secrets.token_hex(16))

    async def load_refresh_token(self, client, refresh_token):
        """检测已轮换刷新令牌的重放，并撤销其整个令牌族。"""
        with self.store.transaction() as db:
            data = self.store.get(db, "refresh", refresh_token)
            used = self.store.get(db, "refresh_used", refresh_token)
            if used and used["client_id"] == client.client_id:
                self.store.revoke_family(db, used["family"])
        return FamilyRefreshToken.model_validate(data) if data and data["client_id"] == client.client_id else None

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        """轮换访问及刷新令牌，拒绝扩大权限或重复消费刷新令牌。"""
        result = None
        with self.store.transaction() as db:
            data = self.store.get(db, "refresh", refresh_token.token, consume=True)
            if data and data["client_id"] == client.client_id:
                if not set(scopes).issubset(data["scopes"]):
                    raise TokenError("invalid_scope", "Cannot expand granted scopes")
                self.store.put(db, "refresh_used", refresh_token.token,
                               {"client_id": client.client_id, "family": data["family"]},
                               data["expires_at"], data["family"])
                db.execute("DELETE FROM oauth_records WHERE kind='access' AND family=?", (data["family"],))
                result = self._issue(db, client.client_id, scopes, data["family"])
            else:
                used = self.store.get(db, "refresh_used", refresh_token.token)
                if used and used["client_id"] == client.client_id:
                    self.store.revoke_family(db, used["family"])
        if result is None:
            raise TokenError("invalid_grant", "Refresh token already consumed or expired")
        return result

    async def load_access_token(self, token):
        """仅接受本服务签发且仍有效的资源绑定访问令牌。"""
        with self.store.transaction() as db:
            data = self.store.get(db, "access", token)
        return FamilyAccessToken.model_validate(data) if data and data["resource"] == self.resource else None

    async def revoke_token(self, token):
        """撤销访问令牌及其关联刷新令牌。"""
        with self.store.transaction() as db:
            self.store.revoke_family(db, token.family)

    def _page(self, identifier, pending, client, *, error=False):
        csrf = secrets.token_urlsafe(32)
        with self.store.transaction() as db:
            current = self.store.get(db, "pending", identifier)
            if current is None:
                return HTMLResponse("授权请求已失效，请重新连接。", status_code=400)
            current["csrf"] = hashlib.sha256(csrf.encode()).hexdigest()
            self.store.put(db, "pending", identifier, current, current["expires_at"])
        name = html.escape((client.get("client_name") or "MCP 客户端")[:200])
        redirect = html.escape(pending["params"]["redirect_uri"])
        callback = urlsplit(pending["params"]["redirect_uri"])
        callback_origin = callback.scheme + "://" + quote(callback.netloc, safe="[]:.-")
        message = '<p role="alert">登录失败，请检查授权账户与密码。</p>' if error else ""
        response = HTMLResponse(f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>授权 Cycling MCP</title>
<style>body{{font:17px system-ui;background:#f5f5f2;color:#182927;margin:0;padding:8vh 20px}}
main{{max-width:460px;margin:auto;background:white;padding:32px;border-radius:12px}}
input,button{{font:inherit;padding:10px;box-sizing:border-box;width:100%;margin:8px 0 18px}}
button{{background:#174e46;color:white;border:0;border-radius:5px}}small{{overflow-wrap:anywhere}}
button[value=deny]{{background:#edf1ee;color:#182927}}[role=alert]{{color:#a02020}}</style>
<main><h1>授权骑行数据访问</h1><p><strong>{name}</strong> 请求读取骑行数据并触发同步。</p>
<p>授权后返回：<small>{redirect}</small></p>{message}
<form method="post" action="/login"><input type="hidden" name="request" value="{identifier}">
<input type="hidden" name="csrf" value="{csrf}"><label>授权账户
<input name="username" autocomplete="username" required value="{html.escape(self.username, quote=True)}"></label>
<label>授权管理密码<input name="password" type="password" autocomplete="current-password" required maxlength="1024"></label>
<button name="decision" value="allow">登录并允许访问</button>
<button name="decision" value="deny" formnovalidate>取消</button></form></main></html>''',
                                status_code=401 if error else 200, headers={
            # Keep the Origin on same-origin form POSTs for the CSRF check below.
            # no-referrer makes browsers send Origin: null for this navigation.
            "Cache-Control": "no-store", "Referrer-Policy": "same-origin", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                f"form-action 'self' {callback_origin}; frame-ancestors 'none'",
        })
        response.set_cookie(COOKIE, csrf, secure=True, httponly=True, samesite="lax", max_age=600, path="/")
        return response

    async def login(self, request):
        """校验浏览器绑定和所有者密码，再执行明确的允许或拒绝操作。"""
        form = await request.form(max_files=0, max_fields=8) if request.method == "POST" else {}
        identifier = str(form.get("request") or request.query_params.get("request", ""))
        with self.store.transaction() as db:
            pending = self.store.get(db, "pending", identifier)
            client = self.store.get(db, "client", pending["client_id"]) if pending else None
        if not pending or not client:
            return HTMLResponse("授权请求已过期，请返回客户端重新连接。", status_code=400)
        if request.method == "GET":
            return self._page(identifier, pending, client)
        if form.get("decision") not in {"allow", "deny"}:
            return HTMLResponse("请明确选择允许或取消。", status_code=400)
        csrf, cookie = str(form.get("csrf", "")), request.cookies.get(COOKIE, "")
        origin = request.headers.get("origin")
        if (not csrf or not secrets.compare_digest(csrf.encode(), cookie.encode())
                or not secrets.compare_digest(hashlib.sha256(csrf.encode()).hexdigest(), pending.get("csrf", ""))
                or (origin is not None and origin != self.base)):
            return HTMLResponse("授权请求校验失败，请重新连接。", status_code=400)
        if form.get("decision") != "deny":
            async with self._password_slots:
                valid = await run_in_threadpool(verify_password, str(form.get("password", "")), self.password_hash)
            if not secrets.compare_digest(str(form.get("username", "")).encode(), self.username.encode()) or not valid:
                return self._page(identifier, pending, client, error=True)
        with self.store.transaction() as db:
            current = self.store.get(db, "pending", identifier, consume=True)
            if not current or current.get("csrf") != pending.get("csrf"):
                return HTMLResponse("授权请求已失效。", status_code=400)
            params = AuthorizationParams.model_validate(current["params"])
            if form.get("decision") == "deny":
                destination = construct_redirect_uri(str(params.redirect_uri), error="access_denied", state=params.state)
            else:
                code, expiry = secrets.token_urlsafe(32), int(time.time()) + 120
                grant = AuthorizationCode(code=code, client_id=current["client_id"], scopes=[SCOPE],
                    code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
                    redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                    resource=self.resource, subject=self.username, expires_at=expiry)
                self.store.put(db, "code", code, grant.model_dump(mode="json"), expiry)
                destination = construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)
        response = RedirectResponse(destination, status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response


class OAuthGuardMiddleware(BaseHTTPMiddleware):
    """限制公开认证入口的请求频率，并补齐资源参数和公共客户端元数据。"""

    def __init__(self, app, provider):
        super().__init__(app)
        self.provider = provider

    async def dispatch(self, request, call_next):
        """在 SDK 处理前校验资源参数，避免请求令牌的目标资源被忽略。"""
        path = request.url.path
        limits = {"/register": (10, 3600), "/authorize": (30, 600), "/login": (20, 600),
                  "/token": (120, 60), "/revoke": (60, 60)}
        if path in limits and request.method != "OPTIONS":
            peer = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[-1].strip()
            if not self.provider.store.allow(path + ":" + peer, *limits[path]):
                return JSONResponse({"error": "temporarily_unavailable"}, status_code=429, headers={"Retry-After": "60"})
        if path in {"/authorize", "/token"}:
            params = dict(request.query_params.multi_items())
            if request.method == "POST":
                body = await request.body()
                if len(body) > 16384:
                    return JSONResponse({"error": "invalid_request"}, status_code=413)
                params = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
                resources = params.get("resource", [])
            else:
                resources = request.query_params.getlist("resource")
            if resources and (len(resources) != 1 or resources[0] != self.provider.resource):
                return JSONResponse({"error": "invalid_target"}, status_code=400)
        if path == "/.well-known/oauth-authorization-server" and request.method in {"GET", "OPTIONS"}:
            settings = self.provider.auth_settings()
            data = build_metadata(settings.issuer_url, None, settings.client_registration_options,
                                  settings.revocation_options).model_dump(mode="json", exclude_none=True)
            data["token_endpoint_auth_methods_supported"] = ["none", "client_secret_post"]
            data["revocation_endpoint_auth_methods_supported"] = ["none", "client_secret_post"]
            return JSONResponse(data, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})
        return await call_next(request)


def make_http_app(mcp, provider=None):
    """构造 HTTP 应用，公开部署时始终启用 OAuth、Host 校验和请求限额。"""
    app = mcp.streamable_http_app()
    if provider is not None:
        app.routes.append(Route("/login", provider.login, methods=["GET", "POST"]))
        app.add_middleware(OAuthGuardMiddleware, provider=provider)
        app.add_middleware(RequestBodyLimitMiddleware, max_body_size=1024 * 1024)
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(provider.base).hostname, "127.0.0.1", "localhost"])
    return app
