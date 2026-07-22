import { useState } from "react";
import {
  ArrowLeftOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  FileProtectOutlined,
  PauseOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { edict } from "../data/mockData.js";

const TAB_COPY = {
  计划: ["4 个工作包", "2 个并行执行", "验收标准已冻结"],
  脉络: ["14 条执行事件", "1 项待人工裁决", "最近心跳 4 秒前"],
  证据: ["6 项变更证据", "8 项验证证据", "3 项治理证据"],
  变更: ["6 个文件受影响", "+184 / -37", "全部位于授权目录"],
  裁决: ["3 项自动允许", "1 项等待裁决", "0 项越权尝试"],
  成本: ["已用 ¥18.60", "预算 ¥30.00", "预测结案 ¥24.80"],
  结案: ["等待独立验收", "证据包将自动导出", "审计意见尚未签署"],
};

function ContractItem({ label, value, detail, tone }) {
  return (
    <div className="contract-item">
      <span>{label}</span>
      <strong className={tone ? `tone-${tone}` : ""}>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function TimelineItem({ item }) {
  const Icon = item.state === "done" ? CheckCircleFilled : item.state === "current" ? SafetyCertificateOutlined : ClockCircleOutlined;
  return (
    <li className={`timeline-item ${item.state}`}>
      <Icon aria-hidden="true" />
      <div className="timeline-copy">
        <div className="timeline-title-row"><strong>{item.title}</strong><span className="mono">{item.time}</span></div>
        <p>{item.detail}</p>
      </div>
    </li>
  );
}

export function EdictDetail({ decisionState, onBack, onDecisionStateChange }) {
  const [activeTab, setActiveTab] = useState("总览");
  const [paused, setPaused] = useState(false);
  const {
    record: decisionRecord,
    reason,
    validationMessage,
  } = decisionState;
  const decisionLocked = decisionRecord !== null;

  function updateDecisionState(patch) {
    onDecisionStateChange((current) => ({ ...current, ...patch }));
  }

  function decide(next) {
    const normalizedReason = reason.trim();
    if ((next === "approved" || next === "revised") && !normalizedReason) {
      updateDecisionState({ record: null, validationMessage: "请填写裁决理由" });
      return;
    }

    onDecisionStateChange({
      record: { outcome: next, reason: normalizedReason },
      reason: normalizedReason,
      validationMessage: "",
    });
  }

  function restoreInitialState() {
    setActiveTab("总览");
    setPaused(false);
    onDecisionStateChange({ record: null, reason: "", validationMessage: "" });
  }

  return (
    <div className="page edict-detail-page">
      <div className="detail-heading">
        <div className="detail-title-wrap">
          <button className="back-button" type="button" aria-label="返回中枢总览" onClick={onBack}>
            <ArrowLeftOutlined aria-hidden="true" />
          </button>
          <div>
            <div className="page-kicker mono">{edict.id} · GOVERNED EXECUTION</div>
            <h1>敕令 · {edict.title}</h1>
            <p>{edict.issuer}</p>
          </div>
        </div>
        <div className="detail-actions">
          <span className="semantic-chip running">{paused ? "已暂停" : "办理中"}</span>
          <button className="secondary-button" type="button" onClick={restoreInitialState}>恢复初始状态</button>
          <button className="secondary-button" type="button" onClick={() => setPaused((value) => !value)}>
            <PauseOutlined aria-hidden="true" />{paused ? "继续" : "暂停"}
          </button>
          <button
            className="accent-button"
            type="button"
            onClick={() => document.getElementById("decision-panel")?.scrollIntoView({ behavior: "smooth", block: "center" })}
          >
            裁决
          </button>
        </div>
      </div>

      <section className="surface-card contract-strip" aria-labelledby="contract-title">
        <div className="contract-title-row">
          <h2 id="contract-title">执行契约</h2>
          <span className="mono muted">CONTRACT · v3</span>
        </div>
        <div className="contract-grid">
          <ContractItem label="执行器" value={edict.executor} detail={edict.workspace} />
          <ContractItem label="风险" value={edict.risk} detail={edict.riskDetail} tone="red" />
          <ContractItem label="预算" value={edict.budget} detail={edict.tokens} tone="gold" />
          <ContractItem label="期限" value={edict.deadline} detail={edict.remaining} tone="cyan" />
        </div>
      </section>

      <div className="detail-tabs" role="tablist" aria-label="敕令详情栏目">
        {edict.tabs.map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={activeTab === tab}
            className={activeTab === tab ? "is-active" : ""}
            onClick={() => setActiveTab(tab)}
          >
            {tab}
          </button>
        ))}
      </div>

      {activeTab !== "总览" ? (
        <section className="surface-card tab-preview" aria-labelledby="tab-preview-title">
          <div>
            <div className="page-kicker">{activeTab} · LIVE SUMMARY</div>
            <h2 id="tab-preview-title">{activeTab}摘要</h2>
            <p>验收稿先验证信息层级与跨栏目连贯性，完整业务数据将在正式实现阶段接入。</p>
          </div>
          <ul>
            {TAB_COPY[activeTab].map((item) => <li key={item}><CheckCircleFilled aria-hidden="true" />{item}</li>)}
          </ul>
        </section>
      ) : (
        <>
          <div className="detail-grid">
            <section className="surface-card timeline-panel" aria-labelledby="timeline-title">
              <div className="section-heading">
                <div>
                  <h2 id="timeline-title">执行脉络</h2>
                  <p>从计划确认到独立验收，每一步都有责任人与证据</p>
                </div>
                <span className="live-stamp mono"><span className="connection-dot" />LIVE · 14:32:18</span>
              </div>
              <ol className="timeline-list">
                {edict.timeline.map((item) => <TimelineItem key={item.title} item={item} />)}
              </ol>
            </section>

            <div className="detail-side-stack">
              <section className="surface-card approval-panel" id="decision-panel" aria-labelledby="decision-title">
                <div className="section-heading compact">
                  <h2 id="decision-title">当前需要裁决</h2>
                  <span className="semantic-chip review">高风险</span>
                </div>
                <p className="principle-note">执行者不能自证完成</p>
                <h3>{edict.decision.title}</h3>
                <code>{edict.decision.command}</code>
                <div className="constraint-row">
                  {edict.decision.constraints.map((item) => <span key={item}>{item}</span>)}
                </div>
                <label className="decision-reason-field">
                  <span>裁决理由</span>
                  <textarea
                    value={reason}
                    disabled={decisionLocked}
                    onChange={(event) => {
                      updateDecisionState({
                        reason: event.target.value,
                        validationMessage: "",
                      });
                    }}
                    placeholder="说明允许执行的依据与边界"
                    rows={3}
                  />
                </label>
                <div className="approval-actions">
                  <button className="secondary-button" type="button" disabled={decisionLocked} onClick={() => decide("rejected")}>驳回</button>
                  <button className="secondary-button" type="button" disabled={decisionLocked} onClick={() => decide("revised")}>修改后批准</button>
                  <button className="accent-button" type="button" disabled={decisionLocked} onClick={() => decide("approved")} aria-label="批准执行">批准</button>
                </div>
                {validationMessage ? <div className="decision-validation" role="status">{validationMessage}</div> : null}
                {decisionRecord ? (
                  <div className={`decision-feedback ${decisionRecord.outcome}`} role="status">
                    <CheckCircleFilled aria-hidden="true" />
                    <div>
                      <strong>{decisionRecord.outcome === "approved" ? "已批准执行" : decisionRecord.outcome === "revised" ? "已要求修改后再执行" : "已驳回本次申请"}</strong>
                      <span>{decisionRecord.outcome === "approved" || decisionRecord.outcome === "revised" ? `裁决依据：${decisionRecord.reason}` : "本地验收状态已更新，尚未写入真实治理时间线"}</span>
                    </div>
                  </div>
                ) : null}
              </section>

              <section className="surface-card runtime-card" aria-labelledby="runtime-title">
                <div className="section-heading compact"><h2 id="runtime-title">运行态</h2><span className="quiet-status">可恢复</span></div>
                <dl>
                  <div><dt>恢复点</dt><dd className="mono">checkpoint-07</dd></div>
                  <div><dt>最近心跳</dt><dd>4 秒前</dd></div>
                  <div><dt>预计完成</dt><dd>16:42</dd></div>
                  <div><dt>审计员</dt><dd>都察院 · 独立上下文</dd></div>
                </dl>
              </section>
            </div>
          </div>

          <section className="surface-card evidence-panel" aria-labelledby="evidence-title">
            <div className="section-heading">
              <div><h2 id="evidence-title">证据包</h2><p>任务不是“声称完成”，而是携带可验证证据结案</p></div>
              <span className="muted">最终结案时自动导出</span>
            </div>
            <div className="evidence-grid">
              {edict.evidence.map((item, index) => (
                <article className="evidence-card" key={item.label}>
                  <FileProtectOutlined aria-hidden="true" />
                  <div>
                    <span>{item.label}</span>
                    <strong className={index === 2 ? "tone-green" : index === 1 ? "tone-gold" : ""}>{item.value}</strong>
                    <small>{item.hint}</small>
                  </div>
                </article>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
