"""Local Codex Responses gateway: ChatGPT subscription + opt-in API providers.

The client ChatGPT bearer token is sent ONLY to ChatGPT. Third-party credentials
stay on the local gateway. This is an experimental single-user implementation.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx
import yaml

LOG = logging.getLogger("codex-multiprovider-gateway")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
MAX_BODY_BYTES = 32 * 1024 * 1024
TIMEOUT = httpx.Timeout(connect=30.0, read=None, write=90.0, pool=30.0)
CATALOG_TTL = 120
HOP_HEADERS = {
    "host", "connection", "proxy-connection", "proxy-authorization", "proxy-authenticate",
    "keep-alive", "transfer-encoding", "upgrade", "te", "trailer", "content-length",
    "cookie", "set-cookie", "x-local-gateway-key",
}
SAFE_RESPONSE_HEADERS = {
    "content-type", "content-encoding", "cache-control", "x-request-id", "retry-after",
    "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests", "openai-processing-ms", "openai-version",
}
VARIABLE_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


@dataclass(frozen=True)
class Provider:
    name: str
    protocol: str
    base_url: str
    auth: str = "none"
    api_key: str = ""


@dataclass(frozen=True)
class Route:
    slug: str
    provider: str
    upstream_model: str
    metadata: dict[str, Any]
    strip_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    local_key: str
    providers: dict[str, Provider]
    routes: dict[str, Route]
    skipped: dict[str, str]


def expand_env(value: str, env: Mapping[str, str]) -> str:
    return VARIABLE_PATTERN.sub(lambda m: env.get(m.group(1), ""), value)


def load_settings(path: str | Path, environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    local_key = env.get("GATEWAY_LOCAL_KEY", "")
    if len(local_key) < 24:
        raise ValueError("GATEWAY_LOCAL_KEY must contain at least 24 characters")
    cfg_path = Path(path).resolve()
    config = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("providers"), dict):
        raise ValueError("providers.yaml needs a providers map")
    if not isinstance(config.get("models"), list):
        raise ValueError("providers.yaml needs a models list")

    providers: dict[str, Provider] = {}
    for name, spec in config["providers"].items():
        if not isinstance(spec, dict):
            raise ValueError(f"Invalid provider {name}")
        protocol = spec.get("protocol")
        if protocol not in ("chatgpt", "responses"):
            raise ValueError(f"Unsupported protocol on provider {name}")
        auth = spec.get("auth", "none")
        if auth not in ("none", "bearer", "api-key"):
            raise ValueError(f"Unsupported auth method on provider {name}")
        key_var = spec.get("api_key_env", "")
        if key_var and (not isinstance(key_var, str) or not key_var.isidentifier()):
            raise ValueError(f"Invalid api_key_env for {name}")
        base = expand_env(str(spec.get("base_url", "")), env).rstrip("/")
        providers[name] = Provider(name, protocol, base, auth, env.get(key_var, "") if key_var else "")
    if "openai-chatgpt" not in providers or providers["openai-chatgpt"].protocol != "chatgpt":
        raise ValueError("openai-chatgpt provider with protocol chatgpt is required")
    # Restrict ChatGPT bearer token to the official backend, never to YAML-specified hosts.
    if providers["openai-chatgpt"].base_url != "https://chatgpt.com/backend-api/codex":
        raise ValueError("ChatGPT provider URL must be https://chatgpt.com/backend-api/codex")

    routes: dict[str, Route] = {}
    skipped: dict[str, str] = {}
    for item in config["models"]:
        if not isinstance(item, dict):
            raise ValueError("Every models entry must be an object")
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug or slug in routes or slug in skipped:
            raise ValueError(f"Invalid or duplicate model slug: {slug!r}")
        name = item.get("provider")
        if name not in providers or name == "openai-chatgpt":
            raise ValueError(f"Model {slug} has invalid external provider")
        provider = providers[name]
        if item.get("enabled", True) is False:
            skipped[slug] = "disabled"
            continue
        upstream_model = expand_env(str(item.get("upstream_model", "")), env)
        if not upstream_model or upstream_model.startswith("YOUR_"):
            skipped[slug] = "missing_upstream_model"
            continue
        if not provider.base_url or not provider.api_key and provider.auth != "none":
            skipped[slug] = "missing_provider_configuration"
            continue
        parsed = urlsplit(provider.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f"Invalid base_url for provider {name}")
        if parsed.scheme == "http" and parsed.hostname != "translator":
            raise ValueError(f"Plain HTTP upstream permitted only for internal translator, not {name}")
        metadata_filename = item.get("metadata_file")
        if not isinstance(metadata_filename, str):
            raise ValueError(f"Model {slug} missing metadata_file")
        metadata_path = (cfg_path.parent / metadata_filename).resolve()
        if not metadata_path.is_relative_to(cfg_path.parent):
            raise ValueError(f"Model {slug} metadata_file escapes project directory")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError(f"Model {slug} metadata_file must be object")
        metadata = dict(metadata)
        metadata["slug"] = slug
        metadata["upgrade"] = None
        if item.get("display_name"):
            metadata["display_name"] = item["display_name"]
        strip_fields = item.get("strip_fields", [])
        if not isinstance(strip_fields, list) or not all(isinstance(x, str) for x in strip_fields):
            raise ValueError(f"Invalid strip_fields in {slug}")
        routes[slug] = Route(slug, name, upstream_model, metadata, tuple(strip_fields))
    return Settings(local_key, providers, routes, skipped)


def make_client(*, catalog: bool = False) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0) if catalog else TIMEOUT,
                             follow_redirects=False, trust_env=False)


def check_local_key(request: Request, settings: Settings) -> None:
    value = request.headers.get("x-local-gateway-key", "")
    if not value or not hmac.compare_digest(value, settings.local_key):
        raise HTTPException(status_code=401, detail="Invalid local gateway key")


def authorize(request: Request, settings: Settings) -> str:
    check_local_key(request, settings)
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer ") or len(auth) < 15:
        raise HTTPException(status_code=401, detail="ChatGPT sign-in required")
    return auth


def chatgpt_headers(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}


def external_headers(request: Request, provider: Provider) -> dict[str, str]:
    # Never forward caller bearer token, cookies, ChatGPT-Account-ID, Codex headers,
    # or the local shared gateway key to a third-party provider.
    headers = {"Content-Type": "application/json", "Accept": request.headers.get("accept", "application/json")}
    if provider.auth == "bearer":
        headers["Authorization"] = f"Bearer {provider.api_key}"
    elif provider.auth == "api-key":
        headers["api-key"] = provider.api_key
    return headers


def merge_catalog(openai: dict[str, Any], routes: dict[str, Route]) -> dict[str, Any]:
    if not isinstance(openai, dict) or not isinstance(openai.get("models"), list):
        raise ValueError("Invalid Codex model catalog returned by ChatGPT")
    model_list = openai["models"]
    if not all(isinstance(m, dict) and isinstance(m.get("slug"), str) for m in model_list):
        raise ValueError("OpenAI catalog contains invalid model objects")
    existing = {m["slug"] for m in model_list}
    conflicts = existing.intersection(routes)
    if conflicts:
        raise ValueError("External model aliases collide with an OpenAI model")
    return {**openai, "models": model_list + [r.metadata for r in routes.values()]}


def cache_key(request: Request) -> str:
    # Hash is used only as an ephemeral cache lookup; bearer token itself is never retained.
    blob = (request.headers.get("authorization", "") + "\0" +
            request.headers.get("chatgpt-account-id", "")).encode()
    return hashlib.sha256(blob).hexdigest()


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not hasattr(app.state, "settings"):
            app.state.settings = load_settings(os.getenv("CONFIG_PATH", "providers.yaml"))
        LOG.info("Gateway started; registered_external_models=%d", len(app.state.settings.routes))
        yield

    application = FastAPI(title="Local Codex Multi-Provider Gateway v2",
                          docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    if settings is not None:
        application.state.settings = settings
    application.state.cache = {}  # account-scoped short-lived model slug cache

    def current_settings(request: Request) -> Settings:
        return request.app.state.settings

    async def openai_catalog(request: Request) -> tuple[int, dict[str, Any] | None]:
        state = request.app.state
        key = cache_key(request)
        cached = state.cache.get(key)
        if cached is not None and cached[0] > time.monotonic():
            return 200, cached[1]
        headers = chatgpt_headers(request)
        base = current_settings(request).providers["openai-chatgpt"].base_url
        async with make_client(catalog=True) as client:
            response = await client.get(f"{base}/models", headers=headers,
                                        params=list(request.query_params.multi_items()))
        if response.status_code != 200:
            LOG.warning("ChatGPT catalog status=%d", response.status_code)
            return response.status_code, None
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            raise ValueError("Invalid OpenAI catalog response")
        # Remove old entries rather than storing account credentials in memory.
        state.cache = {k: v for k, v in state.cache.items() if v[0] > time.monotonic()}
        if len(state.cache) >= 32:
            state.cache.pop(next(iter(state.cache)))
        state.cache[key] = (time.monotonic() + CATALOG_TTL, payload)
        return 200, payload

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "codex-multiprovider-gateway-v2"}

    @application.get("/routes")
    async def routes(request: Request):
        cfg = current_settings(request)
        check_local_key(request, cfg)
        return {"active": {k: {"provider": v.provider, "upstream_model": v.upstream_model}
                           for k, v in cfg.routes.items()}, "skipped": cfg.skipped,
                "openai": "dynamic ChatGPT-signed-in catalog"}

    @application.get("/backend-api/codex/models")
    @application.get("/v1/models")
    async def models(request: Request):
        cfg = current_settings(request)
        authorize(request, cfg)
        try:
            status, catalog = await openai_catalog(request)
            if status != 200 or catalog is None:
                return JSONResponse(status_code=status, content={"error": "ChatGPT model catalog failed", "upstream_status": status})
            result = merge_catalog(catalog, cfg.routes)
        except (httpx.RequestError, ValueError, json.JSONDecodeError) as exc:
            LOG.error("Catalog error type=%s", type(exc).__name__)
            return JSONResponse(status_code=502, content={"error": "Could not build Codex catalog"})
        LOG.info("Catalog models=%d additional=%d", len(result["models"]), len(cfg.routes))
        return JSONResponse(content=result, headers={"Cache-Control": "no-store"})

    @application.post("/backend-api/codex/responses")
    @application.post("/v1/responses")
    async def responses(request: Request):
        cfg = current_settings(request)
        authorize(request, cfg)
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Body too large")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Expected JSON")
        model = payload.get("model") if isinstance(payload, dict) else None
        if not isinstance(model, str) or not model:
            raise HTTPException(status_code=400, detail="Missing model")

        route = cfg.routes.get(model)
        if route:
            provider = cfg.providers[route.provider]
            upstream = provider.base_url + "/responses"
            headers = external_headers(request, provider)
            # Model aliases and deployment names are not necessarily identical.
            replaced = {**payload, "model": route.upstream_model}
            for field in route.strip_fields:
                replaced.pop(field, None)
            data = json.dumps(replaced, separators=(",", ":")).encode()
            route_name = route.provider
        else:
            # A configured external model must never silently fall back to ChatGPT.
            # In particular, this catches external models skipped for missing keys.
            if model in cfg.skipped:
                LOG.warning("Unavailable external model=%s reason=%s", model, cfg.skipped[model])
                return JSONResponse(status_code=503, content={
                    "error": "Configured external model unavailable",
                    "model": model,
                    "reason": cfg.skipped[model],
                })
            # ChatGPT is the authoritative validator for first-party model access.
            # Do not preflight GET /models on every inference request: catalog reads
            # may fail independently of valid inference credentials or be unavailable
            # without the client's version query parameter. Any unknown GPT model
            # will be rejected by ChatGPT's own /responses endpoint.
            upstream = cfg.providers["openai-chatgpt"].base_url + "/responses"
            headers = chatgpt_headers(request)
            data = body  # preserve OpenAI payload byte-for-byte
            route_name = "openai-chatgpt"
        LOG.info("Responses route=%s model=%s streaming=%s", route_name, model, bool(payload.get("stream")))

        client = make_client()
        try:
            outbound = client.build_request("POST", upstream, content=data, headers=headers)
            response = await client.send(outbound, stream=True)
        except httpx.RequestError as exc:
            await client.aclose()
            LOG.error("Upstream transport failure route=%s error=%s", route_name, type(exc).__name__)
            return JSONResponse(status_code=502, content={"error": "Upstream transport failed", "route": route_name})
        LOG.info("Upstream route=%s status=%d", route_name, response.status_code)
        response_headers = {k: v for k, v in response.headers.items()
                            if k.lower() in SAFE_RESPONSE_HEADERS}

        async def iterator():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()
        return StreamingResponse(iterator(), status_code=response.status_code, headers=response_headers)

    return application


app = create_app()
