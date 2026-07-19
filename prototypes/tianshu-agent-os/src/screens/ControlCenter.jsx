import {
  CheckCircleFilled,
  ClockCircleOutlined,
  ExperimentOutlined,
  PlusOutlined,
  RightOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { activeEdicts, controlMetrics, evolution } from "../data/mockData.js";

function MetricCard({ metric }) {
  return (
    <article className="metric-card">
      <div className="metric-label">{metric.label}</div>
      <div className={`metric-value tone-${metric.tone}`}>{metric.value}</div>
      <div className="metric-hint">{metric.hint}</div>
    </article>
  );
}

function ActiveEdict({ item, onOpen }) {
  return (
    <article className="edict-row">
      <div className="edict-row-topline">
        <div>
          <div className="eyebrow mono">{item.id}</div>
          <h3>{item.title}</h3>
        </div>
        <span className={`semantic-chip ${item.statusTone}`}>{item.status}</span>
      </div>
      <div className="edict-meta">{item.executor}<span>／</span>{item.department}</div>
      <div className="progress-track" aria-label={`进度 ${item.progress}%`}>
        <span style={{ width: `${item.progress}%` }} />
      </div>
      <div className="edict-row-footer">
        <span>{item.progress}% · {item.milestone}</span>
        {item.id === "ED-1042" ? (
          <button className="text-button" type="button" aria-label="打开敕令详情" onClick={onOpen}>
            查看治理链 <RightOutlined aria-hidden="true" />
          </button>
        ) : (
          <span className="muted">证据生成中</span>
        )}
      </div>
    </article>
  );
}

export function ControlCenter({ onOpenEdict, onOpenEvolution }) {
  return (
    <div className="page control-page">
      <div className="page-heading-row">
        <div>
          <div className="page-kicker">TIANSHU · GOVERNANCE PLANE</div>
          <h1>中枢总览</h1>
          <p>治理、执行、成长与演化的实时态势</p>
        </div>
        <div className="page-actions">
          <span className="date-stamp mono">2026.07.10 · 周五</span>
          <button className="primary-button" type="button">
            <PlusOutlined aria-hidden="true" />颁发敕令
          </button>
        </div>
      </div>

      <section className="surface-card metrics-panel" aria-labelledby="today-status-title">
        <div className="section-heading compact">
          <div>
            <h2 id="today-status-title">今日态势</h2>
            <p>最近更新 14:32:18</p>
          </div>
          <span className="quiet-status"><span className="connection-dot" />治理链运行正常</span>
        </div>
        <div className="metrics-grid">
          {controlMetrics.map((metric) => <MetricCard key={metric.label} metric={metric} />)}
        </div>
      </section>

      <div className="dashboard-grid">
        <section className="surface-card active-edicts-panel" aria-labelledby="active-edicts-title">
          <div className="section-heading">
            <div>
              <h2 id="active-edicts-title">进行中的敕令</h2>
              <p>按当前里程碑与治理状态排序</p>
            </div>
            <button className="text-button" type="button">查看全部 <RightOutlined aria-hidden="true" /></button>
          </div>
          <div className="edict-list">
            {activeEdicts.map((item) => <ActiveEdict key={item.id} item={item} onOpen={onOpenEdict} />)}
          </div>
        </section>

        <div className="side-stack">
          <section className="surface-card decision-card" aria-labelledby="pending-decision-title">
            <div className="section-heading compact">
              <h2 id="pending-decision-title">当前待裁决</h2>
              <span className="semantic-chip review">高风险</span>
            </div>
            <div className="decision-icon"><SafetyCertificateOutlined aria-hidden="true" /></div>
            <h3>MCP stdio 申请启动外部进程</h3>
            <code>uv run pytest tests/security</code>
            <dl className="constraint-list">
              <div><dt>网络</dt><dd>关闭</dd></div>
              <div><dt>环境</dt><dd>净化</dd></div>
              <div><dt>超时</dt><dd>180s</dd></div>
            </dl>
            <button className="accent-button wide" type="button" onClick={onOpenEdict}>查看并裁决</button>
          </section>

          <section className="surface-card growth-card" aria-labelledby="growth-title">
            <div className="section-heading compact">
              <h2 id="growth-title">成长脉动</h2>
              <span className="muted mono">24H</span>
            </div>
            <div className="growth-item">
              <CheckCircleFilled aria-hidden="true" />
              <div><strong>新增 3 条可信记忆</strong><span>经用户确认后进入长期记忆</span></div>
            </div>
            <div className="growth-item">
              <ClockCircleOutlined aria-hidden="true" />
              <div><strong>技能候选 release-auditor</strong><span>4 次审计 · 3 次复用成功 · 1 次失败待复盘</span></div>
            </div>
          </section>
        </div>
      </div>

      <section className="surface-card evolution-summary" aria-labelledby="evolution-summary-title">
        <div className="section-heading">
          <div>
            <h2 id="evolution-summary-title">成长与演化</h2>
            <p>候选位面只在证据充分后进入人工晋升</p>
          </div>
          <button className="text-button" type="button" onClick={onOpenEvolution}>进入演化中心 <RightOutlined aria-hidden="true" /></button>
        </div>
        <div className="evolution-summary-grid">
          <article className="candidate-tile">
            <div className="eyebrow mono">CANDIDATE</div>
            <h3>{evolution.candidate.version}</h3>
            <p>{evolution.candidate.title}</p>
            <span className="semantic-chip running">CANARY 36%</span>
          </article>
          <article className="score-tile">
            <div className="metric-label">考成结果</div>
            <div className="score-value">{evolution.candidate.score}</div>
            <p>基线 {evolution.candidate.baseline} · 提升 +3.7</p>
            <span>安全 +3.1% · 成本 -6.4%</span>
          </article>
          <article className="gate-tile">
            <div className="metric-label">晋升门槛</div>
            <ul>
              <li className="passed"><CheckCircleFilled aria-hidden="true" />历史回归 48 / 48</li>
              <li className="passed"><CheckCircleFilled aria-hidden="true" />高风险用例 12 / 12</li>
              <li className="active"><ExperimentOutlined aria-hidden="true" />Canary 样本 18 / 50</li>
            </ul>
            <p>达到 50 个样本后提交人工晋升</p>
          </article>
        </div>
      </section>
    </div>
  );
}
