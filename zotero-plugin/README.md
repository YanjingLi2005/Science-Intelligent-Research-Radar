# Research Radar Zotero Plugin（骨架）

这是一个 Zotero 7 插件骨架，用于把 Zotero 条目一键加入 Research Radar 案例。

## 当前状态

- 已包含最小 `manifest.json`、`bootstrap.js`、`zotero-plugin.js`
- 提供 `Zotero.ResearchRadar.importItem(item)` 方法：
  - 读取插件偏好中的 API Base URL 与 Case ID
  - 从条目读取 DOI / URL
  - 调用 `POST /api/cases/{case_id}/sources/import`
- 尚未完成：
  - 右键菜单注册
  - 偏好设置界面
  - 多选条目批量导入
  - 错误提示 UI

## 安装（开发）

1. 在 Zotero 中打开 `Tools → Plugins → ⚙️ → Install Plugin From File...`
2. 选择本目录中的 `.xpi`（需要先把目录打包成 xpi，或使用 Zotero 开发模式加载）

## 后续

- 接入 Zotero 的 item context menu
- 读取本地偏好：API Base URL、默认 Case ID
- 批量导入选中条目
- 导入后打开 Research Radar 对应案例
