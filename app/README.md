# Research Radar Web

Research Radar 的 React 19 + TypeScript + Vite 前端。

```bash
npm install
npm run dev
```

开发服务器默认运行在 `http://localhost:5173`，并将 `/api` 请求代理到
`http://localhost:8501`。提交前运行：

```bash
npm run lint
npm run build
```

生产构建输出到 `app/dist`，FastAPI 在本地开发时会直接托管该目录；Docker
构建会把它复制到 `/app/static`。
