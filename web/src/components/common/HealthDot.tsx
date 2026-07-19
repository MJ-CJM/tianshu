import { useHealth } from "../../hooks/useHealth";
import styles from "./HealthDot.module.css";
import { useT } from "../../i18n";
import { FROZEN_HEALTH_LABEL } from "../../contracts/frozenShell";

export default function HealthDot() {
  const t = useT();
  const { data, isError } = useHealth();

  const state: "ready" | "degraded" | "err" =
    !isError && data?.status === "ready"
      ? "ready"
      : !isError && data?.status === "degraded"
        ? "degraded"
        : "err";

  const dotClass =
    state === "ready" ? styles.ok : state === "degraded" ? styles.warn : styles.err;
  const label =
    state === "ready"
      ? t("comp.healthDot.ok")
      : state === "degraded"
        ? t("comp.healthDot.degraded")
        : t("comp.healthDot.err");
  const isDemo = !isError && data?.profile === "demo";

  const accessibleLabel = `${label}${isDemo ? ` ${t("comp.healthDot.demo")}` : ""}`;

  return (
    <span className={styles.wrapper} role="status" aria-label={accessibleLabel}>
      <span className={`${styles.dot} ${dotClass}`} />
      <span className={styles.label}>
        {FROZEN_HEALTH_LABEL}
      </span>
    </span>
  );
}
