"""深度生成引擎 — NL 需求 → AgentCore 完整可部署产物（配置 + 代码 + system prompt）。

分步调用 LLM：
  Step 1: 需求分解 → 组件选型 + 参数提取
  Step 2: 各组件配置生成（并行）
  Step 3: System Prompt + 工具代码生成
  Step 4: 校验 + 自修复
"""
import json, logging
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger("studio.deep_generate")

# ─── AgentCore 能力映射知识库 ───────────────────────────────────────────────────

CAPABILITY_MAP = """
你是 AgentCore 架构师。根据用户需求，选择最优的 AgentCore 组件组合。

## AgentCore 原生能力清单（优先使用）

### 内置工具（零额外成本，随 Runtime/Harness 提供）
- **browser**: 无头浏览器，能打开网页、提取内容、截图、填表单、点击。适用于：抓取网页、监控页面变化、自动化表单操作。
- **code_interpreter**: Python 沙箱，能 pip install、有网络访问、能执行 boto3。适用于：数据计算、发邮件(boto3+SES)、调 API(requests)、画图、文件处理。
- **web_search**: 搜索互联网返回摘要。适用于：搜索新闻、查信息、竞品监控。限制：仅 us-east-1。

### 组件
- **runtime**: 容器化 Agent，自定义编排代码（strands-agents），支持复杂工具循环。适合需要自定义代码逻辑的场景。
- **harness**: 声明式托管 Agent，AgentCore 跑 Agent 循环，零容器管理，不可变版本。适合标准 Agent 场景。
- **memory_semantic**: 向量存储事实知识，语义检索。适用于：知识库、FAQ。
- **memory_episodic**: 捕获交互经验为 episode，支持回溯反思。适用于：记住历史、学习经验、趋势分析。
- **gateway**: MCP 协议网关，注册外部工具目标（Lambda/远程 MCP）。仅当内置工具不能满足时使用。
- **identity**: 出站凭证管理（API Key、OAuth）。当需要安全调用认证的外部服务时使用。
- **policy**: Cedar 策略引擎，实时拦截/放行工具调用。用于安全限制、合规。
- **evaluations**: 定义测试用例，持续监控 Agent 质量。用于生产环境质量保障。

### 需外部组件（尽量少用）
- **scheduler**: AgentCore 无内置定时能力，用 EventBridge Schedule → invoke-agent-runtime 实现。仅当需求包含定时/周期性执行时引入。

## 选型原则
1. 能用内置工具（browser/code_interpreter/web_search）绝不引入 Gateway + Lambda
2. code_interpreter 能执行 boto3，因此发邮件(SES)、读写 DynamoDB、调 S3 等 AWS 操作无需额外 Lambda
3. 简单场景优先 harness（零代码管理）；需要复杂自定义工具循环时用 runtime
4. 定时触发统一用 EventBridge Schedule（唯一需要的非 AgentCore 组件）
5. 只在客户有自己的私有 API/数据库且无法通过 code_interpreter 访问时，才用 Gateway
"""

DECOMPOSE_PROMPT = CAPABILITY_MAP + """

## 任务
分析用户需求，输出 JSON（严格遵循格式，不要输出其他内容）：

```json
{
  "summary": "一句话描述这个 Agent 做什么",
  "deploy_mode": "harness 或 runtime",
  "deploy_mode_reason": "选择原因",
  "builtin_tools": ["browser", "code_interpreter", "web_search 中需要的"],
  "memory": null 或 {"strategy": "semantic/episodic", "reason": "为什么需要"},
  "gateway_targets": [] 或 [{"name": "工具名", "type": "lambda/remote_mcp", "reason": "为什么内置工具不够"}],
  "identity": null 或 {"reason": "需要什么凭证"},
  "policy": null 或 {"rules_desc": ["自然语言描述的规则"]},
  "evaluations": null 或 {"test_cases_desc": ["验证点描述"]},
  "scheduler": null 或 {"interval": "cron 或 rate 表达式", "trigger_prompt": "唤醒 Agent 时发送的 prompt"},
  "user_params": {"从需求中提取的具体参数，如 URL、阈值、邮箱等"},
  "external_deps": ["非 AgentCore 的外部依赖列表"],
  "estimated_monthly_cost": "$X-Y（粗略估算）"
}
```

用户需求：
"""

CONFIG_PROMPT = """你是 AgentCore 组件配置专家。根据组件类型和业务意图，生成完整配置 JSON。

## 组件类型: {component_type}
## 业务意图: {intent}
## 用户参数: {user_params}
## 部署区域: {region}

## 配置规范
{config_schema}

输出完整配置 JSON（不要输出其他内容）：
"""

SYSTEM_PROMPT_PROMPT = """你是 Agent System Prompt 工程师。根据以下信息生成一个完整的、可直接使用的 System Prompt。

## Agent 概述
{summary}

## 可用工具
{tools_desc}

## 用户参数
{user_params}

## Memory 配置
{memory_desc}

## 要求
1. prompt 必须包含完整的执行流程（Step by step）
2. 每一步明确指定使用哪个工具、传什么参数
3. 包含错误处理逻辑（工具调用失败时怎么办）
4. 包含输出格式规范
5. 如果有 code_interpreter，给出完整可执行的 Python 代码片段（不是伪代码）
6. 如果有定时触发，说明被唤醒时的行为

直接输出 System Prompt 内容（不要包裹在 markdown code block 中）：
"""

TOOL_CODE_PROMPT = """你是 AWS Lambda 开发者。根据以下工具需求生成完整的 Lambda handler 代码。

## 工具名: {tool_name}
## 功能: {intent}
## 输入参数: {input_schema}
## 期望输出: {output_desc}
## 区域: {region}

要求：
1. 使用 Python 3.11
2. handler 函数签名: def handler(event, context)
3. 包含必要的错误处理
4. 只用标准库 + boto3（Lambda 运行时自带）
5. 返回 JSON 序列化的结果

直接输出 Python 代码（不要包裹在 markdown code block 中）：
"""

VALIDATE_PROMPT = """你是 AgentCore 部署校验器。检查以下生成产物是否完整、一致、可部署。

## System Prompt
{system_prompt}

## 组件配置
{components}

## 工具代码
{tool_code}

## 用户原始需求
{original_request}

检查项：
1. System Prompt 中引用的每个工具是否都在组件列表中
2. 代码语法是否正确（Python）
3. 是否完整覆盖了用户需求（没有遗漏的功能点）
4. 是否有安全隐患（硬编码密钥、不安全的网络访问等）
5. Memory/Policy 配置是否与 System Prompt 逻辑一致

输出 JSON：
```json
{{
  "valid": true/false,
  "errors": ["错误描述（如有）"],
  "warnings": ["建议但非阻断的问题"],
  "suggestions": ["优化建议"]
}}
```
"""

# ─── 组件配置 Schema ───────────────────────────────────────────────────────────

COMPONENT_SCHEMAS = {
    "harness": {
        "desc": "Harness 声明式 Agent",
        "schema": """{
  "name": "agent 名称（字母数字，<=23 字符）",
  "model_id": "Bedrock 模型 ID，如 anthropic.claude-sonnet-4-5-20250514",
  "region": "部署区域",
  "system_prompt": "（由后续步骤单独生成，此处留空字符串）",
  "tools": ["内置工具列表: browser / code_interpreter / web_search"],
  "memory": "none 或 semantic 或 episodic",
  "max_iterations": 10,
  "idle_timeout": 900,
  "max_lifetime": 28800
}"""
    },
    "runtime": {
        "desc": "Runtime 自定义编排",
        "schema": """{
  "name": "agent 名称",
  "model_id": "Bedrock 模型 ID",
  "region": "部署区域",
  "system_prompt": "（由后续步骤单独生成，此处留空字符串）",
  "entry": "agentcore_entry.py",
  "protocol": "HTTP",
  "idle_timeout": 900,
  "max_lifetime": 28800
}"""
    },
    "memory_episodic": {
        "desc": "Episodic Memory（经验记忆）",
        "schema": """{
  "strategy": "episodic",
  "namespace": "命名空间模板，如 monitor/{actorId}",
  "expiry_days": 90,
  "reflection_enabled": true/false,
  "reflection_prompt": "反思引导语（如：分析最近的价格走势，识别模式）"
}"""
    },
    "memory_semantic": {
        "desc": "Semantic Memory（知识记忆）",
        "schema": """{
  "strategy": "semantic",
  "namespace": "命名空间",
  "expiry_days": 365
}"""
    },
    "policy": {
        "desc": "Policy 策略引擎",
        "schema": """{
  "rules": [
    {"effect": "permit/forbid", "action": "tool_call", "condition": "Cedar 条件表达式", "description": "规则描述"}
  ]
}"""
    },
    "gateway_target": {
        "desc": "Gateway 工具目标",
        "schema": """{
  "name": "工具名称",
  "type": "lambda",
  "description": "工具描述（Agent 看到的）",
  "input_schema": {"参数名": {"type": "类型", "description": "描述"}},
  "output_schema": "输出格式描述"
}"""
    },
    "scheduler": {
        "desc": "定时触发器（EventBridge）",
        "schema": """{
  "interval": "rate(1 hour) 或 cron(0 9 * * ? *)",
  "trigger_prompt": "唤醒 Agent 时发送的 prompt 内容"
}"""
    },
    "evaluations": {
        "desc": "质量评估",
        "schema": """{
  "test_cases": [
    {"input": "测试输入", "expected_behavior": "期望行为描述"}
  ]
}"""
    }
}


# ─── 生成引擎 ──────────────────────────────────────────────────────────────────

def deep_generate(nl_prompt, llm_fn, region="us-west-2", on_progress=None):
    """
    完整的深度生成流程。

    Args:
        nl_prompt: 用户自然语言需求
        llm_fn: LLM 调用函数，签名 llm_fn(prompt, system_prompt) -> str
        region: 默认部署区域
        on_progress: 可选回调 on_progress(step, total, label) 用于汇报进度

    Returns:
        dict with keys: ok, spec, system_prompt, tool_code, canvas_nodes, deploy_config, validation
    """
    def _progress(step, total, label):
        if on_progress:
            on_progress(step, total, label)

    result = {"ok": False, "error": None}

    # ─── Step 1: 需求分解（max_tokens=2048）───
    _progress(1, 4, "需求分解")
    log.info("Step 1: 需求分解")
    try:
        raw = llm_fn(f"用户需求：{nl_prompt}", DECOMPOSE_PROMPT, max_tokens=2048)
        spec = _parse_json(raw)
        if isinstance(spec, list):
            spec = spec[0] if spec and isinstance(spec[0], dict) else None
        if not spec or not isinstance(spec, dict):
            result["error"] = "需求分解失败：LLM 返回无法解析的 JSON"
            return result
        spec.setdefault("user_params", {})
        spec.setdefault("builtin_tools", [])
        spec.setdefault("decisions", [])
        result["spec"] = spec
    except Exception as e:
        result["error"] = f"需求分解异常: {e}"
        return result

    # ─── Step 2: 组件配置生成（并行，max_tokens=1024 per component）───
    _progress(2, 4, "组件配置生成")
    log.info("Step 2: 组件配置生成")
    components = {}
    deploy_mode = spec.get("deploy_mode", "harness")

    def _gen_component(comp_type, intent, schema_key, fallback):
        schema_info = COMPONENT_SCHEMAS.get(schema_key, COMPONENT_SCHEMAS["harness"])
        try:
            cfg_raw = llm_fn(
                CONFIG_PROMPT.format(
                    component_type=comp_type,
                    intent=intent,
                    user_params=json.dumps(spec.get("user_params", {}), ensure_ascii=False),
                    region=region,
                    config_schema=schema_info["schema"]
                ), "", max_tokens=1024)
            return _parse_json(cfg_raw) or fallback
        except Exception:
            return fallback

    def _gen_decisions():
        """生成选型决策理由（与组件配置并行）。"""
        try:
            raw = llm_fn(
                f"基于以下 Agent 架构选型结果，为每个决策写出理由。\n\n选型结果：{json.dumps(spec, ensure_ascii=False)}\n\n"
                "输出 JSON 数组（每项含 component/chosen/reason/alternative 字段）：",
                "你是架构决策分析师。为每个组件选型决策输出简洁的理由说明。只输出 JSON 数组。",
                max_tokens=1536)
            d = _parse_json(raw)
            return d if isinstance(d, list) else []
        except Exception:
            return []

    # 并行生成所有组件配置 + decisions（decisions 用独立 executor 不阻塞）
    decisions_executor = ThreadPoolExecutor(max_workers=1)
    decisions_future = None
    if not spec.get("decisions"):
        decisions_future = decisions_executor.submit(_gen_decisions)

    futures = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        # 主 Runtime/Harness
        futures["main"] = pool.submit(
            _gen_component, deploy_mode, spec.get("summary", ""),
            deploy_mode if deploy_mode in COMPONENT_SCHEMAS else "harness",
            {"name": "agent", "model_id": "anthropic.claude-sonnet-4-5-20250514", "region": region})
        # Memory
        if spec.get("memory"):
            mem_type = f"memory_{spec['memory'].get('strategy', 'episodic')}"
            futures["memory"] = pool.submit(
                _gen_component, mem_type, spec["memory"].get("reason", ""),
                mem_type if mem_type in COMPONENT_SCHEMAS else "memory_episodic",
                {"strategy": spec["memory"]["strategy"], "expiry_days": 90})
        # Policy
        if spec.get("policy"):
            futures["policy"] = pool.submit(
                _gen_component, "policy",
                json.dumps(spec["policy"].get("rules_desc", []), ensure_ascii=False),
                "policy", {"rules": []})

    # 收集组件结果
    components[deploy_mode] = futures["main"].result()
    components[deploy_mode]["region"] = region
    components[deploy_mode]["tools"] = spec.get("builtin_tools", [])
    if "memory" in futures:
        components["memory"] = futures["memory"].result()
    if "policy" in futures:
        components["policy"] = futures["policy"].result()

    # 非 LLM 组件（直接从 spec 搬，无需调 LLM）
    if spec.get("scheduler"):
        components["scheduler"] = spec["scheduler"]
    if spec.get("gateway_targets"):
        components["gateway_targets"] = spec["gateway_targets"]
    if spec.get("evaluations"):
        components["evaluations"] = spec["evaluations"]

    result["components"] = components

    # ─── Step 3: System Prompt 生成（max_tokens=8192，需要完整输出）───
    _progress(3, 4, "System Prompt 生成")
    log.info("Step 3: System Prompt 生成")
    tools_desc = _build_tools_description(spec, components)
    memory_desc = json.dumps(components.get("memory"), ensure_ascii=False) if components.get("memory") else "无"

    try:
        system_prompt = llm_fn(
            SYSTEM_PROMPT_PROMPT.format(
                summary=spec.get("summary", ""),
                tools_desc=tools_desc,
                user_params=json.dumps(spec.get("user_params", {}), ensure_ascii=False, indent=2),
                memory_desc=memory_desc
            ), "", max_tokens=8192)
        result["system_prompt"] = system_prompt
    except Exception as e:
        result["error"] = f"System Prompt 生成失败: {e}"
        return result

    # 回填 system_prompt 到主组件配置
    components[deploy_mode]["system_prompt"] = system_prompt

    # ─── Step 3.5 + Step 4: 工具代码 + 校验 并行 ───
    tool_code = {}
    validation_future = None

    with ThreadPoolExecutor(max_workers=4) as pool:
        # 校验（与工具代码生成并行）
        def _run_validation():
            try:
                val_raw = llm_fn(
                    VALIDATE_PROMPT.format(
                        system_prompt=system_prompt[:6000],
                        components=json.dumps(components, ensure_ascii=False, indent=2)[:6000],
                        tool_code="（工具代码正在生成中）",
                        original_request=nl_prompt
                    ), "", max_tokens=2048)
                v = _parse_json(val_raw)
                if isinstance(v, list):
                    return {"valid": False, "errors": v, "warnings": [], "suggestions": []}
                elif not isinstance(v, dict):
                    return {"valid": True, "errors": [], "warnings": [], "suggestions": []}
                v.setdefault("valid", True)
                v.setdefault("errors", [])
                v.setdefault("warnings", [])
                v.setdefault("suggestions", [])
                return v
            except Exception:
                return {"valid": True, "errors": [], "warnings": [], "suggestions": []}

        _progress(4, 4, "校验")
        log.info("Step 4: 校验（并行启动）")
        validation_future = pool.submit(_run_validation)

        # Gateway 工具代码生成
        if spec.get("gateway_targets"):
            log.info("Step 3.5: Gateway 工具代码生成")
            def _gen_tool_code(target):
                try:
                    code = llm_fn(
                        TOOL_CODE_PROMPT.format(
                            tool_name=target.get("name", "tool"),
                            intent=target.get("reason", ""),
                            input_schema=json.dumps(target.get("input_schema", {}), ensure_ascii=False),
                            output_desc=target.get("output_desc", "JSON 对象"),
                            region=region
                        ), "", max_tokens=2048)
                    return target["name"], _clean_code_block(code)
                except Exception as e:
                    return target["name"], f"# 代码生成失败: {e}\ndef handler(event, context):\n    return {{'error': 'not implemented'}}"

            tool_futures = [pool.submit(_gen_tool_code, t) for t in spec["gateway_targets"]]
            for f in as_completed(tool_futures):
                name, code = f.result()
                tool_code[name] = code

    result["tool_code"] = tool_code
    result["validation"] = validation_future.result() if validation_future else {"valid": True, "errors": [], "warnings": [], "suggestions": []}

    # 收集 decisions（在整个流程中并行运行，此时大概率已完成）
    if decisions_future is not None:
        spec["decisions"] = decisions_future.result(timeout=60)
    decisions_executor.shutdown(wait=False)

    # ─── 生成画布节点和部署配置 ───
    result["canvas_nodes"] = _build_canvas_nodes(spec, components, region)
    result["deploy_config"] = _build_deploy_config(spec, components, region)
    result["ok"] = True
    log.info("深度生成完成")
    return result


# ─── 辅助函数 ──────────────────────────────────────────────────────────────────

def _parse_json(text):
    """从 LLM 输出中提取 JSON（可能被 markdown 包裹或有前后缀文本）。"""
    import re
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    decoder = json.JSONDecoder()
    for start in range(len(text)):
        if text[start] in ('{', '['):
            try:
                obj, _ = decoder.raw_decode(text[start:])
                return obj
            except json.JSONDecodeError:
                continue
    return None


def _clean_code_block(text):
    """去除 markdown 代码块包裹。"""
    import re
    text = text.strip()
    m = re.match(r"```(?:python)?\s*\n(.*)\n```\s*$", text, re.DOTALL)
    if m:
        return m.group(1)
    return text


def _build_tools_description(spec, components):
    """构建工具描述文本，供 System Prompt 生成使用。"""
    lines = []
    for tool in spec.get("builtin_tools", []):
        if tool == "browser":
            lines.append("- **Browser**: 打开网页、提取内容、截图、填表单。使用方式：Agent 直接描述要访问的 URL 和要做的操作。")
        elif tool == "code_interpreter":
            lines.append("- **Code Interpreter**: 执行 Python 代码（可 pip install、有网络、可用 boto3）。使用方式：Agent 在回复中写 Python 代码块即自动执行。")
        elif tool == "web_search":
            lines.append("- **Web Search**: 搜索互联网获取最新信息。使用方式：Agent 描述搜索关键词。")
    if spec.get("gateway_targets"):
        for target in spec["gateway_targets"]:
            lines.append(f"- **{target['name']}**: {target.get('reason', '自定义工具')}。通过 Gateway MCP 协议调用。")
    return "\n".join(lines) if lines else "无额外工具（Agent 仅做对话）"


def _sanitize_name(name):
    """名称清洗：连字符→下划线，移除其他非法字符，截断 23 字符。"""
    import re
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name.replace('-', '_'))
    name = re.sub(r'_+', '_', name).strip('_')
    return (name or "agent")[:23]


def _build_canvas_nodes(spec, components, region):
    """生成 Studio 画布节点数组（与前端 buildFromSpec 兼容）。"""
    nodes = []
    deploy_mode = spec.get("deploy_mode", "harness")
    main_cfg = components.get(deploy_mode, {})

    # 主节点
    main_node_cfg = {
        "name": _sanitize_name(main_cfg.get("name", "agent")),
        "model_id": main_cfg.get("model_id", "anthropic.claude-sonnet-4-5-20250514"),
        "region": region,
        "system_prompt": main_cfg.get("system_prompt", ""),
        "deploy_mode": deploy_mode,
        **({k: main_cfg[k] for k in ("max_iterations", "idle_timeout", "max_lifetime", "tools") if k in main_cfg})
    }
    # EventBridge Scheduler 配置注入主节点（deploy.sh 生成时读取）
    sched = components.get("scheduler") or spec.get("scheduler")
    if sched:
        main_node_cfg["scheduler_cron"] = sched.get("interval", "")
        main_node_cfg["scheduler_prompt"] = sched.get("trigger_prompt", "执行定时任务")
    nodes.append({
        "type": deploy_mode if deploy_mode == "harness" else "runtime",
        "cfg": main_node_cfg
    })

    # 内置工具节点
    if "code_interpreter" in spec.get("builtin_tools", []):
        nodes.append({"type": "codeinterp", "cfg": {}})
    if "browser" in spec.get("builtin_tools", []):
        nodes.append({"type": "browser", "cfg": {}})

    # Memory
    if components.get("memory"):
        mem = components["memory"]
        nodes.append({
            "type": "memory",
            "cfg": {
                "strategy": mem.get("strategy", "episodic"),
                "expiry": mem.get("expiry_days", 90),
                "namespace": mem.get("namespace", ""),
                "reflection": mem.get("reflection_enabled", False),
            }
        })

    # Gateway + targets
    if spec.get("gateway_targets"):
        nodes.append({"type": "gateway", "cfg": {"auth": "AWS_IAM"}})
        for target in spec["gateway_targets"]:
            nodes.append({
                "type": "mcp",
                "cfg": {
                    "name": target.get("name", "tool"),
                    "type": "lambda",
                    "description": target.get("reason", ""),
                }
            })

    # Policy
    if components.get("policy"):
        nodes.append({
            "type": "policy",
            "cfg": {"rules": components["policy"].get("rules", [])}
        })

    # Web Search（作为 gateway 下的内置 connector）
    if "web_search" in spec.get("builtin_tools", []):
        nodes.append({"type": "mcp", "cfg": {"name": "web_search", "type": "web_search"}})

    return nodes


def _build_deploy_config(spec, components, region):
    """生成部署相关配置摘要。"""
    config = {
        "region": region,
        "deploy_mode": spec.get("deploy_mode", "harness"),
        "requires_scheduler": spec.get("scheduler") is not None,
    }
    if spec.get("scheduler"):
        config["scheduler"] = spec["scheduler"]
    if spec.get("external_deps"):
        config["external_deps"] = spec["external_deps"]
    config["estimated_cost"] = spec.get("estimated_monthly_cost", "未估算")
    return config
