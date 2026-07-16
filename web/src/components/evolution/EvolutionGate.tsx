import { Button } from "antd";
import { useT } from "../../i18n";

export interface EvolutionGateView {
  promotionAllowed: boolean;
  blockingGates: Array<{
    code: string;
    label: string;
    current: number | null;
    required: number | null;
    evidenceUri: string | null;
  }>;
  challengerRouting: {
    enabled: boolean;
    realTraffic: boolean;
    samples: number | null;
  };
}

export interface EvolutionGateProps {
  status: "not_enabled" | "enabled";
  view: EvolutionGateView;
  onPromote?: () => void;
}

export default function EvolutionGate({ status, view, onPromote }: EvolutionGateProps) {
  const t = useT();
  const canPromote = status === "enabled" && view.promotionAllowed;
  return (
    <section aria-labelledby="evolution-gate-title">
      <h2 id="evolution-gate-title">{t("evolutionUi.title")}</h2>
      <p role="status">{status}</p>
      <p>
        {view.challengerRouting.realTraffic
          ? t("evolutionUi.realTraffic")
          : t("evolutionUi.noRealTraffic")}
      </p>
      {view.blockingGates.length > 0 ? (
        <section>
          <h3>{t("evolutionUi.blockingGates")}</h3>
          <ul>
            {view.blockingGates.map((gate) => (
              <li key={gate.code}>
                <span>{gate.label}</span>{" "}
                {gate.current !== null && gate.required !== null ? (
                  <span>{gate.current} / {gate.required}</span>
                ) : null}
                {gate.evidenceUri ? <a href={gate.evidenceUri}>{t("evolutionUi.evidence")}</a> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <Button aria-label={t("evolutionUi.promote")} disabled={!canPromote} onClick={onPromote}>
        {t("evolutionUi.promote")}
      </Button>
    </section>
  );
}
