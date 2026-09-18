# Research Radar 产品路线图（建议）

## P0 — 主流程闭环
- [x] 新建 Case 后直接进入 Claim 确认
- [x] Claim 确认后引导去雷达扫描
- [x] G0 拦截提示增加直达按钮
- [x] 来源库支持 DOI/URL 导入
- [x] 移动端抽屉导航
- [ ] 首次使用 Onboarding 向导（上传 → 提取 → 确认 → 扫描 → 复核 → 行动）
- [ ] Claim 提取/扫描任务完成后的 Toast 通知
- [ ] 影响复核状态跨页一致（Dashboard / Radar / Actions）

## P1 — 信息架构与页面职责
- [ ] 把 Dashboard 的运维型功能（SMTP、Webhook、成本预警、通知历史）迁移到独立“运维/设置”页
- [ ] 独立 Deep Research 历史页（可查看历史简报，不再只存在 React state）
- [ ] Literature QA 独立对话页 / 侧边面板
- [ ] 统一 History / Audit 视图
- [ ] 统一 Data Export 页

## P1 — 产品功能补齐
- [ ] Claim 编辑 / 反馈原因 / 版本间 diff 视图
- [ ] 技能流水线结果结构化渲染（不再是 JSON `<pre>`）
- [ ] 自动周报/Digest 应用内阅读视图
- [ ] 邮件通知模板与发送记录管理
- [ ] 多用户/团队权限（当前是单管理员部署）

## P2 — 体验打磨
- [ ] 所有错误状态支持重试 + toast 统一
- [ ] 暗色模式全面回归（Metrics 已补）
- [ ] 加载骨架屏 / 任务状态 aria-live
- [ ] 可访问性：skip link、焦点管理、颜色之外的状态提示
