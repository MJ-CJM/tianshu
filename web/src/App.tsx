import React, { Suspense } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { ConfigProvider, App as AntApp, Spin } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import zhCN from "antd/locale/zh_CN";
import { ThemeContext, useThemeProvider } from "./hooks/useTheme";
import { getThemeConfig } from "./theme";
import AppLayout from "./components/layout/AppLayout";
import EdictListPage from "./pages/EdictListPage";
import EdictCreatePage from "./pages/EdictCreatePage";
import EdictDetailPage from "./pages/EdictDetailPage";
import ApprovalQueuePage from "./pages/ApprovalQueuePage";
import SchedulerPage from "./pages/SchedulerPage";
import AuditDashboardPage from "./pages/AuditDashboardPage";
import CostDashboardPage from "./pages/CostDashboardPage";
import MemoryDashboardPage from "./pages/MemoryDashboardPage";
import ConsultationPage from "./pages/ConsultationPage";
import CabinetPage from "./pages/CabinetPage";
import PersonaDashboardPage from "./pages/PersonaDashboardPage";
import PersonaDetailPage from "./pages/PersonaDetailPage";
import SystemManagementPage from "./pages/SystemManagementPage";
// Lazy-loaded DAG Battle Map (heavy @xyflow/react dependency)
const DagBattleMapPage = React.lazy(() => import("./pages/DagBattleMapPage"));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function ThemedApp() {
  const themeCtx = useThemeProvider();

  return (
    <ThemeContext.Provider value={themeCtx}>
      <ConfigProvider theme={getThemeConfig(themeCtx.mode)} locale={zhCN}>
        <AntApp>
          <BrowserRouter>
            <Routes>
              <Route element={<AppLayout />}>
                <Route path="/" element={<EdictListPage />} />
                <Route path="/edicts/create" element={<EdictCreatePage />} />
                <Route path="/edicts/:edictId" element={<EdictDetailPage />} />
                <Route path="/approvals" element={<ApprovalQueuePage />} />
                <Route path="/scheduler" element={<SchedulerPage />} />
                <Route path="/audit" element={<AuditDashboardPage />} />
                <Route path="/cost" element={<CostDashboardPage />} />
                <Route path="/memory" element={<MemoryDashboardPage />} />
                <Route path="/consultation" element={<ConsultationPage />} />
                <Route path="/cabinet" element={<CabinetPage />} />
                <Route path="/personas" element={<PersonaDashboardPage />} />
                <Route path="/personas/:personaId" element={<PersonaDetailPage />} />
                <Route path="/system" element={<SystemManagementPage />} />
                <Route
                  path="/dag/:dagId"
                  element={
                    <Suspense fallback={<Spin size="large" style={{ display: 'block', margin: '20vh auto' }} />}>
                      <DagBattleMapPage />
                    </Suspense>
                  }
                />
              </Route>
            </Routes>
          </BrowserRouter>
        </AntApp>
      </ConfigProvider>
    </ThemeContext.Provider>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemedApp />
    </QueryClientProvider>
  );
}
