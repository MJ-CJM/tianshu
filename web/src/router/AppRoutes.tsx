import { Component, lazy, Suspense } from "react";
import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Spin } from "antd";
import AppLayout from "../components/layout/AppLayout";
import PageDataState from "../components/states/PageDataState";
import { problemPageStatus } from "../components/states/problemPageStatus";
import { isApiProblem, toApiProblem } from "../api/client";
import type { ApiProblem } from "../contracts/api";
import { useT } from "../i18n";

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

function routeProblem(error: unknown): ApiProblem {
  if (isApiProblem(error)) return error;
  const problem = toApiProblem(error);
  const message = error instanceof Error ? error.message : "";
  const isChunkFailure =
    error instanceof Error &&
    /ChunkLoadError|Loading chunk|dynamically imported module|Failed to fetch.*module/i.test(
      `${error.name} ${message}`,
    );
  if (!isChunkFailure) {
    return {
      ...problem,
      status: 500,
      code: "route-render-error",
      retryable: true,
    };
  }
  return {
    ...problem,
    status: 503,
    code: "route-chunk-unavailable",
    retryable: true,
  };
}

function RouteFailureState({
  problem,
  onRetry,
}: {
  problem: ApiProblem;
  onRetry: () => void;
}) {
  const t = useT();
  const visibleProblem =
    problem.code === "route-chunk-unavailable"
      ? { ...problem, message: t("pageDataState.chunkDescription") }
      : problem;
  return (
    <PageDataState
      status={problemPageStatus(visibleProblem)}
      data={null}
      problem={visibleProblem}
      isEmpty={(items: unknown[]) => items.length === 0}
      onRetry={onRetry}
    >
      {() => null}
    </PageDataState>
  );
}

interface RouteErrorBoundaryProps {
  children: ReactNode;
  onRetry?: () => void;
}

interface RouteErrorBoundaryState {
  problem: ApiProblem | null;
}

export class RouteErrorBoundary extends Component<
  RouteErrorBoundaryProps,
  RouteErrorBoundaryState
> {
  state: RouteErrorBoundaryState = { problem: null };

  static getDerivedStateFromError(error: unknown): RouteErrorBoundaryState {
    return { problem: routeProblem(error) };
  }

  private retry = () => {
    if (this.props.onRetry) {
      this.props.onRetry();
      this.setState({ problem: null });
      return;
    }
    window.location.reload();
  };

  render() {
    const { problem } = this.state;
    if (!problem) return this.props.children;
    return <RouteFailureState problem={problem} onRetry={this.retry} />;
  }
}

export default function AppRoutes() {
  return (
    <RouteErrorBoundary>
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
    </RouteErrorBoundary>
  );
}
