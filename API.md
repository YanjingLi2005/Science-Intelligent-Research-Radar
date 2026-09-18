# Research Radar — React 前端 API 接口规格

> 基于 `radar/services/` 现有公开方法整理。所有 endpoint 无需 auth（单用户本地应用），
> 请求/响应均为 JSON（文件上传除外）。本章案不包含 FastAPI 实现代码。

---

## 通用约定

- **Base URL**: `/api`
- **Content-Type**: `application/json`（文件上传用 `multipart/form-data`）
- **错误响应**: `{"detail": str}`, HTTP 4xx/5xx
- **case_id** / **manuscript_version_id** / **claim_id** 等主键均为 UUID 字符串
- 所有列表接口默认按 `created_at` 倒序排列

---

## 1. 项目管理

### `POST /api/cases`
创建研究案例（含文稿上传）。

- **请求**: `multipart/form-data`
  - `title`: str — 项目标题
  - `research_question`: str — 研究问题
  - `manuscript`: file — PDF / .tex / .md 文稿
- **响应 201**: `{case_id: str, manuscript_id: str, claims: [...]}`
  - `claims` 为自动提取的候选 Claim 列表（ClaimRevision 对象）
- **错误**:
  - 400 — 文稿文本提取不足（扫描件 PDF 无文本层）
  - 415 — 不支持的文件类型

### `GET /api/cases`
列出所有研究案例。

- **参数**: `?include_synthetic_demo=true|false` （默认 true）
- **响应 200**: `[{id, title, research_question, field, created_at, updated_at}, ...]`

### `GET /api/cases/{case_id}`
获取单个案例详情。

- **响应 200**: `{id, title, research_question, field, settings_json, created_at, updated_at}`
- **错误**: 404

### `POST /api/cases/{case_id}/manuscript`
同步新版本文稿（保留历史版本，自动 carry-over 已确认 Claim）。

- **请求**: `multipart/form-data`
  - `manuscript`: file
- **响应 200**:
  ```json
  {
    "manuscript_id": str,
    "version_no": int,
    "unchanged": bool,
    "carried_claims": int,
    "new_candidates": int,
    "previous_claims_not_found": int,
    "lost_claims": [{"claim_id": str, "stable_key": str, "statement": str}]
  }
  ```

### `DELETE /api/cases/{case_id}`
删除案例（含所有关联数据）。*未实现，需要新增 service 方法。*

---

## 2. 文稿版本

### `GET /api/cases/{case_id}/manuscripts`
列出案例所有文稿版本。

- **响应 200**: `[{id, version_no, file_name, source_type, is_current, created_at}, ...]`

### `GET /api/manuscripts/{manuscript_version_id}`
获取文稿版本全文及解析结构。

- **响应 200**: `{id, case_id, version_no, file_name, content_text, content_hash, sections, paragraphs, sentences}`

---

## 3. Claim 管理

### `GET /api/cases/{case_id}/claims`
列出案例所有 Claim（含最新 revision）。

- **参数**: `?review_state=candidate|confirmed|rejected|superseded`
- **响应 200**: `[{claim_id, stable_key, lifecycle_state, revision: {...}}, ...]`

### `POST /api/claims/{revision_id}/confirm`
确认候选 Claim（G0 通过）。

- **响应 200**: ClaimRevision 对象
- **错误**: 404 / 422（span 验证失败）

### `POST /api/claims/{revision_id}/reject`
拒绝候选 Claim。

- **响应 200**: ClaimRevision 对象

### `PUT /api/claims/{revision_id}`
编辑 Claim（创建新 revision，旧 revision → superseded）。

- **请求**:
  ```json
  {
    "statement": str,
    "centrality": "core" | "major" | "minor",
    "contract": {"task": str|null, "dataset": str|null, "split": str|null, "metric": str|null, "comparator": str|null, "scope": str|null},
    "falsifiable_condition": str
  }
  ```
- **响应 200**: 新 ClaimRevision 对象

### `POST /api/claims/{revision_id}/split`
拆分 Claim 为多个子 Claim。

- **请求**: `{"statements": [str, str, ...]}`
- **响应 200**: `[ClaimRevision, ...]`
- **错误**: 400 — 少于 2 个 statement

---

## 4. 扫描

### `POST /api/cases/{case_id}/scan`
启动一次文献雷达扫描。

- **请求**:
  ```json
  {
    "max_results": int,   // 默认 32
    "analysis_limit": int // 默认 3
  }
  ```
- **响应 202**: `{scan_id: str, status: "running"}`
- **错误**: 409 — 该案例已有活跃扫描
- **注意**: 返回的 `scan_id` 用于后续轮询

### `GET /api/scans/{scan_id}`
轮询扫描状态与进度。

- **响应 200**:
  ```json
  {
    "id": str,
    "case_id": str,
    "mode": str,
    "status": "running" | "completed" | "failed" | "cancelled" | "interrupted",
    "started_at": str,
    "finished_at": str | null,
    "stats": {
      "progress": {"value": 0.0-1.0, "message": str},
      "scanned_papers": int,
      "routed_pairs": int,
      "impact_candidates": int,
      "source_counts": {"arxiv": int, "openalex": int, "citation_graph": int},
      "hyde": bool,
      "source_failures": {"arxiv": str, ...},
      "newest_publication": str | null,
      "analysis_provider": str,
      "analysis_model": str
    },
    "error_message": str | null
  }
  ```

### `POST /api/scans/{scan_id}/cancel`
请求取消扫描（协作式，到达下一阶段边界时生效）。

- **响应 200**: `{"cancelled": true}`
- **响应 200**: `{"cancelled": false}` — 扫描已完成无法取消

### `GET /api/cases/{case_id}/scans`
列出案例所有扫描记录。

- **响应 200**: `[{id, mode, status, started_at, finished_at, ...}, ...]`

### `POST /api/cases/{case_id}/deep-research`
启动一次有边界的深度文献调研（gpt-researcher 风格：子查询拆解 → 多源检索 → 单篇压缩 → 综合简报）。

- **请求**:
  ```json
  {
    "question": "可选，默认使用案例 research_question",
    "depth": 1,
    "max_sources": 12,
    "max_subqueries": 5
  }
  ```
- **响应 202**: `{"scan_id": str, "status": "running", "mode": "deep_research"}`
- **轮询**: 使用 `GET /api/scans/{scan_id}` 或 `GET /api/cases/{case_id}/scans/{scan_id}`；完成后 `stats.research_brief` 包含结构化简报（title / executive_summary / sections / key_insights / contradictions / research_gaps / recommended_next_steps / sources）。
- **错误**: 409 — 该案例已有活跃扫描/深度调研

### `POST /api/cases/{case_id}/literature-qa`
基于该案例已收集的论文库回答问题（混合检索 + LLM 综合，带来源论文与证据片段）。

- **请求**:
  ```json
  {
    "question": "这些论文里有哪些方法提升了检索增强生成的鲁棒性？",
    "top_k": 5
  }
  ```
- **响应 200**:
  ```json
  {
    "answer": "str",
    "sources": [{"source_id": "str", "title": "str", "url": "str", "year": "str|null", "venue": "str|null", "evidence_snippets": ["str"]}],
    "uncertainty": "str",
    "warnings": ["str"]
  }
  ```
- **错误**: 400 — question 为空；404 — case 不存在

### `GET /api/cases/{case_id}/sources`
列出案例关联的文献来源。

- **参数**:
  - `?limit=50` — 返回数量，最大 200
  - `?offset=0` — 偏移量
  - `?source_kind=str` — 按来源类型筛选
  - `?ccf_rank=str` — 按 CCF 等级筛选
  - `?tag=str` — 按标签筛选
- **响应 200**: `[{id, title, authors, source_kind, venue, doi, arxiv_id, ccf_rank, fields_of_study, tags, snapshot_count, created_at}, ...]`
- **说明**: `tags` 为该来源在当前案例中的标签列表。
- **错误**: 404 — case 不存在

### `GET /api/cases/{case_id}/sources/{source_id}`
获取案例关联的单个文献来源及相关证据。

- **响应 200**: `{id, title, authors, source_kind, venue, doi, arxiv_id, arxiv_primary_category, fields_of_study, ccf_rank, tags, pdf_url, cited_by_count, integrity_state, created_at, snapshots: [{id, version_label, title, abstract, content_hash, observed_at}], related_impact_ids, related_claim_ids}`
- **错误**: 404 — case 或 source 不存在

### `GET /api/cases/{case_id}/cost-by-source`
按来源类型汇总案例的 LLM 成本。

- **响应 200**:
  ```json
  {
    "items": [{"source_kind": "str", "cost_usd": 0.0, "run_count": 0}],
    "total_cost_usd": 0.0
  }
  ```
- **错误**: 404 — case 不存在

### `PUT /api/cases/{case_id}/sources/{source_id}/tags`
设置案例中单个来源的标签。

- **请求**: `{"tags": ["must-read", "benchmark"]}`
- **响应 200**: `{"source_id": "str", "tags": ["must-read", "benchmark"]}`
- **说明**: 来源列表和详情响应均包含 `tags`。
- **错误**: 404 — case 或 source 不存在

### `POST /api/cases/{case_id}/sources/import`
把一篇外部论文（arXiv URL / DOI）导入案例。

- **请求**:
  ```json
  {
    "url": "https://arxiv.org/abs/2501.00001",
    "doi": ""
  }
  ```
- **响应 200**:
  ```json
  {
    "source_id": "str",
    "title": "str",
    "url": "str",
    "doi": "str|null",
    "arxiv_id": "str|null",
    "source_kind": "str"
  }
  ```
- **错误**: 400 — 无法解析来源；404 — case 不存在

### `GET /api/cases/{case_id}/digest`
生成个性化雷达周报（结构化 + Markdown）。

- **参数**: `?include_sections=scan,deep,impacts,actions`（可选，按逗号分隔过滤周报章节）
- **响应 200**:
  ```json
  {
    "case_id": "str",
    "title": "str",
    "generated_at": "str",
    "summary": {"scanned_papers": 0, "impacts": 0, "supports": 0, "challenges": 0, "open_actions": 0},
    "sections": [{"heading": "str", "items": ["str"]}],
    "markdown": "str"
  }
  ```
- **错误**: 404 — case 不存在

### `GET /api/cases/{case_id}/auto-scan`
读取案例的自动扫描配置。

- **响应 200**:
  ```json
  {
    "enabled": false,
    "interval_hours": 168,
    "next_run_at": "str|null",
    "last_error": "str|null",
    "last_error_at": "str|null"
  }
  ```

### `PUT /api/cases/{case_id}/auto-scan`
开启/关闭定时雷达扫描。

- **请求**:
  ```json
  {
    "enabled": true,
    "interval_hours": 168,
    "next_run_at": "str|null"
  }
  ```
- **响应 200**: 更新后的配置对象
- **错误**: 404 — case 不存在

### `GET /api/cases/{case_id}/monitoring-stats`
返回监控健康统计。

- **响应 200**:
  ```json
  {
    "total_scans": 0,
    "status_counts": {"completed": 0, "failed": 0},
    "auto_scan_start_failures": 0,
    "last_scan_at": "str|null",
    "monthly_cost_usd": 0.0,
    "cost_alert_exceeded": false
  }
  ```

### `GET /api/cases/{case_id}/cost-alert`
读取月度成本告警配置。

- **响应 200**:
  ```json
  {
    "enabled": false,
    "monthly_budget_usd": 0.0,
    "webhook_url": ""
  }
  ```

### `PUT /api/cases/{case_id}/cost-alert`
开启/关闭月度成本告警。

- **请求**:
  ```json
  {
    "enabled": true,
    "monthly_budget_usd": 20.0,
    "webhook_url": "https://example.com/hook"
  }
  ```
- **响应 200**: 更新后的配置对象
- **说明**: 配置 `webhook_url` 后，调度器会在超预算时（每 24 小时最多一次）向该地址 POST JSON 通知

### `POST /api/cases/{case_id}/cost-alert/test`
向已配置的成本告警 Webhook 发送一次测试通知。

- **响应 200**: `{"sent": true, "webhook_url": "str"}`
- **错误**: 400 — 未配置 Webhook URL；404 — case 不存在；502 — Webhook 测试失败

### `GET /api/cases/{case_id}/digest-webhook`
读取定时周报 Webhook 配置。

- **响应 200**:
  ```json
  {
    "enabled": false,
    "webhook_url": "",
    "schedule": "weekly"
  }
  ```

### `PUT /api/cases/{case_id}/digest-webhook`
开启/关闭定时周报 Webhook 发送。

- **请求**:
  ```json
  {
    "enabled": true,
    "webhook_url": "https://example.com/digest",
    "schedule": "weekly"
  }
  ```
  - `schedule`: `daily` / `weekly` / `monthly`
- **响应 200**: 更新后的配置对象
- **说明**: 调度器会按周期生成雷达周报并 POST JSON（含 markdown / sections）到 Webhook

### `GET /api/cases/{case_id}/scan-webhook`
读取扫描完成通知 Webhook 配置。

- **响应 200**: `{"enabled": false, "webhook_url": "", "notify_on": "all"}`
  - `notify_on`: `completed` / `failed` / `all`

### `PUT /api/cases/{case_id}/scan-webhook`
开启/关闭扫描完成通知 Webhook。

- **请求**: `{"enabled": true, "webhook_url": "https://example.com/scan", "notify_on": "completed"}`
- **响应 200**: 更新后的配置对象（字段同上）
- **错误**: 404 — case 不存在

### `GET /api/cases/{case_id}/email-notify`
读取 SMTP 邮件通知配置。密码字段始终以 `***` 掩码返回。

- **响应 200**:
  ```json
  {
    "enabled": false,
    "smtp_host": "",
    "smtp_port": 587,
    "username": "",
    "password": "",
    "recipient": "",
    "subject_prefix": "[Research Radar] "
  }
  ```

### `PUT /api/cases/{case_id}/email-notify`
保存 SMTP 邮件通知配置。

- **请求**:
  ```json
  {
    "enabled": true,
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "username": "sender@example.com",
    "password": "smtp-secret",
    "recipient": "you@example.com",
    "subject_prefix": "[Research Radar] "
  }
  ```
- **响应 200**: 更新后的配置对象
- **说明**: 密码传 `***` 或空字符串表示“保留已保存密码”；传新值才会替换。配置开启后，调度器生成定时雷达周报时会同时通过 SMTP 发送 Markdown 周报（与 Digest Webhook 共用同一发送周期）。

### `POST /api/cases/{case_id}/email-notify/test`
使用已保存的 SMTP 配置发送一封测试邮件。

- **响应 200**: `{"sent": true, "recipient": "str"}`
- **错误**: 400 — 缺少必填字段（SMTP 服务器/用户名/密码/收件人）；404 — case 不存在；502 — SMTP 发送失败

### `GET /api/cases/{case_id}/scan-filters`
读取扫描定向过滤配置（CCF 等级 / arXiv 分类）。

- **响应 200**:
  ```json
  {
    "ccf_rank": "A|B|C|null",
    "arxiv_categories": ["cs.AI", "cs.CL"]
  }
  ```

### `PUT /api/cases/{case_id}/scan-filters`
保存扫描定向过滤配置。

- **请求**:
  ```json
  {
    "ccf_rank": "A",
    "arxiv_categories": ["cs.AI", "cs.CL"]
  }
  ```
- **响应 200**: 更新后的配置对象
- **错误**: 404 — case 不存在

---

## 5. 影响判断 (Impact)

### `GET /api/scans/{scan_id}/impacts`
列出某次扫描的所有影响候选。

- **参数**: `?review_state=candidate|confirmed|edited|dismissed`
- **响应 200**: `[ImpactCandidate, ...]`
  - 每个 ImpactCandidate 包含：id, claim_revision_id, source_snapshot_id, stance, impact_mode, severity, comparability, suggested_action, strategic_flags, condition_differences, evidence_own, evidence_new, change_depth, review_state, trust_state

### `POST /api/impacts/{impact_id}/confirm`
确认影响（G1 通过，自动生成对应 Action）。

- **请求**: `{"reason": str|null}`
- **响应 200**: ImpactCandidate（含更新后的 review_state）

### `POST /api/impacts/{impact_id}/edit`
编辑影响字段后确认。

- **请求**:
  ```json
  {
    "payload": {
      "stance": str,
      "impact_mode": str,
      "comparability": str,
      "change_depth": int,
      "severity": str,
      "suggested_action": str
    },
    "reason": str|null
  }
  ```
- **响应 200**: ImpactCandidate

### `POST /api/impacts/{impact_id}/dismiss`
忽略影响。

- **请求**: `{"reason": str|null}`
- **响应 200**: ImpactCandidate

### `GET /api/impacts/{impact_id}/decisions`
查看影响的所有审核决策历史。

- **响应 200**: `[ReviewDecision, ...]`

### `GET /api/cases/{case_id}/impacts`
列出案例所有影响（跨扫描）。

- **响应 200**: `[ImpactCandidate, ...]`

---

## 6. 行动 (Action)

### `GET /api/cases/{case_id}/actions`
列出案例所有行动项。

- **参数**:
  - `?scan_run_id=str` — 按扫描筛选
  - `?include_closed=true|false` — 默认 false（仅活跃）
- **响应 200**: `[ActionItem, ...]`
  - 每个 ActionItem 新增 `suggestedSkill` 字段：对应的 CCF-A 技能名称（`ccf_paper_writer` / `ccf_experiment_designer` / `ccf_integrity_auditor` / `ccf_rebuttal_writer` / `ccf_paper_reviewer` / `ccf_reference_auditor`），空字符串表示无需 AI 技能

### `PUT /api/actions/{action_id}/status`
更新行动状态。

- **请求**: `{"status": "proposed" | "open" | "in_progress" | "done" | "dismissed"}`
- **响应 200**: ActionItem
- **错误**: 400 — 无效状态 / 404

### `GET /api/claims/{claim_id}/attention`
获取 Claim 的关注状态（摘要信号）。

- **响应 200**: `{"state": "stable" | "new_support" | "needs_review" | "disputed" | "competitor_pressure" | "revalidation_required"}`

---

## 7. 补丁 (Patch)

### `POST /api/impacts/{impact_id}/patch`
为已确认的影响生成文稿修改建议。

- **响应 201**: PatchProposal 对象
  - 包含：edit_class, target_locator, before_text, after_text, citations, validations, approval_state
- **错误**: 400 — 影响未确认 / 无变更影响 / 404

### `POST /api/patches/{patch_id}/approve`
批准补丁。

- **响应 200**: PatchProposal（approval_state → "approved"）
- **错误**: 422 — 验证未通过

### `POST /api/patches/{patch_id}/reject`
拒绝补丁。

- **响应 200**: PatchProposal（approval_state → "rejected"）

### `GET /api/patches/{patch_id}/validate`
重新验证补丁。

- **响应 200**: `{"before_text_exact": bool, "citations_resolved": bool, "citation_marker_safe": bool, "locked_numbers_unchanged": bool, "original_file_untouched": bool}`

### `POST /api/patches/{patch_id}/export`
导出补丁为 Markdown。

- **响应 200**: `{"markdown": str}`

---

## 8. 审计导出

### `GET /api/cases/{case_id}/audit`
导出案例审计事件。

- **参数**: `?limit=500` — 默认 500，传 `0` 为全量
- **响应 200**: JSON 字符串（application/json）
  ```json
  [
    {
      "id": str,
      "event_type": str,
      "object_type": str,
      "object_id": str,
      "payload": {...},
      "actor_type": str,
      "actor_id": str,
      "created_at": str
    }
  ]
  ```

### `GET /api/cases/{case_id}/skill-runs`
列出该案例最近的 CCF-A 技能执行记录（来自 ModelRun，最多 50 条）。

- **响应 200**:
  ```json
  [
    {
      "id": "str",
      "stage": "skill_ccf_paper_writer",
      "provider": "str",
      "model": "str",
      "created_at": "str|null",
      "latency_ms": 0,
      "input_tokens": 0,
      "output_tokens": 0,
      "output": {}
    }
  ]
  ```

---

## 9. 报告

### `GET /api/scans/{scan_id}/summary`
获取扫描周报摘要。

- **响应 200**:
  ```json
  {
    "scanned_papers": int,
    "routed_papers": int,
    "related_papers": int,
    "critical": int,
    "review": int,
    "informative": int,
    "supports": int,
    "challenges": int,
    "competitor_alerts": int,
    "integrity_alerts": int
  }
  ```

### `GET /api/scans/{scan_id}/action-report`
获取扫描行动报告（自动同步 actions）。

- **响应 200**:
  ```json
  {
    "scan_run_id": str,
    "headline": str,
    "urgent": int,
    "open_actions": int,
    "counts_by_type": {"team_decision": int, "experiment": int, ...},
    "summary": {...},
    "actions": [{"id": str, "type": str, "priority": str, "title": str, "rationale": str, "checklist": [str], "due": str, "status": str}, ...]
  }
  ```

### `GET /api/cases/{case_id}/writing-brief`
获取文稿写作简报（证据分组 + 写作行动）。

- **响应 200**:
  ```json
  {
    "supports": [...],
    "challenges": [...],
    "boundary_and_prior_art": [...],
    "integrity": [...],
    "writing_actions": [...]
  }
  ```

### `POST /api/cases/{case_id}/writing-brief/export`
导出写作简报为 Markdown。

- **响应 200**: `{"markdown": str}`

### `GET /api/claims/{claim_id}/evidence-pack`
获取单个 Claim 的证据包。

- **响应 200**:
  ```json
  {
    "schema": "ResearchRadarEvidencePack.v1",
    "claim": {"id": str, "stable_key": str, "statement": str, "centrality": str, "contract": {...}, "health": str},
    "supports": [...],
    "challenges": [...],
    "integrity": [...],
    "safety_note": str
  }
  ```

---

## 10. 设置

### `GET /api/settings`
读取当前设置（敏感字段已脱敏）。

- **响应 200**:
  ```json
  {
    "llm_provider": str|null,
    "llm_model": str|null,
    "llm_base_url": str|null,
    "local_llm_model": str|null,
    "embedding_provider": str|null,
    "embedding_model": str|null,
    "llm_api_key": "masked_sk-••••abcd",
    "llm_configured": bool,
    "llm_mode": "local" | "remote" | null
  }
  ```

### `PUT /api/settings`
更新设置（写入 `data/settings.local.env`）。

- **请求**: `{"LLM_PROVIDER": "deepseek", "LLM_MODEL": "deepseek-chat", ...}`
- **响应 200**: `{"saved": true, "path": "data/settings.local.env"}`
- **注意**: key 全部大写，与 `.env` 格式一致；空值表示取消设置

---

## 11. 文稿理解 (Manuscript Understanding)

### `POST /api/cases/{case_id}/analyze`
运行一次完整的文稿结构化理解（需已确认 Claim）。

- **响应 200**: `{model_run_id: str, profile: ManuscriptUnderstandingOutput}`
  - profile 包含：title, research_problem, central_thesis, contributions, methods, datasets, evaluation_protocol, key_findings, limitations, terminology, watch_topics, claim_profiles
- **错误**: 400 — 缺少已确认 Claim / 404

### `GET /api/cases/{case_id}/profile`
获取最近的文稿理解 profile（如有）。

- **响应 200**: ManuscriptUnderstandingOutput | `null`

---

## 12. 竞品监控 (Watch Entity)

### `POST /api/cases/{case_id}/watch`
添加竞品/团队监控别名。

- **请求**:
  ```json
  {
    "entity_type": str,
    "canonical_name": str,
    "aliases": [str]
  }
  ```
- **响应 201**: `{watch_id: str}`

### `DELETE /api/watch/{watch_id}`
移除监控。

- **响应 204**

---

## 13. Claim 增强分析

### `POST /api/cases/{case_id}/claims/{rev_id}/decompose`
原子化分解 Claim 为独立可验证的子单元。

- **响应 200**:
  ```json
  {
    "claim_revision_id": str,
    "atomic_claims": [
      {
        "statement": str,
        "source_quote": str,
        "source_locator": str,
        "dependencies": [str],
        "verifiable_independently": bool
      }
    ]
  }
  ```
- **错误**: 404 — Claim revision 不存在

### `POST /api/cases/{case_id}/claims/{rev_id}/verify-semantics`
验证 Claim 的 statement 是否语义准确地反映其 source_quote。防止 LLM 改写时添加强化词或改变含义。

- **响应 200**:
  ```json
  {
    "claim_stable_key": str,
    "statement": str,
    "source_quote": str,
    "faithful": bool,
    "issues": [str],
    "suggested_correction": str,
    "verified": "llm" | "skipped_no_llm" | "failed_llm_call"
  }
  ```

---

## 14. 多位置补丁

### `POST /api/cases/{case_id}/patches/multi-location`
为已确认的影响生成跨段落的多个修改建议（Introduction / Related Work / Discussion / Limitations / Methods）。

- **请求**: `multipart/form-data`, `impact_id`: str
- **响应 200**:
  ```json
  {
    "impact_id": str,
    "global_rationale": str,
    "edits": [
      {
        "section": str,
        "edit_class": str,
        "before_text": str,
        "after_text": str,
        "reason": str
      }
    ],
    "validated_count": int,
    "total_suggested": int,
    "citation_source_ids": [str]
  }
  ```
  - `validated_count` 表示通过 exact-span 验证的编辑数量
  - `total_suggested` 是 LLM 原始建议数量
- **错误**: 400 — 影响未确认 / 404

### `POST /api/cases/{case_id}/patches/{patch_id}/export-diff`
导出补丁为 unified diff 格式。

- **响应 200**: `{"patch_id": str, "diff": str}`

---

## 15. CCF-A 技能执行

### `POST /api/cases/{case_id}/skills/execute`
执行一个 CCF-A 风格 AI Agent 技能。

- **请求**: `multipart/form-data`
  - `skill_name`: str — `ccf_paper_writer` | `ccf_paper_reviewer` | `ccf_integrity_auditor` | `ccf_experiment_designer` | `ccf_rebuttal_writer` | `ccf_reference_auditor`
  - `action_type`: str — 触发类型（如 `writing`, `audit`, `review`, `experiment`, `rebuttal`）
  - `impact_ids`: str — JSON 数组，关联的已确认影响 ID 列表

- **响应 200**:
  ```json
  {
    "skill": str,
    "artifact_type": str,
    "content": {...},
    "warnings": [str],
    "handoff_suggestions": [str]
  }
  ```
  - `artifact_type`: 产物类型 (manuscript_edits / review_report / integrity_report / experiment_plan / rebuttal)
  - `handoff_suggestions`: 建议下一个执行的技能
- **错误**: 404 — case 或 skill 不存在

### `POST /api/cases/{case_id}/skill-pipeline`
按顺序执行一组 CCF-A 技能。

- **请求**:
  ```json
  {
    "pipeline": "revision",
    "skill_names": null,
    "impact_ids": []
  }
  ```
  - `pipeline`: `revision` / `review` / `experiment`，或传 `skill_names` 自定义
- **响应 200**:
  ```json
  {
    "pipeline": "revision",
    "skills": ["ccf_paper_writer", "ccf_integrity_auditor", "ccf_paper_reviewer"],
    "results": [{"skill": "str", "artifact_type": "str", "content": {}, "warnings": [], "handoff_suggestions": []}]
  }
  ```
- **错误**: 400 — 未知 pipeline / 未知技能；404 — case 不存在

### `GET /api/cases/{case_id}/skill-pipeline-runs`
列出该案例最近的 CCF-A 技能流水线执行记录（来自 AuditEvent，最多 50 条）。

- **响应 200**:
  ```json
  [
    {
      "id": "str",
      "event_type": "skill_pipeline_executed",
      "created_at": "str|null",
      "payload": {"pipeline": "revision", "skills": ["..."]}
    }
  ]
  ```

### `GET /api/cases/{case_id}/notification-history`
列出最近的调度通知事件（Webhook 发送/失败、自动扫描启动失败）。

- **参数**: `?event_type=cost_alert_webhook_sent`（可选，按类型筛选）
- **响应 200**:
  ```json
  [
    {
      "id": "str",
      "event_type": "cost_alert_webhook_sent",
      "created_at": "str|null",
      "payload": {}
    }
  ]
  ```

---

## 附录 A: Service 层接入审查结果

| Service | Streamlit 依赖 | 返回值类型 | 参数来源 | FastAPI 兼容 |
|---------|---------------|-----------|---------|-------------|
| CaseService | ❌ 无 | ORM 对象 | 纯参数 | ⚠️ 需包装为 dict/Pydantic |
| ClaimService | ❌ 无 | ORM 对象 | 纯参数 | ⚠️ 需包装为 dict/Pydantic |
| ActionService | ❌ 无 | ORM 对象 | 纯参数 | ⚠️ 需包装为 dict/Pydantic |
| ImpactService | ❌ 无 | Pydantic/list | 纯参数 | ✅ 直接可用 |
| PatchService | ❌ 无 | ORM 对象 | 纯参数 | ⚠️ 需包装为 dict/Pydantic |
| ReviewService | ❌ 无 | ORM 对象 | 纯参数 | ⚠️ 需包装为 dict/Pydantic |
| ConditionService | ❌ 无 | Pydantic | 纯参数 | ✅ 直接可用 |
| EvidenceService | ❌ 无 | Pydantic | 纯参数 | ✅ 直接可用 |
| TrustService | ❌ 无 | Pydantic | 纯参数 | ✅ 直接可用 |
| LedgerService | ❌ 无 | dict | 纯参数 | ✅ 直接可用 |
| RetrievalService | ❌ 无 | list/dict | 纯参数 | ✅ 直接可用 |
| ReportService | ❌ 无 | dict/str | 纯参数 | ✅ 直接可用 |
| ManuscriptUnderstandingService | ❌ 无 | Pydantic | 纯参数 | ✅ 直接可用 |
| WeeklyRadarService | ❌ 无 | str/dict（扫描编排） | 纯参数 | ✅ 直接可用 |
| scan_runner 模块函数 | ❌ 无 | str/ScanRun | 纯参数 | ⚠️ 全局 threading 状态见附录 B |

**结论**: 没有 service 直接依赖 Streamlit。返回 ORM 对象的 service 需要增加一层 `to_dict()` 或 Pydantic response schema 包装；其余可直接用作 FastAPI 路由的返回值。

---

## 附录 B: 数据库并发注意事项（FastAPI 适配）

当前 `radar/db.py` 使用模块级全局变量：

```python
engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
```

**对 FastAPI 的影响**:

1. **SQLite + check_same_thread=False**: FastAPI 默认使用线程池处理请求，`check_same_thread=False` 允许多线程共享同一连接。但 SQLite 仍受限于单写锁——并发写请求会排队。对于 Research Radar 的单用户本地场景，这通常不是问题。

2. **sessionmaker 线程安全**: `SessionLocal` 是线程安全的（SQLAlchemy `sessionmaker` 内部管理连接池），每个请求调用 `SessionLocal()` 获取独立 Session 即可。

3. **推荐 FastAPI 集成模式**:
   ```python
   # 使用 FastAPI dependency injection 而非模块级 session_scope
   def get_db():
       db = SessionLocal()
       try:
           yield db
       finally:
           db.close()
   
   @app.get("/api/cases")
   def list_cases(db: Session = Depends(get_db)):
       ...
   ```
   或者保持现有 `session_scope` 模式，在路由中每次调用 `with session_scope(self.session_factory) as session:` 即可。

4. **scan_runner 全局状态**: `_active_by_case`、`_threads`、`_lock` 是进程级变量，在单进程 FastAPI（`uvicorn` 默认 1 worker）下可正常工作。如需多 worker，需将扫描状态迁移到数据库或 Redis。

5. **config.get_settings() 的 lru_cache**: 进程级缓存，每个 worker 独立缓存一份 Settings，安全。

6. **无需引入 scoped_session**: 当前 `session_scope` 上下文管理器模式已经实现了"每个业务操作一个 session"，在 FastAPI 下每种请求创建一个 session 即可。`scoped_session` 仅在需要跨函数隐式传递 session 时才需要（如 Flask 的 `g`），FastAPI 的 `Depends` 注入模式更清晰。
