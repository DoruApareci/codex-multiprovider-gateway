# Codex Multi-Provider Gateway  (Windows / Docker)

A local **experimental single-user** multi-provider gateway for the **Codex Windows Desktop app**, designed to:

- Preserve your existing **ChatGPT/Codex subscription** for OpenAI GPT models: Codex sends its ChatGPT bearer token to the localhost gateway; the gateway forwards it **only** to `https://chatgpt.com/backend-api/codex`.
- Use independent API keys for **Z.ai GLM**, **Azure OpenAI**, and **DeepSeek** models.
- Optionally use a separate **LiteLLM** translation container for **Google Gemini** and **Moonshot/Kimi** models, so Codex sees a Responses endpoint.
- Populate the Codex model picker with the **live ChatGPT model catalog** plus your configured external models. Each model has a Codex metadata entry.
- Add providers/models through `providers.yaml`, without editing `gateway.py`.
- Reject unknown model aliases instead of accidentally forwarding them to OpenAI.

This is not a published product or verified against your real Windows Codex authentication. **17 tests pass using mocked upstream APIs**, including SSE forwarding, tool-output payload pass-through, and credential isolation. End-to-end agent/tool behavior for each real model still requires testing in your Windows installation.

## Quick start: GLM + your ChatGPT GPT models

1. If v1 is running on port 8787, stop v1 from its old folder: `docker compose down`. Do not delete your existing Codex config or credential files.
2. Extract this  ZIP into a **new private directory**, for example `C:\codex-multiprovider-gateway-`.
3. Open **Windows PowerShell** in that directory:

```powershell
.\scripts\setup.ps1
# Reads your existing Z_AI_KEY environment variable or prompts for the key.
# Creates .env with a random local gateway key + optional provider variables.
docker compose up -d --build
.\scripts\validate.ps1
```

If PowerShell execution policy blocks your script, run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1` from this project directory, after reviewing the script.

4. Back up your existing Codex configuration:

```powershell
Copy-Item "$env:USERPROFILE\.codex\config.toml" `
  "$env:USERPROFILE\.codex\config.toml.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

5. Merge `config.codex.snippet.toml` into `C:\Users\<your-user>\.codex\config.toml`.
   - Put `model`, `model_provider`, and `model_reasoning_effort` **before the first TOML table**.
   - Remove/comment any old `model_catalog_json` setting: it overrides the live catalog endpoint.
   - Add exactly one `[model_providers.LOCAL_GATEWAY]` table. Keep your existing features, MCP servers, projects and other providers.
   - Use **ChatGPT sign-in** with `requires_openai_auth = true`. Do not put an OpenAI API key on this provider. The localhost key is sent separately in `X-Local-Gateway-Key`.
6. Completely exit and relaunch Codex. Start a **new** chat (or fork an old one), then select GLM-5.3 or an OpenAI GPT model available to your account.

The provider used by the app is:

```toml
model_provider = "LOCAL_GATEWAY"
model = "glm-5.3"

[model_providers.LOCAL_GATEWAY]
name = "Local Multi-Provider Gateway"
base_url = "http://127.0.0.1:8787/backend-api/codex"
model_catalog_url = "http://127.0.0.1:8787/backend-api/codex/models"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false
env_http_headers = { "X-Local-Gateway-Key" = "CODEX_LOCAL_GATEWAY_KEY" }
```

**Why this is not ordinary LiteLLM-only setup:** Your OpenAI traffic remains backed by your **ChatGPT subscription** and OpenAI's ChatGPT Codex backend. It does not use `api.openai.com/v1` and does not need `OPENAI_API_KEY`. External providers bill through their own API accounts/plans.

## Add Azure OpenAI

Your Azure model must be deployed and must support the **Responses API**. Azure v1 uses the **deployment name** as `model`, not necessarily the GPT product ID.

In Windows PowerShell:

```powershell
.\scripts\set-provider.ps1 -Name AZURE_OPENAI_API_KEY
.\scripts\set-provider.ps1 -Name AZURE_OPENAI_BASE_URL -PlainText
# e.g. https://your-resource.openai.azure.com/openai/v1
.\scripts\set-provider.ps1 -Name AZURE_OPENAI_DEPLOYMENT -PlainText
# e.g. your-actual-deployment-name

docker compose up -d --force-recreate gateway
.\scripts\validate.ps1
```

`providers.yaml` already defines `azure-gpt` using these environment variables. It remains absent from the dynamic catalog until the required settings are filled.

## Add DeepSeek

```powershell
.\scripts\set-provider.ps1 -Name DEEPSEEK_API_KEY
docker compose up -d --force-recreate gateway
.\scripts\validate.ps1
```

Configured model aliases (enabled automatically if the DeepSeek key is supplied):
- `deepseek-v4-pro` -> `https://api.deepseek.com/responses`, upstream model `deepseek-v4-pro`
- `deepseek-flash` -> `https://api.deepseek.com/responses`, upstream model `deepseek-flash`

The implementation uses the native DeepSeek Responses API, which currently documents these model identifiers. Verify your account's eligibility and usage prices.

## Add Google Gemini with LiteLLM translator

Google aliases are already enabled in `providers.yaml`. Add a Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey), then start both the gateway and translator:

```powershell
.\scripts\set-provider.ps1 -Name GEMINI_API_KEY
docker compose --profile translation up -d --build --force-recreate
.\scripts\validate.ps1
```

You can also fill the `GEMINI_API_KEY=` entry in `.env` directly. The key is passed only to the translator; the gateway authenticates to it with `LITELLM_MASTER_KEY`. Restarting only the gateway does not start the translator or refresh its credentials.

| Codex alias | Google model | Catalog JSON | Reasoning levels (default) |
| --- | --- | --- | --- |
| `google-neon` | `gemini-4-neon` | `catalog/gemini-4-neon.json` | low, medium, high, xhigh (high) |
| `google-carbon` | `gemini-4-carbon` | `catalog/gemini-4-carbon.json` | low, medium, high (medium) |
| `google-hydrogen` | `gemini-4-hydrogen` | `catalog/gemini-4-hydrogen.json` | low, medium, high, xhigh (high) |

All three catalogs expose text/image input and a unified 1,048,576-token context window. Audio, video, PDF input, Google's built-in tools, and long-decode extensions are not enabled by these catalogs. Parallel tool calls and reasoning summaries remain disabled until tested through the adapter. The catalog's default reasoning level can be overridden by Codex's `model_reasoning_effort` setting.

Sources: [Neon](https://aistudio.google.com/docs/models/gemini-4-neon), [Carbon](https://aistudio.google.com/docs/models/gemini-4-carbon), [Hydrogen](https://aistudio.google.com/docs/models/gemini-4-hydrogen), [thinking levels](https://aistudio.google.com/docs/thinking), [LiteLLM Gemini](https://docs.litellm.ai/docs/providers/gemini), and [Responses translation](https://docs.litellm.ai/docs/response_api).

These routes use Gemini API quota/billing. After restarting Codex, test each model with the agent tool loop below. Mocked tests verify gateway routing, credential isolation, and payload forwarding; they do not verify LiteLLM-to-Google compatibility.

Google routes remove Codex's `prompt_cache_key` before forwarding to LiteLLM, because Gemini rejects that parameter. Other request fields remain intact; OpenAI subscription requests retain their cache key. Restart the gateway after changing `providers.yaml`.

## Add Moonshot Kimi with optional LiteLLM translator

Moonshot's Chat Completions API is **not** a Codex Responses API. This  stack optionally uses LiteLLM to translate the two protocols. Translation is less predictable than native Responses, especially for streaming function calls and Codex tools, so **test agents before relying on it**.

1. Provide your Moonshot key:

```powershell
.\scripts\set-provider.ps1 -Name MOONSHOT_API_KEY
```

2. Open `providers.yaml` and change `enabled: false` to `enabled: true` **only** for `kimi-k3` and/or `kimi-k3.1-pro`. The `upstream_model` names must match the aliases in `litellm_config.yaml`.
3. Start the translator profile alongside the gateway:

```powershell
docker compose --profile translation up -d --build
.\scripts\validate.ps1
```

The LiteLLM service is accessible **only on Docker's internal network**, not mapped to your Windows host. Its configuration is in `litellm_config.yaml`. LiteLLM uses the Moonshot API key, and the gateway uses a separate random LiteLLM master key. The gateway does **not** forward the ChatGPT bearer token to LiteLLM.

If LiteLLM cannot support a specific Kimi model's tools, disable it in `providers.yaml` until corrected; listing in a catalog does not guarantee compatibility.

## Add another GLM or any native Responses provider

### New model on an existing provider

Duplicate a `models` entry in `providers.yaml` and give it a **unique Codex-facing slug**:

```yaml
  - slug: my-glm-model
    provider: zai
    upstream_model: VERIFIED_ZAI_MODEL_ID
    metadata_file: catalog/agent-template.json
    display_name: My GLM Model
    enabled: true
```

The gateway substitutes the actual `upstream_model` before sending the request. Edit the metadata template or create a custom metadata JSON if the model has different reasoning, context, modality, or tool support. Do **not** claim a 1M-token context window for a model without provider verification.

### Completely new provider

Add a block under `providers`:

```yaml
  my-provider:
    protocol: responses
    base_url: https://your-provider.example/v1
    auth: bearer
    api_key_env: MY_PROVIDER_KEY
```

Add the credential to `compose.yaml` under `gateway.environment`, then add `MY_PROVIDER_KEY=...` to your local `.env` (not to git), plus a `models` entry referencing `provider: my-provider`. Restart the gateway. Valid `auth` values: `bearer`, `api-key`, and `none`. Only **native Responses** providers can be used directly. For Chat Completions-only providers, configure an adapter like LiteLLM first.

Configuration is loaded on gateway startup; editing YAML alone does not hot-reload the running process.

## Validate routing and agent support

```powershell
# Health and active/disabled models (does not require exposing ChatGPT token):
.\scripts\validate.ps1

# Gateway logs show only model route and HTTP status, not credentials/prompts:
docker compose logs --tail=80 gateway

# If Kimi translation is in use:
docker compose --profile translation logs --tail=80 translator

# Confirm the CLI version. Depending on build, this command may exist:
codex --version
codex debug models
```

After relaunching Codex, the model picker should show the GPT models allowed by your **current ChatGPT account** plus your enabled and configured external aliases. If the dynamic catalog cannot be fetched using your ChatGPT session, the gateway returns the upstream error instead of silently inventing access. The app's own model picker may still have limitations in certain desktop builds.

**Agent test (repeat with GLM, GPT, Azure, DeepSeek, and Kimi as enabled):** Start a new **local** chat with a disposable workspace and ask:

> Use terminal tools to list the workspace, create `gateway-agent-test.txt` containing `agent-ok`, read it back with a tool, and report what you observed. Do not merely suggest commands.

Then request a follow-up edit and confirm the file changes. A text-only answer does **not** prove agent tool compatibility. Confirm command invocation, `function_call` and `function_call_output` handling, the `response.completed` stream event, streaming, conversation continuation, and safe workspace permissions.

## Routing and authentication guarantees

| Route | API path | Authentication |
| --- | --- | --- |
| GPT models returned by account catalog | `https://chatgpt.com/backend-api/codex/responses` | Original ChatGPT bearer + account ID |
| `glm-5.3` | `https://api.z.ai/api/v1/responses` | Z_AI_KEY only |
| `glm-5.3-flash` | `https://api.z.ai/api/v1/responses` | Z_AI_KEY only |
| `azure-gpt` | `${AZURE_OPENAI_BASE_URL}/responses` | Azure `api-key` only |
| `deepseek-*` | `https://api.deepseek.com/responses` | DeepSeek bearer only |
| `google-*` | `http://translator:4000/v1/responses` | Internal LiteLLM master key only; translator uses GEMINI_API_KEY |
| Kimi (opt-in) | `http://translator:4000/v1/responses` | Internal LiteLLM master key only |

The gateway never sends the incoming `Authorization` or `ChatGPT-Account-ID` headers to external providers. It also never forwards the localhost key upstream. The ChatGPT base URL is restricted to the official backend in code even if you edit `providers.yaml`. External model slugs must be predeclared; unknown slugs are checked against your authenticated OpenAI catalog and rejected if not available.

The local shared key and provider credentials live in `.env` as **plaintext**. Restrict this directory to your Windows user, never commit/upload it, and do not expose port 8787 to other devices. Docker binds the gateway only to `127.0.0.1:8787`. Plain HTTP on localhost is for this single-user development setup only. Use HTTPS and a security review for any remote or shared deployment.

## Caveats / troubleshooting

- **OpenAI account availability:** Only models returned by the signed-in ChatGPT catalog are routed to OpenAI. Your ChatGPT subscription access is not transferable to other services.
- **Older threads:** The Codex app may keep the original provider in old conversations. Fork or create a new chat for the gateway provider.
- **Model picker:** `model_catalog_json` overrides `model_catalog_url`; remove it. Ensure the Windows desktop process inherits `CODEX_LOCAL_GATEWAY_KEY` (relaunch it fully).
- **HTTP 401 local gateway:** Wrong local key or missing ChatGPT sign-in. `scripts/validate.ps1` only checks the key and health, not your authenticated ChatGPT session.
- **HTTP 401/403 upstream:** Provider credentials or account permission. Logs show route/status, not tokens.
- **HTTP 404 unknown model:** The model is not enabled/configured in `providers.yaml` or not in your account's OpenAI catalog.
- **Kimi tools fail:** Test LiteLLM Responses conversion and provider-specific tool support. Disable that alias if agent calls do not work.
- **Azure 400:** Confirm exact deployment name and that its model supports Responses. Remove unsupported inference arguments only after testing, using optional `strip_fields` on a particular model route.
- **Gateway startup fails:** Inspect `docker compose logs gateway`; YAML syntax, missing local key, or unknown metadata path are common causes.
- **LiteLLM translator image:** The sample uses its documented `latest` image. For repeatable production operation, pin a tested image digest.
- **Rebuild after editing source:** `docker compose up -d --build`. Restart for changed YAML/env: `docker compose up -d --force-recreate gateway`. Optional translator: `--profile translation`.
- **Rollback:** Stop this stack with `docker compose --profile translation down`, restore your backed-up `config.toml`, and relaunch Codex.

## Local unit tests

For developers with Python installed:

```powershell
py -m pip install -r requirements-dev.txt
py -m pytest -q
```

Unit tests use simulated upstream API responses and do not spend provider credits. Passing tests confirm the proxy's behavior, not the real model's tool support or your desktop application's networking/authentication.

## References

- Official Codex gateway: https://learn.chatgpt.com/docs/enterprise/connect-to-a-gateway
- Official Codex compatibility checklist: https://learn.chatgpt.com/docs/enterprise/gateway-compatibility
- Official Codex gateway catalogs: https://learn.chatgpt.com/docs/enterprise/roll-out-a-gateway
- Azure v1 endpoints: https://learn.microsoft.com/en-us/azure/ai-studio/ai-services/concepts/endpoints
- DeepSeek Responses: https://api-docs.deepseek.com/guides/responses_api/
- LiteLLM supported endpoints: https://docs.litellm.ai/docs/supported_endpoints
- LiteLLM Docker: https://docs.litellm.ai/docs/proxy/docker_quick_start
