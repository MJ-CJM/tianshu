# 安全策略

## 报告漏洞

如发现安全漏洞,**请勿公开开 issue**。请通过邮件私报:

- 邮箱:mj-cjm@outlook.com(主题注明 `[SECURITY] tianshu`)

单人维护,尽力 72 小时内首次响应,**不承诺 SLA**。请在报告中附:受影响版本、
复现步骤、影响评估。修复后会在 CHANGELOG 致谢(除非你要求匿名)。

## 运行时深防御(锦衣卫,v0.2.3+)

天枢在工具执行的最后一公里内建多层防护,详见
[ADR-0010](docs/adr/0010-jinyiwei-runtime-defense-in-depth.md):

- **出站脱敏**:WS / webhook / 通知外发前统一 redact(API key/PEM/JWT/连接串等)。默认开。
- **bash 风险分级**:quote-aware 分段逐段判定,堵 `git log; rm -rf /` 类白名单绕过。默认开。
- **子进程 clean-env**:`shell_exec` 白名单构造 env,不透传 `TIANSHU_*` secrets。默认开。
- **分级急停**:三档(全停/掐网/冻结工具),工具管线入口最前检查,状态持久化、损坏 fail-closed。
  `POST /api/estop/engage` 或 web「系统管理 → 急停」。
- **出厂预算护栏**:默认每日全局上限,超限熔断(`TIANSHU_DAILY_BUDGET_GUARDRAIL_CNY`)。

## 密钥管理

- 凭证 Fernet 密文落库(`TIANSHU_SECRET_MASTER_KEY`)。生成:`tianshu secrets gen-key`。
- **主密钥轮换**:`tianshu secrets rotate-master-key --new-key <key>`(旧密钥解密→新密钥
  重加密,干跑校验+自动备份+解不开即中止)。轮换后更新 env 并重启。
- 请勿将主密钥提交进版本库;泄漏后立即轮换。

## 已知边界(诚实声明)

- 脱敏/分级/急停降低泄漏与失控面,**不等于**沙箱隔离。强隔离(禁网变体容器、
  影子快照回滚)在后续迭代。
- 天枢 MCP server 只暴露手选 tools,**不做流量代理**;连接外部 MCP server 时受准入
  清单治理,但不为外部 server 的行为背书。
- 流式输出跨 chunk 切开的 secret 可能脱敏不到——已知局限。
