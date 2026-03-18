import { Tag } from "antd";
import type { TaskStatus } from "../../api/types";
import { STATUS_COLORS, STATUS_LABELS } from "../../utils/constants";
import styles from "./StatusTag.module.css";

interface StatusTagProps {
  status: TaskStatus;
}

export default function StatusTag({ status }: StatusTagProps) {
  const color = STATUS_COLORS[status];
  const isRunning = status === "running";

  return (
    <Tag
      color={color}
      className={isRunning ? styles.pulse : undefined}
      style={
        isRunning
          ? { boxShadow: `0 0 8px ${color}80` }
          : undefined
      }
    >
      {STATUS_LABELS[status]}
    </Tag>
  );
}
