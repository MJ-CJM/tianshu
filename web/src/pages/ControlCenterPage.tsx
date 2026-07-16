import PageContainer from "../components/common/PageContainer";

export default function ControlCenterPage() {
  return (
    <PageContainer title="中枢总览">
      <section
        style={{
          border: "1px solid var(--ts-color-border)",
          borderRadius: 8,
          padding: 16,
          background: "var(--ts-color-surface)",
          color: "var(--ts-color-text-secondary)",
        }}
      >
        桌面控制壳层已就绪；核心读模型将在后续任务接入。
      </section>
    </PageContainer>
  );
}
