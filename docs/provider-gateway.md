# Provider Gateway

The Provider Gateway (`fluxion-provider`) exposes Fluxion's local agent executors — Claude Code, Codex CLI, Antigravity — behind API endpoints that other coding tools already know how to talk to. A tool that would otherwise call a metered model API calls the gateway instead, and the work runs on a CLI you already pay a subscription for.

It is a **wrap, not a proxy**. Nothing is forwarded to a vendor: the gateway runs a local agent and renders that agent's output as the protocol the caller expected.

Two ingresses are served:

| Endpoint | Protocol | Built against |
| :--- | :--- | :--- |
| `POST /v1/responses` | OpenAI Responses | Codex CLI sub-agents |
| `POST /v1/messages` | Anthropic Messages | Claude Code, and other Messages clients |

---

## Getting Started

### 1. Enable the service

In the Fluxion macOS app, open **Preferences** → **Services** and toggle on **Provider Gateway**.

* **Default port**: `8787` (`http://127.0.0.1:8787`)
* **Security**: binds to loopback only (`127.0.0.1`), with an auto-generated bearer token at `data/provider.token` (mode `0600`).

### 2. Create the token and routing config

```bash
fluxion-provider init
```

This writes the token file and, if you have no routing config yet, a starter `config/provider_routes.json` that routes every role to the local Claude Code CLI. An existing config is never overwritten.

### 3. Check the setup

```bash
fluxion-provider doctor
```

---

## Routing Configuration

`config/provider_routes.json` has three sections.

**`providers`** — which local agents exist. One entry per executor you want to expose:

```json
{
  "id": "local_agy",
  "protocol": "local_agent",
  "executor": "antigravity",
  "enabled": true,
  "default_workspace": "",
  "models": [{ "id": "gemini-3.6-flash-low", "capabilities": { "max_context_tokens": 1000000 } }]
}
```

`default_workspace` is where the agent runs when a request carries no workspace of its own. Leave it empty to have such requests **refused** rather than run against the wrong repository. See [Where the agent runs](#where-the-agent-runs) — the Anthropic ingress always needs this set.

**`policies`** — an ordered candidate list, optionally with fallbacks:

```json
"cheap": {
  "candidates": ["local_agy:gemini-3.6-flash-low"],
  "fallback": ["local_claude:haiku"]
}
```

**`routes`** — which policy each incoming role maps to. The role arrives in the `X-Fluxion-Route` header, which the installed Codex provider entries set per role:

```json
"routes": { "auto": "balanced", "explorer": "cheap", "worker": "balanced" }
```

A request with no such header — every Anthropic Messages request — takes the `auto` route, falling back to `FLUXION_PROVIDER_DEFAULT_POLICY` if `auto` is not mapped.

`config/provider_routes.example.json` shows the fuller shape: several executors, per-role policies, fallbacks.

---

## Client: Codex

### Install

```bash
fluxion-provider install-codex-config
```

This writes a managed block into `~/.codex/config.toml` (one `[model_providers.fluxion_*]` entry per role) plus role files under `~/.codex/agents/`. It shows an interactive diff first and backs up the existing config.

```bash
fluxion-provider print-codex-config    # inspect without writing
fluxion-provider uninstall-codex-config
fluxion-provider rollback-codex-config
```

### You must name the role explicitly

The roles are installed as `fluxion_auto`, `fluxion_explorer`, `fluxion_reviewer`, `fluxion_worker`. The prefix keeps them distinct from Codex's own built-in `explorer` and `worker` roles.

The cost of that is real: asking for **"a worker sub-agent"** in plain language selects Codex's *built-in* worker, which runs on the parent session's provider and never reaches the gateway. Nothing errors — from Codex's point of view nothing went wrong. Say **"use the `fluxion_worker` role"**.

### The parent model must use multi-agent v1

This is the constraint that most often looks like a bug.

Codex has two sub-agent protocols. Under **v2** the spawn payload is encrypted end-to-end, so the delegated task reaches a local agent as an opaque blob. The agent, having received no task, improvises a plausible-looking report — and the parent shows a sub-agent that confidently answered the wrong question, with no error anywhere.

Which protocol is used is decided by the **parent's model**, not by config, and the model's own declaration wins. As of codex-cli 0.145.0:

| Model | Protocol |
| :--- | :--- |
| `gpt-5.6-sol` | v2 — will not work |
| `gpt-5.6-terra` | v2 — will not work |
| `gpt-5.6-luna` | **v1 — works** |
| `codex-auto-review` | v1 |

`~/.codex/models_cache.json` is the source of truth and refreshes itself every 5 minutes, so this table can move. Run the parent session with a v1 model:

```bash
codex -m gpt-5.6-luna
```

`install-codex-config` refuses to write if your config sets `features.multi_agent_v2 = true`, or turns `features.multi_agent` off (which removes `spawn_agent` entirely). It does not write any feature flags of its own — v1 is already the default.

---

## Client: Claude Code (Anthropic Messages)

Point any Anthropic Messages client at the gateway. For Claude Code, use a **separate config directory**:

```bash
CLAUDE_CONFIG_DIR=~/.claude-fluxion \
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 \
ANTHROPIC_AUTH_TOKEN=$(cat data/provider.token) \
  claude
```

Both `authorization: Bearer …` and `x-api-key` authenticate.

Three constraints, all measured rather than assumed:

**The separate `CLAUDE_CONFIG_DIR` is not tidiness.** A Claude Code logged in with a subscription (OAuth) sends *its own OAuth token* to whatever `ANTHROPIC_BASE_URL` points at, and `ANTHROPIC_AUTH_TOKEN` does not override it. Pointing a logged-in Claude Code at any third-party base URL hands that server a live credential. A config directory with no OAuth login authenticates with the gateway token instead.

**Set `default_workspace`.** The Messages protocol has no field for a workspace, so a provider serving this ingress must declare where its agent runs — otherwise every request is refused.

**The endpoint is streaming only.** Claude Code always streams; a client sending `"stream": false` will not get a single-object response.

---

## How a Turn Is Served

### Sticky routes

Each conversation is keyed by whatever identity the ingress can extract — Codex's `thread_id`, Claude Code's `X-Claude-Code-Session-Id` — and that key is remembered in a small SQLite table alongside the provider, the model, the agent session, and the workspace. A conversation stays on the model it started on.

Requests that identify nothing are treated as one-off: sharing a key across unrelated turns would resume a stranger's agent session.

```bash
fluxion-provider routes           # list
fluxion-provider routes --prune   # drop expired rows
```

Rows live 90 days by default (`FLUXION_PROVIDER_STICKY_TTL_HOURS`, `0` disables expiry). They hold routing metadata and a session id — never prompts or answers — and nothing outside the gateway reads them, so pruning does not affect the Fluxion dashboard or task history.

### Session continuity

A follow-up turn resumes the agent session the conversation used last time, so the agent keeps its own memory of the work instead of starting over.

When there is nothing to resume — a first turn, a run that failed before reporting a session id, an expired row, a switch to a different executor — the gateway replays the conversation history that the caller sent, because both protocols are stateless and resend it every turn. Without that replay, a follow-up question would reach an agent that had never seen the earlier turns, with no error to explain the amnesia.

### Where the agent runs

Resolved in this order:

1. **What the request reports.** Codex sends the session's git repo root in its turn metadata.
2. **What this conversation used last time.** Codex reports the workspace only on the turn that *spawns* a sub-agent and omits it on every later message to that sub-agent — so without this, a second message to a live sub-agent would have nowhere to run.
3. **The provider's `default_workspace`.**

If none of the three yields a real directory the turn is refused. Guessing would point an agent at the wrong repository, and it would start editing before anyone noticed.

---

## Known Limits

**The caller's tools never fire.** The gateway emits text only — never `function_call` or `tool_use` events. Every tool runs inside the local agent's own loop, against the same workspace, so the work still happens; it just does not happen through the caller.

For Codex this means the native sub-agent card renders and streams live, but its trace shows narration rather than file reads and commands. For a Messages client it means more: Claude Code declares about 40 tools per request and expects the model to drive them. None will be driven. This ingress serves callers that want an **answer**, not callers that want a model to run their loop.

**Concurrent writers are possible.** Fluxion serializes the local agents it launches against each other, but `spawn_agent` does not block: the main Codex agent keeps running its own tools in the same tree while a sub-agent works, in a separate process with no lock to join. The role files carry advisory text about this, which is the whole of the available mitigation.

**Read-only roles are enforced by the gateway, not by Codex.** `sandbox_mode` in a role file configures Codex's sub-thread, which runs no tools here and therefore constrains nothing. The gateway enforces the declaration itself and refuses to route a read-only role to an executor that cannot run read-only, rather than quietly downgrading — a role advertised as read-only that can still edit the tree is worse than no declaration at all.

**Only local agents.** There is no API-backed upstream and no failover between candidates. A local agent has side effects in a real workspace from its first tool call, so retrying a turn elsewhere would run those effects twice.

---

## Configuration & Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `FLUXION_PROVIDER_ENABLED` | `false` | Enables automatic launch of `fluxion-provider` |
| `FLUXION_PROVIDER_HOST` | `127.0.0.1` | Host IP to bind |
| `FLUXION_PROVIDER_PORT` | `8787` | Port to bind |
| `FLUXION_PROVIDER_TOKEN_FILE` | `data/provider.token` | Bearer auth token file |
| `FLUXION_PROVIDER_CONFIG_FILE` | `config/provider_routes.json` | Routing and policy configuration |
| `FLUXION_PROVIDER_DEFAULT_POLICY` | `balanced` | Policy used when a role maps to nothing |
| `FLUXION_PROVIDER_STICKY_TTL_HOURS` | `2160` (90 days) | How long a conversation stays resumable; `0` disables expiry |
| `FLUXION_PROVIDER_MAX_CONCURRENCY` | `12` | Concurrent in-flight requests |
| `FLUXION_PROVIDER_MAX_REQUEST_BYTES` | `33554432` | Request body size limit |
| `FLUXION_PROVIDER_LOG_BODIES` | `false` | Dump every request body to `data/logs/provider-requests/` |

> `FLUXION_PROVIDER_LOG_BODIES` is a debugging aid for questions like "did the sub-agent actually receive its task?". Captured bodies contain the full task text and any source code in the conversation. Leave it off unless you are actively debugging, and delete the directory afterwards.

---

## Troubleshooting

**The sub-agent answered something unrelated to its task.** The parent is running a v2 model. See [The parent model must use multi-agent v1](#the-parent-model-must-use-multi-agent-v1).

**The sub-agent never ran at all, and nothing errored.** The plain-language role name selected Codex's built-in role. Name `fluxion_worker` explicitly.

**`503 no_route_available` mentioning a workspace.** No workspace could be resolved. Set `default_workspace` on the provider, or run the parent session inside a git repository.

**The agent forgot the earlier part of the conversation.** Check `fluxion-provider routes`: a row showing `cold` has no resumable agent session behind it.

**Nothing reaches the gateway.** `fluxion-provider doctor` checks the token, the port, the routing config, and the bind address.
