import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"
import { inspectAttr } from 'kimi-plugin-inspect-react'

export default defineConfig(({ mode }) => ({
  base: './',
  plugins: [
    // Dev-only instrumentation; never applied to production builds.
    ...(mode === 'development' ? [inspectAttr()] : []),
    react(),
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8501',
        changeOrigin: true,
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    target: 'es2020',
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router'],
          charts: ['recharts'],
          icons: ['lucide-react'],
        },
      },
    },
  },
}));
