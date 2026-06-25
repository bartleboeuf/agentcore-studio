#!/usr/bin/env python3
"""AgentCore Studio backend — local publish + Playground invocation + optional cloud deploy (zero deps, stdlib only).
启动: python3 server.py  →  http://127.0.0.1:8799  (PORT env var override)
Binds 127.0.0.1 only. Executes generated agent code and agentcore CLI locally, do not expose to public."""
import json, os, sys, types, importlib.util, subprocess, re, base64, threading, time, queue, shutil, uuid, tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from i18n import I18n, set_language

AUTH = os.environ.get("STUDIO_PASSWORD")  # Enable Basic Auth for all HTTP requests if set
LANG = os.environ.get("STUDIO_LANG", "en")  # Language (en, fr, zh)
i18n = I18n(LANG)

ROOT = os.path.dirname(os.path.abspath(__file__))
WS = os.path.join(ROOT, "workspace")
PUBLISHED = {}  # name -> {dir, cfg}
JOBS = {}  # job_id -> {lines:[], done, ok, log, name}  后台部署任务（轮询式，绕开 App Runner 流式掐断）

def write_project(name, files):
    d = os.path.join(WS, name); os.makedirs(d, exist_ok=True)
    for fn, txt in files.items():
        fp = os.path.join(d, fn)
        os.makedirs(os.path.dirname(fp), exist_ok=True)  # 支持 skills/foo.py 等子目录
        with open(fp, "w") as f: f.write(txt)
    return d

def clean_stale_region(d, target_region):
    """If local .bedrock_agentcore.yaml agent_arn region differs from target region,
    clear stale toolkit state so next deploy creates fresh in new region (not cross-region update failure)."""
    if not target_region: return None
    yaml_fp = os.path.join(d, ".bedrock_agentcore.yaml")
    if not os.path.isfile(yaml_fp): return None
    try:
        with open(yaml_fp) as f: content = f.read()
        m = re.search(r"agent_arn:\s*arn:aws:bedrock-agentcore:([a-z0-9-]+):", content)
        if m and m.group(1) != target_region:
            old = m.group(1)
            os.remove(yaml_fp)
            import shutil
            shutil.rmtree(os.path.join(d, ".bedrock_agentcore"), ignore_errors=True)
            return i18n.t("region_mismatch", old=old, target=target_region)
    except Exception:
        pass
    return None

def _find_ready_runtime(region, name):
    """Query AWS directly: check if region has same-named READY agent runtime (independent of local workspace)."""
    if not (region and name): return None
    try:
        r = subprocess.run(["aws", "bedrock-agentcore-control", "list-agent-runtimes",
                            "--region", region, "--output", "json"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0: return None
        rts = (json.loads(r.stdout or "{}")).get("agentRuntimes", [])
        # 先精确匹配名字，再前缀匹配（agentcore 偶尔给 ARN 加后缀，但 name 字段通常是裸名）
        for matcher in (lambda nm: nm == name, lambda nm: nm.startswith(name)):
            for rt in rts:
                if matcher(str(rt.get("agentRuntimeName", ""))) and rt.get("status") == "READY":
                    return {"agent_id": rt.get("agentRuntimeId"), "arn": rt.get("agentRuntimeArn"), "region": region}
    except Exception:
        pass
    return None

def delete_runtime(name, region):
    """Delete agent runtime by name in a region (cleanup after rename); doesn't delete associated Memory."""
    if not (name and region): return {"ok": False, "error": i18n.t("missing_name")}
    try:
        r = subprocess.run(["aws", "bedrock-agentcore-control", "list-agent-runtimes",
                            "--region", region, "--output", "json"], capture_output=True, text=True, timeout=20)
        if r.returncode != 0: return {"ok": False, "error": (r.stderr or i18n.t("list_fail"))[:200]}
        rid = None
        for rt in (json.loads(r.stdout or "{}")).get("agentRuntimes", []):
            nm = str(rt.get("agentRuntimeName", ""))
            if nm == name or nm.startswith(name):
                rid = rt.get("agentRuntimeId"); break
        if not rid: return {"ok": False, "error": i18n.t("runtime_not_found", name=name)}
        dd = subprocess.run(["aws", "bedrock-agentcore-control", "delete-agent-runtime",
                            "--agent-runtime-id", rid, "--region", region], capture_output=True, text=True, timeout=30)
        if dd.returncode != 0: return {"ok": False, "error": (dd.stderr or i18n.t("delete_fail"))[:200]}
        return {"ok": True, "id": rid}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def cloud_status(d, region, name=None):
    """Check if a ready (READY) cloud agent exists for fallback demo.
    Prefer local .bedrock_agentcore.yaml (deployed in this workspace); fallback to AWS lookup by name."""
    if not region: return None
    yaml_fp = os.path.join(d, ".bedrock_agentcore.yaml")
    if os.path.isfile(yaml_fp):
        try:
            content = open(yaml_fp).read()
            m = re.search(r"agent_id:\s*([A-Za-z0-9_\-]+)", content)
            if m:
                aid = m.group(1)
                r = subprocess.run(["aws", "bedrock-agentcore-control", "get-agent-runtime",
                                    "--agent-runtime-id", aid, "--region", region,
                                    "--query", "[status,agentRuntimeArn]", "--output", "text"],
                                   capture_output=True, text=True, timeout=15)
                if r.returncode == 0 and r.stdout.split():
                    parts = r.stdout.split()
                    if parts[0] == "READY":
                        return {"agent_id": aid, "region": region, "arn": parts[1] if len(parts) > 1 else None}
        except Exception:
            pass
    # Fallback: query AWS by name directly
    return _find_ready_runtime(region, name)

def _stub_sdk():
    """Create stub bedrock_agentcore module if real one unavailable (for local Playground)."""
    if "bedrock_agentcore" in sys.modules: return
    try: __import__("bedrock_agentcore")
    except Exception:
        m = types.ModuleType("bedrock_agentcore")
        class _App:
            def entrypoint(self, f): return f
            def run(self, *a, **k): pass
        m.BedrockAgentCoreApp = _App
        sys.modules["bedrock_agentcore"] = m

def bedrock_reply(prompt, cfg, history=None):
    """Call Bedrock converse directly for real model reply. Raises exception if no boto3/credentials/model permission."""
    import boto3
    model = cfg.get("model") or "anthropic.claude-3-5-sonnet-20241022-v2:0"
    region = cfg.get("region") or "us-west-2"
    # Cross-region inference: new models need geo prefix for on-demand calls
    import re as _re
    if not _re.match(r"^(us|eu|apac|us-gov)\.", model):
        geo = "eu." if region.startswith("eu-") else "apac." if region.startswith("ap-") else "us-gov." if region.startswith("us-gov") else "us." if region.startswith("us-") else ""
        model = geo + model
    sp = (cfg.get("system_prompt") or "").strip()
    tools, skills = cfg.get("tools") or [], cfg.get("skills") or []
    if tools or skills:
        tools_str = ', '.join(tools) or i18n.t("no_tools")
        skills_str = ', '.join(skills) or i18n.t("no_tools")
        sp += f"\n({i18n.t('available_tools')}: {tools_str}; {i18n.t('available_skills')}: {skills_str})"
    br = boto3.client("bedrock-runtime", region_name=region)
    msgs = []
    for h in (history or []):
        role = h.get("role"); txt = h.get("content")
        if role in ("user", "assistant") and txt:
            msgs.append({"role": role, "content": [{"text": str(txt)}]})
    msgs.append({"role": "user", "content": [{"text": prompt}]})
    kw = {"modelId": model, "messages": msgs,
          "inferenceConfig": {"maxTokens": 1024, "temperature": 0.7}}
    if sp.strip(): kw["system"] = [{"text": sp.strip()}, {"cachePoint": {"type": "default"}}]
    r = br.converse(**kw)
    return r["output"]["message"]["content"][0]["text"]

def anthropic_reply(prompt, cfg, sp=None, history=None):
    """Fallback when Bedrock unreachable: call Claude via Anthropic-compatible endpoint.
    Requires ANTHROPIC_API_KEY and ANTHROPIC_BASE_URL env vars (SDK auto-reads proxy)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(i18n.t("error_anthropic_key_missing"))
    import anthropic, re as _re
    model = cfg.get("model") or "anthropic.claude-sonnet-4-5-20250929-v1:0"
    m = model
    for pre in ("us.", "eu.", "apac.", "us-gov."):
        if m.startswith(pre): m = m[len(pre):]
    if m.startswith("anthropic."): m = m[len("anthropic."):]
    m = _re.sub(r"-v\d+:\d+$", "", m)  # Strip Bedrock version suffix -v1:0
    m = _re.sub(r"-\d{8}$", "", m)      # Strip date suffix -> use alias like claude-sonnet-4-5
    if not m.startswith("claude"):
        raise RuntimeError(i18n.t("error_invalid_model", model=model))
    system = ((sp if sp is not None else cfg.get("system_prompt")) or "").strip()
    tools, skills = cfg.get("tools") or [], cfg.get("skills") or []
    if tools or skills:
        tools_str = ', '.join(tools) or i18n.t("no_tools")
        skills_str = ', '.join(skills) or i18n.t("no_tools")
        system += f"\n({i18n.t('available_tools')}: {tools_str}; {i18n.t('available_skills')}: {skills_str})"
    client = anthropic.Anthropic()  # 自动读取 ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY
    msgs = []
    for h in (history or []):
        role = h.get("role"); txt = h.get("content")
        if role in ("user", "assistant") and txt:
            msgs.append({"role": role, "content": str(txt)})
    msgs.append({"role": "user", "content": prompt})
    def _call(mid):
        kw = {"model": mid, "max_tokens": 1024, "messages": msgs}
        if system.strip(): kw["system"] = system.strip()
        r = client.messages.create(**kw)
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text") or "（空响应）"
    try:
        return _call(m)
    except Exception:
        if m != "claude-sonnet-4-5":  # 代理可能无此别名 -> 退回已知可用默认
            return _call("claude-sonnet-4-5")
        raise

def run_agent(name, prompt, sp=None, history=None):
    info = PUBLISHED.get(name)
    if not info: return i18n.t("not_published"), "error"
    _stub_sdk()
    entry = os.path.join(info["dir"], info["cfg"].get("entry", "agentcore_entry.py"))
    # 1) 优先真实运行已发布的 entry.py（需框架依赖，如 strands）
    try:
        spec = importlib.util.spec_from_file_location("ac_" + name, entry)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        res = mod.invoke({"prompt": prompt, "history": history or [], **({"system_prompt": sp} if sp else {})})
        out = res.get("result") or res.get("error") or json.dumps(res)
        if out and not res.get("error"): return out, "real"
    except Exception:
        pass
    # 2) 直连 Bedrock converse（有 boto3+凭证+模型权限即返回真实回复）
    try:
        return bedrock_reply(prompt, info["cfg"], history), "real"
    except Exception:
        pass
    # 2.5) Bedrock 不可达时，经 Anthropic 代理端点兜底（ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY）
    try:
        return anthropic_reply(prompt, info["cfg"], sp, history), "real"
    except Exception:
        pass
    # 3) 文本兜底（无依赖/无凭证）
    return fallback(prompt, info["cfg"]), "mock"

def fallback(prompt, cfg):
    sp = (cfg.get("system_prompt") or "").strip()
    persona = f"Based on '{sp}': " if sp else ""
    tools, skills = cfg.get("tools") or [], cfg.get("skills") or []
    tools_str = ', '.join(tools) or i18n.t("no_tools")
    skills_str = ', '.join(skills) or i18n.t("no_tools")
    extra = f"({i18n.t('available_tools')}: {tools_str}; {i18n.t('available_skills')}: {skills_str})" if (tools or skills) else ""
    return f"{persona}Mock response for '{prompt}'. {extra} {i18n.t('mock_response')}"

def deploy_cloud(name):
    """Execute deploy.sh to publish project to AWS."""
    info = PUBLISHED.get(name)
    if not info: return i18n.t("not_published"), False
    try:
        r = subprocess.run(["bash", "deploy.sh"], cwd=info["dir"], capture_output=True, text=True, timeout=900)
        out = (r.stdout + r.stderr)[-6000:] or i18n.t("no_output")
        ok = r.returncode == 0 or i18n.t("deploy_completed") in out or i18n.t("agent_created") in out
        if ok: info["deployed"] = True
        return out, ok
    except Exception as e:
        return f"{i18n.t('deploy_fail')}: {e}", False

def _extract(out):
    """Extract actual agent response from noisy agentcore invoke output."""
    dec = json.JSONDecoder(); i = 0; cand = None; any_d = None
    while True:
        j = out.find("{", i)
        if j < 0: break
        try:
            obj, end = dec.raw_decode(out[j:]); i = j + end
            if isinstance(obj, dict):
                any_d = obj
                if any(k in obj for k in ("result", "response", "output")): cand = obj
        except Exception:
            i = j + 1
    d = cand or any_d
    if isinstance(d, dict):
        return d.get("result") or d.get("response") or d.get("output") or json.dumps(d, ensure_ascii=False)
    # Defense: CLI may wrap JSON by terminal width (insert real newlines), try unwrapping Response section
    m = re.search(r"Response:\s*(\{.*\})", out, re.DOTALL)
    if m:
        try:
            collapsed = re.sub(r"\n", "", m.group(1))
            obj = json.loads(collapsed)
            if isinstance(obj, dict):
                return obj.get("result") or obj.get("response") or obj.get("output") or json.dumps(obj, ensure_ascii=False)
        except Exception:
            pass
    noise = ("suppress_recommendation", "silence this warning", "recommendation", "set agentcore_", "💡", "⚠")
    lines = [l for l in out.splitlines() if l.strip() and not any(k in l.lower() for k in noise)]
    return lines[-1] if lines else i18n.t("no_output")

def _invoke_runtime_arn(arn, region, prompt, sp=None, history=None, session=None):
    """Fallback: invoke cloud runtime by ARN directly via AWS data-plane API, no local workspace needed."""
    import hashlib as _hl
    sid = _hl.sha256(session.encode()).hexdigest() if session else (uuid.uuid4().hex + uuid.uuid4().hex)  # Reuse stable 64-hex session id per session
    payload_b64 = base64.b64encode(json.dumps({"prompt": prompt, "history": history or [], **({"system_prompt": sp} if sp else {})}).encode()).decode()  # Default cli_binary_format=base64
    outpath = None
    try:
        fd, outpath = tempfile.mkstemp(suffix=".out"); os.close(fd)
        r = subprocess.run(["aws", "bedrock-agentcore", "invoke-agent-runtime",
                            "--agent-runtime-arn", arn, "--region", region,
                            "--runtime-session-id", sid,
                            "--content-type", "application/json", "--accept", "application/json",
                            "--payload", payload_b64, outpath],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return (r.stderr or r.stdout).strip()[-1500:] or i18n.t("cloud_deploy_fail"), "cloud-error"
        body = open(outpath, encoding="utf-8", errors="replace").read()
        return _extract(body), "cloud"
    except Exception as e:
        return f"{i18n.t('cloud_deploy_fail')}: {e}", "error"
    finally:
        if outpath and os.path.isfile(outpath):
            try: os.remove(outpath)
            except Exception: pass

def invoke_cloud(name, prompt, region=None, sp=None, history=None, session=None):
    info = PUBLISHED.get(name)
    if not info:
        # No PUBLISHED record (e.g., container restart cleared memory) → still query AWS by name as fallback, avoid false "not published"
        reg = region or "us-west-2"
        cs = _find_ready_runtime(reg, name)
        if cs: return _invoke_runtime_arn(cs["arn"], reg, prompt, sp, history, session)
        return i18n.t("not_published_detail"), "error"
    # Fallback: local workspace has no deploy state (e.g., managed/fresh container), but cloud has ready runtime → call AWS API by ARN
    if not os.path.isfile(os.path.join(info["dir"], ".bedrock_agentcore.yaml")) and info["cfg"].get("deploy_mode") != "harness":
        arn = info.get("cloud_arn"); reg = info.get("cloud_region") or info["cfg"].get("region") or region
        if not arn and reg:
            cs = _find_ready_runtime(reg, name)
            if cs: arn = cs["arn"]; info["cloud_arn"] = arn; info["cloud_region"] = reg
        if arn and reg: return _invoke_runtime_arn(arn, reg, prompt, sp, history, session)
        return i18n.t("not_published_detail"), "error"
    # Harness mode: use agentcore invoke --harness CLI (project in <pn>/ subdir)
    if info["cfg"].get("deploy_mode") == "harness":
        pn = info["cfg"].get("harness_name") or "".join(ch for ch in name if ch.isalnum()) or "agent"
        proj_dir = os.path.join(info["dir"], pn)
        if not os.path.isdir(proj_dir):
            proj_dir = info["dir"]  # 回退：项目可能直接在 dir
        try:
            r = subprocess.run(
                ["npx", "@aws/agentcore", "invoke", "--harness", pn, "--prompt", prompt],
                cwd=proj_dir, capture_output=True, text=True, timeout=120,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "AGENTCORE_SUPPRESS_RECOMMENDATION": "1", "COLUMNS": "100000"})
            out = re.sub(r"\x1b\[[0-9;]*m", "", (r.stdout + r.stderr))
            if r.returncode == 0:
                return _extract(out), "cloud"
            return out.strip()[-1500:] or i18n.t("no_output"), "cloud-error"
        except FileNotFoundError:
            return i18n.t("no_agentcore_cli"), "error"
        except Exception as e:
            return f"{i18n.t('harness_invoke_fail')} {e}", "error"
    # Runtime mode: use agentcore invoke CLI
    payload = json.dumps({"prompt": prompt, "history": history or [], **({"system_prompt": sp} if sp else {})})
    try:
        r = subprocess.run(["agentcore", "invoke", payload], cwd=info["dir"],
                           capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", "AGENTCORE_SUPPRESS_RECOMMENDATION": "1", "COLUMNS": "100000"})
        out = re.sub(r"\x1b\[[0-9;]*m", "", (r.stdout + r.stderr))
        if r.returncode == 0:
            return _extract(out), "cloud"
        return out.strip()[-1500:] or i18n.t("no_output"), "cloud-error"
    except FileNotFoundError:
        return i18n.t("no_agentcore_runtime"), "error"
    except Exception as e:
        return f"{i18n.t('cloud_deploy_fail')}: {e}", "error"

class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*"); self.end_headers(); self.wfile.write(b)
    def do_OPTIONS(self): self._send(204, "")
    def _authed(self):
        if not AUTH: return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                if base64.b64decode(h[6:]).decode().split(":", 1)[1] == AUTH: return True
            except Exception: pass
        self.send_response(401); self.send_header("WWW-Authenticate", f'Basic realm="{i18n.t("server_title")}"')
        self.send_header("Content-Length", "0"); self.end_headers(); return False
    def do_GET(self):
        if not self._authed(): return
        if self.path.startswith("/api/deploy-status"):
            q = parse_qs(urlparse(self.path).query)
            jid = (q.get("job_id") or [""])[0]; cur = int((q.get("cursor") or ["0"])[0])
            job = JOBS.get(jid)
            if not job: self._send(404, json.dumps({"error": i18n.t("job_not_found")})); return
            ln = job["lines"]; resp = {"lines": ln[cur:], "next": len(ln), "done": job["done"], "ok": job["ok"]}
            if job["done"]: resp["log"] = job["log"]
            self._send(200, json.dumps(resp)); return
        path = "/index.html" if self.path in ("/", "") else self.path.split("?")[0]
        fp = os.path.join(ROOT, path.lstrip("/"))
        if os.path.isfile(fp):
            ct = "text/html; charset=utf-8" if fp.endswith(".html") else "text/plain"
            with open(fp, "rb") as f: content = f.read()
            if fp.endswith(".html"):
                inject = f'<script>window.STUDIO_LANG="{LANG}";</script>'.encode()
                content = content.replace(b'</head>', inject + b'</head>', 1)
            self._send(200, content, ct)
        else: self._send(404, "not found", "text/plain")
    def do_POST(self):
        if not self._authed(): return
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n) or "{}")
        if self.path == "/api/publish":
            d = write_project(data["name"], data["files"])
            # Write uploaded zip skill packages (base64), verify contain SKILL.md
            zip_warn = []
            for fn, b64 in (data.get("zips") or {}).items():
                try:
                    import zipfile, io
                    raw = base64.b64decode(b64)
                    zf = zipfile.ZipFile(io.BytesIO(raw))
                    if not any(n.lower().endswith("skill.md") for n in zf.namelist()):
                        zip_warn.append(i18n.t("zip_skip", fn=fn)); continue
                    fp = os.path.join(d, fn)
                    os.makedirs(os.path.dirname(fp), exist_ok=True)
                    with open(fp, "wb") as f: f.write(raw)
                except Exception as e:
                    zip_warn.append(i18n.t("zip_error", fn=fn, error=str(e)))
            PUBLISHED[data["name"]] = {"dir": d, "cfg": data.get("cfg", {})}
            region_notice = clean_stale_region(d, (data.get("cfg") or {}).get("region"))
            resp = {"ok": True, "dir": d, "zip_warn": zip_warn}
            if region_notice: resp["region_notice"] = region_notice
            cs = cloud_status(d, (data.get("cfg") or {}).get("region"), data.get("name"))
            if cs:
                PUBLISHED[data["name"]]["cloud_arn"] = cs.get("arn")
                PUBLISHED[data["name"]]["cloud_region"] = cs.get("region")
                resp["cloud_ready"] = True; resp["cloud_agent"] = cs["agent_id"]; resp["cloud_region"] = cs["region"]
            self._send(200, json.dumps(resp))
        elif self.path == "/api/invoke":
            out, mode = run_agent(data["name"], data.get("prompt", ""), data.get("system_prompt"), data.get("history"))
            self._send(200, json.dumps({"result": out, "mode": mode}))
        elif self.path == "/api/deploy":
            self._send(200, json.dumps({"job_id": start_deploy_job(data["name"])}))
        elif self.path == "/api/invoke-cloud":
            out, mode = invoke_cloud(data["name"], data.get("prompt", ""), (data.get("cfg") or {}).get("region") or data.get("region"), data.get("system_prompt"), data.get("history"), data.get("session")); self._send(200, json.dumps({"result": out, "mode": mode}))
        elif self.path == "/api/delete-runtime":
            self._send(200, json.dumps(delete_runtime(data.get("name"), data.get("region"))))
        else: self._send(404, "{}")
    def log_message(self, *a): pass

def _deploy_keep(s):
    """Filter deploy output: keep relevant lines only."""
    s = s.strip()
    if not s: return False
    if s.startswith((">>>", "#", "✅", "⚠", "❌", "☁️", "===")): return True  # Contains ###STEP###/###SKIP### (trace)
    low = s.lower()
    kw = ("error", "exception", "traceback", "failed", "fail:", "denied",
          "completed", "created", "created/updated", "deploying", "building",
          "pushing", "uploading", "success", "arn:aws", "endpoint", "ready")
    return any(k in low for k in kw)

def start_deploy_job(name):
    """Start background deploy job, return job_id. deploy.sh runs in background thread, writes key lines/result to JOBS (frontend polls for delta)."""
    jid = uuid.uuid4().hex[:16]
    JOBS[jid] = {"lines": [], "done": False, "ok": False, "log": "", "name": name}
    info = PUBLISHED.get(name)
    if not info:
        JOBS[jid].update(done=True, ok=False, log=i18n.t("not_published"))
        return jid
    def worker():
        job = JOBS[jid]; lines = []
        try:
            env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
            cmd = ["stdbuf", "-oL", "-eL", "bash", "deploy.sh"] if shutil.which("stdbuf") else ["bash", "deploy.sh"]
            p = subprocess.Popen(cmd, cwd=info["dir"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1, env=env)
            for line in iter(p.stdout.readline, ""):
                line = line.rstrip("\n"); lines.append(line)
                if _deploy_keep(line): job["lines"].append(line)
            p.stdout.close(); rc = p.wait(timeout=1800)
            out = "\n".join(lines)
            ok = rc == 0 or i18n.t("deploy_completed") in out or i18n.t("agent_created") in out
            if ok: info["deployed"] = True
            job["ok"] = ok; job["log"] = out[-4000:]
        except Exception as e:
            job["ok"] = False; job["log"] = ("\n".join(lines) + f"\n{i18n.t('deploy_fail')}: {e}")[-4000:]
        job["done"] = True
    threading.Thread(target=worker, daemon=True).start()
    return jid

if __name__ == "__main__":
    os.makedirs(WS, exist_ok=True)
    port = int(os.environ.get("PORT", 8799))
    host = os.environ.get("HOST", "127.0.0.1")
    print(i18n.t("server_start", host=host, port=port))
    ThreadingHTTPServer((host, port), H).serve_forever()
