#!/usr/bin/env python3
"""AgentCore Studio 后端 — 本地发布 + Playground 调用 + 可选云部署（零依赖，仅标准库）。
启动: python3 server.py  →  http://127.0.0.1:8799  (可用 PORT 环境变量覆盖)
仅绑定 127.0.0.1，会在本机执行生成的 agent 代码与 agentcore CLI，请勿暴露到公网。"""
import json, os, sys, importlib.util, subprocess, re, base64, threading, time, queue, shutil, uuid, tempfile, logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("studio")

AUTH = os.environ.get("STUDIO_PASSWORD")  # 设置则对所有 HTTP 请求启用 Basic Auth

ROOT = os.path.dirname(os.path.abspath(__file__))
WS = os.path.join(ROOT, "workspace")
STATE_FILE = os.path.join(WS, ".studio_state.json")
PUBLISHED = {}  # name -> {dir, cfg}
JOBS = {}  # job_id -> {lines:[], done, ok, log, name}  后台部署任务（轮询式，绕开 App Runner 流式掐断）


def _load_state():
    """启动时从本地文件恢复 PUBLISHED 状态，容器重启不丢失。"""
    global PUBLISHED
    if not os.path.isfile(STATE_FILE):
        return
    try:
        with open(STATE_FILE) as f:
            saved = json.load(f)
        for name, info in saved.items():
            d = info.get("dir", "")
            if os.path.isdir(d):
                PUBLISHED[name] = info
        log.info(f"已恢复 {len(PUBLISHED)} 个已发布 Agent 的状态")
    except Exception as e:
        log.warning(f"加载状态文件失败: {e}")


def _save_state():
    """将 PUBLISHED 持久化到本地（轻量，每次 publish 时调用）。"""
    try:
        serializable = {}
        for name, info in PUBLISHED.items():
            serializable[name] = {
                "dir": info.get("dir", ""),
                "cfg": info.get("cfg", {}),
                "cloud_arn": info.get("cloud_arn"),
                "cloud_region": info.get("cloud_region"),
                "deployed": info.get("deployed", False),
            }
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"保存状态文件失败: {e}")

def write_project(name, files):
    d = os.path.join(WS, name); os.makedirs(d, exist_ok=True)
    for fn, txt in files.items():
        fp = os.path.join(d, fn)
        os.makedirs(os.path.dirname(fp), exist_ok=True)  # 支持 skills/foo.py 等子目录
        with open(fp, "w") as f: f.write(txt)
    return d

def clean_stale_region(d, target_region):
    """若本地 .bedrock_agentcore.yaml 记录的 agent_arn 区域与目标区域不一致，
    清除过期的 toolkit 状态，使下次部署在新区域全新创建（而非跨区 Update 失败）。"""
    if not target_region: return None
    yaml_fp = os.path.join(d, ".bedrock_agentcore.yaml")
    if not os.path.isfile(yaml_fp): return None
    try:
        with open(yaml_fp) as f: content = f.read()
        m = re.search(r"agent_arn:\s*arn:aws:bedrock-agentcore:([a-z0-9-]+):", content)
        if m and m.group(1) != target_region:
            old = m.group(1)
            os.remove(yaml_fp)
            shutil.rmtree(os.path.join(d, ".bedrock_agentcore"), ignore_errors=True)
            return f"检测到旧部署区域 {old} 与目标 {target_region} 不一致，已清除本地状态，将在 {target_region} 全新创建"
    except Exception:
        pass
    return None

def _find_ready_runtime(region, name):
    """直接查 AWS：region 内是否有同名且 READY 的 agent runtime（不依赖本地工作区）。"""
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
    """按名字删除某 region 内的 agent runtime（用于改名后清理旧孤儿）；不删除关联 Memory。"""
    if not (name and region): return {"ok": False, "error": "缺少 name 或 region"}
    try:
        r = subprocess.run(["aws", "bedrock-agentcore-control", "list-agent-runtimes",
                            "--region", region, "--output", "json"], capture_output=True, text=True, timeout=20)
        if r.returncode != 0: return {"ok": False, "error": (r.stderr or "list 失败")[:200]}
        rid = None
        for rt in (json.loads(r.stdout or "{}")).get("agentRuntimes", []):
            nm = str(rt.get("agentRuntimeName", ""))
            if nm == name or nm.startswith(name):
                rid = rt.get("agentRuntimeId"); break
        if not rid: return {"ok": False, "error": "未找到 runtime " + name}
        dd = subprocess.run(["aws", "bedrock-agentcore-control", "delete-agent-runtime",
                            "--agent-runtime-id", rid, "--region", region], capture_output=True, text=True, timeout=30)
        if dd.returncode != 0: return {"ok": False, "error": (dd.stderr or "delete 失败")[:200]}
        return {"ok": True, "id": rid}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def list_agents(region):
    """列出某 region 内所有 agent runtime（多 Agent 编排：从云端拉取已发布 agent）。"""
    if not region:
        return {"ok": False, "error": "缺少 region", "agents": []}
    try:
        r = subprocess.run(["aws", "bedrock-agentcore-control", "list-agent-runtimes",
                            "--region", region, "--output", "json"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return {"ok": False, "error": (r.stderr or "list 失败")[:300], "agents": []}
        out = []
        rts = (json.loads(r.stdout or "{}")).get("agentRuntimes", [])
        def _proto(aid):
            try:
                g = subprocess.run(["aws", "bedrock-agentcore-control", "get-agent-runtime",
                                    "--agent-runtime-id", aid, "--region", region,
                                    "--query", "protocolConfiguration.serverProtocol", "--output", "text"],
                                   capture_output=True, text=True, timeout=12)
                p = (g.stdout or "").strip()
                return p if p and p != "None" else None
            except Exception:
                return None
        import concurrent.futures as _cf
        protos = {}
        ids = [rt.get("agentRuntimeId") for rt in rts if rt.get("agentRuntimeId")]
        with _cf.ThreadPoolExecutor(max_workers=8) as _ex:
            for aid, pr in zip(ids, _ex.map(_proto, ids)):
                protos[aid] = pr
        for rt in rts:
            out.append({"name": rt.get("agentRuntimeName"), "arn": rt.get("agentRuntimeArn"),
                        "id": rt.get("agentRuntimeId"), "status": rt.get("status"),
                        "protocol": protos.get(rt.get("agentRuntimeId"))})
        return {"ok": True, "agents": out, "region": region}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "agents": []}

def _spec_bucket(region):
    try:
        r = subprocess.run(["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
                           capture_output=True, text=True, timeout=15)
        acct = (r.stdout or "").strip()
        if not acct or acct == "None": return None
        return f"agentcore-studio-state-{acct}-{region}"
    except Exception:
        return None

def list_specs(region):
    """列出 state 桶 specs/ 下已存档的 Studio 画布 spec（agent 名集合），供导入交叉标注。"""
    if not region: return {"ok": False, "error": "缺少 region", "specs": []}
    bucket = _spec_bucket(region)
    if not bucket: return {"ok": False, "error": "无法获取账号（凭证缺失）", "specs": []}
    try:
        r = subprocess.run(["aws", "s3api", "list-objects-v2", "--bucket", bucket,
                            "--prefix", "specs/", "--region", region,
                            "--query", "Contents[].Key", "--output", "json"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            # 桶不存在 = 还没发布过任何带 spec 的 agent，视作空集（非错误）
            return {"ok": True, "specs": [], "region": region}
        keys = json.loads(r.stdout or "null") or []
        names = [k[len("specs/"):-len(".json")] for k in keys
                 if k.startswith("specs/") and k.endswith(".json")]
        return {"ok": True, "specs": names, "region": region}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "specs": []}

def get_spec(region, name):
    """读回某 agent 的 Studio 画布 spec（用于导入重建画布）。"""
    if not region or not name: return {"ok": False, "error": "缺少 region/name"}
    bucket = _spec_bucket(region)
    if not bucket: return {"ok": False, "error": "无法获取账号（凭证缺失）"}
    try:
        r = subprocess.run(["aws", "s3", "cp", f"s3://{bucket}/specs/{name}.json", "-", "--region", region],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return {"ok": False, "error": ("未找到该 agent 的 Studio 配置（可能不是 Studio 发布的）：" + (r.stderr or ""))[:300]}
        return {"ok": True, "spec": json.loads(r.stdout or "{}")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}

def cloud_status(d, region, name=None):
    """检测是否已有就绪(READY)的云端 agent，用作演示托底环境。
    优先用本地 .bedrock_agentcore.yaml（本工作区部署过）；否则直接按名字查 AWS（托管/全新容器也能命中）。"""
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
    # 托底：直接按名字查 AWS
    return _find_ready_runtime(region, name)


def bedrock_reply(prompt, cfg, history=None):
    """直接调用 Bedrock converse 返回真实模型回复。无 boto3/凭证/模型权限则抛异常。"""
    import boto3
    model = cfg.get("model") or "anthropic.claude-3-5-sonnet-20241022-v2:0"
    region = cfg.get("region") or "us-west-2"
    # 跨区域推理配置：新模型按需调用需带地域前缀
    import re as _re
    if not _re.match(r"^(us|eu|apac|us-gov)\.", model):
        geo = "eu." if region.startswith("eu-") else "apac." if region.startswith("ap-") else "us-gov." if region.startswith("us-gov") else "us." if region.startswith("us-") else ""
        model = geo + model
    sp = (cfg.get("system_prompt") or "").strip()
    tools, skills = cfg.get("tools") or [], cfg.get("skills") or []
    if tools or skills:
        sp += f"\n（可用工具: {', '.join(tools) or '无'}; 技能: {', '.join(skills) or '无'}）"
    br = boto3.client("bedrock-runtime", region_name=region)
    msgs = []
    for h in (history or []):
        role = h.get("role"); txt = h.get("content")
        if role in ("user", "assistant") and txt:
            msgs.append({"role": role, "content": [{"text": str(txt)}]})
    msgs.append({"role": "user", "content": [{"text": prompt}]})
    kw = {"modelId": model, "messages": msgs,
          "inferenceConfig": {"maxTokens": cfg.get("max_tokens") or 1024, "temperature": 0.7}}
    if sp.strip(): kw["system"] = [{"text": sp.strip()}, {"cachePoint": {"type": "default"}}]
    r = br.converse(**kw)
    text = r["output"]["message"]["content"][0]["text"]
    u = r.get("usage") or {}
    trace = {"model": model,
             "usage": {"input_tokens": u.get("inputTokens"), "output_tokens": u.get("outputTokens"), "total_tokens": u.get("totalTokens")},
             "latency_ms": (r.get("metrics") or {}).get("latencyMs")}
    return text, trace

def anthropic_reply(prompt, cfg, sp=None, history=None):
    """Bedrock 不可达时的兜底：经 Anthropic 兼容端点调 Claude。
    需环境变量 ANTHROPIC_API_KEY（必填）与 ANTHROPIC_BASE_URL（代理地址，anthropic SDK 自动读取）。"""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("未配置 ANTHROPIC_API_KEY")
    import anthropic, re as _re
    model = cfg.get("model") or "anthropic.claude-sonnet-4-5-20250929-v1:0"
    m = model
    for pre in ("us.", "eu.", "apac.", "us-gov."):
        if m.startswith(pre): m = m[len(pre):]
    if m.startswith("anthropic."): m = m[len("anthropic."):]
    m = _re.sub(r"-v\d+:\d+$", "", m)  # 去 Bedrock 版本后缀 -v1:0
    m = _re.sub(r"-\d{8}$", "", m)      # 去日期后缀 -> 代理用别名，如 claude-sonnet-4-5
    if not m.startswith("claude"):
        raise RuntimeError(f"模型 {model} 非 Claude，Anthropic 代理不支持")
    system = ((sp if sp is not None else cfg.get("system_prompt")) or "").strip()
    tools, skills = cfg.get("tools") or [], cfg.get("skills") or []
    if tools or skills:
        system += f"\n（可用工具: {', '.join(tools) or '无'}; 技能: {', '.join(skills) or '无'}）"
    client = anthropic.Anthropic()  # 自动读取 ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY
    msgs = []
    for h in (history or []):
        role = h.get("role"); txt = h.get("content")
        if role in ("user", "assistant") and txt:
            msgs.append({"role": role, "content": str(txt)})
    msgs.append({"role": "user", "content": prompt})
    def _call(mid):
        kw = {"model": mid, "max_tokens": cfg.get("max_tokens") or 1024, "messages": msgs}
        if system.strip(): kw["system"] = system.strip()
        r = client.messages.create(**kw)
        _txt = "".join(b.text for b in r.content if getattr(b, "type", "") == "text") or "（空响应）"
        _u = getattr(r, "usage", None)
        _tr = {"model": mid, "usage": ({"input_tokens": getattr(_u, "input_tokens", None), "output_tokens": getattr(_u, "output_tokens", None)} if _u else {})}
        return _txt, _tr
    try:
        return _call(m)
    except Exception:
        if m != "claude-sonnet-4-5":  # 代理可能无此别名 -> 退回已知可用默认
            return _call("claude-sonnet-4-5")
        raise

def llm_complete(prompt, system_prompt, model=None, region=None, max_tokens=None):
    """单次 LLM 补全（NL→画布等用）。优先 Bedrock converse，不可达则 Anthropic 代理兜底。"""
    cfg = {"model": model or "anthropic.claude-sonnet-4-5-20250929-v1:0",
           "region": region or "us-west-2", "system_prompt": system_prompt or "",
           "max_tokens": max_tokens or 1024}
    try:
        return bedrock_reply(prompt, cfg, None)[0]
    except Exception:
        return anthropic_reply(prompt, cfg, system_prompt, None)[0]

def _run_entry_sandboxed(entry, name, prompt, sp=None, history=None):
    """在子进程中执行 entry.py 的 invoke 函数，避免恶意代码污染主进程。"""
    runner_code = f"""
import json, sys, importlib.util, types
# 优先用真实 SDK（entry 可能 import bedrock_agentcore.tools.* 内置工具）；缺失才 stub 最小 App
try:
    import bedrock_agentcore  # noqa: F401
except ImportError:
    m = types.ModuleType("bedrock_agentcore")
    class _App:
        def entrypoint(self, f): return f
        def run(self, *a, **k): pass
    m.BedrockAgentCoreApp = _App
    sys.modules["bedrock_agentcore"] = m
spec = importlib.util.spec_from_file_location("ac_entry", {json.dumps(entry)})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
payload = json.loads(sys.stdin.read())
res = mod.invoke(payload)
# strands Agent 会把回复流式打印到 stdout，用标记行隔离结果 JSON
print("\\n__AC_RESULT__" + json.dumps(res, ensure_ascii=False))
"""
    payload = json.dumps({"prompt": prompt, "history": history or [], **({"system_prompt": sp} if sp else {})})
    try:
        r = subprocess.run(
            [sys.executable, "-c", runner_code],
            input=payload, capture_output=True, text=True, timeout=60,
            cwd=os.path.dirname(entry),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if r.returncode == 0 and r.stdout.strip():
            # 从标记行取结果（strands 会把流式回复混进 stdout）；无标记则按整体 JSON 解析（兼容旧 entry）
            raw = r.stdout.strip()
            marker = raw.rfind("__AC_RESULT__")
            res = json.loads(raw[marker + len("__AC_RESULT__"):] if marker >= 0 else raw)
            out = res.get("result") or res.get("error") or json.dumps(res)
            if out and not res.get("error"):
                return out, res.get("trace")
    except subprocess.TimeoutExpired:
        log.warning(f"entry.py 执行超时: {name}")
    except Exception as e:
        log.debug(f"entry.py 子进程执行失败 ({name}): {e}")
    return None, None


def run_agent(name, prompt, sp=None, history=None):
    info = PUBLISHED.get(name)
    if not info: return "尚未发布，请先点击「发布」", "error", None
    entry = os.path.join(info["dir"], info["cfg"].get("entry", "agentcore_entry.py"))
    # 1) 在子进程中运行 entry.py（沙箱隔离，超时保护）
    if os.path.isfile(entry):
        out, trace = _run_entry_sandboxed(entry, name, prompt, sp, history)
        if out:
            return out, "real", trace
        log.info(f"entry.py 未成功执行 ({name})，回退到 Bedrock converse")
    # 2) 直连 Bedrock converse（有 boto3+凭证+模型权限即返回真实回复；但不执行已编排的工具）
    try:
        txt, tr = bedrock_reply(prompt, info["cfg"], history); return txt, "bedrock", tr
    except Exception as e:
        log.debug(f"Bedrock converse 失败 ({name}): {e}")
    # 2.5) Bedrock 不可达时，经 Anthropic 代理端点兜底（ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY）
    try:
        txt, tr = anthropic_reply(prompt, info["cfg"], sp, history); return txt, "bedrock", tr
    except Exception as e:
        log.debug(f"Anthropic 兜底失败 ({name}): {e}")
    # 3) 文本兜底（无依赖/无凭证）
    return fallback(prompt, info["cfg"]), "mock", None

def fallback(prompt, cfg):
    sp = (cfg.get("system_prompt") or "").strip()
    persona = f"依据设定「{sp}」，" if sp else ""
    tools, skills = cfg.get("tools") or [], cfg.get("skills") or []
    extra = f"（已注册工具: {', '.join(tools) or '无'}; 技能: {', '.join(skills) or '无'}）" if (tools or skills) else ""
    return f"{persona}针对「{prompt}」给出演示回复。{extra}配置真实模型凭证后将返回真实 Agent 响应。"

def deploy_cloud(name):
    info = PUBLISHED.get(name)
    if not info: return "尚未发布", False
    try:
        r = subprocess.run(["bash", "deploy.sh"], cwd=info["dir"], capture_output=True, text=True, timeout=900)
        out = (r.stdout + r.stderr)[-6000:] or "（无输出）"
        ok = r.returncode == 0 or "Deployment completed successfully" in out or "Agent created/updated" in out
        if ok: info["deployed"] = True
        return out, ok
    except Exception as e:
        return f"部署失败: {e}", False

def _extract(out):
    """从 agentcore invoke 的噪声输出里提取 agent 实际响应。返回 (text, trace|None)。"""
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
        text = d.get("result") or d.get("response") or d.get("output") or json.dumps(d, ensure_ascii=False)
        return text, d.get("trace")
    # 防御：CLI 可能按终端宽度把 JSON 折行（插入真实换行），尝试在 Response 段去掉折行后重组解析
    m = re.search(r"Response:\s*(\{.*\})", out, re.DOTALL)
    if m:
        try:
            collapsed = re.sub(r"\n", "", m.group(1))
            obj = json.loads(collapsed)
            if isinstance(obj, dict):
                text = obj.get("result") or obj.get("response") or obj.get("output") or json.dumps(obj, ensure_ascii=False)
                return text, obj.get("trace")
        except Exception:
            pass
    noise = ("suppress_recommendation", "silence this warning", "recommendation", "set agentcore_", "💡", "⚠")
    lines = [l for l in out.splitlines() if l.strip() and not any(k in l.lower() for k in noise)]
    return (lines[-1] if lines else "（无输出）"), None

def _invoke_runtime_arn(arn, region, prompt, sp=None, history=None, session=None):
    """托底：不依赖本地工作区，直接用 AWS 数据面 API 按 ARN 调用云端 runtime。"""
    import hashlib as _hl
    sid = _hl.sha256(session.encode()).hexdigest() if session else (uuid.uuid4().hex + uuid.uuid4().hex)  # 同会话复用稳定 64-hex session id
    payload_b64 = base64.b64encode(json.dumps({"prompt": prompt, "history": history or [], **({"system_prompt": sp} if sp else {})}).encode()).decode()  # 默认 cli_binary_format=base64
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
            return (r.stderr or r.stdout).strip()[-1500:] or "云端调用失败", "cloud-error", None
        body = open(outpath, encoding="utf-8", errors="replace").read()
        text, trace = _extract(body)
        return text, "cloud", trace
    except Exception as e:
        return f"云端调用失败: {e}", "error", None
    finally:
        if outpath and os.path.isfile(outpath):
            try: os.remove(outpath)
            except Exception: pass

def invoke_cloud(name, prompt, region=None, sp=None, history=None, session=None):
    info = PUBLISHED.get(name)
    if not info:
        # PUBLISHED 无记录（如容器重启清空内存）→ 仍按名字查 AWS 托底，避免误报"尚未发布"
        reg = region or "us-west-2"
        cs = _find_ready_runtime(reg, name)
        if cs: return _invoke_runtime_arn(cs["arn"], reg, prompt, sp, history, session)
        return "尚未发布（云端也未找到同名就绪 Agent）", "error", None
    # 托底：本地工作区无部署状态（如托管/全新容器），但云端已有就绪 runtime → 直接按 ARN 调 AWS API
    if not os.path.isfile(os.path.join(info["dir"], ".bedrock_agentcore.yaml")) and info["cfg"].get("deploy_mode") != "harness":
        arn = info.get("cloud_arn"); reg = info.get("cloud_region") or info["cfg"].get("region") or region
        if not arn and reg:
            cs = _find_ready_runtime(reg, name)
            if cs: arn = cs["arn"]; info["cloud_arn"] = arn; info["cloud_region"] = reg
        if arn and reg: return _invoke_runtime_arn(arn, reg, prompt, sp, history, session)
        return "尚未发布（云端也未找到同名就绪 Agent）", "error", None
    # Harness 模式: 用 agentcore invoke --harness CLI（项目在 <pn>/ 子目录）
    if info["cfg"].get("deploy_mode") == "harness":
        pn = info["cfg"].get("harness_name") or "".join(ch for ch in name if ch.isalnum()) or "agent"
        proj_dir = os.path.join(info["dir"], pn)
        if not os.path.isdir(proj_dir):
            proj_dir = info["dir"]  # 回退：项目可能直接在 dir
        try:
            r = subprocess.run(
                ["npx", "@aws/agentcore@preview", "invoke", "--harness", pn, "--prompt", prompt],
                cwd=proj_dir, capture_output=True, text=True, timeout=120,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "AGENTCORE_SUPPRESS_RECOMMENDATION": "1", "COLUMNS": "100000"})
            out = re.sub(r"\x1b\[[0-9;]*m", "", (r.stdout + r.stderr))
            if r.returncode == 0:
                text, trace = _extract(out)
                return text, "cloud", trace
            return out.strip()[-1500:] or "（无输出）", "cloud-error", None
        except FileNotFoundError:
            return "未找到 @aws/agentcore CLI，请运行: npm install -g @aws/agentcore@preview", "error", None
        except Exception as e:
            return f"Harness 调用失败: {e}", "error", None
    # Runtime 模式: 用 agentcore invoke CLI
    payload = json.dumps({"prompt": prompt, "history": history or [], **({"system_prompt": sp} if sp else {})})
    try:
        r = subprocess.run(["agentcore", "invoke", payload], cwd=info["dir"],
                           capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", "AGENTCORE_SUPPRESS_RECOMMENDATION": "1", "COLUMNS": "100000"})
        out = re.sub(r"\x1b\[[0-9;]*m", "", (r.stdout + r.stderr))
        if r.returncode == 0:
            text, trace = _extract(out)
            return text, "cloud", trace
        return out.strip()[-1500:] or "（无输出）", "cloud-error", None
    except FileNotFoundError:
        return "未找到 uv / agentcore CLI，无法调用云端 runtime", "error", None
    except Exception as e:
        return f"云端调用失败: {e}", "error", None

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
        self.send_response(401); self.send_header("WWW-Authenticate", 'Basic realm="AgentCore Studio"')
        self.send_header("Content-Length", "0"); self.end_headers(); return False
    def do_GET(self):
        if not self._authed(): return
        if self.path.startswith("/api/deploy-status"):
            q = parse_qs(urlparse(self.path).query)
            jid = (q.get("job_id") or [""])[0]; cur = int((q.get("cursor") or ["0"])[0])
            job = JOBS.get(jid)
            if not job: self._send(404, json.dumps({"error": "job not found"})); return
            ln = job["lines"]; resp = {"lines": ln[cur:], "next": len(ln), "done": job["done"], "ok": job["ok"]}
            if job["done"]: resp["log"] = job["log"]
            self._send(200, json.dumps(resp)); return
        path = "/index.html" if self.path in ("/", "") else self.path.split("?")[0]
        fp = os.path.realpath(os.path.join(ROOT, path.lstrip("/")))
        if not fp.startswith(os.path.realpath(ROOT)):
            self._send(403, "forbidden", "text/plain"); return
        if os.path.isfile(fp):
            ct = "text/html; charset=utf-8" if fp.endswith(".html") else "text/plain"
            with open(fp, "rb") as f: self._send(200, f.read(), ct)
        else: self._send(404, "not found", "text/plain")
    def do_POST(self):
        if not self._authed(): return
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n) or "{}")
        if self.path == "/api/publish":
            d = write_project(data["name"], data["files"])
            # 写入上传的 zip 技能包（base64），并校验含 SKILL.md
            zip_warn = []
            for fn, b64 in (data.get("zips") or {}).items():
                try:
                    import zipfile, io
                    raw = base64.b64decode(b64)
                    zf = zipfile.ZipFile(io.BytesIO(raw))
                    if not any(n.lower().endswith("skill.md") for n in zf.namelist()):
                        zip_warn.append(f"{fn}: 未含 SKILL.md，已跳过"); continue
                    fp = os.path.join(d, fn)
                    os.makedirs(os.path.dirname(fp), exist_ok=True)
                    with open(fp, "wb") as f: f.write(raw)
                except Exception as e:
                    zip_warn.append(f"{fn}: {e}")
            PUBLISHED[data["name"]] = {"dir": d, "cfg": data.get("cfg", {})}
            _save_state()
            region_notice = clean_stale_region(d, (data.get("cfg") or {}).get("region"))
            resp = {"ok": True, "dir": d, "zip_warn": zip_warn}
            if region_notice: resp["region_notice"] = region_notice
            # harness 与 runtime 是不同资源类型；runtime 云端就绪检测(list-agent-runtimes)对 harness 会误报（命中同名旧 runtime），故 harness 模式跳过
            cs = cloud_status(d, (data.get("cfg") or {}).get("region"), data.get("name")) if (data.get("cfg") or {}).get("deploy_mode") != "harness" else None
            if cs:
                PUBLISHED[data["name"]]["cloud_arn"] = cs.get("arn")
                PUBLISHED[data["name"]]["cloud_region"] = cs.get("region")
                _save_state()
                resp["cloud_ready"] = True; resp["cloud_agent"] = cs["agent_id"]; resp["cloud_region"] = cs["region"]
            log.info(f"发布 Agent: {data['name']}")
            self._send(200, json.dumps(resp))
        elif self.path == "/api/invoke":
            out, mode, trace = run_agent(data["name"], data.get("prompt", ""), data.get("system_prompt"), data.get("history"))
            self._send(200, json.dumps({"result": out, "mode": mode, "trace": trace}))
        elif self.path == "/api/llm":
            try:
                txt = llm_complete(data.get("prompt", ""), data.get("system_prompt", ""), data.get("model"), data.get("region"))
                self._send(200, json.dumps({"ok": True, "text": txt}))
            except Exception as e:
                self._send(200, json.dumps({"ok": False, "error": str(e)[:300]}))
        elif self.path == "/api/deploy":
            self._send(200, json.dumps({"job_id": start_deploy_job(data["name"])}))
        elif self.path == "/api/invoke-cloud":
            out, mode, trace = invoke_cloud(data["name"], data.get("prompt", ""), (data.get("cfg") or {}).get("region") or data.get("region"), data.get("system_prompt"), data.get("history"), data.get("session")); self._send(200, json.dumps({"result": out, "mode": mode, "trace": trace}))
        elif self.path == "/api/delete-runtime":
            self._send(200, json.dumps(delete_runtime(data.get("name"), data.get("region"))))
        elif self.path == "/api/list-agents":
            self._send(200, json.dumps(list_agents(data.get("region"))))
        elif self.path == "/api/list-specs":
            self._send(200, json.dumps(list_specs(data.get("region"))))
        elif self.path == "/api/get-spec":
            self._send(200, json.dumps(get_spec(data.get("region"), data.get("name"))))
        elif self.path == "/api/deep-generate":
            from deep_generate import deep_generate
            nl = data.get("prompt", "")
            region = data.get("region", "us-west-2")
            if not nl.strip():
                self._send(200, json.dumps({"ok": False, "error": "请输入需求描述"})); return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            def _sse_progress(step, total, label):
                try:
                    evt = json.dumps({"step": step, "total": total, "label": label}, ensure_ascii=False)
                    self.wfile.write(f"event: progress\ndata: {evt}\n\n".encode())
                    self.wfile.flush()
                except Exception:
                    pass
            try:
                def _deep_llm(p, sp, max_tokens=4096):
                    return llm_complete(p, sp, max_tokens=max_tokens)
                result = deep_generate(nl, _deep_llm, region, on_progress=_sse_progress)
                final = json.dumps(result, ensure_ascii=False)
                self.wfile.write(f"event: done\ndata: {final}\n\n".encode())
                self.wfile.flush()
            except Exception as e:
                log.error(f"深度生成失败: {e}")
                err = json.dumps({"ok": False, "error": str(e)[:500]}, ensure_ascii=False)
                self.wfile.write(f"event: done\ndata: {err}\n\n".encode())
                self.wfile.flush()
        else: self._send(404, "{}")
    def log_message(self, *a): pass

def _deploy_keep(s):
    s = s.strip()
    if not s: return False
    if s.startswith((">>>", "#", "✅", "⚠", "❌", "☁️", "===")): return True  # 含 ###STEP###/###SKIP###（trace）
    low = s.lower()
    kw = ("error", "exception", "traceback", "failed", "fail:", "denied",
          "completed", "created", "created/updated", "deploying", "building",
          "pushing", "uploading", "success", "arn:aws", "endpoint", "ready")
    return any(k in low for k in kw)

def start_deploy_job(name):
    """启动后台部署任务，返回 job_id。deploy.sh 在后台线程跑完并把关键行/结果写入 JOBS（前端轮询取增量）。"""
    jid = uuid.uuid4().hex[:16]
    JOBS[jid] = {"lines": [], "done": False, "ok": False, "log": "", "name": name}
    info = PUBLISHED.get(name)
    if not info:
        JOBS[jid].update(done=True, ok=False, log="尚未发布")
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
            ok = rc == 0 or "Deployment completed successfully" in out or "Agent created/updated" in out
            if ok:
                info["deployed"] = True
                _save_state()
            job["ok"] = ok; job["log"] = out[-4000:]
        except Exception as e:
            job["ok"] = False; job["log"] = ("\n".join(lines) + f"\ndeploy error: {e}")[-4000:]
        job["done"] = True
    threading.Thread(target=worker, daemon=True).start()
    return jid

if __name__ == "__main__":
    os.makedirs(WS, exist_ok=True)
    _load_state()
    port = int(os.environ.get("PORT", 8799))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"AgentCore Studio  →  http://{host}:{port}   (Ctrl+C 退出)")
    ThreadingHTTPServer((host, port), H).serve_forever()
