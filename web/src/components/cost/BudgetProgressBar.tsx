import { Card, Progress, Typography, Button, InputNumber, Space, notification } from "antd";
import { useState } from "react";
import type { BudgetStatus } from "../../api/types";
import { useSetCostBudget } from "../../hooks/useCost";

const { Text } = Typography;

interface Props {
  budget: BudgetStatus | null | undefined;
  loading: boolean;
}

export default function BudgetProgressBar({ budget, loading }: Props) {
  const [editing, setEditing] = useState(false);
  const [newBudget, setNewBudget] = useState<number>(0);
  const setBudgetMutation = useSetCostBudget();

  if (loading) {
    return <Card title="预算" loading />;
  }

  const handleSave = () => {
    if (newBudget <= 0) {
      notification.warning({ message: "预算金额须大于零" });
      return;
    }
    setBudgetMutation.mutate(
      { scope: "global", budgetCny: newBudget },
      {
        onSuccess: () => {
          notification.success({ message: "预算已更新" });
          setEditing(false);
        },
      },
    );
  };

  if (!budget) {
    return (
      <Card title="预算">
        <Text type="secondary">暂未设置预算</Text>
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
              设置预算
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
    <Card title="预算">
      <Progress
        percent={Number(percent.toFixed(1))}
        status={status}
        format={() => `¥${budget.spent_cny.toFixed(2)} / $${budget.budget_cny.toFixed(2)}`}
      />
      <div style={{ marginTop: 8 }}>
        <Text type="secondary">
          剩余: ${budget.remaining_cny.toFixed(2)} ({budget.period})
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
              保存
            </Button>
            <Button size="small" onClick={() => setEditing(false)}>
              取消
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
          修改预算
        </Button>
      )}
    </Card>
  );
}
