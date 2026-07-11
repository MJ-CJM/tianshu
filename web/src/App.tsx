import React, { Suspense } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { ConfigProvider, App as AntApp, Spin } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import zhCN from "antd/locale/zh_CN";
import enUS from "antd/locale/en_US";
import { ThemeContext, useThemeProvider } from "./hooks/useTheme";
import { LocaleContext, useLocaleProvider } from "./hooks/useLocale";
import { getThemeConfig } from "./theme";
import { AuthProvider } from "./auth/AuthProvider";
import LoginGate from "./auth/LoginGate";
import AppLayout from "./components/layout/AppLayout";
import RoyalStudyPage from "./pages/RoyalStudyPage";
import EdictCreatePage from "./pages/EdictCreatePage";
import EdictDetailPage from "./pages/EdictDetailPage";
import SchedulerPage from "./pages/SchedulerPage";
import AuditDashboardPage from "./pages/AuditDashboardPage";
import CostDashboardPage from "./pages/CostDashboardPage";
import MemoryDashboardPage from "./pages/MemoryDashboardPage";
import ConsultationPage from "./pages/ConsultationPage";
import CabinetPage from "./pages/CabinetPage";
import HongluisiPage from "./pages/HongluisiPage";
import TongzhengPage from "./pages/TongzhengPage";
import PersonaDashboardPage from "./pages/PersonaDashboardPage";
import PersonaDetailPage from "./pages/PersonaDetailPage";
import SystemManagementPage from "./pages/SystemManagementPage";
import SessionRulesPage from "./pages/SessionRulesPage";
import UniversePage from "./pages/UniversePage";
import EvalsPage from "./pages/EvalsPage";
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
  const localeCtx = useLocaleProvider();
  const antdLocale = localeCtx.locale === "en" ? enUS : zhCN;

  return (
    <ThemeContext.Provider value={themeCtx}>
      <LocaleContext.Provider value={localeCtx}>
        <ConfigProvider theme={getThemeConfig(themeCtx.mode)} locale={antdLocale}>
          <AntApp>
            <AuthProvider>
              <BrowserRouter>
                <LoginGate>
                  <Routes>
                    <Route element={<AppLayout />}>
                      <Route path="/" element={<RoyalStudyPage />} />
                      <Route path="/edicts/create" element={<EdictCreatePage />} />
                      <Route path="/edicts/:edictId" element={<EdictDetailPage />} />
                      <Route path="/approvals" element={<RoyalStudyPage />} />
                      <Route path="/scheduler" element={<SchedulerPage />} />
                      <Route path="/audit" element={<AuditDashboardPage />} />
                      <Route path="/cost" element={<CostDashboardPage />} />
                      <Route path="/memory" element={<MemoryDashboardPage />} />
                      <Route path="/consultation" element={<ConsultationPage />} />
                      <Route path="/cabinet" element={<CabinetPage />} />
                      <Route path="/hongluisi" element={<HongluisiPage />} />
                      <Route path="/tongzheng" element={<TongzhengPage />} />
                      <Route path="/personas" element={<PersonaDashboardPage />} />
                      <Route path="/personas/:personaId" element={<PersonaDetailPage />} />
                      <Route path="/system" element={<SystemManagementPage />} />
                      <Route path="/session-rules" element={<SessionRulesPage />} />
                      <Route path="/universes" element={<UniversePage />} />
                      <Route path="/evals" element={<EvalsPage />} />
                      <Route
                        path="/dag/:dagId"
                        element={
                          <Suspense
                            fallback={
                              <Spin
                                size="large"
                                style={{ display: "block", margin: "20vh auto" }}
                              />
                            }
                          >
                            <DagBattleMapPage />
                          </Suspense>
                        }
                      />
                    </Route>
                  </Routes>
                </LoginGate>
              </BrowserRouter>
            </AuthProvider>
          </AntApp>
        </ConfigProvider>
      </LocaleContext.Provider>
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
