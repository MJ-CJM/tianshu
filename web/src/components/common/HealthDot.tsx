import { useHealth } from "../../hooks/useHealth";
import styles from "./HealthDot.module.css";

export default function HealthDot() {
  const { data, isError } = useHealth();
  const isOk = !isError && data?.status === "ok";

  return (
    <span className={styles.wrapper}>
      <span
        className={`${styles.dot} ${isOk ? styles.ok : styles.err}`}
      />
      <span className={styles.label}>
        {isOk ? "通政" : "断讯"}
      </span>
    </span>
  );
}
