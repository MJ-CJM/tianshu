import { Layout } from "antd";
import { Outlet } from "react-router-dom";
import AppHeader from "./AppHeader";
import AppSidebar from "./AppSidebar";
import { useWebSocket } from "../../hooks/useWebSocket";
import { useWsQueryInvalidation } from "../../hooks/useWsQueryInvalidation";
import { useWsPolicyToasts } from "../../hooks/useWsPolicyToasts";
import styles from "./AppLayout.module.css";

export default function AppLayout() {
  const { isConnected, subscribe } = useWebSocket();
  useWsQueryInvalidation(subscribe);
  useWsPolicyToasts(subscribe);

  return (
    <Layout className={styles.root}>
      <AppHeader isWsConnected={isConnected} />
      <Layout className={styles.body}>
        <AppSidebar />
        <Layout.Content className={styles.content}>
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
