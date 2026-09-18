# Research Radar

科学界的情报 Agent：上传你的论文，雷达持续盯住公开文献，判断每一条新证据对论文核心主张的影响，并把新证据定时改造成实验、引用与写作行动。

> 当前仓库已重构为模块化后端 + 新一代前端，并加入学术声誉 / 竞品实验室影响力画像能力。`main` 分支即当前生产版本。

---

## 核心功能

### 持续文献雷达
- 多数据源扫描：arXiv、OpenAlex、Semantic Scholar、PubMed、Crossref、中文文献（OpenAlex `language:zh`）。
- 自动从文稿与 Claim 提炼检索主题，持续监控公开文献。
- 支持 CCF 等级 / arXiv 分类过滤、自动定时扫描、成本统计。

### 影响判断与证据链
- 新文献与论文 Claim 自动配对，输出支持 / 挑战 / 中性 / 不确定。
- 六字段条件比较（Task / Dataset / Split / Metric / Comparator / Scope）。
- 逐字证据锚定、NLI 二次校验、证据忠实度评估。
- 生成实验、引用、写作行动，并落到可审计的 Action / Patch。

### 学术声誉与竞品实验室影响力画像
- 自动检索作者 H-Index、引用量、所属机构。
- 计算论文引文速度（Citation Velocity）与社会热度。
- 识别 Top-Tier Lab：Stanford、MIT、UC Berkeley、CMU、Oxford、Cambridge、Tsinghua、Peking、Google DeepMind、OpenAI、Meta FAIR、Microsoft Research 等。
- 当竞争论文来自顶级实验室或高 H-Index 作者时，自动提高影响紧迫度（Urgency Boost）。
- 前端展示 Author & Lab Profile 徽章，并提供团队动态 Drawer 时间线。

### 引文核验
- 第一层：参考文献真实性核验，解析 BibTeX / 纯文本 / PDF，跨 CrossRef、OpenAlex、arXiv、DBLP 匹配，输出 Verified / Unverified + Confidence + 外部记录链接。
- 第二层：引用句支持度核验，从被引论文全文 / 摘要中检索证据，输出 Supported / Partially Supported / Unsupported / Uncertain。
- 支持全文获取与 abstract-only 降级，拿不到全文时不会阻断主流程。

### 文献问答与深度调研
- 文献级 QA：基于已收录来源，带引用锚点回答问题。
- Deep Research：多源检索、结构化简报、历史记录。

### 来源库与成本
- 来源列表、详情、标签、批量操作、JSON / Markdown 导出、BibTeX 导出。
- DOI / arXiv / 标题多键去重。
- 扫描成本按来源拆分、成本趋势、月度预算告警（Webhook）。

### 通知与自动化
- 扫描完成 Webhook、周报 Webhook（每日 / 每周 / 每月）。
- SMTP 邮件通知、通知历史。
- MCP 服务器：为 Agent / IDE 提供来源管理、成本、通知、检索回执、引用健康等工具。

### 技能流水线
- CCF-A 技能系统：实验设计、论文评审、反驳、引用审计、写作等。
- 支持结构化卡片渲染，避免原始 JSON 展示。

### 浏览器扩展 & Zotero 插件
- 快速收录文献，DOI 自动识别，右键导入。
- 与后端 Source / SourceSnapshot 打通。

---

## 工程结构

```text
radar/
  api.py                 # FastAPI 应用入口（轻量）
  routes/                # 模块化路由：auth / cases / claims / citations / scans / sources / skills / settings / patches / qa
  services/              # 领域服务：雷达扫描、引文核验、影响判断、检索、缓存、补丁等
  llm/                   # LLM Gateway、Provider、上下文管理、提示词
  adapters/              # arXiv / OpenAlex / Crossref / DBLP / Semantic Scholar / Unpaywall / PubMed 等
  schemas.py             # Pydantic 数据契约
  models.py              # SQLAlchemy 模型
  tracing.py             # 可观测性 / 追踪
app/                     # React + TypeScript + Vite 前端
docs/                    # 部署 / 工作流 / 设计文档
scripts/                 # 部署脚本、评测脚本
tests/                   # 后端测试
```

---

## 快速开始

### 本地开发

```bash
# 后端
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env   # 按需填写 LLM / embedding 配置
uvicorn radar.api:app --host 0.0.0.0 --port 8501

# 前端（另开终端）
cd app
npm install
npm run dev
```

前端开发服务器默认代理到 `http://localhost:8501`。

### Docker 部署

```bash
docker compose up -d --build
```

本地默认构建轻量标准镜像。需要包含 PyTorch、Docling 和 PaperQA2 的增强镜像时：

```bash
RADAR_INSTALL_EXTRAS=full RADAR_IMAGE_TAG=local-enhanced docker compose up -d --build
```

生产环境由 GitHub Actions 构建并发布镜像，服务器只拉取镜像，不在 SSH 会话中构建依赖。每次推送自动使用 `standard`；手动运行 workflow 时可选择 `enhanced`。

#### 启用增强版分析能力

增强版镜像包含 PyTorch、NLI、Docling 和 PaperQA2，但不会自动打开这些功能。发布增强版的步骤是：

1. 打开 GitHub 仓库的 **Actions**。
2. 选择 **Build and Deploy Research Radar**。
3. 点击 **Run workflow**。
4. 选择要发布的分支，并将 `profile` 设为 `enhanced`。
5. 点击 **Run workflow**，等待构建和部署完成。

增强版部署后，可在服务器的 `.env` 或 `data/settings.local.env` 中按需启用，修改后重启应用：

```env
NLI_ENABLED=true
PDF_PARSER_BACKEND=docling
PAPERQA2_ENABLED=true
```

```bash
docker compose restart research-radar
```

只想使用稳定轻量版本时，保持默认的 `standard` 即可。增强版只改变镜像内可用的依赖，具体功能仍由上述开关控制。

---

## 公网部署与账号管理

公网部署建议关闭公开注册，改由管理员创建账号：

1. 首次部署时暂时保持 `ALLOW_REGISTRATION=true`，注册第一个账号；第一个账号会自动成为管理员。
2. 登录后，将服务器上的 `data/settings.local.env` 改为：

   ```env
   ALLOW_REGISTRATION=false
   ```

3. 重启应用：

   ```bash
   docker compose restart research-radar
   ```

4. 管理员进入“用户与权限”页面，使用“创建用户”给团队成员开通账号。

公开注册关闭后，未登录用户不能自行注册；登录和注册接口也会限制短时间内的重复尝试。

- 桌面端：`https://<服务器IP>`（自签名证书，需手动信任）或 `http://<服务器IP>`
- 移动端 / 局域网：`http://<服务器IP>:8080`（Caddy 提供 HTTP 回退端口，避免 iOS 无法继续自签名 HTTPS）

---

## 测试与构建

```bash
PYTHONPATH=. pytest -q
cd app && npm run build
```

---

## 文档

- [API.md](./API.md) — REST API 与通知 / Webhook 配置
- [docs/MCP.md](./docs/MCP.md) — MCP 工具清单
- [docs/WORKFLOW.md](./docs/WORKFLOW.md) — 产品工作流
- [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md) — 部署与公网运维
- [docs/HERO_DESIGN.md](./docs/HERO_DESIGN.md) — 首页 Hero 设计探索
- [docs/NAV_IA_DESIGN.md](./docs/NAV_IA_DESIGN.md) — 导航信息架构探索
- [docs/REGRESSION_CHECKLIST.md](./docs/REGRESSION_CHECKLIST.md) — 发布回归清单
- [extension/](./extension) — Chrome 扩展
- [zotero-plugin/](./zotero-plugin) — Zotero 插件骨架

---

## 仓库说明

- `main`：当前生产版本，包含模块化后端、学术声誉 / 竞品画像、引文核验、标准 / 增强镜像部署等能力。
- `feat/citation-reference-check`：历史特性分支，已合并进 `main`。
- `experimental` / 其他分支：按需要保留的先行版本或部署分支。
