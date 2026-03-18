import { BrowserRouter, Routes, Route } from "react-router-dom";
import { ConfigProvider, App as AntApp } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import zhCN from "antd/locale/zh_CN";
import themeConfig from "./theme";
import AppLayout from "./components/layout/AppLayout";
import EdictListPage from "./pages/EdictListPage";
import EdictCreatePage from "./pages/EdictCreatePage";
import EdictDetailPage from "./pages/EdictDetailPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider theme={themeConfig} locale={zhCN}>
        <AntApp>
          <BrowserRouter>
            <Routes>
              <Route element={<AppLayout />}>
                <Route path="/" element={<EdictListPage />} />
                <Route path="/edicts/create" element={<EdictCreatePage />} />
                <Route path="/edicts/:edictId" element={<EdictDetailPage />} />
              </Route>
            </Routes>
          </BrowserRouter>
        </AntApp>
      </ConfigProvider>
    </QueryClientProvider>
  );
}
