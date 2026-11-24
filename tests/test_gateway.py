import json
import shutil
from pathlib import Path

import httpx
import pytest
import yaml

import gateway

ROOT = Path(__file__).resolve().parents[1]
KEY = "x" * 40
CHATGPT_TOKEN = "Bearer test-chatgpt-not-a-real-token"

class Chunked(httpx.AsyncByteStream):
    def __init__(self, content):
        self.content = content
    async def __aiter__(self):
        yield self.content
    async def aclose(self):
        pass

def streamed(status, content, content_type='application/json', **headers):
    return httpx.Response(status, headers={'content-type': content_type, **headers},
                          stream=Chunked(content))


def load(**env):
    data = {"GATEWAY_LOCAL_KEY": KEY, "Z_AI_KEY": "zai-private-key", **env}
    return gateway.load_settings(ROOT / "providers.yaml", data)


@pytest.fixture
def settings():
    return load()


def fake_upstream(monkeypatch, handler):
    def client(*, catalog=False):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 follow_redirects=False)
    monkeypatch.setattr(gateway, "make_client", client)


def auth_headers(**kwargs):
    return {"Authorization": CHATGPT_TOKEN, "ChatGPT-Account-ID": "test-account",
            "X-Local-Gateway-Key": KEY, **kwargs}


async def client_for(settings):
    app = gateway.create_app(settings)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local")


@pytest.mark.asyncio
async def test_models_merge_and_openaI_credentials_only_go_to_chatgpt(settings, monkeypatch):
    seen = []
    def handler(request):
        seen.append(request)
        assert request.url.host == "chatgpt.com"
        assert request.headers["authorization"] == CHATGPT_TOKEN
        assert request.headers["chatgpt-account-id"] == "test-account"
        assert "x-local-gateway-key" not in request.headers
        return httpx.Response(200, json={"models": [{"slug": "gpt-6-sol", "display_name": "GPT"}], "etag": "x"})
    fake_upstream(monkeypatch, handler)
    async with await client_for(settings) as client:
        r = await client.get("/backend-api/codex/models?client_version=0.1", headers=auth_headers())
    assert r.status_code == 200
    assert {m["slug"] for m in r.json()["models"]} == {"gpt-6-sol", "glm-5.3", "glm-5.3-flash"}
    flash = next(m for m in r.json()["models"] if m["slug"] == "glm-5.3-flash")
    assert flash["description"] == settings.routes["glm-5.3-flash"].metadata["description"]
    assert "GLM-5.3-Flash" in flash["description"]
    assert flash["input_modalities"] == ["text", "image"]
    assert r.json()["etag"] == "x"
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["glm-5.3", "glm-5.3-flash"])
async def test_zai_streaming_and_credential_isolation(settings, monkeypatch, model):
    events = b'event: response.completed\ndata: {"type":"response.completed"}\n\n'
    def handler(req):
        assert req.url == "https://api.z.ai/api/v1/responses"
        assert req.headers.get("authorization") == "Bearer zai-private-key"
        assert "chatgpt-account-id" not in req.headers
        assert "x-local-gateway-key" not in req.headers
        assert json.loads(req.content)["model"] == model
        return streamed(200, events, 'text/event-stream')
    fake_upstream(monkeypatch, handler)
    async with await client_for(settings) as client:
        result = await client.post("/v1/responses", json={"model": model, "stream": True}, headers=auth_headers())
    assert result.status_code == 200
    assert result.content == events
    assert result.headers["content-type"].startswith("text/event-stream")


@pytest.mark.asyncio
async def test_azure_deployment_remap_and_api_key(monkeypatch):
    cfg = load(AZURE_OPENAI_API_KEY="azure-private-key",
               AZURE_OPENAI_BASE_URL="https://resource.openai.azure.com/openai/v1",
               AZURE_OPENAI_DEPLOYMENT="my-company-gpt-deployment")
    def handler(req):
        assert str(req.url) == "https://resource.openai.azure.com/openai/v1/responses"
        assert req.headers["api-key"] == "azure-private-key"
        assert "authorization" not in req.headers
        assert "chatgpt-account-id" not in req.headers
        assert json.loads(req.content)["model"] == "my-company-gpt-deployment"
        return streamed(200, b'{"id":"resp-1", "output":[]}')
    fake_upstream(monkeypatch, handler)
    async with await client_for(cfg) as client:
        r = await client.post("/v1/responses", json={"model":"azure-gpt"}, headers=auth_headers())
    assert r.status_code == 200
    assert r.json()["id"] == "resp-1"


@pytest.mark.asyncio
async def test_deepseek_uses_responses_api_and_bearer(monkeypatch, tmp_path):
    config = yaml.safe_load((ROOT / "providers.yaml").read_text())
    next(model for model in config["models"] if model["slug"] == "deepseek-v4-pro")["enabled"] = True
    config_path = tmp_path / "providers.yaml"
    config_path.write_text(yaml.safe_dump(config))
    shutil.copytree(ROOT / "catalog", tmp_path / "catalog")
    cfg = gateway.load_settings(config_path, {"GATEWAY_LOCAL_KEY": KEY, "DEEPSEEK_API_KEY": "deepseek-secret"})
    def handler(req):
        assert str(req.url) == "https://api.deepseek.com/responses"
        assert req.headers["authorization"] == "Bearer deepseek-secret"
        assert "chatgpt-account-id" not in req.headers
        assert json.loads(req.content)["model"] == "deepseek-v4-pro"
        return streamed(200, b'{"output":[]}')
    fake_upstream(monkeypatch, handler)
    async with await client_for(cfg) as client:
        r = await client.post("/v1/responses", json={"model":"deepseek-v4-pro"}, headers=auth_headers())
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_unknown_models_rejected_by_chatgpt_responses(settings, monkeypatch):
    requested = []
    def handler(req):
        requested.append(str(req.url))
        assert json.loads(req.content)["model"] == "not-configured"
        return streamed(404, b'{"error":"unknown model"}')
    fake_upstream(monkeypatch, handler)
    async with await client_for(settings) as client:
        r = await client.post("/v1/responses", json={"model":"not-configured"}, headers=auth_headers())
    assert r.status_code == 404
    assert len(requested) == 1
    assert requested[0] == "https://chatgpt.com/backend-api/codex/responses"


@pytest.mark.asyncio
async def test_openai_response_forwarded_byte_for_byte(settings, monkeypatch):
    events = b'event: response.completed\ndata: {"type":"response.completed","response":{"output":[]}}\n\n'
    seen = []
    def handler(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"slug": "gpt-6-sol"}]})
        assert req.url.path.endswith("/responses")
        seen.append(req)
        assert req.headers["authorization"] == CHATGPT_TOKEN
        assert "x-local-gateway-key" not in req.headers
        return streamed(200, events, 'text/event-stream')
    fake_upstream(monkeypatch, handler)
    payload = {"model":"gpt-6-sol", "stream": True,
               "input":[{"type":"function_call_output", "call_id":"abc", "output":"result"}]}
    async with await client_for(settings) as client:
        r = await client.post("/v1/responses", json=payload, headers=auth_headers())
    assert r.content == events
    assert json.loads(seen[0].content) == payload


@pytest.mark.asyncio
async def test_auth_and_upstream_error_status(settings, monkeypatch):
    def handler(req):
        assert req.url.host == "api.z.ai"
        return streamed(429, b'{"error":"quota"}', **{"retry-after": "12"})
    fake_upstream(monkeypatch, handler)
    async with await client_for(settings) as client:
        assert (await client.get("/routes")).status_code == 401
        assert (await client.get("/v1/models", headers={"X-Local-Gateway-Key": KEY})).status_code == 401
        result = await client.post("/v1/responses", json={"model":"glm-5.3"}, headers=auth_headers())
    assert result.status_code == 429
    assert result.headers.get("retry-after") == "12"
    assert result.json()["error"] == "quota"


def test_optional_models_hidden_without_keys(settings):
    assert "glm-5.3" in settings.routes
    assert "azure-gpt" in settings.skipped
    assert "deepseek-v4-pro" in settings.skipped
    assert "kimi-k3" in settings.skipped
    assert "google-neon" in settings.skipped


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["neon", "carbon", "hydrogen"])
async def test_google_translator_route_and_credential_isolation(monkeypatch, name):
    alias = f"google-{name}"
    cfg = load(LITELLM_MASTER_KEY="sk-internal-translator", GEMINI_API_KEY="google-private-key")
    translator = yaml.safe_load((ROOT / "litellm_config.yaml").read_text())
    deployment = next(model for model in translator["model_list"] if model["model_name"] == alias)
    assert deployment["litellm_params"]["model"] == f"gemini/gemini-4-{name}"
    assert deployment["litellm_params"]["api_key"] == "os.environ/GEMINI_API_KEY"
    catalog = gateway.merge_catalog({"models": []}, cfg.routes)
    metadata = next(model for model in catalog["models"] if model["slug"] == alias)
    assert metadata["input_modalities"] == ["text", "image"]
    assert metadata["context_window"] == 1048576
    assert metadata["default_reasoning_level"] in {
        level["effort"] for level in metadata["supported_reasoning_levels"]
    }
    payload = {"model": alias, "stream": True, "reasoning": {"effort": "high"},
               "input": [{"type": "function_call_output", "call_id": "google-call", "output": "agent-ok"}]}
    events = b'event: response.completed\ndata: {"type":"response.completed"}\n\n'

    def handler(request):
        assert str(request.url) == "http://translator:4000/v1/responses"
        assert request.headers["authorization"] == "Bearer sk-internal-translator"
        assert "google-private-key" not in request.headers.values()
        assert "chatgpt-account-id" not in request.headers
        assert "x-local-gateway-key" not in request.headers
        assert json.loads(request.content) == payload
        return streamed(200, events, "text/event-stream")

    fake_upstream(monkeypatch, handler)
    async with await client_for(cfg) as client:
        response = await client.post("/v1/responses", json=payload, headers=auth_headers())
    assert response.status_code == 200
    assert response.content == events


def test_invalid_chatgpt_upstream_rejected(tmp_path):
    content = (ROOT / "providers.yaml").read_text().replace(
        "https://chatgpt.com/backend-api/codex", "https://example.com/stolen")
    (tmp_path / "providers.yaml").write_text(content)
    with pytest.raises(ValueError, match="ChatGPT provider URL"):
        gateway.load_settings(tmp_path / "providers.yaml", {"GATEWAY_LOCAL_KEY": KEY})


def test_invalid_or_duplicate_model_rejected(tmp_path):
    content = (ROOT / "providers.yaml").read_text().replace("  - slug: glm-5.3", "  - slug: glm-5.3", 1)
    # Repeat one configured entry and assert duplicate slug detection happens before metadata load.
    content += '\n  - slug: glm-5.3\n    provider: zai\n    upstream_model: glm-5.3\n    enabled: true\n'
    (tmp_path / "providers.yaml").write_text(content)
    shutil.copytree(ROOT / "catalog", tmp_path / "catalog")
    with pytest.raises(ValueError, match="duplicate"):
        gateway.load_settings(tmp_path / "providers.yaml", {"GATEWAY_LOCAL_KEY": KEY, "Z_AI_KEY": "z"})


def test_catalog_collision_rejected(settings):
    with pytest.raises(ValueError, match="collide"):
        gateway.merge_catalog({"models":[{"slug":"glm-5.3"}]}, settings.routes)

@pytest.mark.asyncio
async def test_optional_kimi_translator_route(monkeypatch, tmp_path):
    content = (ROOT / "providers.yaml").read_text()
    content = content.replace('    upstream_model: kimi-k3\n    metadata_file',
                              '    upstream_model: kimi-k3\n    metadata_file')
    # Enable only the K3 (not K3.1) declaration.
    content = content.replace('    display_name: Kimi K3 (Moonshot via LiteLLM)\n    enabled: false',
                              '    display_name: Kimi K3 (Moonshot via LiteLLM)\n    enabled: true')
    (tmp_path / "providers.yaml").write_text(content)
    shutil.copytree(ROOT / "catalog", tmp_path / "catalog")
    cfg = gateway.load_settings(tmp_path / "providers.yaml", {
        "GATEWAY_LOCAL_KEY": KEY, "LITELLM_MASTER_KEY": "sk-internal-translator"})
    assert "kimi-k3" in cfg.routes
    def handler(req):
        assert str(req.url) == "http://translator:4000/v1/responses"
        assert req.headers["authorization"] == "Bearer sk-internal-translator"
        assert "chatgpt-account-id" not in req.headers
        assert json.loads(req.content)["model"] == "kimi-k3"
        return streamed(200, b'{"output":[]}')
    fake_upstream(monkeypatch, handler)
    async with await client_for(cfg) as client:
        r = await client.post("/v1/responses", json={"model":"kimi-k3"}, headers=auth_headers())
    assert r.status_code == 200


def test_add_provider_model_with_only_yaml_edit(tmp_path):
    content = (ROOT / "providers.yaml").read_text()
    content += '''\n  - slug: my-new-glm\n    provider: zai\n    upstream_model: custom-glm-identifier\n    metadata_file: catalog/agent-template.json\n    display_name: My New GLM\n    enabled: true\n'''
    (tmp_path / "providers.yaml").write_text(content)
    shutil.copytree(ROOT / "catalog", tmp_path / "catalog")
    cfg = gateway.load_settings(tmp_path / "providers.yaml", {
        "GATEWAY_LOCAL_KEY": KEY, "Z_AI_KEY": "zai-key"})
    assert cfg.routes["my-new-glm"].upstream_model == "custom-glm-identifier"
    assert cfg.routes["my-new-glm"].metadata["slug"] == "my-new-glm"
