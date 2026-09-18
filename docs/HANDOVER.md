# Research Radar 开发交接文档

> 本文档面向接手的开发者，说明当前项目状态、架构、部署方式、已知问题和下一步维护事项。

---

## 1. 项目概况

**Research Radar** 是一个科学情报 Agent：

- 上传论文后，系统提取并人工确认 Claim。
- 持续扫描公开文献（arXiv、OpenAlex、Semantic Scholar、PubMed、Crossref、中文文献等）。
- 判断每篇新证据对 Claim 的影响，输出支持 / 挑战 / 中性 / 不确定。
- 生成实验、引用、写作行动，并支持引用核验与论文改写。
- 新增学术声誉 / 竞品实验室影响力分析能力。

### 当前状态

| 项目 | 状态 |
|---|---|
| GitHub 仓库 | `https://github.com/GZ-November/research-radar` |
| 主分支 | `main` |
| 当前提交 | `b51c182 docs: rewrite README ...` |
| 生产服务器 | `101.96.220.158` |
| 公网访问 | `http://101.96.220.158` |
| 服务器运行镜像 | 本地构建的 `ghcr.io/gz-november/research-radar:local` |
| 服务健康 | `GET /api/health` → `ok` |

---

## 2. 技术栈

- **后端**：Python 3.12 / FastAPI / Pydantic v2 / SQLAlchemy
- **数据库**：SQLite（每用户独立数据目录），持久化向量存储使用 Chroma
- **LLM**：支持远程 Provider + 本地 Ollama，统一走 `radar/llm/` Gateway
- **前端**：React 19 / TypeScript / Vite / Tailwind
- **部署**：Docker Compose + Caddy（反向代理 / TLS）
- **CI/CD**：GitHub Actions，构建 standard / enhanced 镜像并发布到 GHCR

---

## 3. 代码结构

```text
radar/
  api.py                 # FastAPI 入口（轻量）
  routes/                # 模块化路由
  services/              # 领域服务
  llm/                   # LLM Gateway / Provider / 上下文管理
  adapters/              # 外部学术数据源适配器
  schemas.py             # Pydantic 契约
  models.py              # SQLAlchemy 模型
  config.py              # 配置
  tracing.py             # 可观测性
app/
  src/pages/             # 页面
  src/components/        # 组件（AuthorLabDrawer 等）
  src/api.ts             # API 客户端
docs/
  HANDOVER.md            # 本文件
scripts/                 # 部署 / 评测脚本
tests/                   # 后端测试
```

核心模块：

- `radar/routes/`：auth、cases、claims、citations、scans、sources、skills、settings、patches、qa。
- `radar/services/`：`weekly_radar_service`、`citation_support_service`、`reference_validity_service`、`impact_service`、`retrieval_service`、`search_cache` 等。
- `radar/llm/gateway.py`：统一结构化输出、重试、追踪、成本估计。
- `radar/adapters/`：arXiv、OpenAlex、CrossRef、DBLP、Semantic Scholar、PubMed、Unpaywall。
- 前端新增：`AuthorLabDrawer`、`BenchmarkComparison`、`ErrorBoundary`、`StreamRenderer` 等。

---

## 4. 已实现功能

### 4.1 持续文献雷达
- 多源扫描、去重、CCF 等级 / arXiv 分类过滤、自动定时扫描。
- 扫描完成 / 失败 / 取消统一 Toast 和页面状态提示。

### 4.2 Claim 与影响判断
- Claim 提取、人工确认、编辑、拆分、版本 diff。
- 六字段条件比较（Task / Dataset / Split / Metric / Comparator / Scope）。
- 逐字证据锚定、NLI 二次校验、证据忠实度评估。
- 支持/挑战/中性/不确定 + 严重度 + 行动建议。

### 4.3 引文核验
- 第一层：参考文献真实性核验（BibTeX / 纯文本 / PDF，CrossRef / OpenAlex / arXiv / DBLP）。
- 第二层：引用句支持度核验（Supported / Partially Supported / Unsupported / Uncertain）。
- 支持全文获取、abstract-only 降级。

### 4.4 学术声誉与竞品实验室影响力画像
- 作者 H-Index、引用量、机构、引文速度。
- Top-Tier Lab 识别。
- 高影响力竞争论文自动 Urgency Boost。
- 前端 Author & Lab Profile 徽章、团队动态 Drawer。

### 4.5 其他
- 来源库、成本统计、通知 Webhook / 邮件、MCP 服务、技能流水线、文献 QA、Deep Research。
- 多用户账号、角色权限、管理员创建用户、登录/注册限流。

---

## 5. 本地开发

```bash
# 后端
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn radar.api:app --host 0.0.0.0 --port 8501

# 前端
cd app
npm install
npm run dev
```

测试与构建：

```bash
PYTHONPATH=. pytest -q
cd app && npm run build
```

---

## 6. 部署方式

### 6.1 仓库部署流程
- GitHub Actions `.github/workflows/deploy.yml`
- 触发条件：推送到 `main` / `experimental`，或手动 `workflow_dispatch` 选择 `standard` / `enhanced`
- 流程：
  1. CI 构建镜像并推送到 GHCR
  2. 将 `deploy_server.sh` 和 `docker-compose.yml` 通过 SSH 下发到服务器
  3. 服务器从国内镜像源拉取镜像
  4. `docker compose up -d --no-build`
  5. 健康检查

### 6.2 服务器手动部署 / 本地构建
由于服务器到 GHCR / 国内镜像的网络较慢，当前生产环境使用服务器本地构建：

```bash
cd /root/research-radar

# 同步最新源码（可通过 git archive 或 git pull）
git archive --format=tar HEAD | tar -x -C /root/research-radar

# 使用国内镜像构建 standard 镜像
NPM_REGISTRY=https://registry.npmmirror.com \
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
TORCH_INDEX_URL=https://mirrors.aliyun.com/pytorch-wheels/cpu/ \
RADAR_INSTALL_EXTRAS= \
docker compose build research-radar

# 启动
docker compose up -d --no-build
```

### 6.3 标准版 / 增强版
- `standard`：轻量镜像，不含 PyTorch / Docling / PaperQA2。
- `enhanced`：包含 NLI、Docling、PaperQA2 等依赖；功能开关：
  ```env
  NLI_ENABLED=true
  PDF_PARSER_BACKEND=docling
  PAPERQA2_ENABLED=true
  ```

---

## 7. 服务器信息与访问

- 服务器 IP：`101.96.220.158`
- SSH：`root@101.96.220.158 -p 22`
- 代码目录：`/root/research-radar`
- 数据目录：`/root/research-radar/data`
  - `data/users.db`：账号库
  - `data/users/<username>/`：每用户独立数据库与设置
  - `data/research_radar.db`：全局/默认库
- 证书目录：`/root/research-radar/certs`
- 重要：服务器 SSH 密码 / 私钥不要写入仓库，保持放在 GitHub Secrets 或原负责人处。

### GitHub Secrets
- `SERVER_HOST`
- `SERVER_PORT`
- `SERVER_USER`
- `SERVER_SSH_KEY`
- `SERVER_GHCR_USERNAME`
- `SERVER_GHCR_TOKEN`

---

## 8. 已知问题与风险

1. **GHCR 镜像包需要保持 public**
   - 国内镜像 `ghcr.nju.edu.cn` 只能代理 public 包。
   - 若改回 private，CI 自动部署会失败。
   - 长期方案：使用可写 `write:packages` 的 Token 自动切换，或改用国内容器仓库。

2. **服务器到 GitHub / GHCR 网络慢**
   - CI 的“服务器 git fetch”已移除，避免卡死。
   - 当前可以走服务器本地构建作为回退。

3. **公网注册目前保持开放**
   - 原负责人暂未关闭。
   - 正式对外建议改为：
     ```env
     ALLOW_REGISTRATION=false
     ```
   - 管理员可通过 Admin 页面“创建用户”开通账号。

4. **HTTPS 尚未使用正式域名**
   - 当前是自签名证书 + HTTP 80/8080。
   - 如需正式公网，应配置真实域名和 Let's Encrypt，并关闭 8080 明文暴露。

5. **Caddy 当前配置**
   - `Caddyfile` 支持 80 / 8080 / 443 自签名。
   - 改造时注意 iOS 兼容说明。

6. **Chroma 向量库**
   - 必须显式传入 embeddings，避免自动下载内置模型。
   - 若 embedding 服务不可用，检索会降级到关键词/BM25。

---

## 9. 接手指南 / TODO

### 接手第一步
1. 获取 GitHub 仓库访问权限。
2. 获取服务器 SSH 访问方式（找原负责人）。
3. 确认 GitHub Secrets 完整。
4. 本地跑通：
   ```bash
   PYTHONPATH=. pytest -q
   cd app && npm run build
   ```
5. 登录服务器确认服务：
   ```bash
   curl http://127.0.0.1/api/health
   docker compose ps
   ```

### 后续建议顺序
1. 关闭公网注册或接入邀请制。
2. 配置正式域名 + HTTPS。
3. 建立数据库/数据目录每日备份。
4. 把服务器本地构建流程固化为脚本，或换用国内容器仓库。
5. 继续验证新功能（学术声誉画像、引文核验、扫描状态）。
6. 回归测试：登录、历史文档、扫描速度（p95 回退 ≤10%）、Claim 提取召回。

---

## 10. 联系方式

- 当前交接说明由原开发整理。
- 服务器密码 / SSH 私钥请向原负责人单独获取，不要提交到 Git。
