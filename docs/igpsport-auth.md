# iGPSPORT CN Web login protocol notes

Observed from a sanitized browser request on 2026-10-20:

1. GET `https://prod.zh.igpsport.com/service/edge-core/api/public/timestamp`
2. POST `https://prod.zh.igpsport.com/service/auth/account/login`

The POST JSON contains `appId: "igpsport-web"`, `username` and `password`. The observed headers include:

- `Origin: https://app.igpsport.cn`
- `Referer: https://app.igpsport.cn/`
- `qiwu-app-version: 8.07.08`
- `x-access-key: AKIDWebClient`
- `x-platform: web`
- `x-nonce` (per-request random value)
- `x-timestamp` (time-dependent value)
- `x-signature` (cryptographic signature, algorithm **not yet established**)

## What is and is not established

The public timestamp endpoint may be used to synchronize time for the signature. A single request does not establish how `x-signature` is computed, whether the body participates, which canonicalization is used, or whether the server expects extra session state. Do not replay the captured nonce, timestamp or signature: they may be bound to a particular request.

**Until the signing scheme is validated**, automatic password login is disabled. Use `IGPSPORT_TOKEN` from a locally stored environment variable for the MVP. Token expiry requires manual renewal.

## Investigation checklist

- Inspect the Web JavaScript bundle for the function that constructs `x-signature`; verify against at least two sanitized login requests.
- Determine whether the signature covers method, path, JSON body, nonce and timestamp.
- Inspect the timestamp endpoint response shape, not just the request.
- Confirm whether activity-list and activity-detail GET requests also require signature headers.
- Test authentication with disposable credentials and without logging any passwords or tokens.
- Do not store real passwords, tokens, session cookies or signed requests in GitHub, issues, tests or chat.

The sample login request used for these notes contained credentials; they have **not** been copied into this repository.
