import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Spin } from "antd";
import AppLayout from "../components/layout/AppLayout";

const ControlCenterPage = lazy(() => import("../pages/ControlCenterPage"));
const RoyalStudyPage = lazy(() => import("../pages/RoyalStudyPage"));
const EdictCreatePage = lazy(() => import("../pages/EdictCreatePage"));
const EdictDetailPage = lazy(() => import("../pages/EdictDetailPage"));
const SchedulerPage = lazy(() => import("../pages/SchedulerPage"));
const AuditDashboardPage = lazy(() => import("../pages/AuditDashboardPage"));
const CostDashboardPage = lazy(() => import("../pages/CostDashboardPage"));
const MemoryDashboardPage = lazy(() => import("../pages/MemoryDashboardPage"));
const ConsultationPage = lazy(() => import("../pages/ConsultationPage"));
const CabinetPage = lazy(() => import("../pages/CabinetPage"));
const HongluisiPage = lazy(() => import("../pages/HongluisiPage"));
const TongzhengPage = lazy(() => import("../pages/TongzhengPage"));
const PersonaDashboardPage = lazy(() => import("../pages/PersonaDashboardPage"));
const PersonaDetailPage = lazy(() => import("../pages/PersonaDetailPage"));
const SystemManagementPage = lazy(() => import("../pages/SystemManagementPage"));
const SessionRulesPage = lazy(() => import("../pages/SessionRulesPage"));
const UniversePage = lazy(() => import("../pages/UniversePage"));
const EvalsPage = lazy(() => import("../pages/EvalsPage"));
const DagBattleMapPage = lazy(() => import("../pages/DagBattleMapPage"));

export default function AppRoutes() {
  return (
    <Suspense fallback={<Spin size="large" style={{ display: "block", margin: "20vh auto" }} />}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<Navigate to="/control" replace />} />
          <Route path="/control" element={<ControlCenterPage />} />
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
          <Route path="/dag/:dagId" element={<DagBattleMapPage />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
