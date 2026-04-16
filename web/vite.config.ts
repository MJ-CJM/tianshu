import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 7999,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        ws: true,
      },
      "/health": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "../src/tianshu/web/static",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ["react", "react-dom", "react-router-dom"],
          antd: ["antd", "@ant-design/icons"],
        },
      },
    },
  },
});
