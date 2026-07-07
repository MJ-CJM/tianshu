# persona 全局记忆访问开关(memory_global_read)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** persona 加 `memory_global_read` 布尔开关,开启后该 persona 用 `memory_search` 主动检索时可查所有 persona 的记忆(绕过 self/dept/court 限定)。

**Architecture:** 照已有布尔字段 `can_delegate` 的贯通模式,把 `memory_global_read` 加到「model → storage(migration/save/update/read)→ loader → API → 前端」全链路;核心在 `_memory_search` 加一个判断:开关开 → `visible_ids=None` → `fts_search` 跨全 persona。只影响主动检索,自动注入/drawer 不变。

**Tech Stack:** Python 3.12、SQLite、FastAPI、pydantic;前端 React + Ant Design + i18n。

> **测试策略**:遵循 `feedback_test_last`(功能优先、测试统一补)。Task 1–4 实现 + 手动验证,pytest 集中在 Task 5。Python 命令用 `.venv/bin/python`。
>
> **关键参照**:本计划高度机械——几乎每处都是「照 `can_delegate` 在同位置加 `memory_global_read`」。`can_delegate` 的全落点可用 `rg -n 'can_delegate|canDelegate' src/ web/src/` 随时对照。

---

## File Structure(按层)

| 层 | 文件 | 改动 |
|---|---|---|
| Model | `persona/model.py` | 加字段 |
| Storage | `storage.py` | migration + save + update + read 四处 |
| Loader | `persona/loader.py` | dict↔persona + frontmatter 三处 |
| 核心 | `tools/memory_tools.py` | `_memory_search` 加判断(议题 B 独有) |
| API | `gateway/api.py` | persona list/create/get/update 透传 |
| 前端 | `web/src/api/types.ts`、`PersonaDashboardPage.tsx`、`PersonaDetailPage.tsx`、`i18n/*.json` | 类型 + 表单 + 展示 + label |
| 测试 | `tests/persona/`、`tests/memory/` | 往返 + 核心判断 |

---

### Task 1: 后端字段贯通(model + storage + loader)

**Files:**
- Modify: `src/tianshu/persona/model.py`、`src/tianshu/storage.py`、`src/tianshu/persona/loader.py`

- [ ] **Step 1: model 加字段**

`persona/model.py` 在 `can_delegate: bool = False`(:27)之后加:
```python
    memory_global_read: bool = False  # 高权限：绕过记忆访问控制，可读所有 persona 的记忆
```

- [ ] **Step 2: storage migration**

`storage.py` 的 `_migrate()` 里 `migrations` list(persona 段,`skills_allowed`/`llm_config_name` 那几行附近 ~:535)加一行:
```python
            # Phase 8: persona 全局记忆读开关
            "ALTER TABLE personas ADD COLUMN memory_global_read INTEGER DEFAULT 0",
```

- [ ] **Step 3: storage save_persona**

`save_persona`(:2114)的 INSERT,在 `can_delegate` 列与值各加一处(列清单加 `memory_global_read`、VALUES 加一个 `?`、values tuple 在 `int(persona.get("can_delegate", False)),` 之后加):
```python
                    int(persona.get("memory_global_read", False)),
```
确保 INSERT 列名、`?` 占位、values 三者顺序对齐(把 memory_global_read 紧跟 can_delegate)。

- [ ] **Step 4: storage update_persona**

`update_persona`(:2153)的 `allowed` set 加 `"memory_global_read"`;并把 `can_delegate` 的 elif 扩展为同时处理两者:
```python
            elif key in ("can_delegate", "memory_global_read"):
                sets.append(f"{key} = ?")
                params.append(int(value))
```

- [ ] **Step 5: storage _row_to_persona_dict**

`_row_to_persona_dict`(:2284)在 `"can_delegate": bool(row["can_delegate"]),` 之后加(防御性,兼容旧库无此列):
```python
            "memory_global_read": bool(row["memory_global_read"]) if "memory_global_read" in keys else False,
```

- [ ] **Step 6: loader 三处**

`persona/loader.py`:
- `_load_persona_from_dir`(:184 `can_delegate=meta.get(...)` 后)加 `memory_global_read=meta.get("memory_global_read", False),`
- `_persona_to_dict`(:201 `"can_delegate": persona.can_delegate,` 后)加 `"memory_global_read": persona.memory_global_read,`
- `_dict_to_persona`(:240 `can_delegate=d.get(...)` 后)加 `memory_global_read=d.get("memory_global_read", False),`

- [ ] **Step 7: 手动验证——字段往返 + migration**

Run:
```bash
.venv/bin/python -c "
import tempfile, pathlib
from tianshu.storage import Storage
tmp = pathlib.Path(tempfile.mkdtemp())
s = Storage(str(tmp/'t.db')); s.init_db()
s.save_persona({'id':'wym','name':'王','department':'neige','can_delegate':False,'memory_global_read':True})
row = s.get_persona('wym')
assert row['memory_global_read'] is True, row
# 默认值
s.save_persona({'id':'x','name':'X','department':'neige'})
assert s.get_persona('x')['memory_global_read'] is False
print('OK persona field round-trip:', row['memory_global_read'])
"
```
Expected: `OK persona field round-trip: True`,无 AssertionError。

- [ ] **Step 8: Commit**

```bash
git add src/tianshu/persona/model.py src/tianshu/storage.py src/tianshu/persona/loader.py
git commit -m "feat(persona): 后端贯通 memory_global_read 字段（照 can_delegate）"
```

---

### Task 2: 核心判断(`_memory_search` 全局读)

**Files:**
- Modify: `src/tianshu/tools/memory_tools.py`(`_memory_search`,~:47-56)

- [ ] **Step 1: 在 caller 限定条件追加 memory_global_read 短路**

现状:
```python
        caller = get_current_persona()
        visible_ids: list[str] | None = None
        if caller and getattr(caller, "id", None):
            visible_ids = [caller.id, "court"]
            dept = getattr(caller, "department", None)
            if dept:
                visible_ids.append(f"_dept_{dept}")
```

替换为:
```python
        caller = get_current_persona()
        visible_ids: list[str] | None = None
        # memory_global_read 为真：跳过限定，visible_ids 保持 None → fts_search 跨全 persona 检索
        if (
            caller
            and getattr(caller, "id", None)
            and not getattr(caller, "memory_global_read", False)
        ):
            visible_ids = [caller.id, "court"]
            dept = getattr(caller, "department", None)
            if dept:
                visible_ids.append(f"_dept_{dept}")
```

- [ ] **Step 2: 手动验证——开关控制可见范围**

Run:
```bash
.venv/bin/python -c "
import tempfile, pathlib, asyncio
from unittest.mock import patch
from tianshu.storage import Storage
from tianshu.persona.model import AgentPersona
from tianshu.memory.models import MemoryEntry
from tianshu.tools.memory_tools import _memory_search
tmp = pathlib.Path(tempfile.mkdtemp())
s = Storage(str(tmp/'t.db')); s.init_db()
# 别的 persona 的私有记忆
s.save_memory_entry(MemoryEntry(persona_id='other', category='observation', content='机密 secret-xyz'))
def mk(gr):
    return AgentPersona(id='wym', name='王', department='neige', soul_path=tmp/'s', role_path=tmp/'r', memory_path=tmp/'m', memory_global_read=gr)
# 关：查不到别人的私有
with patch('tianshu.tools.memory_tools.get_current_persona', return_value=mk(False)):
    r = asyncio.run(_memory_search(s, query='secret-xyz'))
    assert 'secret-xyz' not in r.payload, 'OFF should not see other private'
# 开：查得到
with patch('tianshu.tools.memory_tools.get_current_persona', return_value=mk(True)):
    r = asyncio.run(_memory_search(s, query='secret-xyz'))
    assert 'secret-xyz' in r.payload, 'ON should see all'
print('OK global-read gate works')
"
```
Expected: `OK global-read gate works`(关→查不到别人私有;开→查得到)。若 `ToolResult` 的字段名不是 `.payload`,subagent 据实读 `tools/types.py` 的 `ok_result` 结构调整断言。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/memory_tools.py
git commit -m "feat(memory): memory_search 支持 memory_global_read 全局检索"
```

---

### Task 3: API 透传(`gateway/api.py`)

**Files:**
- Modify: `src/tianshu/gateway/api.py`(persona list/create/get/update,can_delegate 落点 :1459/1567/1581/1703 + update 端点调 update_persona 处)

- [ ] **Step 1: 照 can_delegate 在每个 persona 端点加 memory_global_read**

用 `rg -n 'can_delegate' src/tianshu/gateway/api.py` 定位全部落点,每处旁边照样加 `memory_global_read`:
- create 端点构造 `AgentPersona(...)`(:1567 `can_delegate=body.get("can_delegate", False),` 后)加 `memory_global_read=body.get("memory_global_read", False),`
- create 返回 dict(:1581 `"can_delegate": persona.can_delegate,` 后)加 `"memory_global_read": persona.memory_global_read,`
- list 端点返回 dict(:1459)同样加 `"memory_global_read": p.memory_global_read,`
- update 端点返回 dict(:1703)加 `"memory_global_read": updated.memory_global_read,`
- update 端点若显式列举传给 `update_persona`/`loader` 的字段(而非透传整个 body),把 `memory_global_read` 也纳入(对照该端点 can_delegate 的处理)。

- [ ] **Step 2: 手动验证——import 无误 + grep 对齐**

Run:
```bash
.venv/bin/python -c "import tianshu.gateway.api; print('IMPORT OK')" && \
echo "--- 对齐检查：每个 can_delegate 落点应有相邻 memory_global_read ---" && \
rg -n 'can_delegate|memory_global_read' src/tianshu/gateway/api.py
```
Expected: `IMPORT OK`;且 grep 输出里 persona 端点的 can_delegate 每处都有对应的 memory_global_read。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/gateway/api.py
git commit -m "feat(api): persona 端点透传 memory_global_read"
```

---

### Task 4: 前端 + i18n

**Files:**
- Modify: `web/src/api/types.ts`、`web/src/pages/PersonaDashboardPage.tsx`、`web/src/pages/PersonaDetailPage.tsx`、`web/src/i18n/locales/{zh-classic,en,zh-modern}.json`

- [ ] **Step 1: 照 can_delegate 在前端每个落点加 memory_global_read**

用 `rg -n 'can_delegate|canDelegate' web/src/` 定位全部落点,逐处照样加:
- `api/types.ts`(:539/553/592):Persona / CreatePersona / UpdatePersona 三接口,在 `can_delegate` 旁加 `memory_global_read?: boolean;`(Persona 主接口用非可选 `memory_global_read: boolean;`,与 `can_delegate` 一致)。
- `PersonaDashboardPage.tsx`:创建表单默认值(:457 `can_delegate: false,` 后)加 `memory_global_read: false,`;`Form.Item`(:602)旁照样加一个:
  ```tsx
  <Form.Item name="memory_global_read" label={t("persona.form.persona.field.memoryGlobalRead")} valuePropName="checked">
    <Switch />
  </Form.Item>
  ```
  (控件类型与 can_delegate 那项保持一致——若 can_delegate 用的是 `<Checkbox>` 而非 `<Switch>`,照它。)
- `PersonaDetailPage.tsx`:详情展示(:226 `Descriptions.Item` 的 canDelegate 那块后)加一项 `memoryGlobalRead`;编辑表单初值(:828)加 `memory_global_read: persona.memory_global_read,`;编辑 `Form.Item`(:1058)照加。
- i18n 三文件(`zh-classic`/`en`/`zh-modern`.json):在 `canDelegate` 的两处(form + detail,:1428/1464 附近)旁各加 `memoryGlobalRead`:
  - zh-classic / zh-modern:`"memoryGlobalRead": "全局读记忆"`
  - en:`"memoryGlobalRead": "Global memory read"`

- [ ] **Step 2: 手动验证——类型检查/构建通过**

先看 `web/package.json` 的 scripts,跑类型检查或构建(常见 `npm run build` 或 `npx tsc --noEmit`):
```bash
cd web && npm run build 2>&1 | tail -15
```
Expected: 构建/类型检查通过,无 TS 报错(尤其无 memory_global_read 相关类型缺失)。若 `web` 用 pnpm/yarn,据 `package.json`/lockfile 调整。

- [ ] **Step 3: Commit**

```bash
git add web/src/api/types.ts web/src/pages/PersonaDashboardPage.tsx web/src/pages/PersonaDetailPage.tsx web/src/i18n/locales/zh-classic.json web/src/i18n/locales/en.json web/src/i18n/locales/zh-modern.json
git commit -m "feat(web): persona 表单 + 详情支持 memory_global_read 开关"
```

---

### Task 5: 补 pytest 测试

**Files:**
- Create: `tests/persona/test_memory_global_read.py`

- [ ] **Step 1: 写测试**

Create `tests/persona/test_memory_global_read.py`(复用 conftest 的 `storage` fixture):
```python
"""persona memory_global_read 开关测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from tianshu.memory.models import MemoryEntry
from tianshu.persona.model import AgentPersona
from tianshu.tools.memory_tools import _memory_search


def test_persona_field_round_trip(storage, tmp_path):
    storage.save_persona({"id": "wym", "name": "王", "department": "neige", "memory_global_read": True})
    assert storage.get_persona("wym")["memory_global_read"] is True
    storage.save_persona({"id": "x", "name": "X", "department": "neige"})
    assert storage.get_persona("x")["memory_global_read"] is False


def _persona(tmp_path, gr):
    return AgentPersona(
        id="wym", name="王", department="neige",
        soul_path=tmp_path / "s", role_path=tmp_path / "r", memory_path=tmp_path / "m",
        memory_global_read=gr,
    )


def test_global_read_off_hides_other_private(storage, tmp_path):
    storage.save_memory_entry(MemoryEntry(persona_id="other", category="observation", content="机密 secret-xyz"))
    with patch("tianshu.tools.memory_tools.get_current_persona", return_value=_persona(tmp_path, False)):
        r = asyncio.run(_memory_search(storage, query="secret-xyz"))
    assert "secret-xyz" not in r.payload


def test_global_read_on_sees_all(storage, tmp_path):
    storage.save_memory_entry(MemoryEntry(persona_id="other", category="observation", content="机密 secret-xyz"))
    with patch("tianshu.tools.memory_tools.get_current_persona", return_value=_persona(tmp_path, True)):
        r = asyncio.run(_memory_search(storage, query="secret-xyz"))
    assert "secret-xyz" in r.payload
```
> 注:`ToolResult` 的内容字段若不是 `.payload`,据 `tools/types.py` 的 `ok_result` 实际结构调整(如 `.content`/`.data`)。`AgentPersona` 必填字段以 `model.py` 为准补齐。

- [ ] **Step 2: 跑测试**

Run:
```bash
.venv/bin/python -m pytest tests/persona/test_memory_global_read.py -v
```
Expected: 4 个用例全部 PASS。

- [ ] **Step 3: Commit**

```bash
git add tests/persona/test_memory_global_read.py
git commit -m "test(persona): memory_global_read 往返 + 全局读 gate 测试"
```

---

## Self-Review

**1. Spec coverage**(对 spec §2):
- 字段加 + 默认 False → Task 1。✅
- 开关开 → 全检索 → Task 2 核心判断。✅
- 关 → 现状不变 → Task 2 短路条件 + `test_global_read_off_hides_other_private`。✅
- 字段往返保留 → Task 1 Step 7 + `test_persona_field_round_trip`。✅
- 前端表单/详情 → Task 4。✅
- 旧库 migration → Task 1 Step 2。✅

**2. Placeholder scan**: 机械处以「照 can_delegate + file:line + 完整示例代码」给出,核心/migration/save/update/测试给完整代码;两处显式标注「据实际结构调整」(`ToolResult` 字段名、前端控件类型)——这是有意的防御,因为它们依赖未在本计划完整展开的现有结构,subagent 对照现状即可,非占位。✅

**3. Type consistency**: `memory_global_read`(snake_case 后端/JSON)全程一致;前端 `memory_global_read`(types/表单 name/i18n key `memoryGlobalRead`)与 `can_delegate`/`canDelegate` 的命名分工一致。✅

无遗漏。Task 间依赖:Task 2 用 Task 1 的 model 字段;Task 3 用 Task 1 的 storage/loader;Task 4 用 Task 3 的 API;Task 5 用 Task 1+2。按序执行。
