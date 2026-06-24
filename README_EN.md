# AgentCore Studio

> Drag-drop orchestration · Deploy your AI Agent in one click | From canvas to cloud in minutes

**AgentCore Studio** is a visual orchestration workbench for Amazon Bedrock AgentCore. Drag and drop six core components — Runtime, Memory, Gateway, Identity, Observability, Policy — plus built-in tools (Code Interpreter, Browser) and MCP tools/Skills mounted under Gateway. Assemble an AI Agent like building blocks. Every config change **generates real deployable artifacts in real-time** (entry code, deploy script, IAM policy, component registry). After configuration, **one-click publish to cloud** with true deployment to AWS. Verify with the built-in **Playground**: direct Bedrock connection with second-level feedback locally, true cloud execution remotely.

## Architecture

![AgentCore Studio Architecture](architecture.png)

> Developers drag-drop orchestration in Studio (deployable on App Runner / EC2 / ECS / EKS / locally) → artifacts go through CI/CD (CodeBuild → S3 → ECR) → deploy to Agent Runtime in Amazon Bedrock AgentCore, which orchestrates Memory, Identity, Policy, Observability, Gateway tool layer and built-in sandbox tools. Editable source file: [`architecture.drawio`](architecture.drawio).

## Features

- 🎨 **Visual drag-drop orchestration** of all AgentCore components. Click node to pop edit panel. Dropdown toggles for field linking (e.g., Skill source inline/path/upload, Identity inbound/outbound, Gateway IAM/JWT, Policy Cedar/natural language, Runtime code source ECR/S3)
- 📦 **One-click scenario templates**: minimal chat / customer service (with tools) / data analytics / full stack. Paint the canvas in seconds
- 🔗 **Accurate connectors** (Runtime is hub; MCP/Skill hang off Gateway)
- 📄 **Real-time artifact generation**: `agentcore_entry.py` / `deploy.sh` / `iam-policy.json` / `requirements.txt` / `registry.json`
- 📚 **Live Registry**: real-time registration of all components, built-in tools, MCP/Skill
- 🎮 **Playground conversation**: **local direct Bedrock `converse` real model reply** (true if credentials present, fallback to mock) / AWS cloud modes; **edit System Prompt live** — system prompt injected per conversation, tweak one line, see effect immediately, no redeploy
- 🚀 **Publish pipeline trace**: when publishing to cloud, show component publish path, light up each step (pending→in progress→✓), "all publish complete" when ready. Background job + polling (survives stream timeout), live log + deploy timer heartbeat
- ⚡ **Incremental publish (three states)**: each component fingerprint-judged — artifact/code change→full rebuild; config-only change (protocol/timeout/env/description)→`update-agent-runtime` rapid update (reuse artifact, no rebuild, keep ARN); no change→skip. Identity / MCP Target / Policy / Memory / Gateway all support fast in-place update
- ✅ **Smart validation**: incomplete components auto-**skip publish** (Runtime required or blocks). Status bar shows "configured/total"
- ☁️ **One-click true deploy to AWS Bedrock AgentCore** (in-place update, old version serves during rebuild). Auto-detect cloud ready Agent as demo fallback

> Detail: model IDs auto-prefixed with cross-region inference config (`us.`/`eu.`/`apac.`) per region; default region `us-west-2`, default model Claude Sonnet 4.5; region switch auto-clears stale toolkit local state.

## Local Execution

```bash
python3 server.py            # Open http://127.0.0.1:8799
# Custom port / enable access password:
PORT=9000 STUDIO_PASSWORD=yourpass python3 server.py
```

> Backend executes generated code and calls `agentcore` CLI. Binds `127.0.0.1` by default — do not expose to public without auth.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | HTTP server port | `8799` |
| `HOST` | HTTP server bind host | `127.0.0.1` |
| `STUDIO_PASSWORD` | Enable Basic Auth (recommended for public deploy) | unset (no auth) |
| `STUDIO_LANG` | Language: en, fr, zh | `en` |

## Multi-Language Support

AgentCore Studio now supports **English**, **French**, and **Simplified Chinese** (中文).

### Python Backend
- Language set via `STUDIO_LANG` environment variable
- Import `i18n` module: `from i18n import i18n, t`
- Translations in `i18n.py` under `TRANSLATIONS` dict
- Access: `i18n.t("key_name")` or `t("key_name", param1="value")`

### Frontend (JavaScript)
- Include `i18n.js` in HTML
- Language auto-detected from browser locale or `localStorage.language`
- Access: `t("key_name")` or `i18n.t("key_name")`
- Set language: `i18n.setLang("fr")` or `localStorage.setItem("language", "fr")`

### Adding New Strings
1. Add key-value pairs to each language in `i18n.py` and `i18n.js`
2. Use `i18n.t("key_name")` in code
3. Template variables: `i18n.t("region_mismatch", old="us-west-2", target="eu-west-1")`

## Files

| File | Description |
|------|-------------|
| `index.html` | Single-file frontend (fonts inlined, offline-capable) |
| `server.py` | Zero-dependency backend (publish / Playground / deploy / cloud invoke) |
| `i18n.py` | Multi-language module (Python) |
| `i18n.js` | Multi-language module (JavaScript) |
| `Dockerfile` | Container image (built-in agentcore CLI + AWS CLI + zip) — optional, use any container platform |
| `README_EN.md` | English documentation (this file) |
| `README.md` | Chinese documentation |

## Common Dev Tasks

### Debug Playground Issues

1. Check frontend console (network tab for `/api/query` payload)
2. Verify local `workspace/<name>/agentcore_entry.py` exists + valid Python
3. Check `bedrock_reply()` exception handling (Bedrock throttle? auth? model unsupported?)
4. Fallback to `anthropic_reply()` if Bedrock down — requires `ANTHROPIC_API_KEY` env var

### Debug Cloud Deploy

1. Poll `/api/deploy-status?job_id=X&cursor=0` for live log lines
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
- **Cloud**: verify `/api/deploy-status` traces deploy steps → check AWS console for runtime status

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| 502 Bad Gateway (cloud deploy) | Bedrock throttle or permission denied | Check IAM role policy, region availability |
| Playground no reply | `agentcore_entry.py` syntax error or import missing | Check console; inspect generated entry file |
| Region mismatch error | `.bedrock_agentcore.yaml` from old region | `clean_stale_region()` auto-clears on region change |
| "Runtime not found" on delete | Agent ARN region differs from query region | Verify region parameter matches deployment |
| Multi-turn returns empty | History not passed or converse API error | Check `/api/query` payload includes `history` array |
| Wrong language displayed | Browser locale or localStorage conflict | Set `localStorage.setItem("language", "en")` manually |

## Dependencies & Requirements

- **Python 3.7+** (standard library only: json, os, subprocess, http.server, urllib, re, base64, threading, time, queue, shutil, uuid, tempfile)
- **AWS CLI** (for `bedrock-agentcore-control`, `aws s3`, `aws iam`)
- **agentcore CLI** (in container or local PATH for deploy)
- **boto3** (optional, for Bedrock converse; raises RuntimeError if Bedrock selected but boto3 unavailable)
- **anthropic SDK** (optional, fallback to Anthropic API if Bedrock fails)
- **strands-agents** (optional, for local entry point runtime in Playground)
- **playwright** (optional, for Browser tool real web scraping)

Deployed container (Dockerfile) includes all above.

## Git Workflow

Commits are in both English and Chinese for clarity. Recent work:
- Multi-language support (i18n for Python + JavaScript)
- UI layout reordering (Playground → Artifacts → Registry default order)
- Minimal scenario with real web-search (Gateway connector + SigV4 MCP)
- Auto-silent publish on first message (no manual publish button)
- Multi-turn memory (frontend holds history, passed to all backends)
- Incremental deploy (fingerprint-based rebuild/update/skip)
- Prompt caching (strands tool branches cache tool def; converse adds cache point)

Commits are atomic and well-scoped; follow this pattern for new work.

## License

© 2024 Amazon Web Services. See LICENSE for terms.
