import { Button } from "antd";
import { useT } from "../../i18n";

export interface EvidenceBundleView {
  id: string;
  status: "open" | "closed";
  version: number;
  digest: string | null;
  downloadUrl: string | null;
  executor: {
    id: string;
    displayName: string;
    level: "managed" | "contained" | "observe-only";
  };
  artifacts: readonly {
    digest: string;
    mediaType: string;
    sizeBytes: number;
  }[];
  checks: readonly {
    name: string;
    status: "passed" | "failed" | "unavailable" | "skipped";
    exitCode: number | null;
  }[];
  policies: readonly string[];
  cost: string;
  environment: readonly string[];
  auditor: {
    id: string;
    verdict: "pass" | "fail";
    reason: string;
  };
  missingMandatory: readonly string[];
  replayAvailable: boolean;
}

export interface EvidenceBundlePanelProps {
  bundle: EvidenceBundleView;
  onReplay?: (bundleId: string) => void;
}

function StringList({ values }: { values: readonly string[] }) {
  return (
    <ul>
      {values.map((value) => (
        <li key={value}>{value}</li>
      ))}
    </ul>
  );
}

export default function EvidenceBundlePanel({ bundle, onReplay }: EvidenceBundlePanelProps) {
  const t = useT();
  return (
    <section aria-labelledby={`evidence-title-${bundle.id}`}>
      <h2 id={`evidence-title-${bundle.id}`}>{t("evidenceUi.title")}</h2>
      <p role="status">{t(`evidenceUi.status.${bundle.status}`)}</p>
      <dl>
        <dt>{t("evidenceUi.version")}</dt>
        <dd>{bundle.version}</dd>
        <dt>{t("evidenceUi.digest")}</dt>
        <dd>
          {bundle.digest ? <code>{bundle.digest}</code> : t("evidenceUi.digestPending")}
        </dd>
      </dl>
      {bundle.status === "closed" && bundle.downloadUrl ? (
        <a href={bundle.downloadUrl} download>
          {t("evidenceUi.download")}
        </a>
      ) : null}

      <section>
        <h3>{t("evidenceUi.executor")}</h3>
        <dl>
          <dt>{t("evidenceUi.executorName")}</dt>
          <dd>{bundle.executor.displayName}</dd>
          <dt>{t("evidenceUi.executorId")}</dt>
          <dd>{bundle.executor.id}</dd>
          <dt>{t("evidenceUi.executorLevel")}</dt>
          <dd>{bundle.executor.level}</dd>
        </dl>
      </section>

      <section>
        <h3>{t("evidenceUi.artifacts")}</h3>
        <ul>
          {bundle.artifacts.map((artifact) => (
            <li key={artifact.digest}>
              <code>{artifact.digest}</code> · {artifact.mediaType} · {artifact.sizeBytes} B
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h3>{t("evidenceUi.checks")}</h3>
        <ul>
          {bundle.checks.map((check) => (
            <li key={check.name}>
              {check.name}: {t(`evidenceUi.checkStatus.${check.status}`)}
              {check.exitCode === null ? "" : ` · exit ${check.exitCode}`}
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h3>{t("evidenceUi.policies")}</h3>
        <StringList values={bundle.policies} />
      </section>
      <section>
        <h3>{t("evidenceUi.cost")}</h3>
        <p>{bundle.cost}</p>
      </section>
      <section>
        <h3>{t("evidenceUi.environment")}</h3>
        <StringList values={bundle.environment} />
      </section>
      <section>
        <h3>{t("evidenceUi.auditor")}</h3>
        <p>{bundle.auditor.id}</p>
        <p>{t(`evidenceUi.verdict.${bundle.auditor.verdict}`)}</p>
        <p>{bundle.auditor.reason}</p>
      </section>
      {bundle.missingMandatory.length > 0 ? (
        <section role="alert">
          <h3>{t("evidenceUi.missingMandatory")}</h3>
          <StringList values={bundle.missingMandatory} />
        </section>
      ) : null}
      {bundle.replayAvailable ? (
        <div>
          <p>{t("evidenceUi.replayWarning")}</p>
          {onReplay ? (
            <Button onClick={() => onReplay(bundle.id)}>{t("evidenceUi.replay")}</Button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
