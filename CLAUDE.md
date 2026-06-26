# CLAUDE.md

AgentCore Studio — visual drag-drop orchestrator for Amazon Bedrock AgentCore. Single-file backend (`server.py`, 450 lines, zero external deps) + single-file frontend (`index.html`). Generates deployable artifacts (entry code, deploy script, IAM policy, registry) in real-time, publishes to AWS.

## Quick Start

```bash
python3 server.py              # http://127.0.0.1:8799 (default)
PORT=9000 python3 server.py    # Custom port
STUDIO_PASSWORD=x python3 server.py  # Enable Basic Auth
```

Binds `127.0.0.1` only. No auth = local-only. Executes generated agent code via `agentcore` CLI + AWS CLI.

## Architecture

- **Frontend**: `index.html` (canvas, drag/drop, config preview)
- **Backend**: `server.py` (HTTP, project I/O, local runtime, cloud deploy)
- **Target**: Bedrock AgentCore (runtime + memory + gateway + identity + policy + observability)
- **Backends**: Bedrock converse (primary), Anthropic API fallback

## Key Concepts

**Project Model**: User drags components → frontend emits JSON → POST `/api/publish` writes `workspace/<name>/` (entry, deploy.sh, iam-policy.json, registry.json) → `PUBLISHED` dict tracks active projects

**Playground**: POST `/api/query` → run local `agentcore_entry.py` (dynamic import) → fallback chain: Bedrock → Anthropic API → mock response. Supports multi-turn via `history` param.

**Cloud Deploy**: POST `/api/deploy_start` → background job (JOBS dict, poll-based) → runs `deploy.sh` → agentcore CLI creates/updates runtime in AWS. Incremental: fingerprint-based (code change = rebuild; config change = update; no change = skip).

**State Management**:
- `workspace/<name>/` — local project files
- `.bedrock_agentcore.yaml` — agent ARN + ID (persisted)
- `PUBLISHED` — in-memory dict (recreated on restart)

**Region Handling**: Default `us-west-2`, user-selectable. Auto-cleans stale `.bedrock_agentcore/` on region switch → forces full redeploy. Model IDs auto-prefixed with geo (us./eu./apac./us-gov.) for cross-region inference.

**Components**:
- Runtime (strands entrypoint) | Memory (state store) | Gateway (MCP + skills)
- Identity (auth) | Policy (Cedar/NL) | Observability (trace/log)
- Built-in: Code Interpreter, Browser (Playwright CDP), internal tools
- Skills (packaged .zip) | MCP (Web Search, Lambda, custom)

## Code Reference

### `server.py` Functions

| Function | Purpose |
|----------|---------|
| `write_project(name, files)` | Write workspace files |
| `clean_stale_region(d, region)` | Clear `.bedrock_agentcore/` on region mismatch |
| `_find_ready_runtime(region, name)` | Query AWS for READY runtime |
| `bedrock_reply(prompt, cfg, history)` | Bedrock converse (primary) |
| `anthropic_reply(prompt, cfg, sp, history)` | Anthropic API (fallback) |
| `run_agent(name, prompt, sp, history)` | Local `agentcore_entry.py` |
| `invoke_cloud(name, prompt, region, sp, history, session)` | Cloud runtime call |
| `deploy_cloud(name)` | Execute `deploy.sh` |
| `start_deploy_job(name)` | Queue async deploy (non-blocking) |

### HTTP API

| Endpoint | Method | Payload | Behavior |
|----------|--------|---------|----------|
| `/api/publish` | POST | `{name, files, region, type}` | Write workspace, return registry |
| `/api/query` | POST | `{name, prompt, sp, history, source}` | Playground (local/cloud) |
| `/api/deploy_start` | POST | `{name, region}` | Start async cloud deploy |
| `/api/deploy-status` | GET | `?job_id=X&cursor=Y` | Poll deploy progress |
| `/api/delete-runtime` | POST | `{name, region}` | Delete orphaned runtime |
| `/api/invoke-cloud` | POST | `{name, prompt, region, sp, history, session}` | Cloud runtime invoke |

## Dev Tasks

**Debug Playground**:
1. Check frontend console (network → `/api/query` payload)
2. Verify `workspace/<name>/agentcore_entry.py` exists, valid Python
3. Check `bedrock_reply()` exceptions (throttle? auth? unsupported model?)
4. Test `anthropic_reply()` fallback — requires `ANTHROPIC_API_KEY`

**Debug Cloud Deploy**:
1. Poll `/api/deploy-status?job_id=X&cursor=0` for live logs
2. Check `workspace/<name>/.bedrock_agentcore/` state
3. `aws bedrock-agentcore-control list-agent-runtimes --region us-west-2`
4. Verify IAM policy via CloudFormation or `aws iam get-role-policy`

**Multi-Turn Conversation**: Frontend collects `history: [{role, content}, ...]` → POST `/api/query` with `history` → both backends inject into converse. Session ID = hash(project + user session).

**Add Component**: (1) Frontend: add node type in `index.html` (canvas, edit panel) (2) Backend: extend config schema (3) Deploy: update `deploy.sh` (CloudFormation, IAM, env) (4) Registry: update generation logic

## Testing

No automated tests. Manual verification: drag/edit UI → check `workspace/<name>/` files → publish → check Registry. Playground: verify local response + multi-turn + fallback chain (Bedrock → Anthropic → mock). Cloud: verify `/api/deploy-status` traces steps → check AWS console.

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| 502 Bad Gateway (deploy) | Bedrock throttle/denied | Check IAM policy, region availability |
| Playground no reply | `agentcore_entry.py` syntax error | Check console, inspect entry file |
| Region mismatch error | `.bedrock_agentcore.yaml` old region | `clean_stale_region()` auto-clears |
| "runtime not found" | ARN region ≠ query region | Match region param to deployment |
| Multi-turn empty | History not passed | Check `/api/query` payload has `history` |

## Dependencies

- **Python 3.7+** (stdlib: json, os, subprocess, http.server, urllib, re, base64, threading, time, queue, shutil, uuid, tempfile)
- **AWS CLI** (`bedrock-agentcore-control`, `aws s3`, `aws iam`)
- **agentcore CLI** (container or PATH)
- **boto3** (optional, Bedrock converse; raises error if missing + Bedrock selected)
- **anthropic SDK** (optional, API fallback)
- **strands-agents** (optional, local Playground runtime)
- **playwright** (optional, Browser tool web scraping)

Dockerfile includes all.

## Workflow

Commits atomic, well-scoped. Recent:
- Multi-language support (i18n Python/JS: en/fr/zh)
- UI reorder (Playground → Artifacts → Registry default)
- Real web-search (Gateway + SigV4 MCP)
- Auto-silent publish (first msg auto-publishes)
- Multi-turn memory (frontend history → all backends)
- Incremental deploy (fingerprint-based)
- Prompt caching (strands + converse)

## i18n Setup

**Python** (`i18n.py`):
- Import: `from i18n import i18n, t`
- Use: `i18n.t("key")` or `t("key", param="val")`
- Set lang: `i18n.set_lang("fr")`
- Env var: `STUDIO_LANG=en|fr|zh` (default: en)

**Frontend** (`i18n.js`):
- Include: `<script src="i18n.js"></script>`
- Use: `t("key")` or `i18n.t("key")`
- Auto-detect: browser locale or `localStorage.language`
- Set: `i18n.setLang("fr")` or `localStorage.setItem("language", "fr")`

Add strings to both `TRANSLATIONS` dicts (Python/JS). Template vars: `i18n.t("key", old="X", target="Y")`.
