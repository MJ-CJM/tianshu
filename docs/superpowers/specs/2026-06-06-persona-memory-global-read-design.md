# persona 全局记忆访问开关(memory_global_read)— 设计文档

| 项 | 值 |
|---|---|
| 日期 | 2026-06-06 |
| 议题 | B（接议题 A 之后） |
| 状态 | 设计已确认，待 review → writing-plans |
| 范围 | `src/tianshu/persona/`、`storage.py`、`tools/memory_tools.py`、`gateway/api.py`、`web/src/` |

---

## 1. 背景

议题 A 已让记忆召回走全量 FTS5。`memory_search` 工具(`memory_tools.py:_memory_search`)按调用方 persona 自动限定可见范围:

```python
visible_ids = [caller.id, "court"]            # + f"_dept_{caller.department}"
ids = fts_search(storage._conn, query, persona_ids=visible_ids, limit=limit)
```

底层 `fts_search` 在 `persona_ids=None` 时即**跨全 persona 检索**(已有分支)。需求:给 persona 加一个开关,开启后该 persona 主动检索时可查所有 persona 的记忆。

## 2. 目标与验收

1. `AgentPersona` 新增布尔字段 `memory_global_read`(默认 `False`)。
2. 开关为 `True` 时,该 persona 调 `memory_search` 可检索**所有 persona**的记忆条目(绕过 self/dept/court 限定)。
3. 开关为 `False`(默认)时,行为与现状完全一致(限 self + court + dept)。
4. 字段在 persona 的「DB ⇄ 内存 ⇄ 文件 frontmatter」往返中正确保留。
5. 前端 persona 创建/编辑表单可勾选该开关,详情页可展示。
6. 旧数据库平滑升级(migration),无需手动干预。

## 3. 非目标

- **只影响主动检索**(`memory_search` 工具)。执行前自动注入(`_recall_fulltext`)与 drawer L2 召回**不受影响**,仍限 self+court(避免把别的 persona 私有记忆塞进 prompt)。
- `persona_query` 工具**不改**——`memory_global_read` 是内部读权限,不进 persona 查询/展示。
- 不引入分级权限,仅布尔开关。

## 4. 设计

### 4.1 字段

`AgentPersona`(`persona/model.py`)新增,紧随 `can_delegate` 风格:
```python
memory_global_read: bool = False  # 高权限：绕过记忆访问控制，可读所有 persona 的记忆
```

### 4.2 核心判断(`memory_tools.py` `_memory_search`)

```python
caller = get_current_persona()
visible_ids: list[str] | None = None
if caller and getattr(caller, "id", None) and not getattr(caller, "memory_global_read", False):
    visible_ids = [caller.id, "court"]
    dept = getattr(caller, "department", None)
    if dept:
        visible_ids.append(f"_dept_{dept}")
ids = fts_search(storage._conn, query, persona_ids=visible_ids, limit=limit)
```

即在原限定条件上追加 `and not ...memory_global_read`:开关开 → 不进限定分支 → `visible_ids` 保持 `None` → `fts_search` 跨全 persona。无 caller(非 agent 上下文)时维持现状(`None`,向后兼容)。

### 4.3 持久化贯通(照 `can_delegate` 模板)

| 文件:行(can_delegate 参照) | 改动 |
|---|---|
| `persona/model.py:27` | 加字段 |
| `storage.py:248` DDL | (跟随 `skills_allowed` 惯例,只走 migration,不动 DDL) |
| `storage.py:_migrate()` ~:533 | 加 `ALTER TABLE personas ADD COLUMN memory_global_read INTEGER DEFAULT 0`(沿用现有逐条 ALTER 的容错写法) |
| `storage.py:save_persona:2116/2128` | INSERT 列 + `int(persona.get("memory_global_read", False))` |
| `storage.py:update_persona:2155/2166` | 字段列表 + `elif key == "memory_global_read"` 的 SET(bool→int) |
| `storage.py:_row_to_persona_dict:2295` | `"memory_global_read": bool(row["memory_global_read"]) if "memory_global_read" in keys else False` |
| `loader.py:_dict_to_persona:240` | `memory_global_read=d.get("memory_global_read", False)` |
| `loader.py:_persona_to_dict:201` | `"memory_global_read": persona.memory_global_read` |
| `loader.py:_load_persona_from_dir:184` | `memory_global_read=meta.get("memory_global_read", False)`(SOUL.md frontmatter) |

### 4.4 API 透传(`gateway/api.py`,照 `can_delegate` :1459/1567/1581/1703)

persona 的 list / create / get / update 端点各加一处 `memory_global_read` 字段读写。

### 4.5 前端(照 `can_delegate`)

| 文件 | 改动 |
|---|---|
| `web/src/api/types.ts:539/553/592` | Persona / CreatePersona / UpdatePersona 三接口加 `memory_global_read?: boolean` |
| `PersonaDashboardPage.tsx:457/602` | 创建表单默认值 + `Form.Item` checkbox |
| `PersonaDetailPage.tsx:226/828/1058` | 详情展示 Tag + 编辑表单初值 + 编辑 checkbox |
| `i18n/locales/{zh-classic,en,zh-modern}.json` | 加 `memoryGlobalRead` label(form + detail 两处,与 `canDelegate` 同级) |

### 4.6 安全

- 默认 `False`,显式开启才生效。
- 字段注释明确标注「绕过记忆访问控制的高权限读开关」。
- `memory_search` 是 `T0_READONLY`、已带 caller 上下文,审计链路不变。

## 5. 测试

- `_memory_search`:caller `memory_global_read=True` → 能查到**别的 persona** 的 `private` 记忆;`=False` → 查不到(仍限 self+court+dept)。
- persona 往返:`save_persona` → `list_personas`/`_dict_to_persona` 后 `memory_global_read` 保留。
- 旧库 migration:无该列的库经 `_migrate` 后可读写该字段。

> 遵循 `feedback_test_last`:功能优先,测试统一补。上表为验收口径。

## 6. 风险

- **字段贯通遗漏**:9+ 处后端 + 前端,漏一处会导致开关"配了不生效"或"存了读不回"。靠 `can_delegate` 全落点清单逐处对照 + 往返测试兜底。
- **migration 容错**:`ALTER ADD COLUMN` 对已有该列的库会报错;须沿用 `_migrate` 现有逐条容错写法(确认其 try/except 粒度后照搬)。
