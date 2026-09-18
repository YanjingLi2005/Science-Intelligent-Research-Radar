# Research Radar — 全代码审查报告（2026-07-31）

> 审查范围：`radar/`（FastAPI 后端，~15K 行）、`app/src/`（React 19 前端，~6K 行）、`tests/`（31 个文件，~9K 行）、基础设施与文档。
> 方法：6 个并行审查代理逐文件阅读 + 前后端契约交叉验证 + 全量测试运行（252/252 通过，9.3s）。
> 结论：架构设计扎实、信任不变量（verbatim 引用 / G0/G1/G2 门）在扫描路径上执行得很好，但有 **8 个 CRITICAL** 和 **~50 个 MAJOR** 问题，其中若干个会静默破坏产品核心承诺。

---

## 一、CRITICAL（8 个，建议优先全部修复）

### C1. `edit_candidate` 绕过逐字引用校验，直接确认 claim（G0 门失效）
`radar/services/claim_service.py:898-923`
- `confirm_candidate`/`_set_state` 会检查 `source_quote in manuscript.content_text`（不通过则 `ValueError("span_failed")`），但 `edit_candidate` 直接复制旧 quote 并写入 `review_state="confirmed"`，**不做任何 span 校验**。
- 前端 Paper 页的"编辑"按钮（`app/src/pages/Paper.tsx:382-387`）恰好调用这条路：对一个待确认 claim 点编辑 → 新 revision 直接变成"已确认"，且不清空契约、证据链断裂、`stable_key` 被清空。
- 后果：产品核心不变量"无逐字引用，无确认"被绕过。已确认 claim 可以带一条不在手稿中的 quote，进入扫描证据与 patch 数字锁定。
- 修复：`edit_candidate` 复用与 `_set_state` 相同的 span 检查；前端去掉编辑按钮或做成真正的编辑 UI（必须回传完整契约）。三个审查代理（领域、前端、测试）独立发现了此问题。

### C2. 快照 `content_hash` 被全文哈希覆写，增量缓存永久失效 + 数据库无限膨胀
`radar/services/weekly_radar_service.py:1230-1251, 1484-1495`
- `_store_records` 用**摘要哈希**建快照，`_store_full_text` 随后把同一快照的 `content_hash` 覆写为**全文哈希**。
- 第二次扫描时摘要哈希查不到 → 新建快照 → 再次全文提取 → P3 缓存（按 `(claim_revision_id, source_snapshot_id)` 配对）**永远 miss**。
- 后果：每周扫描把所有全文论文重新跑 LLM 评估（重复计费）、重复创建 ImpactCandidate、`source_snapshots` 和 FTS 索引以每论文 ~800KB/周的速度无限增长。
- 修复：`content_hash` 保持为摘要哈希（不可变身份），全文哈希放独立列（如 `full_text_hash`）。

### C3. 数据库从不自动初始化 — 全新部署即坏
`radar/api.py`（无 lifespan 钩子）+ `Dockerfile`（CMD 裸 `uvicorn radar.api:app`）
- `init_database()` 只在 `case_service.load_demo_case` 和测试中调用；应用启动路径没有任何调用。
- 后果：按 README 快速上手（全新 clone → `docker compose up`）→ 打开应用 → 第一次写操作 `sqlite3.OperationalError: no such table: research_cases` → 500。
- 修复：在 `api.py` 加 FastAPI lifespan 处理器调用 `init_database()`（幂等）。

### C4. 模糊携带保留 `confirmed` 状态，正文变更后旧结论"自动确认"
`radar/services/claim_service.py:547-585, 150-204`
- `sync_manuscript_version` 在相似度 ≥0.85 时携带旧 revision，并**原样保留 `review_state="confirmed"`**，即使匹配是模糊的（相似度 <1.0）。实测 "92.1% accuracy" vs "91.2%" 相似度 0.952 → 被携带。
- 后果：手稿 v2 改了一个数字，已确认 claim 带着旧陈述（92.1%）和新 span（91.2%）继续处于 confirmed，陈述与证据自相矛盾，且不经过任何人重新确认。
- 修复：相似度 <1.0 的携带必须降级为 `candidate` 并重新做语义验证；只有精确匹配可以保留 `confirmed`。

### C5. OpenAI 提供商的严格 JSON Schema 不兼容，整条 pipeline 在 OpenAI 下不可用
`radar/llm/provider.py:157-163`
- 对非 DeepSeek 提供商发送 `{"type": "json_schema", "strict": true}` + pydantic 原始 `model_json_schema()`。OpenAI strict 模式要求所有属性进 `required` 且 `additionalProperties: false`，而 5/6 个响应模型都有可选属性。
- 后果：`LLM_PROVIDER=openai` 时 `impact_assessment`、`action_advice`、`retrieval_rerank`、`search_query_generation` 全部以 400 失败；只有 hyde 能跑，看起来像间歇性故障。
- 修复：OpenAI 分支发送前递归处理 schema（补 `additionalProperties: false`、required 全属性）。

### C6. PaperQA2 矛盾检测从未把论文喂给 PaperQA2 — 特性静默死亡
`radar/adapters/paperqa2.py:167-182, 248-277`
- `search_contradictions`/`search_supporting` 接受 `paper_paths`、`assess_claim` 接受 `manuscript_text`，但**这些参数从未被使用** —— 查询前没有构建 `Docs`，PaperQA2 在一个空库上回答问题；且 `_run_query` 用 `except Exception: return None` 吞掉一切错误。
- 后果：`verdict: "unavailable"` 永远出现；或 PaperQA2 在空库上幻觉出一个答案 → 给 `uncertainty_sources` 追加无依据的矛盾信号。
- 修复：查询前用传入论文全文（+ 可选手稿）构建 `Docs`；区分"未安装/未配置"与"运行了但没找到"。

### C7. 扫描失败路径把真实统计清零；完成后的步骤异常把 completed 翻成 failed
`radar/services/weekly_radar_service.py:1051-1074, 2158-2177`（两个审查代理独立发现）
- `except Exception` 分支调用 `_finish_scan(status="failed", stats={"scanned_papers": 0, "impact_candidates": 0})`，而 `_finish_scan` 用 `{**existing, **new}` 合并 → **真实计数被 0 覆盖**。
- `_action_advice` + `sync_scan_actions` 在 `_finish_scan("completed")` 之后仍处于同一 try 内；若它们抛异常（如 DB 错误），扫描被翻回 failed，而 12 个已验证 impact 已在库中。
- 修复：失败分支保留现有统计（只补缺的键）；advice/action 步骤单独 try/except，失败记为 warning 而非翻转扫描状态。

### C8. `superseded` 状态映射错误，拆分 claim 后项目接口永久 500
`radar/api.py:1114` + `radar/api_schemas.py:80`
- `_REVIEW_STATE_CLAIM_STATUS` 把 `"superseded" → "history"`，但 `ClaimOut.status` 的 Literal 只允许 `"valid"|"supported"|"disputed"|"revalidate"`。`split_candidate`（claim_service.py:925-958）会把父 revision 标记为 superseded。
- 后果：任何 claim 被拆分后，`GET /api/cases/{id}` 与 `GET /api/cases/{id}/claims` 永久 500（Pydantic 校验失败），无法恢复。
- 修复：把 `"history"` 加入 status Literal（或映射到 `"revalidate"`），并加拆分后取项目的回归测试。

---

## 一·补、CRITICAL 修复记录（2026-07-31 当日完成，284/284 测试通过）

| # | 修复内容 | 位置 |
|---|---------|------|
| C1 | `edit_candidate` 增加与 `_set_state` 相同的逐字引用校验（span_failed → 400）；编辑待确认 claim 不再自动确认（保持 candidate）；前端移除误导性"编辑"按钮 | `claim_service.py:898`、`api.py:290`、`Paper.tsx` |
| C2 | `SourceSnapshot` 新增 `full_text_hash` 列；`content_hash` 保持摘要身份不可变；迁移 v8 修复存量被覆写的行（含跨扫描缓存回归测试） | `weekly_radar_service.py:1230,1492`、`models.py`、`db.py` |
| C3 | FastAPI lifespan 启动时调用 `init_database()`；全新部署不再 "no such table" | `api.py` |
| C4 | 相似度 <1.0 的携带降级为 `candidate`（需重新 G0 确认）；仅精确匹配保留 `confirmed` | `claim_service.py:562` |
| C5 | `_strict_json_schema` 转换：所有对象补 `additionalProperties: false`、required 全覆盖、剥离 `type: null` 与 `default`；16 个响应模型参数化合规测试 | `llm/provider.py` |
| C6 | PaperQA2 用传入的论文文本构建 `Docs` 喂给 `agent_query`（不再空库查询）；`_run_query` 区分"不可用"与"真实阴性"；裁决层增加显式否定检测 + 分数阈值（"支持该 claim"不再误判为 challenged） | `adapters/paperqa2.py` |
| C7 | 失败分支保留真实 `scanned_papers`/`impact_candidates` 计数；advice/action 步骤单独 try/except，失败记 warning 审计而不翻转 completed 状态 | `weekly_radar_service.py:1052-1096` |
| C8 | `ClaimOut.status` Literal 增加 `"history"`；前端 `ClaimStatus` 类型 + `claimStatusMeta`（历史 badge）；history 行隐藏确认/拒绝按钮 | `api_schemas.py:80`、`mock.ts`、`Paper.tsx` |

顺带修复（排查中发现）：
- **litellm 导入时 `load_dotenv()` 把项目 `.env` 合并进 os.environ** —— 这是 llm_factory/local_settings/hyde 等测试在全量运行中失败的根本原因（`test_paperqa2` 引入 paper-qa 导入后触发）。`tests/conftest.py` 模块级设置 `LITELLM_MODE=PROD` 关掉该行为，全量测试恢复 284/284。
- `edit_claim` 路由捕获 `ValueError`（span_failed）→ 400。
- C4 相关的 `test_reworded_claim_is_carried_by_fuzzy_match` 断言更新为新语义。

新增回归测试：C1 ×3、C2 ×2（含迁移修复）、C3 ×1、C4 ×1、C5 ×17（参数化）、C6 ×6、C7 ×2、C8 ×1，共 ~33 个。

---

## 一·补·二、MAJOR 修复记录（2026-07-31 当日完成，305/305 测试通过）

| # | 修复内容 | 位置 |
|---|---------|------|
| M2 | pipeline 两条持久化路径（LLM + 启发式回退）都经 `resolve_exact_quote` 锚点校验：伪造引用丢弃并记入 `dropped_anchors`；locator 改为 `offset:{start}-{end}` | `claim_pipeline.py` |
| M3 | `incremental_extract`：只携带每 claim 最新 revision（按 claim_id 去重）；跳过 superseded/rejected；变更区内仍逐字存在的已确认 claim 以 `candidate` 重门控携带（不再孤儿化）；携带前标记前驱 superseded；重新提取时用 held_ranges 防止为已携带文本铸造新 claim | `claim_pipeline.py` |
| M4 | `diff_sections` 全文本 diff（去掉 5000 字符截断）；改名检测（相似度 ≥0.5 的删除+新增标题对视为改名，按当前标题报告并附 `previous_heading`） | `claim_diff.py` |
| M7 | `impact_confirmed` 实时重读 impact 行（驳回后失效）；新增第 7 项校验 `after_text_differs`；审批门要求**精确的 7 项键集** + 全部为真（`all({})` 漏洞关闭） | `patch_service.py` |
| M10 | 确认/拒绝（主按钮路径）补 G0Feedback；编辑/拆分补 G0Feedback（edit_fields/before/after 值）+ AuditEvent（`claim_edited`/`claim_split`）；`delete_case`/`reset_demo_case` 级联删除 G0Feedback | `claim_service.py`、`case_service.py` |
| M12 | `recover_interrupted_scans` 接入 FastAPI lifespan（启动时恢复崩溃扫描，显式传 `session_factory` 保证测试可注入） | `api.py` |
| M13 | 跨源去重改为标题单一命名空间（`_merge_deduped`）：同标题 DOI 记录优先；同 DOI 不同标题折叠；citation_hits 按池子净增计数 | `weekly_radar_service.py` |
| M21 | Crossref 撤回检测按 `update-to`/`updated-by` 条目的 `type` 分类（retraction/withdrawal → retracted；expression-of-concern → concern；correction/corrigendum/erratum/addendum → corrected），不再子串匹配序列化 JSON | `adapters/crossref.py` |
| M22 | 引用保真：NFKC 逐字符归一化（连字 ﬁ→fi，偏移安全）；软连字符 U+00AD 无条件删除；连字符+空白断词连接；LaTeX 内联命令剥离（`\emph{}` 保留内容、`\cite{}` 丢弃）；`resolve_exact_quote` 增加无连字符匹配兜底（断行连字符 vs 真实连字符歧义） | `evidence_service.py` |
| M23 | CJK 脚本感知的 token 预算：`_cjk_ratio` + 按脚本混合的 chars-per-token（CJK 1.8 / ASCII 4.0），中文重文本自动收缩字符上限，不再静默超出 Ollama 32K 窗口 | `llm/text_utils.py` |
| M39 | `CompetitorEntry` 增加 `id` 字段并在 GET 列表返回；前端 `CompetitorItem.id` 必填、删除用 id（移除 team 名兜底）；mock 数据同步 | `api_schemas.py`、`api.py`、`Paper.tsx` |
| M26 | 技能执行收集全部 evidence 的 paperId（`kind === 'impact'` 永不匹配的恒空列表修复，去掉 `as string` 强转） | `Improve.tsx` |

新增回归测试：M2 ×2（新文件 test_claim_pipeline.py 亦补 M3 ×2）、M7 ×2、M10 ×2、M3/M4 ×4（新文件 test_claim_diff.py）、M12 ×1、M13 ×1、M21 ×1、M22 ×3、M23 ×2、M39 ×1 —— 全套 305/305。

---

## 一·补·三、MAJOR 修复记录·第三批（2026-07-31 当日完成，317/317 测试通过）

| # | 修复内容 | 位置 |
|---|---------|------|
| M5 | 原子分解使用真实手稿上下文（按 section_id / offset 窗口取段落，不再 `quote*2`）；原子引用经 `resolve_exact_quote` 校验，伪造引用丢弃；记录 ModelRun | `claim_service.py` |
| M6 | 语义验证在 LLM 不可用/失败时返回 `faithful: None`（显式"未验证"）而非 `True`；`claim_stable_key` 取真实值 | `claim_service.py` |
| M9 | `enforce_stance` 只对 confirm/edit 生效；覆写时在 ReviewDecision/AuditEvent payload 记录 `condition_blocked`（requested/enforced/comparability），不再静默；dismiss 不再动 stance | `review_service.py` |
| M1 | `track_changes` 双版本各取最新 revision 比较（不再撞旧 superseded 行） | `claim_pipeline.py` |
| M14 | 扫描发现的撤回论文生成 `paper_integrity` 告警（挂到 case 已确认 claim，上限 3，幂等），`integrity_alerts` 入扫描统计；配对循环跳过 retracted/concern 来源（不再对撤回论文跑 LLM 评估） | `impact_service.py`、`weekly_radar_service.py` |
| M18 | `_start_scan` 保留 `cancel_requested`（start 与 worker 认领之间的取消被兑现为 cancelled，不再静默丢弃） | `weekly_radar_service.py` |
| M19 | P3 配对缓存包含 dismissed 影响（人类已决定，不再每周重复评估/重复创建候选） | `weekly_radar_service.py` |
| M15 | 嵌入配置不完整时服务构造降级为 None（`embedding_misconfigured` 诊断进扫描统计），不再让每次扫描在开始前失败 | `weekly_radar_service.py` |
| M34 | `PUT /api/settings` 持久化前用 `Settings(**probe)` 校验所有值，坏值 → 400（不再毒化 lru_cache 使全应用 500） | `api.py` |
| M29 | 无 LLM 时 patch 生成 / manuscript-understanding 明确抛 `llm_not_configured`（路由 → 400），不再 `AttributeError` 500 | `patch_service.py`、`manuscript_understanding_service.py` |
| M41 | `switchProject`/`refreshProject` 请求令牌：最后发出的请求胜出，快速切换不再出现项目与 ID 错配 | `ProjectContext.tsx` |
| M42 | `loading`（页面门控）与 `refreshing`（静默）拆分；后台刷新失败不再被误报为操作失败、不再翻页到错误视图；`refreshCaseList` 失败不再清空项目列表 | `ProjectContext.tsx` |
| M44 | Radar 页硬编码 `{v: 47}` 改为真实 `papers.length` | `Radar.tsx` |

## CCFA-Skills 集成验证（用户源仓库 mikubaka88/CCFA-Skills）

**结论：功能已成功搭建且完整可用，本日全部修复未破坏该层**（`git diff` 未触碰 `radar/skills/*`、`radar/llm/prompts/*`、`registry.py`、`action_service.py`）。

- **链路全通**：`action_service.ACTION_SKILL_MAP`（7 种 action → 5 技能）→ `api.py:1269 suggestedSkill` → 前端 Actions 按钮 → `POST /skills/execute` → `get_registry()` 注册 5 技能 → `_llm_skill` → 结构化输出 → 前端 diff 展示。5 个技能名与注册名、5 个 prompt 文件（`paper_writer`/`paper_reviewer`/`integrity_auditor`/`experiment_designer`/`rebuttal_writer`）全部对齐。新增 `tests/test_skills.py` 4 项端到端验证。
- **补齐的集成缺口**（验证中发现，本次修复）：
  1. `record_skill_run` 从未被调用 → 技能执行零审计。现在 `_llm_skill` 内部持久化 ModelRun + `skill_*_executed` AuditEvent（与 pipeline 其余部分同一审计标准）
  2. Actions.tsx 技能执行同样传空 impact 列表（与 Improve.tsx 同类）→ 改为传全部 evidence paperId
  3. `project_state.claims[].stable_key` 硬编码 `""` → 改为真实 stable key（AuditorSkill 的 ClaimSupport 依赖）
- **已知残余**（未在本日范围）：`route()`/`can_handle` 服务端路由仍未被 API 使用（客户端直传 skill_name）；`execute_skill` 每次调用重复注册 5 技能（无害覆盖）；writer 手稿截断 20K 无省略标记；`_skill_prompt` 缺 prompt 文件时静默回退。

---

## 一·补·四、MAJOR 修复记录·第四批（2026-07-31 当日完成，319/319 测试通过）

| # | 修复内容 | 位置 |
|---|---------|------|
| 技能层 | `execute_skill` 服务端路由：空 `skill_name` 由 `registry.route()` 按 action_type 解析；显式名称需通过 `can_handle`（未知技能 404、动作不符 400）；5 技能注册移入 `get_registry()`（进程级单次，移除每请求重复注册） | `api.py`、`registry.py` |
| 技能层 | writer 手稿 20K / api 项目状态 50K 截断改用 `truncate_for_prompt`（带省略标记）；`_skill_prompt` 缺失 prompt 文件时记录 warning（不再静默回退裸 JSON） | `writer.py`、`api.py`、`base.py` |
| M16 | `rank_sources` 列投影（只取 id/title/abstract/content_text）；`route_claims` 接受预加载 `snapshot_text` 三元组 —— `_rank_pairs` 每扫描只加载/分词一次，不再每配对重复拉取全文 | `retrieval_service.py`、`weekly_radar_service.py` |
| M20 | 手稿 DocumentMap 每扫描构建一次，`_assess_pair` 共享（不再每配对全稿重解析） | `weekly_radar_service.py` |
| M31 | `_build_audit` 从 `validation_json`/`raw_response` 推导 verdict：错误 → fail、丢弃锚点/降级 → warn、其余 pass（不再硬编码 pass） | `api.py` |
| M36 | Form JSON 字段（edit_fields/before_values/after_values/impact_ids）经 `_parse_json_field` 防御性解析，畸形输入 → 400 | `api.py` |
| M38 | `delete_case` 冲突改专用 `ScanActiveError` 异常类型（不再字符串匹配） | `case_service.py`、`api.py` |
| M40 | "最小改写方案"死流程打通：生成最小改写按钮（取首个已采纳影响 → `generatePatch`）+ 导出补丁 Diff 按钮（`exportPatchDiff` 下载） | `Improve.tsx` |
| M43 | Actions 页渲染 actions/papers/claims 初始加载错误横幅（不再把后端挂掉显示为"暂无扫描结果"） | `Actions.tsx` |
| M46 | `kimi-plugin-inspect-react` 仅 dev 模式应用（生产构建不再加载仪器插件）；vite 构建验证通过 | `vite.config.ts` |

---

## 一·补·五、MAJOR 修复记录·第五批（2026-07-31 当日完成，321/321 测试通过）

| # | 修复内容 | 位置 |
|---|---------|------|
| M17 | `competitor_flag` 逐作者 + 词边界匹配（`(?<!\w)alias(?!\w)`，允许连字符边界）：`han` 不再命中 "Khan"、`li` 不再命中 "Alibaba"、`kim` 不再命中 "Kimberly"；整串相等优先 | `retrieval_service.py` |
| M28 | patch 生成客户端改用 `llm_manuscript_timeout_seconds`（240s 默认）而非通用 `llm_timeout_seconds`（120s）—— 长手稿 patch 调用不再在 120s 超时 | `patch_service.py` |

---

## 一·补·六、Chroma 向量检索接线（M32/M33，2026-07-31，327/327 测试通过）

按用户决策接线 ChromaDB（原"混合检索"的向量腿是死代码：`build_vector_store` 零调用、`_vector_scores` 零调用、MemoryVectorStore 恒返回空、Chroma `add` 会重复 id 崩溃）：

| 修复 | 内容 |
|------|------|
| MemoryVectorStore | `add()` 接收并存储 embeddings（不再静默返回空）；无 embeddings 的 add 不破坏已有数据 |
| ChromaVectorStore | `add` 改 `upsert`（快照 id 跨扫描稳定，重复索引替换而非崩溃）；embeddings 显式传入（不再走 Chroma 默认 ONNX 下载）；metadatas 拷贝后写 `_content_hash`（不再改调用方数据） |
| 索引链路 | `index_snapshots` 用配置的 embedding client 计算向量并传入 store；失败降级记录（绝不 fail 扫描）；文档用 `content_text[:8000]`（富集后含全文，不再偏好 abstract） |
| 排序链路 | `rank_sources` 向量腿优先查询持久 store（`_vector_scores` 终于被调用）；store 缺失/失败时回退实时重嵌入 |
| 扫描接线 | `WeeklyRadarService` 构造时按 `vector_store_enabled`/`vector_store_dir` 构建 store（Chroma 优先，内存兜底，失败不阻断扫描）；`index_snapshots` 移到全文富集**之后**（索引覆盖完整论文） |
| 注入点 | `WeeklyRadarService` 新增 `embedding_client` 注入参数（与 search_adapter/llm_client 一致） |
| 验证 | 真实安装 chromadb 后 327/327 通过（含 Chroma upsert 幂等、metadatas 不被修改、索引含富集全文、排序查询 store 4 项测试） |

---

## 二、MAJOR（按主题归纳，~50 个）

### 信任门 / 领域正确性
- **M1** 模糊携带降级缺失（C4 的配套）；`track_changes` 比较第一个匹配 revision 而非最新（`claim_pipeline.py:612-635`）→ 误分类 modified、diff 错误。
- **M2** pipeline 提取（`claim_pipeline.py:321-336, 508-523`）直接持久化未经校验的 LLM quote + 空 6 字段契约（`contract_json={}`）→ G0 确认必然 `span_failed`，且条件对齐时回退到手稿级契约（用错来源）。
- **M3** `incremental_extract` 携带上一版本**所有** revision（含被 superseded/拒绝的）→ claim 重复、revision DAG 断裂；变更区内已确认 claim 被孤儿化后又被当作新 claim 重建（`claim_pipeline.py:443-527`）。
- **M4** `diff_sections` 只 diff 前 5000 字符（`claim_diff.py:94`）：长段落尾部的修改被当作 unchanged；改名的章节被当作 unchanged。修复：按 offset 范围 diff、改名按相似度映射。
- **M5** 原子分解伪造引用：prompt 把 `source_quote * 2` 当"手稿上下文"（`claim_service.py:699-733`），返回的 atom quote 不校验、不持久化。
- **M6** 语义验证在 LLM 不可用/失败时返回 `faithful: True`（`claim_service.py:752-793`）→ 未验证被当成已验证。
- **M7** README 宣称"7 项校验"实际只有 6 项，其中 2 项硬编码 `True`（`patch_service.py:360-380`）；审批门对空 validations 也通过（`all({})` 为 True，line 403）。
- **M8** 多位置 patch 不落库、不记 ModelRun/AuditEvent，异常被吞成成功空结果（`patch_service.py:417-518`）。
- **M9** `enforce_stance` 静默覆写用户的 stance 编辑（`review_service.py:51-55`）→ 用户确认的判断被悄悄降级，审计与存储不一致。
- **M10** 确认/拒绝/编辑/拆分未写 `G0Feedback`（主 UI 按钮路径）或 AuditEvent（编辑/拆分完全无审计）；`delete_case`/`reset_demo_case` 从不删除 `G0Feedback` 行（泄漏）。
- **M11** 前端：拒绝不持久（刷新后被拒绝 claim 恢复成待确认，Paper.tsx:303-394）；编辑按钮绕过 G0（C1）；补丁审批按钮纯本地状态（Actions.tsx:131-136）。

### 扫描 / 缓存 / 生命周期
- **M12** `recover_interrupted_scans` **从未被调用**（`scan_runner.py:149-179`）：进程被杀后 ScanRun 永远停在 running，该 case 从此无法扫描（`ScanAlreadyRunningError`）。修复：启动时 + 定时调用。
- **M13** 跨源去重：arXiv 无 DOI + OpenAlex 有 DOI 的同篇论文 → 两个 key → 重复评估两次（`weekly_radar_service.py:305-317`）。修复：标题优先 + DOI 覆盖。
- **M14** 撤回传播只对 `ClaimSourceLink` 已确认的论文生效；扫描新发现的撤回论文只置 `integrity_state`，**无任何用户可见告警**（`impact_service.py:49-118`）。
- **M15** 嵌入配置不完整时 `WeeklyRadarService.__init__` 直接抛错 → **每次扫描在开始前就失败**（`weekly_radar_service.py:190-194`），与全项目"降级而非崩溃"的设计不一致。
- **M16** `rank_sources`/`route_claims` 全列加载 `content_text`（最多 ~40MB）+ 每快照重新分词（`retrieval_service.py:246-386`）；FTS 每次扫描全表重建（O(corpus)，line 205-224），且重建发生在排序**之后**。
- **M17** `competitor_flag` 子串匹配：别名 `han` 匹配 "Khan"、`li` 匹配 "Alibaba" → 假竞争对手告警（`retrieval_service.py:388-399`）。修复：按 token 边界 / 作者整串匹配。
- **M18** `start_scan` 无条件把 `cancel_requested` 重置为 `running`，取消窗口期丢请求（`weekly_radar_service.py:1166-1173`）。
- **M19** 被 dismiss 的配对每周重复评估（缓存只排除 dismissed 的"证据"而非"配对"，`weekly_radar_service.py:949-958`）。
- **M20** `_claim_section_context` 每个配对全稿重解析（`weekly_radar_service.py:255-256, 1685`）；`latest_profile` 不绑定当前手稿版本（`manuscript_understanding_service.py:256-278`）。

### LLM / 适配器 / 引用保真
- **M21** Crossref 撤回检测是子串匹配（`crossref.py:30-45`）：`"correct" in text` 误报/漏报；`withdrawal`、`erratum`、`corrigendum` 类型全部漏检。修复：按 `entry["type"]` 分类。
- **M22** 引用保真：无 NFKC 规范化（连字 `ﬁ` 永不相配）、连字符连接只处理 `-\n` 不处理 `- ` 和软连字符（`evidence_service.py:30-68`）；LaTeX 命令（`\emph{...}`）不剥离（`latex.py:16`）→ 引用验证静默失败，论文被 block。修复：统一在 `_normalized_text_with_offsets` 处理。
- **M23** `CHARS_PER_TOKEN = 3.5` 对中文方向性错误（中文 ~1.5-2 字符/token）：中文手稿 token 预算被低估 2 倍，Ollama 32K 窗口（`ollama.py:52`）溢出被静默截断（`text_utils.py:8-32`）。
- **M24** FallbackSearchAdapter 捕获一切异常（`fallback.py:14-23`），包括编程错误 → fixture 数据冒充真实搜索结果流入 pipeline（当前未接线，是潜伏陷阱）。
- **M25** 技能路由是死的：`route()`/`can_handle` 零调用，API 直接执行客户端传来的 `skill_name`（`registry.py:28-32`）；`record_skill_run` 从不被调用 → 技能执行零审计（`_llm_skill` 计算出的 token/延迟/模型全被丢弃）。
- **M26** 技能执行时前端永远传空 impact 列表：`kind === 'impact'` 永不匹配（evidence kind 是 support/challenge/completeness）（`Improve.tsx:120-122`）。
- **M27** OpenAI 的 `thinking`/`reasoning_effort` 参数无条件发送；通用兼容代理拒绝未知字段即 4xx 不可重试（`provider.py:130-152`）。
- **M28** 手稿在 20,000/50,000 字符硬截断且无标记（`writer.py:31`、`api.py:716`）；`llm_manuscript_timeout_seconds` 从不用于 patch 生成（240s 设置白设，`patch_service.py:209-213`）。

### 后端基础 / API
- **M29** patch/manuscript-understanding 服务在未配置 LLM 时无条件 `ProviderLLMClient(settings)` → 500（`patch_service.py:68-72`、`manuscript_understanding_service.py:52-62`），违反全项目 None 门控设计。
- **M30** `POST /api/cases` 不捕获 `ValueError` → 扫描 PDF 得到 500 而非 400 提示（`api.py:191-198`）；上传无大小限制、整文件读入内存。
- **M31** `_build_audit` 对所有 ModelRun 硬编码 `result="pass"`、从不读 `validation_json`、从不导出 AuditEvent 行（`api.py:1294`）。
- **M32** `MemoryVectorStore.add` 从不存 embeddings → `query()` 恒返回 `[]`（`vector_store.py:31-48`）；Chroma 走默认 ONNX 嵌入函数 + 重复 id 崩溃（`collection.add` 而非 `upsert`，line 93-100）—— 目前两者都是死代码（`build_vector_store` 零调用）。
- **M33** "混合检索"名不副实：`_vector_scores` 从未被调用（`retrieval_service.py:159-176`）、FTS 只在全表扫描后回退 —— 实际运行的只有 词法 + BM25 + 候选池实时嵌入。要么接线 Chroma，要么删除死代码并改文档。
- **M34** Settings 写入无校验（`api.py:880-893`）：`LLM_MAX_TOKENS: abc` 会毒化整个进程，所有接口（含 GET /settings）500，需手改文件恢复。
- **M35** 配置优先级 env > .env > settings.local.env（`config.py:50-56`）：Docker 环境变量会静默覆盖 UI 里保存的设置。
- **M36** `edit_claim`/`execute_skill`/`track_changes` 的 `json.loads` 未捕获 → 畸形输入 500；`execute_skill` 每次调用重复注册 5 个技能（`api.py:648-738`）。
- **M37** `export_patch_diff` 声称 unified diff 实为 200 字符截断的 Markdown 摘要（`patch_service.py:521-531`），无法当 patch 用。
- **M38** `delete_case` 用异常消息字符串匹配冲突（`str(exc) == "case_scan_active"`，`api.py:215-218`）。

### 前端
- **M39** 移除竞争对手 100% 失败：GET 响应无 `id`（`api_schemas.py:48-51`），team 名被当 watch_id 发给后端 → 404（`Paper.tsx:158-167`）。
- **M40** `generatePatch`/`exportPatchDiff` 零调用 → "最小改写方案"审批流程是死胡同（`Improve.tsx:288-339`）；`splitClaim`/`decomposeClaim`/`verifyClaimSemantics`/`editImpact` 同样未接线。
- **M41** `switchProject` 无排序守卫：快速切换两个项目时，先发的请求后返回 → 头部显示 B 项目、页面渲染 A 项目（`ProjectContext.tsx:56-72`）。修复：递增 token 丢弃过期响应。
- **M42** `refreshProject` 置 `loading=true` → 每次确认操作整页闪"加载中"；刷新失败被误报为操作失败（`ProjectContext.tsx:80-94`）。修复：拆 `initialLoading` 与静默 `refreshing`。
- **M43** Actions 页吞掉初始加载错误：后端挂了显示"暂无扫描结果"而非报错（`Actions.tsx:36-49`）；`refreshCaseList` 出错清空整个项目列表（`ProjectContext.tsx:96-107`）。
- **M44** Radar 页硬编码统计 `{v: 47}` 冒充实时数据（`Radar.tsx:144`）；扫描完成后的刷新失败把 completed 显示成 failed（`Radar.tsx:58-87`）。
- **M45** `mock.ts`（806 行）是死代码，仅作类型来源；`Claim.confirmed` 含 `'history'`（后端从不产生）——类型所有权掩盖了拒绝不持久 bug。迁移类型到 `src/types/` 或从 OpenAPI 生成。
- **M46** `kimi-plugin-inspect-react` 无条件加载进生产构建（`vite.config.ts:8`）；BrowserRouter 无路由使用；`use-mobile` 无导入。

### 测试 / 基础设施 / 文档
- **M47** `edit_candidate`（C1）**零测试覆盖**；`claim_pipeline`/`claim_diff` 全部核心函数（run_pipeline、incremental_extract、track_changes、diff_claims、diff_sections）**零直接测试** —— 最近两个提交的旗舰功能恰是最没测的代码。
- **M48** `test_api.py:165` 用 `pytest.skip("no claim candidates")` 逃避断言 → 提取回归会让测试静默通过；`test_list_claims` 全部断言都在 `if claims:` 内。
- **M49** 离线纪律依赖每个测试手动注入 `llm_client=`/`settings=`；autouse stub 没盖住 `build_analysis_llm`/`configured_embedding_client` → 任何用默认构造服务的测试会打真实付费 API（当前只是运气好全注入了）。
- **M50** 真实 PDF 解析测试 `skipif` 在 `.gitignore` 的路径上（`data/papers/`）→ 全新 clone/CI 上 pymupdf4llm 提取路径零覆盖。修复：`tests/fixtures/` 提交一个样例 PDF。
- **M51** `POST /api/cases/{id}/scans` 无 API 级测试；且 `scan_runner` 在 import 时绑定 `SessionLocal`，naive 测试会打真实 DB + 真实 arXiv/OpenAlex + 真实 key。
- **M52** 无健康检查：无 `/api/health` 路由、Dockerfile 无 HEALTHCHECK、compose 无 healthcheck；`docker-compose.yml` 不传 env 且 `.dockerignore` 排除 `.env` → 容器跑默认配置；`data/settings.local.env` 里的占位 key `LLM_API_KEY=22` 是 truthy → 容器会对 DeepSeek 发真实请求然后 401，而不是干净地报"未配置"。
- **M53** `API.md` 与真实 42 条路由严重漂移（大量死文档端点 + 缺失新端点）；README 测试数 245 实际 252；`app/info.md` 是无关脚手架；`research-radar-web/` 是重复的废弃前端目录。
- **M54** `.env` 含真实 DeepSeek key（`sk-290a...`）在演示目录里 —— 建议轮换；`.gitignore` 缺 `.env.*` 变体。
- **M55** `db.py:212` `_migrate_g0_feedback_table` 引用未导入的 `G0Feedback`（潜伏 NameError）；`scan_runner.py:254-260` 每次取消轮询泄漏一个 session。

---

## 三、做得好的地方（后续开发应保持）

- **信任链在扫描路径上真实执行**：证据提取 → `resolve_exact`（拒绝歧义双匹配）→ TrustService → enforce_stance；无证据无 impact、无条件对齐无方向性判断，全部在持久化**之前**。
- **手稿永不被自动修改**：没有 apply 端点；patch 审批只翻转 `approval_state`。
- **失败隔离与降级链优秀**：arXiv 3s 礼貌间隔 + 锁、PDF 槽位保留、429 指数退避、按来源计数失败、LLM 各阶段降级为确定性回退且全程记 ModelRun。
- **测试质量整体高**：252/252 通过、DB 隔离干净（per-test tmp SQLite）、协议双替身而非 mock 镜像、信任不变量有真实验证（golden case 10 条 verbatim quote、G2 需全部校验通过 + sha256 不可变、G1 回滚原子性）。
- **session_scope 事务模式、扫描状态机（预建行 + 协作取消 + 心跳恢复）、uuid5 确定性 action id、提示词明确禁止编造引用/数字/引用标记** —— 都是正确的工程决策。
- **CORS 收紧、密钥永不回显（repr=False、mask 语义正确）、成本估算集中在 config**。

---

## 四、建议的修复路线（按依赖排序）

1. **信任门闭环（第 1 周）**：C1（edit_candidate 校验 + 前端按钮）、C4（模糊携带降级）、M2（pipeline 引用校验）、M10（G0Feedback/审计补全）、M7/M8（patch 校验与持久化）—— 先把产品核心承诺修实。
2. **扫描生命周期（第 2 周）**：C2（快照身份不可变）、C7（失败统计与终态保护）、M12（接线 recover_interrupted_scans）、M13（跨源去重）、M3/M4（incremental 提取正确性）。
3. **LLM 集成正确性**：C5（OpenAI strict schema）、C6（PaperQA2 喂文档）、M23（CJK token 预算）、M22（NFKC/连字符/LaTeX 归一化）、M21（Crossref 按 type 分类）。
4. **API/前端契约**：C8（superseded Literal）、M39（competitor id）、M26（impactIds）、M40（接线 generatePatch 或删除死代码）、M41/M42/M43（加载与竞态）。
5. **启动与部署**：C3（init_database lifespan）、M34（settings 校验）、M35（配置优先级文档化）、M52（healthcheck + env 接线 + 占位 key 视为未配置）、M54（轮换 key）。
6. **测试加固**：M47-M51（补 pipeline/claim_diff/paperqa2/crossref 测试、修 skip 逃逸、autouse stub 补全、提交样例 PDF、scans 路由注入点）。

## 五、给 Claude Code 后续开发的注意事项

- 仓库**没有 CLAUDE.md** —— 强烈建议建立，记录：测试约定（`.venv/bin/python -m pytest`、离线 stub、`db_session_factory` 模式、禁止跨测试文件 import）、`data/` 路径相对 CWD 的陷阱、multipart vs JSON 路由分布、`_ALLOWED_SETTINGS_KEYS` 白名单、FTS 表在迁移系统之外。
- `weekly_radar_service.py`（2222 行，~8 个职责）建议拆为 `services/scan/` 包（缓存 / 候选管线 / 排序 / 配对评估 / LLM runner）。
- 改动 DB 状态枚举时，必须同步改 `api_schemas.py` 的 Literal（C8 是教训）。
- 新 Settings 字段要同时加进 `_ALLOWED_SETTINGS_KEYS` 和 `config.py` 价格表，否则 UI 不可编辑 / 账本计 ¥0。
- `stable_key` 格式（`C{n}` / `C{n}.1`）是承重的：加 `(case_id, stable_key)` 唯一约束并让 `_next_stable_key_number` 识别带点的 key。
- `scripts/verify_live_stack.py` 调用了私有方法 `_store_records`，改名会静默破坏验证脚本。
