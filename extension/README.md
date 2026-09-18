# Research Radar Importer（浏览器扩展）

一个极简 Chrome MV3 扩展：阅读 arXiv / DOI 页面时，一键把当前论文加入 Research Radar 案例。

## 安装（开发模式）

1. 打开 `chrome://extensions`
2. 开启「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择本目录 `extension/`

## 使用

1. 点击扩展图标
2. 填写 API Base URL（默认 `http://localhost:8501/api`）
3. 填写 Case ID（如 `case-demo-radar`）
4. 在 arXiv / DOI 页面点击「从当前页导入到雷达」

也可以使用右键菜单：
- 在页面上右键 → 「把当前页加入 Research Radar」
- 在链接上右键 → 「把这个链接加入 Research Radar」

扩展会把当前页面/链接 URL 发给：

```
POST /api/cases/{case_id}/sources/import
{"url": "https://arxiv.org/abs/...", "doi": "10.xxxx/..."}
```

扩展会自动从 URL 或页面 `<meta name="citation_doi">` / 正文中提取 DOI；后端会通过 arXiv / OpenAlex 解析并存入 Source/SourceSnapshot。
