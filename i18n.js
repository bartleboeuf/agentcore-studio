// Multi-language support (EN, FR, ZH) for AgentCore Studio frontend
const TRANSLATIONS = {
  zh: {
    // UI Labels
    app_title: "AgentCore Studio",
    app_desc: "拖拽编排 · 一键上线你的 AI Agent ｜ From canvas to cloud in minutes",
    playground: "Playground",
    artifacts: "产物",
    registry: "Registry",
    publish_button: "发布",
    deploy_button: "部署到云端",
    send_button: "发送",
    system_prompt: "系统提示",
    component_title: "组件",
    settings: "设置",
    region: "区域",
    model: "模型",

    // Status Messages
    publishing: "发布中...",
    deploying: "部署中...",
    deploy_success: "部署成功",
    deploy_failed: "部署失败",
    published: "已发布",
    not_published: "未发布",

    // Component Names
    runtime: "Runtime",
    memory: "Memory",
    gateway: "Gateway",
    identity: "Identity",
    policy: "Policy",
    observability: "Observability",

    // Errors
    error_required: "必填项",
    error_invalid: "无效输入",
    error_loading: "加载失败",
    error_network: "网络错误",

    // Confirmations
    confirm_delete: "确认删除？",
    confirm_deploy: "确认部署到云端？",
  },
  en: {
    // UI Labels
    app_title: "AgentCore Studio",
    app_desc: "Drag-drop orchestration · Deploy your AI Agent in one click | From canvas to cloud in minutes",
    playground: "Playground",
    artifacts: "Artifacts",
    registry: "Registry",
    publish_button: "Publish",
    deploy_button: "Deploy to Cloud",
    send_button: "Send",
    system_prompt: "System Prompt",
    component_title: "Components",
    settings: "Settings",
    region: "Region",
    model: "Model",

    // Status Messages
    publishing: "Publishing...",
    deploying: "Deploying...",
    deploy_success: "Deployment succeeded",
    deploy_failed: "Deployment failed",
    published: "Published",
    not_published: "Not published",

    // Component Names
    runtime: "Runtime",
    memory: "Memory",
    gateway: "Gateway",
    identity: "Identity",
    policy: "Policy",
    observability: "Observability",

    // Errors
    error_required: "Required field",
    error_invalid: "Invalid input",
    error_loading: "Failed to load",
    error_network: "Network error",

    // Confirmations
    confirm_delete: "Confirm delete?",
    confirm_deploy: "Confirm deploy to cloud?",
  },
  fr: {
    // UI Labels
    app_title: "AgentCore Studio",
    app_desc: "Orchestration par glisser-déposer · Déployez votre Agent IA en un clic | Du canvas au cloud en minutes",
    playground: "Playground",
    artifacts: "Artefacts",
    registry: "Registre",
    publish_button: "Publier",
    deploy_button: "Déployer vers le cloud",
    send_button: "Envoyer",
    system_prompt: "Indicatif système",
    component_title: "Composants",
    settings: "Paramètres",
    region: "Région",
    model: "Modèle",

    // Status Messages
    publishing: "Publication en cours...",
    deploying: "Déploiement en cours...",
    deploy_success: "Déploiement réussi",
    deploy_failed: "Déploiement échoué",
    published: "Publié",
    not_published: "Non publié",

    // Component Names
    runtime: "Runtime",
    memory: "Mémoire",
    gateway: "Passerelle",
    identity: "Identité",
    policy: "Politique",
    observability: "Observabilité",

    // Errors
    error_required: "Champ obligatoire",
    error_invalid: "Entrée invalide",
    error_loading: "Échec du chargement",
    error_network: "Erreur réseau",

    // Confirmations
    confirm_delete: "Confirmer la suppression ?",
    confirm_deploy: "Confirmer le déploiement vers le cloud ?",
  },
};

class I18n {
  constructor(lang = "en") {
    this.lang = TRANSLATIONS[lang] ? lang : "en";
  }

  t(key, vars = {}) {
    let text = TRANSLATIONS[this.lang][key] || key;
    // Simple variable substitution: {varName} → value
    Object.entries(vars).forEach(([k, v]) => {
      text = text.replace(`{${k}}`, v);
    });
    return text;
  }

  setLang(lang) {
    if (TRANSLATIONS[lang]) {
      this.lang = lang;
    }
  }

  getLang() {
    return this.lang;
  }

  getSupportedLangs() {
    return Object.keys(TRANSLATIONS);
  }
}

// Global instance (default English)
const i18n = new I18n(
  localStorage.getItem("language") || navigator.language.split("-")[0] || "en"
);

// Helper function for quick access
function t(key, vars = {}) {
  return i18n.t(key, vars);
}

// Export for module systems
if (typeof module !== "undefined" && module.exports) {
  module.exports = { I18n, i18n, t };
}
