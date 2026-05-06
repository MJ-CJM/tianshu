import { Card, Progress, Typography, Button, InputNumber, Space, notification } from "antd";
import { useState } from "react";
import type { BudgetStatus } from "../../api/types";
import { useSetCostBudget } from "../../hooks/useCost";
import { useT } from "../../i18n";

const { Text } = Typography;

interface Props {
  budget: BudgetStatus | null | undefined;
  loading: boolean;
}

export default function BudgetProgressBar({ budget, loading }: Props) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [newBudget, setNewBudget] = useState<number>(0);
  const setBudgetMutation = useSetCostBudget();

  if (loading) {
    return <Card title={t("cost.budget.title")} loading />;
  }

  const handleSave = () => {
    if (newBudget <= 0) {
      notification.warning({ message: t("cost.budget.amountInvalid") });
      return;
    }
    setBudgetMutation.mutate(
      { scope: "global", budgetCny: newBudget },
      {
        onSuccess: () => {
          notification.success({ message: t("cost.budget.updated") });
          setEditing(false);
        },
      },
    );
  };

  if (!budget) {
    return (
      <Card title={t("cost.budget.title")}>
        <Text type="secondary">{t("cost.budget.empty")}</Text>
        <div style={{ marginTop: 12 }}>
          <Space>
            <InputNumber
              prefix="¥"
              min={0}
              step={1}
              value={newBudget}
              onChange={(v) => setNewBudget(v ?? 0)}
              style={{ width: 120 }}
            />
            <Button
              type="primary"
              size="small"
              loading={setBudgetMutation.isPending}
              onClick={handleSave}
            >
              {t("cost.budget.set")}
            </Button>
          </Space>
        </div>
      </Card>
    );
  }

  const percent = budget.budget_cny > 0
    ? Math.min(100, (budget.spent_cny / budget.budget_cny) * 100)
    : 0;

  const status = budget.exceeded ? "exception" : percent > 80 ? "active" : "normal";

  return (
    <Card title={t("cost.budget.title")}>
      <Progress
        percent={Number(percent.toFixed(1))}
        status={status}
        format={() => `¥${budget.spent_cny.toFixed(2)} / $${budget.budget_cny.toFixed(2)}`}
      />
      <div style={{ marginTop: 8 }}>
        <Text type="secondary">
          {t("cost.budget.remaining", { remaining: budget.remaining_cny.toFixed(2), period: budget.period })}
        </Text>
      </div>
      {editing ? (
        <div style={{ marginTop: 12 }}>
          <Space>
            <InputNumber
              prefix="¥"
              min={0}
              step={1}
              value={newBudget}
              onChange={(v) => setNewBudget(v ?? 0)}
              style={{ width: 120 }}
            />
            <Button size="small" type="primary" loading={setBudgetMutation.isPending} onClick={handleSave}>
              {t("button.save")}
            </Button>
            <Button size="small" onClick={() => setEditing(false)}>
              {t("common.cancel")}
            </Button>
          </Space>
        </div>
      ) : (
        <Button
          size="small"
          style={{ marginTop: 8 }}
          onClick={() => {
            setNewBudget(budget.budget_cny);
            setEditing(true);
          }}
        >
          {t("cost.budget.edit")}
        </Button>
      )}
    </Card>
  );
}
