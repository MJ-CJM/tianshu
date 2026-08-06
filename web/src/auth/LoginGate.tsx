import React, { useState } from "react";
import { Alert, Button, Form, Input, Spin } from "antd";
import { useT } from "../i18n";
import { useAuth } from "./AuthContext";
import styles from "./LoginGate.module.css";

export default function LoginGate({ children }: { children: React.ReactNode }) {
  const t = useT();
  const { status, login, retry } = useAuth();
  const [token, setToken] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [invalid, setInvalid] = useState(false);

  if (status === "authenticated") return <>{children}</>;

  if (status === "checking") {
    return (
      <main className={styles.shell} aria-label={t("auth.loading")}>
        <Spin size="large" />
      </main>
    );
  }

  if (status === "error") {
    return (
      <main className={styles.shell}>
        <section className={styles.card}>
          <Alert type="error" showIcon message={t("auth.unavailable")} />
          <Button block onClick={retry}>
            {t("auth.retry")}
          </Button>
        </section>
      </main>
    );
  }

  const submit = async () => {
    const credential = token.trim();
    if (!credential || submitting) return;
    setToken("");
    setInvalid(false);
    setSubmitting(true);
    try {
      await login(credential);
    } catch {
      setInvalid(true);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className={styles.shell}>
      <section className={styles.card} aria-labelledby="auth-title">
        <div className={styles.brand}>
          <img src="/brand.png" alt="" aria-hidden width={38} height={38} />
          <div>
            <h1 id="auth-title">{t("comp.appHeader.brand")}</h1>
            <p>{t("auth.subtitle")}</p>
          </div>
        </div>
        <div className={styles.rule} />
        <p className={styles.tagline}>{t("comp.appHeader.tagline")}</p>
        {invalid ? (
          <Alert type="error" showIcon message={t("auth.invalid")} />
        ) : null}
        <Form layout="vertical" onFinish={() => void submit()}>
          <Form.Item label={t("auth.tokenLabel")} required>
            <Input.Password
              autoComplete="off"
              autoFocus
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder={t("auth.tokenPlaceholder")}
              aria-label={t("auth.tokenLabel")}
            />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            block
            loading={submitting}
            disabled={!token.trim()}
          >
            {t("auth.login")}
          </Button>
        </Form>
      </section>
    </main>
  );
}
