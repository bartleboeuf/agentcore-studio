"""Multi-language support (EN, FR, ZH) for AgentCore Studio."""

TRANSLATIONS = {
    "zh": {
        # UI Messages
        "app_title": "AgentCore Studio",
        "app_desc": "拖拽编排 · 一键上线你的 AI Agent ｜ From canvas to cloud in minutes",
        "not_published": "尚未发布，请先点击「发布」",
        "not_published_detail": "尚未发布（云端也未找到同名就绪 Agent）",
        "missing_runtime": "缺少 Runtime 组件（必配）",
        "missing_name": "缺少 name 或 region",
        "publish_button": "发布",
        "deploy_button": "部署到云端",
        "playground_label": "Playground",
        "send_message": "发送",
        "system_prompt": "系统提示",

        # Status Messages
        "deploying": "正在部署",
        "deploy_success": "部署成功",
        "deploy_fail": "部署失败",
        "no_output": "（无输出）",
        "empty_response": "（空响应）",
        "deploy_completed": "Deployment completed successfully",
        "agent_created": "Agent created/updated",

        # Cloud Messages
        "cloud_deploy_fail": "云端调用失败",
        "cloud_error": "云端调用失败:",
        "region_mismatch": "检测到旧部署区域 {old} 与目标 {target} 不一致，已清除本地状态，将在 {target} 全新创建",
        "runtime_not_found": "未找到 runtime {name}",
        "no_agentcore_cli": "未找到 @aws/agentcore CLI，请运行: npm install -g @aws/agentcore@preview",
        "no_agentcore_runtime": "未找到 uv / agentcore CLI，无法调用云端 runtime",
        "harness_invoke_fail": "Harness 调用失败:",

        # Config Messages
        "available_tools": "可用工具",
        "available_skills": "技能",
        "no_tools": "无",
        "mock_response": "配置真实模型凭证后将返回真实 Agent 响应",
        "job_not_found": "job not found",
        "list_fail": "list 失败",
        "delete_fail": "delete 失败",
        "zip_skip": "{fn}: 未含 SKILL.md，已跳过",
        "zip_error": "{fn}: {error}",

        # Errors
        "error_bedrock_unavailable": "Bedrock 不可达",
        "error_anthropic_key_missing": "未配置 ANTHROPIC_API_KEY",
        "error_invalid_model": "模型 {model} 非 Claude，Anthropic 代理不支持",
        "error_auth_fail": "认证失败",

        # Server
        "server_title": "AgentCore Studio",
        "server_start": "AgentCore Studio  →  http://{host}:{port}   (Ctrl+C 退出)",
    },
    "en": {
        # UI Messages
        "app_title": "AgentCore Studio",
        "app_desc": "Drag-drop orchestration · Deploy your AI Agent in one click | From canvas to cloud in minutes",
        "not_published": "Not published yet, please click the 'Publish' button first",
        "not_published_detail": "Not published (no ready Agent with the same name found in cloud either)",
        "missing_runtime": "Runtime component is required",
        "missing_name": "Missing name or region",
        "publish_button": "Publish",
        "deploy_button": "Deploy to Cloud",
        "playground_label": "Playground",
        "send_message": "Send",
        "system_prompt": "System Prompt",

        # Status Messages
        "deploying": "Deploying",
        "deploy_success": "Deployment succeeded",
        "deploy_fail": "Deployment failed",
        "no_output": "(no output)",
        "empty_response": "(empty response)",
        "deploy_completed": "Deployment completed successfully",
        "agent_created": "Agent created/updated",

        # Cloud Messages
        "cloud_deploy_fail": "Cloud invocation failed",
        "cloud_error": "Cloud invocation failed:",
        "region_mismatch": "Detected region mismatch: old deployment in {old}, new target {target}. Cleared local state. Will create fresh in {target}",
        "runtime_not_found": "Runtime {name} not found",
        "no_agentcore_cli": "@aws/agentcore CLI not found, please run: npm install -g @aws/agentcore@preview",
        "no_agentcore_runtime": "uv / agentcore CLI not found, cannot invoke cloud runtime",
        "harness_invoke_fail": "Harness invocation failed:",

        # Config Messages
        "available_tools": "Available tools",
        "available_skills": "Skills",
        "no_tools": "none",
        "mock_response": "Configure real model credentials to return actual Agent responses",
        "job_not_found": "job not found",
        "list_fail": "list failed",
        "delete_fail": "delete failed",
        "zip_skip": "{fn}: missing SKILL.md, skipped",
        "zip_error": "{fn}: {error}",

        # Errors
        "error_bedrock_unavailable": "Bedrock unavailable",
        "error_anthropic_key_missing": "ANTHROPIC_API_KEY not configured",
        "error_invalid_model": "Model {model} is not Claude, Anthropic proxy does not support it",
        "error_auth_fail": "Authentication failed",

        # Server
        "server_title": "AgentCore Studio",
        "server_start": "AgentCore Studio  →  http://{host}:{port}   (Ctrl+C to exit)",
    },
    "fr": {
        # UI Messages
        "app_title": "AgentCore Studio",
        "app_desc": "Orchestration par glisser-déposer · Déployez votre Agent IA en un clic | Du canvas au cloud en minutes",
        "not_published": "Pas encore publié, veuillez d'abord cliquer sur « Publier »",
        "not_published_detail": "Non publié (aucun Agent prêt avec le même nom trouvé dans le cloud non plus)",
        "missing_runtime": "Le composant Runtime est obligatoire",
        "missing_name": "Name ou region manquant",
        "publish_button": "Publier",
        "deploy_button": "Déployer vers le cloud",
        "playground_label": "Playground",
        "send_message": "Envoyer",
        "system_prompt": "Indicatif système",

        # Status Messages
        "deploying": "Déploiement en cours",
        "deploy_success": "Déploiement réussi",
        "deploy_fail": "Déploiement échoué",
        "no_output": "(pas de sortie)",
        "empty_response": "(réponse vide)",
        "deploy_completed": "Déploiement effectué avec succès",
        "agent_created": "Agent créé/mis à jour",

        # Cloud Messages
        "cloud_deploy_fail": "Invocation du cloud échouée",
        "cloud_error": "Invocation du cloud échouée :",
        "region_mismatch": "Décalage de région détecté : ancien déploiement dans {old}, nouvelle cible {target}. État local effacé. Sera créé à nouveau dans {target}",
        "runtime_not_found": "Runtime {name} non trouvé",
        "no_agentcore_cli": "@aws/agentcore CLI non trouvé, veuillez exécuter : npm install -g @aws/agentcore@preview",
        "no_agentcore_runtime": "uv / agentcore CLI non trouvé, impossible d'invoquer le runtime du cloud",
        "harness_invoke_fail": "Invocation Harness échouée :",

        # Config Messages
        "available_tools": "Outils disponibles",
        "available_skills": "Compétences",
        "no_tools": "aucun",
        "mock_response": "Configurez les identifiants réels du modèle pour retourner les réponses d'Agent réel",
        "job_not_found": "job not found",
        "list_fail": "l'affichage a échoué",
        "delete_fail": "la suppression a échoué",
        "zip_skip": "{fn} : SKILL.md manquant, ignoré",
        "zip_error": "{fn} : {error}",

        # Errors
        "error_bedrock_unavailable": "Bedrock indisponible",
        "error_anthropic_key_missing": "ANTHROPIC_API_KEY non configuré",
        "error_invalid_model": "Le modèle {model} n'est pas Claude, le proxy Anthropic ne le supporte pas",
        "error_auth_fail": "Authentification échouée",

        # Server
        "server_title": "AgentCore Studio",
        "server_start": "AgentCore Studio  →  http://{host}:{port}   (Ctrl+C pour quitter)",
    },
}

class I18n:
    def __init__(self, lang="en"):
        self.lang = lang if lang in TRANSLATIONS else "en"

    def t(self, key, **kwargs):
        """Get translation by key, with optional format parameters."""
        text = TRANSLATIONS.get(self.lang, {}).get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text

    def set_lang(self, lang):
        """Switch language."""
        if lang in TRANSLATIONS:
            self.lang = lang

    def supported_languages(self):
        """Return list of supported language codes."""
        return list(TRANSLATIONS.keys())

# Global i18n instance, defaulting to English
i18n = I18n("en")

def set_language(lang):
    """Set global language."""
    i18n.set_lang(lang)

def t(key, **kwargs):
    """Shorthand for translation lookup."""
    return i18n.t(key, **kwargs)

def get_supported_languages():
    """Get list of supported languages."""
    return i18n.supported_languages()
