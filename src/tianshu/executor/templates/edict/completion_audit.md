现在进入完成审计 (completion audit)。这一步独立于质量评分，目的是核实目标的**每一条要求**都有具体证据。

{objective_block}

## acceptance.checks 清单

{checks_list}

## 审计步骤

1. 把目标拆成可验证的具体要求（如"修改了 X 文件"、"测试覆盖 Y 场景"、"产出文件 Z"）。
2. 对每条 acceptance.checks 与每条具体要求，逐一贴出真实证据：
   - 文件路径与关键内容片段（绝对路径，可被读取验证）
   - 命令输出（含 exit code）
   - 测试结果（含通过/失败计数）
   - artifact 引用（路径 / URL）
3. 对每一条标注证据状态：`missing`（无证据）、`weak`（证据不足以确认）、`uncertain`（不确定）、`ok`（明确达成）。
4. 任何一条 ≠ `ok`，本次审计判定 `passed = false`。

## 严格守则

- **不允许把以下内容当作完成证据**：
  - "我做了努力" / "已经完成大部分" / "计划很详尽"
  - "测试看起来通过了"（无具体输出）
  - "所有相关文件都已修改"（无路径列举）
  - "审计本身就是证据"
- 若任何要求"看上去完成了但找不到具体证据" → 标 `weak`，判 `passed = false`。
- 不要乐观估计。当出现犹豫时一律判 `passed = false`。

## 输出格式

仅输出一段 JSON（不要其他文字、不要 markdown 包裹）：

```json
{{
  "passed": false,
  "gaps": [
    {{
      "check_name": "tests",
      "requirement": "pytest 全绿",
      "evidence_status": "missing",
      "suggested_action": "运行 pytest tests/ 并贴 exit code 与统计"
    }}
  ]
}}
```

字段约束：
- `passed`: bool
- `gaps`: list；`passed=true` 时为空
- `evidence_status` ∈ {{"missing", "weak", "uncertain", "ok"}}
- 每个 gap 的 `suggested_action` 必须可执行（动词开头）
