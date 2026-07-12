# 天枢 Agent OS Web 审批稿 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个不连接后端、用于桌面 Web 审批的三页天枢 Agent OS 高保真原型；本轮不开发或验收手机端。

**Architecture:** 在 `prototypes/tianshu-agent-os/` 创建独立 Vite React 应用。`AppShell` 负责全局导航与主题，三个 screen 组件只消费本地模拟数据，交互状态留在浏览器内存；生产 `web/` 与后端零改动。

**Tech Stack:** React 19、Vite 6、Ant Design Icons、Vitest、Testing Library、CSS custom properties。

## Global Constraints

- 产品定位：天枢是一个可治理、可验证、持续成长的自进化 Agent OS。
- 完整保留敕令、政要、百官、外朝四组部门导航，并在顶部增加中枢总览。
- 四组十四部门名称、顺序和分组逐字对齐生产侧栏，使用 `百官阁`；Logo、标语、右上五项、主题和折叠控件属于冻结区。
- 朱砂只用于当前选中、待裁决、风险和焦点；禁止霓虹、强渐变与重阴影。
- 原型不访问 API、不写持久数据、不修改生产前后端源码。
- 桌面基准 1440 × 1024，并检查 1280px 宽度；本轮不开发或验收手机端。
- 使用现有 `web/public/brand.png` Logo；保留原页头的语言、连接、健康状态与侧栏底部主题/收起控件。
- 页面审批前不迁移到生产代码，也不提交原型改动。

---

### Task 1: 独立原型骨架与导航契约

**Files:**
- Create: `prototypes/tianshu-agent-os/package.json`
- Create: `prototypes/tianshu-agent-os/src/App.jsx`
- Create: `prototypes/tianshu-agent-os/src/App.test.jsx`
- Create: `prototypes/tianshu-agent-os/src/test/setup.js`
- Create: `prototypes/tianshu-agent-os/src/data/mockData.js`
- Create: `prototypes/tianshu-agent-os/.gitignore`

**Interfaces:**
- Consumes: no external API.
- Produces: `App`, `NAV_GROUPS`, `SCREEN_IDS`, and local screen-selection behavior.

- [ ] **Step 1: Bootstrap the self-contained prototype and install its pinned dependencies**

Run the Product Design prototype bootstrap script against the exact destination, then add `@ant-design/icons`, `vitest`, `jsdom`, `@testing-library/react`, and `@testing-library/jest-dom`. Add scripts `test: vitest run` and `test:watch: vitest`.

- [ ] **Step 2: Write the failing navigation contract test**

```jsx
it("keeps the original department map and opens all three approval screens", async () => {
  render(<App />);
  expect(screen.getByRole("button", { name: "中枢总览" })).toBeInTheDocument();
  expect(screen.getByText("敕令", { selector: ".nav-group-title" })).toBeInTheDocument();
  expect(screen.getByText("政要", { selector: ".nav-group-title" })).toBeInTheDocument();
  expect(screen.getByText("百官", { selector: ".nav-group-title" })).toBeInTheDocument();
  expect(screen.getByText("外朝", { selector: ".nav-group-title" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "打开敕令详情" }));
  expect(screen.getByRole("heading", { name: /开源发布安全加固/ })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "演化中心" }));
  expect(screen.getByRole("heading", { name: "演化中心" })).toBeInTheDocument();
});
```

- [ ] **Step 3: Run the test and verify RED**

Run: `npm test -- --run src/App.test.jsx`

Expected: FAIL because `App` does not yet expose the required navigation and screens.

- [ ] **Step 4: Implement minimal navigation state and realistic mock data**

Create exact screen ids `control`, `edict`, `evolution`; define the four required navigation groups and the realistic edict/evolution records described in the spec. Render simple headings and controls sufficient for the test.

- [ ] **Step 5: Run the test and verify GREEN**

Run: `npm test -- --run src/App.test.jsx`

Expected: PASS.

### Task 2: 全局框架与中枢总览

**Files:**
- Create: `prototypes/tianshu-agent-os/src/components/AppShell.jsx`
- Create: `prototypes/tianshu-agent-os/src/screens/ControlCenter.jsx`
- Modify: `prototypes/tianshu-agent-os/src/App.jsx`
- Modify: `prototypes/tianshu-agent-os/src/App.test.jsx`
- Modify: `prototypes/tianshu-agent-os/src/styles.css`

**Interfaces:**
- Consumes: `NAV_GROUPS`, mock edicts, approval and evolution summaries.
- Produces: desktop shell, current brand header, locale/status controls, theme/collapse controls, and `ControlCenter({ onOpenEdict, onOpenEvolution })`.

- [ ] **Step 1: Write failing tests for current brand shell, theme, sidebar collapse, and dashboard actions**

```jsx
it("preserves the current brand shell and sidebar controls", async () => {
  render(<App />);
  expect(screen.getByRole("img", { name: "天枢 Logo" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "彩蛋" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "深色模式" }));
  expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  await userEvent.click(screen.getByRole("button", { name: "收起侧栏" }));
  expect(screen.getByRole("button", { name: "展开侧栏" })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the targeted tests and verify RED**

Run: `npm test -- --run src/App.test.jsx`

Expected: FAIL on missing theme and drawer controls.

- [ ] **Step 3: Implement the shell and dashboard composition**

Implement the exact desktop sidebar groups, header status items, today metrics, active edicts, pending decision, growth pulse, and promotion gate. Use CSS variables derived from the existing palette, Ant Design Icons, semantic HTML, and accessible button names. Do not spend this phase on a mobile drawer or mobile acceptance.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `npm test -- --run src/App.test.jsx`

Expected: PASS.

### Task 3: 敕令治理详情与可操作裁决

**Files:**
- Create: `prototypes/tianshu-agent-os/src/screens/EdictDetail.jsx`
- Modify: `prototypes/tianshu-agent-os/src/App.jsx`
- Modify: `prototypes/tianshu-agent-os/src/App.test.jsx`
- Modify: `prototypes/tianshu-agent-os/src/styles.css`

**Interfaces:**
- Consumes: selected edict record.
- Produces: `EdictDetail({ edict, onBack })` with local tabs and approval decision state.

- [ ] **Step 1: Write the failing approval behavior test**

```jsx
it("requires a reason before recording a governed decision", async () => {
  render(<App initialScreen="edict" />);
  expect(screen.getByText("执行者不能自证完成")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "批准执行" }));
  expect(screen.getByText(/请填写裁决理由/)).toBeInTheDocument();
  await userEvent.type(screen.getByRole("textbox", { name: "裁决理由" }), "已核对影响范围与恢复点");
  await userEvent.click(screen.getByRole("button", { name: "批准执行" }));
  expect(screen.getByRole("status")).toHaveTextContent("已批准");
  expect(screen.getByText(/裁决依据已写入治理证据/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the targeted test and verify RED**

Run: `npm test -- --run src/App.test.jsx -t "records a governed approval"`

Expected: FAIL because the detail screen and decision state are missing.

- [ ] **Step 3: Implement contract, pulse, decision and evidence surfaces**

Match the approved task-detail composition: contract strip, eight tabs, execution timeline, high-risk decision card, running state and three evidence cards. Buttons must update only local state and keep a reset control.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `npm test -- --run src/App.test.jsx`

Expected: PASS.

### Task 4: 演化中心与受控晋升

**Files:**
- Create: `prototypes/tianshu-agent-os/src/screens/EvolutionCenter.jsx`
- Modify: `prototypes/tianshu-agent-os/src/App.jsx`
- Modify: `prototypes/tianshu-agent-os/src/App.test.jsx`
- Modify: `prototypes/tianshu-agent-os/src/styles.css`

**Interfaces:**
- Consumes: candidate, champion, evaluation suite and canary mock data.
- Produces: `EvolutionCenter()` with stage filter and local promotion decision state.

- [ ] **Step 1: Write the failing promotion-gate test**

```jsx
it("blocks promotion until every mandatory gate passes", async () => {
  render(<App initialScreen="evolution" />);
  expect(screen.getByText("先回归评测，再晋升")).toBeInTheDocument();
  expect(screen.getByText("18 / 50")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "批准晋升" })).toBeDisabled();
  expect(screen.getByText(/Canary 样本未达强制门槛/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the targeted test and verify RED**

Run: `npm test -- --run src/App.test.jsx -t "requires evidence"`

Expected: FAIL because the evolution gate is missing.

- [ ] **Step 3: Implement the promotion pipeline and comparison surfaces**

Implement candidate summary, five-stage promotion pipeline, champion/candidate score comparison, evaluation-suite cards, canary progress and promotion decision controls. Keep risk and unresolved gates visually distinct from passed evidence.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `npm test -- --run src/App.test.jsx`

Expected: PASS.

### Task 5: Build, browser verification and design QA

**Files:**
- Create: `prototypes/tianshu-agent-os/design-qa.md`
- Create: `prototypes/tianshu-agent-os/artifacts/control-desktop.png`
- Create: `prototypes/tianshu-agent-os/artifacts/edict-desktop.png`
- Create: `prototypes/tianshu-agent-os/artifacts/evolution-desktop.png`

**Interfaces:**
- Consumes: complete prototype and source visual paths from the spec.
- Produces: verified local preview and a `design-qa.md` that separately reports shell fidelity and product integrity; both must pass before overall `passed`.

- [ ] **Step 1: Run automated verification**

Run: `npm test && npm run build`

Expected: all tests pass and Vite exits 0.

- [ ] **Step 2: Start the local server and verify primary interactions**

Open the exact local URL in the Codex in-app browser. Verify navigation, existing Logo and header status, locale selection, theme toggle, sidebar collapse, tabs, approval feedback and promotion feedback. Check the browser console for errors.

- [ ] **Step 3: Capture desktop evidence**

Capture 1440 × 1024 for all three pages and check 1280px desktop width in dark/light and expanded/collapsed shell states. Confirm no horizontal overflow, clipped primary actions or overlapping persistent controls.

- [ ] **Step 4: Run iterative design QA**

Compare each implementation capture against its source reference at the same viewport/state, including focused checks for typography, spacing, palette, icons/assets and product copy. Record every P0/P1/P2 fix and recapture until `design-qa.md` says `final result: passed`.

- [ ] **Step 5: Keep the preview running for user approval**

Return the clickable local URL first, plus the three captured views. Do not publish externally or migrate the design to production before explicit approval.
