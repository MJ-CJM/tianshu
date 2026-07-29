# 统一模型注册表（百官侧模型配置）

> 2026-07-29 落地。借鉴 pi（provider 声明式内置 + models.json 用户层 + models.dev 目录）
> 与 hermes-agent（ProviderProfile 插件 + 目录多级缓存）的模型配置方式；
> 天枢是 server+web 形态，配置真源为 DB+API+UI 而非用户手编文件。
> 客卿侧（默认模型持久化、凭证注入、LLM Gateway forward）属后续专项，见文末。

## 四层架构

```
┌ ① ProviderProfile 内置声明        src/tianshu/providers/profiles.py
│    ~18 条：anthropic/openai/deepseek/zhipu/moonshot/minimax/dashscope/
│    siliconflow/volcengine/openrouter/gemini/groq/ollama/custom
│    + coding plan：zhipu-coding/kimi-coding/minimax-coding/qwen-coding
│    字段：litellm_prefix / api_protocol / default_base_url / key_env /
│          models_dev_id / billing(per_token|subscription) / cache_usage_field /
│          supports_prompt_caching / aliases(迁移回填匹配)
├ ② model_providers 表（用户 provider 实例，只存与 profile 的差异）
│    api_key_ref 三态：'' = 落 profile.key_env 环境变量
│                      'credential' = network_credentials(kind='llm_provider') 加密凭证
│                      '$ENV:NAME' = 显式环境变量引用
│    门面：src/tianshu/providers/registry.py（CRUD/resolve_key/test_connectivity）
├ ③ 模型目录 catalog（不入 DB）      src/tianshu/providers/model_catalog.py
│    打包快照 resources/models_catalog.json（scripts/fetch_models_catalog.py 生成，
│    只收录 profile 引用的 provider，~650 模型）→ 磁盘缓存 ~/.tianshu/cache/ →
│    POST /api/model-catalog/refresh 手动拉 models.dev
│    定价 USD/1M → CNY/1K（汇率 TIANSHU_LLM_USD_CNY_RATE，默认 7.2）；
│    subscription 归零；litellm.model_cost 兜底
└ ④ llm_configs 表 =「命名模型指派」（name → provider_id + model + 采样参数）
     api_key 明文列已被迁移 0020 物理删除；LLMConfigState.api_key 是运行时解析结果
     persona.llm_config_name / 任务槽位 绑"配置名"，语义不变
```

## 关键机制

- **模型串文法** `provider/model[:thinking]`（providers/model_ref.py）：`:suffix`
  仅当 ∈ {off,minimal,low,medium,high,xhigh} 才剥离（保住 `ollama/qwen3:32b`）；
  `normalize_for_allowlist()` 供白名单比对（客卿专项接线）。
- **litellm 仍是唯一调用层**：profile 不带 transport hook，只声明 litellm_prefix。
  `LLMClient` 新增 litellm_prefix / usage_dialect / prompt_caching 三个声明式参数
  （None = 回落模型名启发式旧路径，供 doctor/沙箱评估直构場景）。
  `llm.py::_resolve_model` 的 api_base 子串猜测（_PROVIDER_HINTS）降级为该旧路径兜底。
- **定价**：cost/tracker.py 的 `_DEFAULT_PRICING` 硬编码字典已删除；
  `lookup_pricing` 走目录解析器（wiring 注入配置实例；未装配时懒建打包快照默认目录）。
- **迁移**（storage/migrations.py，photo 0008 先例）：
  - 0019_model_providers：建表 + llm_configs 加 provider_id 列 + 按
    aliases/模型前缀/裸模型启发式回填（匹配不上 → custom-<hash>，不猜前缀）。
  - 0020_encrypt_llm_config_keys：明文 key 加密入 network_credentials → 临时表
    `_llm_configs_v20` 重建删列（secure_delete + `_RESERVED_TEMP_TABLES` 登记 +
    `_SENSITIVE_MIGRATION_NAMES` WAL 截断）。**明文 key 存在且
    TIANSHU_SECRET_MASTER_KEY 缺失时迁移拒绝启动**（与 0008 同语义，错误信息给出生成命令）。
  - 0021_app_settings：KV 表；AgentConfigState 全量持久化（修复重启即丢），
    含 task_slots / keqing_* 字段。
- **env 种子**：TIANSHU_LLM_* 仍仅库空时生效；首启转译为 provider 实例 +
  `$ENV:TIANSHU_LLM_API_KEY` 引用（wiring 把 .env 值 setdefault 进程 env）。
  TIANSHU_EVAL_LLM_* 沙箱直连与 doctor 保持凭证隔离，不入注册表。
- **任务槽位**（config_manager.TASK_SLOTS）：court(廷议)/memory(记忆四件套)/
  synthesis(人格合成)/edict_parse(敕令解析) 绑配置名，
  `ProviderManager.get_client_for_slot(slot, temperature=…, max_tokens=…)`；
  未配置一律落全局 active。已收敛的原直构点：memory/{diarist,historian,compactor,reflect}、
  consultation/{session,synthesizer}、edicts_api(原硬编码 deepseek-flash)、
  profile_synthesizer(原死代码默认参数)。diagnostics/doctor 刻意保持直连。

## API 面

- 新增 `/api/model-providers*`（profiles / CRUD / key / models / test）与
  `/api/model-catalog/{status,refresh}`（gateway/model_providers_api.py）。
- `/api/configs` 增 provider_id（收 api_key 时写穿注册表）；legacy `GET/PUT /config`
  已标 deprecated + Deprecation 头。
- `/api/providers`（运行时配额/定价覆盖表）保留，与 model_providers 分工：
  供应商/凭证 vs 配额/覆盖价；`/providers/pricing/defaults` 改返回目录快照状态。

## 后续客卿专项（本次不做，登记不丢）

1. keqing_default_models 持久化接线（app_settings 底座已就绪）
2. PiAdapter.auth_env_vars 硬编码 env 白名单 → profile 派生注入（vault key →
   keqing_env_map → 子进程；coding plan 对 claude-code 的 ANTHROPIC_BASE_URL/AUTH_TOKEN 重定向）
3. LLM Gateway `_default_forward` 用 registry 的 provider→(base_url,key) 实现真实转发
4. scoped_token.allows_model 接 normalize_for_allowlist（修 `:thinking` 匹配 bug）
5. web KeqingManagementPage / EdictForm executor_model 接 ModelSelect 与目录 API
