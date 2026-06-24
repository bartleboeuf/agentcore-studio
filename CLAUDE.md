# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**AgentCore Studio** — visual drag-drop orchestrator for Amazon Bedrock AgentCore agents. Single-file backend (`server.py`, 450 lines, zero external deps) + single-file frontend (`index.html`). Generates deployable artifacts (entry code, deploy script, IAM policy, registry) in real-time and publishes to AWS.

**Architecture**:
- Frontend: `index.html` (canvas, drag/drop, real-time config preview)
- Backend: `server.py` (HTTP handler, project I/O, local agent runtime, cloud deploy, Playground invocation)
- Deploy target: Amazon Bedrock AgentCore (agent runtime + memory + gateway + identity + policy + observability)
- Supported backends: Bedrock converse API (primary), Anthropic API fallback (when Bedrock unreachable)

## Running Locally

```bash
python3 server.py              # http://127.0.0.1:8799 (default port 8799)
PORT=9000 python3 server.py    # Custom port
STUDIO_PASSWORD=x python3 server.py  # Enable Basic Auth (recommended for public deployment)
```

Binds `127.0.0.1` only (not public-safe without auth). Executes generated agent code locally via `agentcore` CLI + AWS CLI.

## Key Concepts

### Project Model
- User drags/configures components on canvas → frontend emits JSON config
- POST `/api/publish` writes config to `workspace/<name>/` (entry code, deploy.sh, iam-policy.json, registry.json)
- `PUBLISHED` dict tracks active projects (name → {dir, cfg})

### Playground (Local Agent)
- POST `/api/query` → run local `agentcore_entry.py` (imported + invoked dynamically)
- Falls back: Bedrock → Anthropic API → simulated response
- Supports multi-turn: `history` parameter seeds converse API for context

### Cloud Deploy
- POST `/api/deploy_start` → background job (JOBS dict, poll-based to survive App Runner stream cutoff)
- Job runs `deploy.sh` in workspace → agentcore CLI creates/updates runtime in AWS
- Incremental: fingerprint-based (code change = rebuild; config change = quick update; no change = skip)

### State
- `workspace/<name>/` — local project files (entry, deploy script, registry, etc.)
- `.bedrock_agentcore.yaml` — stored in project dir, tracks deployed agent ARN + ID (persisted across runs)
- `PUBLISHED` — in-memory, populated on first query/deploy per project (re-created on server restart)

### Region Handling
- Default: `us-west-2`. User selectable.
- Auto-cleans stale `.bedrock_agentcore/` dir if region changed → forces full redeploy in new region
- Model names auto-prefixed with geo (us./eu./apac./us-gov.) for cross-region inference

### Component Tools
- **Runtime** — agent entrypoint (strands Python app)
- **Memory** — conversational state store
- **Gateway** — multiplexes MCP tools + skills
- **Identity** — auth/inbound+outbound
- **Policy** — Cedar or natural language auth
- **Observability** — tracing/logging
- **Built-in tools** — Code Interpreter, Browser (Playwright via CDP), internal tools
- **Skills** — packaged as `.zip`, extracted, mounted under Gateway
- **MCP targets** — Web Search, Lambda, custom connectors

## Code Paths

### `server.py` Key Functions

| Function | Purpose |
|----------|---------|
| `write_project(name, files)` | Write workspace files (entry, deploy.sh, etc.) |
| `clean_stale_region(d, region)` | Clear `.bedrock_agentcore/` if region mismatch |
| `_find_ready_runtime(region, name)` | Query AWS for existing READY agent runtime |
| `bedrock_reply(prompt, cfg, history)` | Call Bedrock converse API (primary) |
| `anthropic_reply(prompt, cfg, history)` | Call Anthropic API (fallback) |
| `run_agent(name, prompt, sp, history)` | Run local `agentcore_entry.py` (Playground) |
| `invoke_cloud(name, prompt, region, sp, history, session)` | Call deployed cloud runtime (Bedrock AgentCore) |
| `deploy_cloud(name)` | Execute `deploy.sh` to publish to AWS |
| `start_deploy_job(name)` | Queue background deploy job (non-blocking) |

### HTTP API

| Endpoint | Method | Payload | Behavior |
|----------|--------|---------|----------|
| `/api/publish` | POST | `{name, files, region, type}` | Write workspace, return registry |
| `/api/query` | POST | `{name, prompt, sp, history, source}` | Playground (local/cloud) |
| `/api/deploy_start` | POST | `{name, region}` | Start async cloud deploy job |
| `/api/job_poll` | GET | `?job_id=X&cursor=Y` | Poll deploy progress (non-blocking) |
| `/api/delete_runtime` | POST | `{name, region}` | Delete orphaned agent runtime |
| `/api/cloud_status` | POST | `{name, region}` | Check cloud agent READY status |

## Common Dev Tasks

### Debug Playground Issues
1. Check frontend console (network tab for `/api/query` payload)
2. Verify local `workspace/<name>/agentcore_entry.py` exists + valid Python
3. Check `bedrock_reply()` exception handling (Bedrock throttle? auth? model unsupported?)
4. Fallback to `anthropic_reply()` if Bedrock down — requires `ANTHROPIC_API_KEY` env var

### Debug Cloud Deploy
1. Poll `/api/job_poll?job_id=X&cursor=0` for live log lines
2. Check `workspace/<name>/.bedrock_agentcore/` state (YAML, runtime config)
3. Run `aws bedrock-agentcore-control list-agent-runtimes --region us-west-2` to verify runtime exists
4. Check IAM policy (deployed by `deploy.sh` via CloudFormation or direct `aws iam put-role-policy`)

### Test Multi-Turn Conversation
- Frontend collects `history: [{role: 'user', content: '...'}, {role: 'assistant', content: '...'}]`
- POST `/api/query` with `history` array
- Both `bedrock_reply()` and `anthropic_reply()` inject history into converse messages
- Session ID (for cloud) derived from project name + user session

### Add New Component Type
1. Frontend: add node type in `index.html` (canvas, edit panel)
2. Backend: extend config schema in entry template
3. Deploy: update `deploy.sh` to wire up component (CloudFormation, IAM, env vars)
4. Registry: update generation logic to list component

## Testing Notes

- **No automated tests** — project is single-file backend + frontend verification
- **Manual verification**: drag/edit in UI → check generated files in `workspace/<name>/` → publish → check Registry
- **Playground**: verify local agent response + multi-turn history + fallback chains (Bedrock → Anthropic → mock)
- **Cloud**: verify `/api/job_poll` traces deploy steps → check AWS console for runtime status

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| 502 Bad Gateway (cloud deploy) | Bedrock throttle or permission denied | Check IAM role policy, region availability |
| Playground no reply | `agentcore_entry.py` syntax error or import missing | Check console; inspect generated entry file |
| Region mismatch error | `.bedrock_agentcore.yaml` from old region | `clean_stale_region()` auto-clears on region change |
| "未找到 runtime" on delete | Agent ARN region differs from query region | Verify region parameter matches deployment |
| Multi-turn returns empty | History not passed or converse API error | Check `/api/query` payload includes `history` array |

## Dependencies & Requirements

- **Python 3.7+** (standard library only: json, os, subprocess, http.server, urllib, re, base64, threading, time, queue, shutil, uuid, tempfile)
- **AWS CLI** (for `bedrock-agentcore-control`, `aws s3`, `aws iam`)
- **agentcore CLI** (in container or local PATH for deploy)
- **boto3** (optional, for Bedrock converse; raises RuntimeError if Bedrock selected but boto3 unavailable)
- **anthropic SDK** (optional, fallback to Anthropic API if Bedrock fails)
- **strands-agents** (optional, for local entry point runtime in Playground)
- **playwright** (optional, for Browser tool real web scraping)

Deployed container (Dockerfile) includes all above.

## git Workflow

Commits are in Chinese (feature/fix/ui tags). Recent work:
- UI layout reordering (Playground → Artifacts → Registry default order)
- Minimal scenario with real web-search (Gateway connector + SigV4 MCP)
- Auto-silent publish on first message (no manual publish button)
- Multi-turn memory (frontend holds history, passed to all backends)
- Incremental deploy (fingerprint-based rebuild/update/skip)
- Prompt caching (strands tool branches cache tool def; converse adds cache point)

Commits are atomic and well-scoped; follow this pattern for new work.
