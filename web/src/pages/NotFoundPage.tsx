import { Button, Result } from "antd";
import { useNavigate } from "react-router-dom";
import { useT } from "../i18n";

export default function NotFoundPage() {
  const navigate = useNavigate();
  const t = useT();

  return (
    <Result
      status="404"
      title={t("notFoundPage.title")}
      subTitle={t("notFoundPage.description")}
      extra={
        <Button type="primary" onClick={() => navigate("/control", { replace: true })}>
          {t("notFoundPage.back")}
        </Button>
      }
    />
  );
}
