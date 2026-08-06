import { BrowserRouter } from "react-router-dom";
import { ConfigProvider, App as AntApp } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import zhCN from "antd/locale/zh_CN";
import enUS from "antd/locale/en_US";
import { ThemeContext, useThemeProvider } from "./hooks/useTheme";
import { LocaleContext, useLocaleProvider } from "./hooks/useLocale";
import { getThemeConfig } from "./theme";
import { AuthProvider } from "./auth/AuthProvider";
import LoginGate from "./auth/LoginGate";
import AppRoutes from "./router/AppRoutes";

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
        <ConfigProvider
          theme={getThemeConfig(themeCtx.mode)}
          locale={antdLocale}
        >
          <AntApp>
            <AuthProvider>
              <BrowserRouter>
                <LoginGate>
                  <AppRoutes />
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
