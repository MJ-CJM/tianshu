import { useEffect, useState } from "react";
import { CloseOutlined } from "@ant-design/icons";
import { AppShell } from "./components/AppShell.jsx";
import { ControlCenter } from "./screens/ControlCenter.jsx";
import { EdictDetail } from "./screens/EdictDetail.jsx";
import { EvolutionCenter } from "./screens/EvolutionCenter.jsx";

export const SCREEN_IDS = ["control", "edict", "evolution"];

const INITIAL_EDICT_DECISION = {
  record: null,
  reason: "",
  validationMessage: "",
};

export function App({ initialScreen = "control" }) {
  const [screen, setScreen] = useState(SCREEN_IDS.includes(initialScreen) ? initialScreen : "control");
  const [dark, setDark] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [reservedDepartment, setReservedDepartment] = useState(null);
  const [edictDecision, setEdictDecision] = useState(INITIAL_EDICT_DECISION);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    return () => document.documentElement.removeAttribute("data-theme");
  }, [dark]);

  function navigate(target, label) {
    setMobileOpen(false);
    if (target && SCREEN_IDS.includes(target)) {
      setScreen(target);
      setReservedDepartment(null);
      return;
    }
    setReservedDepartment(label);
  }

  let content;
  if (screen === "edict") {
    content = (
      <EdictDetail
        decisionState={edictDecision}
        onBack={() => setScreen("control")}
        onDecisionStateChange={setEdictDecision}
      />
    );
  } else if (screen === "evolution") {
    content = <EvolutionCenter onBack={() => setScreen("control")} />;
  } else {
    content = <ControlCenter onOpenEdict={() => setScreen("edict")} onOpenEvolution={() => setScreen("evolution")} />;
  }

  return (
    <AppShell
      currentScreen={screen}
      dark={dark}
      mobileOpen={mobileOpen}
      onCloseMobile={() => setMobileOpen(false)}
      onNavigate={navigate}
      onOpenMobile={() => setMobileOpen(true)}
      onToggleTheme={() => setDark((value) => !value)}
    >
      {reservedDepartment ? (
        <div className="reserved-notice" aria-live="polite">
          <div><strong>{reservedDepartment}</strong><span>该部门保留在正式信息架构中，本轮只验收中枢、敕令详情与演化中心。</span></div>
          <button className="icon-button" type="button" aria-label="关闭部门提示" onClick={() => setReservedDepartment(null)}><CloseOutlined aria-hidden="true" /></button>
        </div>
      ) : null}
      {content}
    </AppShell>
  );
}
