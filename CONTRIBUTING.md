# Contributing to Research Radar

Research Radar 是一个本地优先的 AI 研究助手，欢迎贡献。

## 开发环境

```bash
git clone https://github.com/GZ-November/research-radar.git
cd research-radar
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,vectors,paperqa2]'
cd app && npm install && cd ..
```

## 项目结构

```
radar/                  # Python 后端
  api.py                # FastAPI 应用
  config.py             # Pydantic 配置
  db.py                 # 数据库引擎与 session
  models.py             # SQLAlchemy ORM 模型
  schemas.py            # Pydantic 数据合约
  vector_store.py       # 向量库抽象 (ChromaDB)
  llm/                  # LLM 客户端抽象
    prompts/            # 提示词模板
      skills/           # CCF-A 技能提示词
  skills/               # CCF-A Agent 技能层
  services/             # 业务逻辑
  adapters/             # 外部文献源适配器
  embeddings/           # Embedding 抽象
  parsers/              # 文档解析器
app/                    # React 前端
tests/                  # pytest 测试
```

## 提交规范

- PR 需通过 `python -m pytest` 全部测试
- 新增功能需包含对应测试
- 提示词修改需同时更新中英双语注释
- 不提交 `.env` 或 `data/settings.local.env`

## 添加新技能

1. 在 `radar/skills/` 创建新文件，继承 `SkillProtocol`
2. 在 `radar/llm/prompts/skills/` 创建提示词模板
3. 在 `radar/api.py` 的技能端点注册
4. 添加测试到 `tests/`

## 许可证

MIT License — 详见 [LICENSE](./LICENSE)
