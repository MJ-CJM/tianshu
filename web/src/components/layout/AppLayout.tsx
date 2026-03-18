import { Layout } from "antd";
import { Outlet } from "react-router-dom";
import AppHeader from "./AppHeader";
import AppSidebar from "./AppSidebar";
import styles from "./AppLayout.module.css";

export default function AppLayout() {
  return (
    <Layout className={styles.root}>
      <AppHeader />
      <Layout>
        <AppSidebar />
        <Layout.Content className={styles.content}>
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  );
}
