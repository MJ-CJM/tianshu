import type { TaskStatus } from "../../api/types";
import SemanticTag from "../common/SemanticTag";
import { STATUS_COLORS, STATUS_LABELS } from "../../utils/constants";
import styles from "./StatusTag.module.css";

interface StatusTagProps {
  status: TaskStatus;
}

export default function StatusTag({ status }: StatusTagProps) {
  const isRunning = status === "running";
  // 「待朱批」是全屏唯一的实色标签:朱砂底,等主上落笔
  const isNeedsReview = status === "needs_review";

  return (
    <SemanticTag
      colorVar={STATUS_COLORS[status]}
      solid={isNeedsReview}
      className={isRunning ? styles.pulse : undefined}
    >
      {STATUS_LABELS[status]}
    </SemanticTag>
  );
}
