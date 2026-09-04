## Problem Statement

Every model call the platform makes today is bound to one vendor, and the only in-spine call is bound to one *purpose*. `vaultos/routing/engines.py` is the spine's entire LLM surface: `_call_haiku()` (engines.py:126–138) posts raw `httpx` to `https://api.anthropic.com/v1/messages` with the model pinned to `HAIKU_MODEL` (engines.py:17), and `_call_ollama()` (engines.py:175–195) posts to Ollama's **native** `/api/chat` with Ollama-only options (`format`, `think`, `keep_alive`). Both functions take `(transcript, state, convo, registry)` and return a routed `{tier, skill, panels}` dict with the router's system prompt baked in by `router_system()` (engines.py:66). There is no "complete this against a schema" primitive underneath them; the provider concept does not exist to be promoted.

The portability thesis needs exactly that primitive: finance match proposals (ADR-0024, weeks 3–4) and later commitment extraction must run on whatever endpoint an employer sanctions — an Anthropic key, an Azure OpenAI deployment, an enterprise gateway, or a local Ollama — authenticated with an API key and a base URL, never a personal CLI login. Without it, the reconciliation demo for Substack 1 (Oct 2) cannot show the one thing it is meant to show: the same data, a different provider, the same proposals.

This spec implements the decision recorded as ADR-0023 (the spine owns a model-provider module; the runner's `claude -p` path is exempt) and tracks issue #30.

## Solution

A new infrastructure package, `vaultos/llm/`, exposing **one operation** — complete a message list against a JSON schema and return validated structured output — behind **two providers** plus `none`: the Anthropic Messages API, and one OpenAI-compatible chat-completions client that covers Ollama (`/v1`), Azure OpenAI, and enterprise gateways alike. Configuration is a provider name, a model, a key, and a base URL; nothing is read from a CLI profile or OAuth store. Every call emits one `llm.completion` event carrying tokens, latency, and outcome, which is what `docs/egress.md` and the Substack operational numbers are built from. The voice router keeps its own two engines untouched; migrating it onto this module is a separate ticket.

## User Stories

1. As finance's proposal code, I want to call `llm.complete(messages, schema)` and get back a dict that already validates against my schema, so that a malformed model reply is an exception at the call site, never a bad proposal row.
2. As an operator on a corporate laptop, I want to point the spine at my employer's sanctioned endpoint by setting four environment variables, so that no code changes and no personal credentials are involved.
3. As an operator with no sanctioned endpoint, I want `VAULTOS_LLM_PROVIDER=none` to make every call fail fast with a named error, so that finance falls back to rules-only matching instead of hanging on a network timeout.
4. As the person writing `docs/egress.md`, I want the module to make exactly one outbound host per provider and none at startup, so that the egress table has one row per provider and the "nothing phones home" claim in `SECURITY.md` stays true.
5. As the person writing Substack 1, I want token counts, latency, provider, and model recorded per call, so that "same data, different provider" comes with numbers.
6. As a developer, I want the test suite to cover both providers without network access or API spend, so that CI stays hermetic (the existing suite's standing rule — see `tests/conftest.py`'s `VOICE_ROUTER` default).
7. As a developer, I want a manual smoke script I can run against a local Ollama and against Anthropic, so that a provider regression is caught before it reaches a finance run.
8. As a reviewer of ADR-0022 conformance, I want `llm` injected through the app context like `conn`, `settings`, and `registry` are today (`vaultos/api/deps.py`), so that modules depend on infrastructure and never construct providers themselves.

## Implementation Decisions

### Interface

- `vaultos/llm/__init__.py` exports `LLM`, `CompletionRequest`, `CompletionResult`, and the error hierarchy. One public method: `LLM.complete(req: CompletionRequest) -> CompletionResult`.
- `CompletionRequest`: `messages: list[{role: "user"|"assistant", content: str}]`, `system: str | None`, `schema: dict` (JSON Schema, object-typed, `additionalProperties: false`), `purpose: str` (a short tag such as `finance.match_proposal`, carried into the event), `max_tokens: int = 4096`, `temperature: float | None = None`.
- `CompletionResult`: `data: dict` (validated), `provider: str`, `model: str`, `input_tokens: int | None`, `output_tokens: int | None`, `latency_ms: int`, `raw_text: str`.
- Errors, all subclasses of `LLMError`: `ProviderUnavailable` (provider `none`, or missing config), `ProviderError` (HTTP/network failure after retries, or a `refusal` stop), `TruncatedOutput` (`stop_reason == "max_tokens"` / `finish_reason == "length"`), `SchemaError` (reply is not JSON, or does not validate).
- **The result is validated locally on every call, regardless of what the provider promises.** Anthropic's `output_config.format` guarantees schema-valid JSON; OpenAI-compatible servers vary — Ollama honors `response_format` json_schema on recent versions and silently ignores it on older ones. One validator (`jsonschema`, Draft 2020-12, a new pure-Python dependency) makes both paths equivalent to the caller. Pydantic cannot validate against an arbitrary schema dict, which is why it is not reused despite already being present via FastAPI.

### Providers

- **`anthropic`** — `POST {base_url}/v1/messages`, headers `x-api-key` and `anthropic-version: 2023-06-01`, body `{model, max_tokens, system, messages, output_config: {format: {type: "json_schema", schema}}}`. The first `text` block is the JSON. `stop_reason` is checked before content is read: `refusal` → `ProviderError`, `max_tokens` → `TruncatedOutput`. Default `base_url` is `https://api.anthropic.com`; overriding it is how an enterprise gateway fronting the Anthropic API is reached. Default model `claude-opus-5`; thinking is left at that model's default (adaptive) and `output_config.effort` is exposed as `VAULTOS_LLM_EFFORT` because extraction workloads often do well at `low`/`medium`.
- **`openai`** — `POST {base_url}/chat/completions`, `Authorization: Bearer <key>` by default, body `{model, messages (system as a role: "system" message), response_format: {type: "json_schema", json_schema: {name, schema, strict: true}}, temperature}`. `choices[0].message.content` is the JSON; `finish_reason == "length"` → `TruncatedOutput`. No default `base_url` or model — Ollama is `http://127.0.0.1:11434/v1` with a local model name, Azure and gateways are whatever the employer hands over. An empty key is legal (Ollama). `VAULTOS_LLM_AUTH_HEADER` (default `Authorization`, value `Bearer <key>`; alternative `api-key`, value `<key>`) covers Azure's classic header without a second provider. **No native Ollama `/api/chat` provider is built** — the `/v1` endpoint covers it, and one client is the point.
- **`none`** — every `complete()` raises `ProviderUnavailable` immediately, before any I/O.
- Both HTTP providers are raw `httpx`, matching the precedent in `engines.py`, sharing one small retry policy (429 and ≥500, two retries, exponential backoff; 4xx otherwise is terminal). The official `anthropic` SDK was considered and rejected for this module specifically: with no explicit key it resolves credentials from `ANTHROPIC_AUTH_TOKEN` and from an `ant auth login` profile on disk — exactly the personal-login path the thesis forbids — and one raw client for both providers keeps the dependency surface and the egress story flat. The interface is the seam; the SDK can be swapped in behind it later if `parse()`/typed errors earn their keep.

### Configuration and wiring

- Read only from a `VAULTOS_LLM_*` namespace, added to `Settings` (`vaultos/config.py:22–31`): `VAULTOS_LLM_PROVIDER` (`none` default — the spine must boot with no model configured), `VAULTOS_LLM_MODEL`, `VAULTOS_LLM_API_KEY`, `VAULTOS_LLM_BASE_URL`, `VAULTOS_LLM_TIMEOUT_S` (default 60), `VAULTOS_LLM_EFFORT`, `VAULTOS_LLM_AUTH_HEADER`. `ANTHROPIC_API_KEY` and `OLLAMA_URL` are **not** read here; they stay the voice router's until it migrates, so "which endpoint is this process using" has one answer per component.
- Missing required config for a non-`none` provider raises `ConfigError` at startup, the same fail-fast contract `VAULT_ROOT` has (`config.py:6–13`).
- Constructed once in `lifespan` (`vaultos/main.py`), stored as `app.state.llm`, injected via a new `get_llm()` in `vaultos/api/deps.py`. Per ADR-0022 this is the `ctx.llm` modules receive; no module imports `vaultos.llm` providers directly.
- **No network at construction.** Constructing a provider validates config only; the first outbound byte is the first `complete()`.

### Events

- Every `complete()` — success or failure — emits one `llm.completion` event: `{ts, provider, model, purpose, input_tokens, output_tokens, latency_ms, ok, error_kind}`. Token counts come from the provider's `usage` block; cost in dollars is deliberately **not** computed here (price tables drift; tokens do not) — derive it at read time.
- The events *table* is ADR-0024's (weeks 3–4). Until it lands, `LLM` takes an `emit: Callable[[dict], None]` at construction whose default writes the same dict through `logging` at INFO. ADR-0024 replaces the default with the table writer without touching this module.

### Where finance calls it

`vaultos/finance/matching.py` stays pure (its docstring, matching.py:1: "Pure functions -- no DB"). Proposal generation for ambiguous matches is new code under ADR-0024 and is the first caller; this spec only guarantees the interface fits it: one request per ambiguous transaction, schema `{candidate_id, confidence, rationale}`, purpose `finance.match_proposal`.

## Testing Decisions

- Both providers isolate their HTTP call in a single `_post()` method, the pattern `engines.py:126` already documents as "isolated for test stubbing". Tests monkeypatch `_post()` with **recorded fixtures**: JSON files under `tests/fixtures/llm/<provider>/` capturing real request/response pairs (redacted) for the happy path, a schema-violating reply, a non-JSON reply, `refusal`, `max_tokens`/`length`, a 429-then-200 retry, and a 500-after-retries failure. No network in CI, no spend.
- Contract tests run the same cases against both providers through the public `complete()` so the two are provably equivalent to a caller.
- Config tests cover: `none` boots and raises on use; a non-`none` provider with a missing model or URL fails at `Settings()`; `VAULTOS_LLM_AUTH_HEADER` switches the header; nothing is read from `ANTHROPIC_API_KEY`.
- Event tests assert one event per call with the correct `ok`/`error_kind` for every fixture above.
- `scripts/llm_smoke.py` (not run by `pytest`) sends one fixed request to whichever provider the environment configures and prints the `CompletionResult` — the manual smoke against Ollama and Anthropic the plan calls for. Run it before any finance run on a new endpoint.

## Out of Scope

- Migrating `routing/engines.py` onto this module (separate ticket; the router keeps `ANTHROPIC_API_KEY`/`OLLAMA_URL` until then).
- Streaming, tool use, multi-turn conversation state, agentic loops — those belong to the `exec` component, not `llm`.
- Prompt caching, batch API, cost-in-USD, a per-provider price table.
- A native Ollama `/api/chat` client (explicitly rejected above).
- Any change to `runner.js` — exempt under ADR-0020/0023.

## Further Notes

Read with ADR-0022 (module contract: `llm` is infrastructure, injected, never imported by a module), ADR-0023 (this decision), and issue #30 (which records the two corrections to the original plan text — `engines.py` is not an abstraction to promote, and the OpenAI-compatible client is new code). `docs/egress.md` (issue #34) should be drafted from this module's two provider rows once it exists, since they will be the only outbound calls the finance module makes.

### Decisions (2026-09-04, resolved)

All three build-gating decisions from the PR #37 body were accepted as proposed:

1. **Raw `httpx` for both providers** — no official `anthropic` SDK (its implicit
   credential resolution is the personal-login path the portability thesis forbids;
   one raw client also covers every OpenAI-compatible gateway with the same pattern).
2. **Local `jsonschema` validation on every call, regardless of provider.**
3. **No native Ollama `/api/chat` client** — `/v1` covers it.

The module is clear to build. See also the engine-seam design (claude-workspace#14):
`exec` is a distinct seam; the `ask`-style cheap-model offload is a calling
convention on this module, not a third seam.
