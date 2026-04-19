# Tianshu Project Instructions

> **项目定位**：天枢是一座会与你共同成长的宫殿。内有用户分身（emperor），外有辅佐的六部官员。任何重构 / 新特性都应服务"共生成长"这一核心叙事 —— 不是再造一个聊天工具，而是沉淀长期关系。

## gstack

Use the `/browse` skill from gstack for all web browsing tasks. **Never use `mcp__claude-in-chrome__*` tools.**

### Available Skills

| Skill | Purpose |
|-------|---------|
| `/office-hours` | Brainstorming and idea validation |
| `/plan-ceo-review` | CEO/founder-mode plan review |
| `/plan-eng-review` | Engineering architecture plan review |
| `/plan-design-review` | Designer's eye plan review |
| `/design-consultation` | Create design system and DESIGN.md |
| `/review` | Pre-landing PR code review |
| `/ship` | Ship workflow: tests, review, PR creation |
| `/land-and-deploy` | Merge PR, wait for CI, verify production |
| `/canary` | Post-deploy canary monitoring |
| `/benchmark` | Performance regression detection |
| `/browse` | Headless browser for QA and dogfooding |
| `/qa` | Systematic QA testing + bug fixing |
| `/qa-only` | Report-only QA testing (no fixes) |
| `/design-review` | Visual design audit on live site |
| `/setup-browser-cookies` | Import cookies for authenticated testing |
| `/setup-deploy` | Configure deployment settings |
| `/retro` | Weekly engineering retrospective |
| `/investigate` | Systematic debugging with root cause analysis |
| `/document-release` | Post-ship documentation updates |
| `/codex` | Second opinion / adversarial code review |
| `/cso` | Chief Security Officer audit |
| `/careful` | Safety guardrails for destructive commands |
| `/freeze` | Restrict edits to a specific directory |
| `/guard` | Maximum safety mode (careful + freeze) |
| `/unfreeze` | Remove edit restrictions |
| `/gstack-upgrade` | Upgrade gstack to latest version |

### Troubleshooting

If gstack skills aren't working, rebuild the binary and re-register skills:

```bash
cd .claude/skills/gstack && ./setup
```
