"""Live end-to-end verification: real arXiv search + real LLM + real DB.

Runs the true external chain against a throwaway SQLite file (the working
``data/research_radar.db`` is never touched) and prints a step-by-step
✅/❌ report. Any failed step exits with a non-zero status. Works with either
a remote provider or a local Ollama model — whatever the factory resolves.
"""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from radar.adapters.arxiv import ArxivSearchAdapter
from radar.config import Settings
from radar.db import create_db_engine, init_database
from radar.llm.factory import build_analysis_llm, describe_llm_setup
from radar.models import Source, SourceSnapshot
from radar.schemas import WatchQuery
from radar.services.weekly_radar_service import WeeklyRadarService


SEARCH_QUERY = "retrieval augmented generation"
MAX_RESULTS = 3


class PaperBrief(BaseModel):
    """Minimal structured output used to prove the live LLM path."""

    title: str
    one_sentence_relevance: str


def check_configuration(settings: Settings) -> tuple[bool, str]:
    """Return (ok, message) for the analysis-LLM configuration; no network."""

    setup = describe_llm_setup(settings)
    if not setup["configured"]:
        missing = ", ".join(setup["missing"])
        return (
            False,
            f"未配置分析模型，远程缺少：{missing}"
            "（或配置 LOCAL_LLM_MODEL 使用本地 Ollama）",
        )
    mode = "本地 Ollama" if setup["mode"] == "local" else "远程 API"
    return True, f"{mode} · 模型 {setup['model']}"


def _report(step: str, ok: bool, detail: str) -> None:
    print(f"{'✅' if ok else '❌'} {step}: {detail}")


def main() -> int:
    settings = Settings()

    ok, message = check_configuration(settings)
    _report("配置检查", ok, message)
    if not ok:
        return 1

    with TemporaryDirectory(prefix="research-radar-live-") as directory:
        adapter = ArxivSearchAdapter(Path(directory) / "arxiv-cache")
        try:
            records = adapter.search(
                "live-verification",
                WatchQuery(query=SEARCH_QUERY, max_results=MAX_RESULTS),
            )
        except Exception as exc:
            _report("arXiv 搜索", False, str(exc))
            return 1
        _report(
            "arXiv 搜索",
            bool(records),
            f"查询 {SEARCH_QUERY!r} 返回 {len(records)} 条结果"
            if records
            else "没有返回任何结果",
        )
        if not records:
            return 1

        llm_client = build_analysis_llm(settings)
        first = records[0]
        prompt = (
            "Summarize in one sentence why this paper is relevant to "
            f"'{SEARCH_QUERY}'.\nTITLE: {first.title}\nABSTRACT: {first.abstract}"
        )
        try:
            brief = llm_client.generate_structured(
                stage="live_verification",
                prompt=prompt,
                response_model=PaperBrief,
            )
        except Exception as exc:
            _report("LLM 结构化输出", False, str(exc))
            return 1
        _report(
            "LLM 结构化输出",
            True,
            f"返回 title={brief.title!r} · {brief.one_sentence_relevance[:100]}",
        )

        engine = create_db_engine(f"sqlite:///{Path(directory) / 'verify.db'}")
        init_database(engine)
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        try:
            # Reuse the scan pipeline's real storage path against the temp DB.
            service = WeeklyRadarService(
                factory,
                search_adapter=adapter,
                llm_client=llm_client,
                settings=settings,
            )
            service._store_records(records)
            with factory() as session:
                stored_sources = session.scalar(
                    select(func.count()).select_from(Source)
                )
                stored_snapshots = session.scalar(
                    select(func.count()).select_from(SourceSnapshot)
                )
            ok = stored_sources > 0 and stored_snapshots > 0
            _report(
                "落库",
                ok,
                f"临时库写入 {stored_sources} 个 Source / {stored_snapshots} 个 Snapshot",
            )
            return 0 if ok else 1
        except Exception as exc:
            _report("落库", False, str(exc))
            return 1
        finally:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
