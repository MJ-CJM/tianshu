import { Card, type CardProps } from "antd";
import styles from "./GlowCard.module.css";

export default function GlowCard(props: CardProps) {
  return (
    <Card {...props} className={`${styles.card} ${props.className ?? ""}`} />
  );
}
