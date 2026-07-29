"""内置 ProviderProfile 声明清单 —— 模型供应商的唯一静态真相源。

设计（借鉴 hermes ProviderProfile / pi builtinProviders，大幅简化）：
- litellm 是唯一 transport，profile 不带任何协议 hook；差异全部收敛为声明字段。
- DB（model_providers 表）只存用户差异（base_url 覆盖 / key 引用 / 启停）；
  身份、litellm 前缀、默认端点、env 名、目录映射全在这里。
- 新增一个供应商 = 在 BUILTIN_PROFILES 加一条声明，下游（模型串解析、
  目录、定价、cache 统计、web 下拉）自动生效。
"""

from __future__ import annotations

from dataclasses import dataclass

CUSTOM_PROFILE_ID = "custom"


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    display_name: str
    # litellm 模型串前缀；"" = 不加前缀（模型名原样交给 litellm）。
    litellm_prefix: str
    # 请求协议（信息性 + prompt caching 判定）：openai 兼容 / anthropic messages。
    api_protocol: str = "openai"
    # 默认端点；"" = litellm 内置默认（anthropic/openai 官方直连等）。
    default_base_url: str = ""
    # key 的规范 env 变量名；"" = 无需鉴权（ollama 本地）。
    key_env: str = ""
    # models.dev 目录 provider id；"" = 无目录（custom/ollama/volcengine）。
    models_dev_id: str = ""
    # per_token | subscription；subscription（coding plan）定价归零。
    billing: str = "per_token"
    # usage 对象 cache-hit 字段方言：anthropic | deepseek | openai。
    cache_usage_field: str = "openai"
    # 是否注入 anthropic cache_control 断点（仅 anthropic 官方直连确认支持）。
    supports_prompt_caching: bool = False
    # api_base 子串匹配（迁移回填旧 llm_configs 用）。
    aliases: tuple[str, ...] = ()
    notes: str = ""


BUILTIN_PROFILES: tuple[ProviderProfile, ...] = (
    ProviderProfile(
        id="anthropic",
        display_name="Anthropic",
        litellm_prefix="anthropic",
        api_protocol="anthropic",
        key_env="ANTHROPIC_API_KEY",
        models_dev_id="anthropic",
        cache_usage_field="anthropic",
        supports_prompt_caching=True,
        aliases=("anthropic",),
    ),
    ProviderProfile(
        id="openai",
        display_name="OpenAI",
        litellm_prefix="openai",
        key_env="OPENAI_API_KEY",
        models_dev_id="openai",
        aliases=("api.openai.com",),
    ),
    ProviderProfile(
        id="deepseek",
        display_name="DeepSeek 深度求索",
        litellm_prefix="deepseek",
        default_base_url="https://api.deepseek.com",
        key_env="DEEPSEEK_API_KEY",
        models_dev_id="deepseek",
        cache_usage_field="deepseek",
        aliases=("deepseek",),
    ),
    ProviderProfile(
        id="zhipu",
        display_name="智谱 GLM",
        litellm_prefix="openai",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        key_env="ZHIPU_API_KEY",
        models_dev_id="zhipuai",
        aliases=("bigmodel.cn",),
    ),
    ProviderProfile(
        id="moonshot",
        display_name="月之暗面 Kimi",
        litellm_prefix="openai",
        default_base_url="https://api.moonshot.cn/v1",
        key_env="MOONSHOT_API_KEY",
        models_dev_id="moonshotai-cn",
        aliases=("moonshot",),
    ),
    ProviderProfile(
        id="minimax",
        display_name="MiniMax",
        litellm_prefix="openai",
        default_base_url="https://api.minimaxi.com/v1",
        key_env="MINIMAX_API_KEY",
        models_dev_id="minimax-cn",
        aliases=("minimaxi", "minimax"),
    ),
    ProviderProfile(
        id="dashscope",
        display_name="阿里云百炼 Qwen",
        litellm_prefix="openai",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        key_env="DASHSCOPE_API_KEY",
        models_dev_id="alibaba-cn",
        aliases=("dashscope",),
    ),
    ProviderProfile(
        id="siliconflow",
        display_name="硅基流动 SiliconFlow",
        litellm_prefix="openai",
        default_base_url="https://api.siliconflow.cn/v1",
        key_env="SILICONFLOW_API_KEY",
        models_dev_id="siliconflow-cn",
        aliases=("siliconflow",),
    ),
    ProviderProfile(
        id="volcengine",
        display_name="火山方舟（豆包）",
        litellm_prefix="openai",
        default_base_url="https://ark.cn-beijing.volces.com/api/v3",
        key_env="ARK_API_KEY",
        aliases=("volces.com", "volcengine"),
        notes="models.dev 无目录条目；模型 id（ep-xxx 或模型名）需手动填写",
    ),
    ProviderProfile(
        id="openrouter",
        display_name="OpenRouter",
        litellm_prefix="openrouter",
        default_base_url="https://openrouter.ai/api/v1",
        key_env="OPENROUTER_API_KEY",
        models_dev_id="openrouter",
        aliases=("openrouter",),
    ),
    ProviderProfile(
        id="gemini",
        display_name="Google Gemini",
        litellm_prefix="gemini",
        key_env="GEMINI_API_KEY",
        models_dev_id="google",
        aliases=("generativelanguage.googleapis.com",),
    ),
    ProviderProfile(
        id="groq",
        display_name="Groq",
        litellm_prefix="groq",
        key_env="GROQ_API_KEY",
        models_dev_id="groq",
        aliases=("groq",),
    ),
    ProviderProfile(
        id="ollama",
        display_name="Ollama（本地）",
        litellm_prefix="ollama",
        default_base_url="http://localhost:11434",
        aliases=("11434", "ollama"),
        notes="无需 key；模型清单来自本地实例，目录不适用",
    ),
    # --- Coding Plan（订阅制，定价归零；端点与 env 名以 models.dev 为准）---
    ProviderProfile(
        id="zhipu-coding",
        display_name="智谱 GLM Coding Plan",
        litellm_prefix="openai",
        default_base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        key_env="ZHIPU_API_KEY",
        models_dev_id="zhipuai-coding-plan",
        billing="subscription",
        aliases=("bigmodel.cn/api/coding",),
    ),
    ProviderProfile(
        id="kimi-coding",
        display_name="Kimi For Coding",
        litellm_prefix="openai",
        default_base_url="https://api.kimi.com/coding/v1",
        key_env="KIMI_API_KEY",
        models_dev_id="kimi-for-coding",
        billing="subscription",
        aliases=("api.kimi.com",),
    ),
    ProviderProfile(
        id="minimax-coding",
        display_name="MiniMax Coding Plan",
        litellm_prefix="anthropic",
        api_protocol="anthropic",
        default_base_url="https://api.minimaxi.com/anthropic",
        key_env="MINIMAX_API_KEY",
        models_dev_id="minimax-cn-coding-plan",
        billing="subscription",
        cache_usage_field="anthropic",
        aliases=("minimaxi.com/anthropic",),
    ),
    ProviderProfile(
        id="qwen-coding",
        display_name="阿里 Qwen Coding Plan",
        litellm_prefix="openai",
        default_base_url="https://coding.dashscope.aliyuncs.com/v1",
        key_env="ALIBABA_CODING_PLAN_API_KEY",
        models_dev_id="alibaba-coding-plan-cn",
        billing="subscription",
        aliases=("coding.dashscope",),
    ),
    ProviderProfile(
        id=CUSTOM_PROFILE_ID,
        display_name="自定义（OpenAI 兼容）",
        litellm_prefix="openai",
        notes="任意 OpenAI 兼容端点；base_url 必填，模型 id 手动填写",
    ),
)

_PROFILES_BY_ID: dict[str, ProviderProfile] = {p.id: p for p in BUILTIN_PROFILES}


def get_profile(profile_id: str) -> ProviderProfile | None:
    return _PROFILES_BY_ID.get(profile_id)


def custom_profile() -> ProviderProfile:
    return _PROFILES_BY_ID[CUSTOM_PROFILE_ID]


# 裸模型名（无 api_base、无前缀段）的保守启发式；仅在前两级匹配都落空时使用。
_BARE_MODEL_HINTS: tuple[tuple[str, str], ...] = (
    ("claude", "anthropic"),
    ("gpt-", "openai"),
    ("chatgpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("deepseek", "deepseek"),
    ("gemini", "gemini"),
    ("glm", "zhipu"),
    ("moonshot", "moonshot"),
    ("kimi", "moonshot"),
    ("qwen", "dashscope"),
)


def match_profile_for_config(model: str, api_base: str) -> ProviderProfile | None:
    """运行期为 (model, api_base) 组合匹配 profile（自动供给 provider 实例用）。

    匹配次序：api_base 别名 → 模型前缀段（``anthropic/...``）→ 裸模型启发式。
    匹配不上返回 None，调用方落 custom——宁可保守也不猜错前缀。
    """
    profile = match_profile_by_base_url(api_base)
    if profile is not None:
        return profile
    if "/" in model:
        head = model.split("/", 1)[0].lower()
        profile = get_profile(head)
        if profile is not None:
            return profile
    if not api_base:
        lowered = model.lower()
        for prefix, profile_id in _BARE_MODEL_HINTS:
            if lowered.startswith(prefix):
                return get_profile(profile_id)
    return None


def match_profile_by_base_url(api_base: str) -> ProviderProfile | None:
    """按 api_base 子串匹配 profile（迁移回填旧 llm_configs 用）。

    宁可不匹配也不猜错：匹配不上返回 None，调用方落 custom。
    aliases 更长（更具体）的 profile 优先，避免 "minimax" 误吞
    "minimaxi.com/anthropic" 这类前缀重叠。
    """
    base = (api_base or "").lower()
    if not base:
        return None
    best: ProviderProfile | None = None
    best_len = 0
    for profile in BUILTIN_PROFILES:
        for alias in profile.aliases:
            if alias in base and len(alias) > best_len:
                best = profile
                best_len = len(alias)
    return best
