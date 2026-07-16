import { Button } from "antd";
import { useT } from "../../i18n";

export interface EvidenceBundleView {
  id: string;
  digest: string;
  downloadUrl: string;
  artifacts: readonly string[];
  checks: readonly string[];
  policies: readonly string[];
  cost: string;
  environment: readonly string[];
  auditorConclusion: string;
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
      <dl>
        <dt>{t("evidenceUi.digest")}</dt>
        <dd>
          <code>{bundle.digest}</code>
        </dd>
      </dl>
      <a href={bundle.downloadUrl} download>
        {t("evidenceUi.download")}
      </a>

      <section>
        <h3>{t("evidenceUi.artifacts")}</h3>
        <StringList values={bundle.artifacts} />
      </section>
      <section>
        <h3>{t("evidenceUi.checks")}</h3>
        <StringList values={bundle.checks} />
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
        <p>{bundle.auditorConclusion}</p>
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
