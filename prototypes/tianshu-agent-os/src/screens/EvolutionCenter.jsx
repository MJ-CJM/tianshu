import { useMemo, useState } from "react";
import {
  ArrowLeftOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  ExperimentOutlined,
  RocketOutlined,
  RollbackOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { evolution } from "../data/mockData.js";

const FILTERS = ["全部评测", "回归", "安全", "Canary"];

function PipelineStage({ stage, index }) {
  const Icon = stage.state === "done" ? CheckCircleFilled : stage.state === "current" ? ExperimentOutlined : ClockCircleOutlined;
  return (
    <li className={`pipeline-stage ${stage.state}`}>
      <div className="pipeline-index mono">0{index + 1}</div>
      <Icon aria-hidden="true" />
      <strong>{stage.label}</strong>
      <span>{stage.meta}</span>
    </li>
  );
}

export function EvolutionCenter({ onBack }) {
  const [filter, setFilter] = useState("全部评测");
  const [decision, setDecision] = useState(null);
  const canPromote = evolution.promotionGate.current >= evolution.promotionGate.required;

  const visibleSuites = useMemo(() => {
    if (filter === "全部评测") return evolution.suites;
    if (filter === "回归") return evolution.suites.filter((item) => item.label.includes("回归") || item.label.includes("回滚"));
    if (filter === "安全") return evolution.suites.filter((item) => item.label.includes("风险"));
    return evolution.suites.filter((item) => item.label.includes("Canary"));
  }, [filter]);

  return (
    <div className="page evolution-page">
      <div className="detail-heading evolution-heading">
        <div className="detail-title-wrap">
          <button className="back-button" type="button" aria-label="返回中枢总览" onClick={onBack}>
            <ArrowLeftOutlined aria-hidden="true" />
          </button>
          <div>
            <div className="page-kicker">CONTROLLED EVOLUTION · GOVERNED GROWTH</div>
            <h1>演化中心</h1>
            <p>先回归评测，再晋升</p>
          </div>
        </div>
        <div className="heading-badge">
          <SafetyCertificateOutlined aria-hidden="true" />
          <div><span>演化策略</span><strong>人工门控</strong></div>
        </div>
      </div>

      <section className="surface-card candidate-brief" aria-labelledby="candidate-title">
        <div className="candidate-version-block">
          <div className="eyebrow mono">CANDIDATE UNIVERSE</div>
          <h2 id="candidate-title">{evolution.candidate.version}</h2>
          <p>{evolution.candidate.title}</p>
          <span className="semantic-chip running">CANARY 36%</span>
        </div>
        <dl className="candidate-facts">
          <div><dt>来源</dt><dd>{evolution.candidate.source}</dd></div>
          <div><dt>演化假设</dt><dd>{evolution.candidate.hypothesis}</dd></div>
          <div><dt>回滚点</dt><dd className="mono">{evolution.candidate.rollback}</dd></div>
        </dl>
        <div className="candidate-score-block">
          <span>综合考成</span>
          <strong>{evolution.candidate.score}</strong>
          <small>冠军基线 {evolution.candidate.baseline}</small>
        </div>
      </section>

      <section className="surface-card pipeline-panel" aria-labelledby="pipeline-title">
        <div className="section-heading">
          <div><h2 id="pipeline-title">晋升流水线</h2><p>每一步都有输入、证据与明确的退出条件</p></div>
          <span className="quiet-status"><span className="connection-dot" />Canary 进行中</span>
        </div>
        <ol className="pipeline-list">
          {evolution.pipeline.map((stage, index) => <PipelineStage key={stage.label} stage={stage} index={index} />)}
        </ol>
      </section>

      <div className="evolution-main-grid">
        <section className="surface-card comparison-panel" aria-labelledby="comparison-title">
          <div className="section-heading">
            <div><h2 id="comparison-title">冠军与候选对照</h2><p>同一评测集、同一成本口径、同一安全边界</p></div>
            <span className="muted mono">PAIRED EVAL</span>
          </div>
          <div className="comparison-table" role="table" aria-label="候选位面对照">
            <div className="comparison-row comparison-head" role="row">
              <span role="columnheader">指标</span><span role="columnheader">当前冠军</span><span role="columnheader">候选位面</span><span role="columnheader">变化</span>
            </div>
            {evolution.comparisons.map((item) => (
              <div className="comparison-row" role="row" key={item.label}>
                <strong role="cell">{item.label}</strong>
                <span role="cell" className="mono">{item.champion}</span>
                <span role="cell" className="mono candidate-value">{item.candidate}</span>
                <span role="cell" className="mono delta-value">{item.delta}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="surface-card promotion-card" aria-labelledby="promotion-title">
          <div className="section-heading compact">
            <h2 id="promotion-title">晋升裁决</h2>
            <span className="semantic-chip review">待人工</span>
          </div>
          <div className="promotion-icon"><RocketOutlined aria-hidden="true" /></div>
          <h3>是否将候选设为新的冠军位面？</h3>
          <p>Canary 尚有 32 个样本未完成。达到 50 个样本后才开放人工晋升；本轮只验证本地门禁与交互状态，不写入真实演化档案。</p>
          <div className="promotion-guardrails">
            <span><CheckCircleFilled aria-hidden="true" />回归集通过</span>
            <span><CheckCircleFilled aria-hidden="true" />安全集通过</span>
            <span className="waiting"><ClockCircleOutlined aria-hidden="true" />样本积累中</span>
          </div>
          {!canPromote ? <p className="promotion-blocker" id="promotion-gate-blocker">Canary 样本未达强制门槛</p> : null}
          <div className="promotion-actions">
            <button className="secondary-button" type="button" onClick={() => setDecision("rejected")}>驳回候选</button>
            <button className="secondary-button" type="button" onClick={() => setDecision("observe")}>继续观察</button>
            <button
              className="accent-button"
              type="button"
              disabled={!canPromote}
              aria-describedby={!canPromote ? "promotion-gate-blocker" : undefined}
              onClick={() => setDecision("approved")}
            >
              批准晋升
            </button>
          </div>
          {decision ? (
            <div className={`decision-feedback ${decision}`} role="status">
              {decision === "approved" ? <RocketOutlined aria-hidden="true" /> : <RollbackOutlined aria-hidden="true" />}
              <div>
                <strong>{decision === "approved" ? "已批准候选晋升" : decision === "observe" ? "已延长 Canary 观察" : "已驳回候选位面"}</strong>
                <span>{decision === "approved" ? `本地验收状态已更新，预设回滚点 ${evolution.candidate.rollback}` : "本地验收状态已更新，尚未写入真实演化档案"}</span>
              </div>
            </div>
          ) : null}
        </section>
      </div>

      <section className="surface-card eval-suite-panel" aria-labelledby="eval-suite-title">
        <div className="section-heading eval-heading">
          <div><h2 id="eval-suite-title">评测与证据</h2><p>从“会成长”变成“知道为何可以成长”</p></div>
          <div className="filter-tabs" role="tablist" aria-label="评测筛选">
            {FILTERS.map((item) => (
              <button key={item} type="button" role="tab" aria-selected={filter === item} className={filter === item ? "is-active" : ""} onClick={() => setFilter(item)}>{item}</button>
            ))}
          </div>
        </div>
        <div className="suite-grid">
          {visibleSuites.map((suite) => (
            <article className={`suite-card ${suite.tone}`} key={suite.label}>
              <div className="suite-icon">{suite.tone === "pass" ? <CheckCircleFilled aria-hidden="true" /> : <ExperimentOutlined aria-hidden="true" />}</div>
              <div><span>{suite.label}</span><strong>{suite.value}</strong><small>{suite.hint}</small></div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
