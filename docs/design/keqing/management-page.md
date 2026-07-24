# 客卿管理页设计

> 2026-07-24。前置:[执行主体本体论·百官(内臣) vs 客卿(外臣)](../domain-model.md#5-执行主体本体论百官内臣-vs-客卿外臣)。

## 定位

客卿是**外臣**(外聘 coding agent),非百官。本页治理「外聘人才」——能力/健康/隔离/治理策略,**不含**人格(SOUL/ROLE)、京察、自进化(那是百官品类)。**红线**:别把客卿管理做成百官 dashboard 的翻版。

## 三块

1. **注册表 · 健康体检**(只读):每 backend 的安装/版本(pi 0.79.3 vs 钉死 0.81.1 → drift 红字)、能力声明(会话续用/插话/验收/权限塑形/用量)、凭证来源。
2. **治理默认**(可编辑):默认模型、凭证网关开关、per-run 预算、模型白名单。
3. **凭证**(只读):来源提示,**无 raw key 输入框**(守 P3/P4 凭证隔离)。

## 实现

- **后端**:`agent_config` 加 4 字段(`keqing_default_model`/`keqing_gateway_enabled`/`keqing_per_run_budget_cny`/`keqing_model_allowlist`),沿用 `/agent-config` GET/PUT 暴露链(config.py state → models/api.py `AgentConfig`+Update → config_api.py 映射)。`GET /api/keqing/status` 只读体检:`installed` 用 `shutil.which`、版本从 CLI package.json **读文件**取(不 spawn 进程,守 `test_no_direct_process_launch`),drift = installed≠pinned。
- **前端**:`pages/KeqingManagementPage.tsx` + `router/AppRoutes` `/keqing` + `navigation/departments` group-system 导航项 + 三语 i18n。执行器选 keqing:* 时 `EdictForm` 显示 `executor_model` 可选输入。
- **测试**:后端 `test_keqing_status.py`(9)+ config round-trip;前端 `KeqingManagementPage.test.tsx`(2)。

## 本期不做

guard deny/allow 工具策略可视化编辑(v2)· raw key 存储/输入(永不做,违反凭证隔离)· 客卿 persona/京察/自进化(永不做,违反本体论)。
