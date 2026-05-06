import { useHealth } from "../../hooks/useHealth";
import styles from "./HealthDot.module.css";
import { useT } from "../../i18n";

export default function HealthDot() {
  const t = useT();
  const { data, isError } = useHealth();
  const isOk = !isError && data?.status === "ok";

  return (
    <span className={styles.wrapper}>
      <span
        className={`${styles.dot} ${isOk ? styles.ok : styles.err}`}
      />
      <span className={styles.label}>
        {isOk ? t("comp.healthDot.ok") : t("comp.healthDot.err")}
      </span>
    </span>
  );
}
