import { useT } from "../../i18n";

export interface GovernanceCapabilityView {
  id: string;
  label: string;
  requested: "mandatory" | "advisory" | "unrequested";
  effective: "enforced" | "best_effort" | "observed" | "unsupported";
}

export interface GovernanceContractCardProps {
  executorLevel: "managed" | "contained" | "observe-only" | null;
  requestedExecutor: string;
  effectiveExecutor: string;
  capabilities: readonly GovernanceCapabilityView[];
  mandatoryMismatches: readonly string[];
  advisoryGaps: readonly string[];
}

const CONTAINED_CAPABILITIES = new Set([
  "action_interception",
  "workspace_control",
  "network_control",
  "governed_apply_merge",
]);

export default function GovernanceContractCard({
  executorLevel,
  requestedExecutor,
  effectiveExecutor,
  capabilities,
  mandatoryMismatches,
  advisoryGaps,
}: GovernanceContractCardProps) {
  const t = useT();
  const visibleCapabilities =
    executorLevel === "contained"
      ? capabilities.filter(({ id }) => CONTAINED_CAPABILITIES.has(id))
      : capabilities;
  const visibleMandatoryMismatches =
    executorLevel === "contained"
      ? mandatoryMismatches.filter((item) =>
          [...CONTAINED_CAPABILITIES].some((capability) => item.startsWith(capability)),
        )
      : mandatoryMismatches;
  const visibleAdvisoryGaps =
    executorLevel === "contained"
      ? advisoryGaps.filter((item) => CONTAINED_CAPABILITIES.has(item))
      : advisoryGaps;

  return (
    <section aria-labelledby="governance-contract-title">
      <h2 id="governance-contract-title">{t("governanceUi.title")}</h2>
      <dl>
        <dt>{t("governanceUi.requestedExecutor")}</dt>
        <dd>{requestedExecutor}</dd>
        <dt>{t("governanceUi.effectiveExecutor")}</dt>
        <dd>{effectiveExecutor}</dd>
        <dt>{t("governanceUi.executorLevel")}</dt>
        <dd>{executorLevel ?? t("governanceUi.executorLevelUnknown")}</dd>
      </dl>
      {executorLevel === "contained" ? <p>{t("governanceUi.containedCaveat")}</p> : null}

      <table>
        <thead>
          <tr>
            <th scope="col">{t("governanceUi.capability")}</th>
            <th scope="col">{t("governanceUi.requested")}</th>
            <th scope="col">{t("governanceUi.effective")}</th>
          </tr>
        </thead>
        <tbody>
          {visibleCapabilities.map((capability) => (
            <tr key={capability.id}>
              <th scope="row">{capability.label}</th>
              <td>{t(`governanceUi.requestedState.${capability.requested}`)}</td>
              <td>{t(`governanceUi.effectiveState.${capability.effective}`)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {visibleMandatoryMismatches.length > 0 ? (
        <section>
          <h3>{t("governanceUi.mandatoryMismatches")}</h3>
          <ul>{visibleMandatoryMismatches.map((item) => <li key={item}>{item}</li>)}</ul>
        </section>
      ) : null}
      {visibleAdvisoryGaps.length > 0 ? (
        <section>
          <h3>{t("governanceUi.advisoryGaps")}</h3>
          <ul>{visibleAdvisoryGaps.map((item) => <li key={item}>{item}</li>)}</ul>
        </section>
      ) : null}
    </section>
  );
}
