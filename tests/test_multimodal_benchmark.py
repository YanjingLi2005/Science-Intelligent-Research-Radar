"""Unit tests for multimodal table extraction, benchmark difference engine, and LaTeX booktabs."""

from pathlib import Path
import tempfile
import pytest

from radar.api_schemas import PaperOut
from radar.services.impact_service import ImpactService
from radar.services.manuscript_parser import (
    extract_tables_from_pdf,
    extract_tables_from_text,
)


def test_extract_tables_from_text_markdown():
    text = """
Some intro text before table.

| Model | DomainQA (EM) | HotpotQA (F1) | Latency (ms) |
| :--- | :---: | :---: | :---: |
| RadarNet (Ours) | 68.7 | 74.3 | 120 |
| Concurrent SOTA | 71.2 | 72.8 | 95 |

Some text after table.
"""
    tables = extract_tables_from_text(text)
    assert len(tables) == 1
    t = tables[0]
    assert t["headers"] == ["Model", "DomainQA (EM)", "HotpotQA (F1)", "Latency (ms)"]
    assert len(t["rows"]) == 2
    assert t["rows"][0][0] == "RadarNet (Ours)"
    assert t["rows"][1][0] == "Concurrent SOTA"


def test_extract_tables_from_text_empty():
    assert extract_tables_from_text("") == []
    assert extract_tables_from_text("Just plain academic abstract with no tables.") == []


def test_extract_tables_from_pdf_nonexistent():
    assert extract_tables_from_pdf("/nonexistent/file.pdf") == []


def test_extract_tables_from_pdf_with_fitz(tmp_path):
    import fitz

    # Create a small valid test PDF with a table
    pdf_path = tmp_path / "test_table.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Table 1: Benchmark Results on DomainQA")
    page.insert_text((50, 80), "| Model | Exact Match | F1 |")
    page.insert_text((50, 100), "| Ours | 68.7 | 74.3 |")
    doc.save(str(pdf_path))
    doc.close()

    # Even if table recognition heuristic on drawn text is empty, the function must not crash
    tables = extract_tables_from_pdf(pdf_path)
    assert isinstance(tables, list)


def test_extract_benchmark_metrics_from_table():
    sample_tables = [
        {
            "caption": "Table 1: Main benchmark results",
            "headers": ["Dataset", "Exact Match", "F1 Score"],
            "rows": [
                ["DomainQA", "71.2%", "76.4%"],
                ["HotpotQA", "65.0%", "70.1%"],
            ],
        }
    ]
    metrics = ImpactService.extract_benchmark_metrics(sample_tables)
    assert len(metrics) == 4
    assert metrics[0]["dataset"] == "DomainQA"
    assert metrics[0]["metric"] == "Exact Match"
    assert metrics[0]["value"] == 71.2
    assert metrics[0]["unit"] == "%"


def test_extract_benchmark_metrics_from_text():
    text = "We evaluate on DomainQA: 71.2% and HotpotQA achieves 75.8%."
    metrics = ImpactService.extract_benchmark_metrics(text)
    assert len(metrics) >= 2
    datasets = [m["dataset"] for m in metrics]
    assert "DomainQA" in datasets
    assert "HotpotQA" in datasets


def test_compute_benchmark_comparison_higher_is_better():
    # 1. Competitor outperforms our model by +2.5% (Warning)
    incoming = [{"dataset": "DomainQA", "metric": "Exact Match", "value": 71.2, "unit": "%"}]
    comp = ImpactService.compute_benchmark_comparison(
        own_claim_contract={"dataset": "DomainQA", "metric": "Exact Match"},
        incoming_metrics=incoming,
        own_metrics=[{"dataset": "DomainQA", "metric": "Exact Match", "value": 68.7}],
    )
    assert len(comp) == 1
    assert comp[0]["delta"] == 2.5
    assert comp[0]["warning"] is True
    assert comp[0]["warning_level"] in {"moderate", "critical"}

    # 2. Our model outperforms competitor by +3.7% (Advantage)
    incoming_lower = [{"dataset": "DomainQA", "metric": "Exact Match", "value": 65.0, "unit": "%"}]
    comp_lower = ImpactService.compute_benchmark_comparison(
        own_claim_contract={"dataset": "DomainQA", "metric": "Exact Match"},
        incoming_metrics=incoming_lower,
        own_metrics=[{"dataset": "DomainQA", "metric": "Exact Match", "value": 68.7}],
    )
    assert comp_lower[0]["delta"] == -3.7
    assert comp_lower[0]["warning"] is False
    assert comp_lower[0]["warning_level"] == "advantage"


def test_compute_benchmark_comparison_lower_is_better_latency():
    # In latency, competitor has 80ms vs our 120ms -> competitor is faster by -40ms (Warning for us)
    incoming = [{"dataset": "DomainQA", "metric": "Inference Latency (ms)", "value": 80.0, "unit": "ms"}]
    comp = ImpactService.compute_benchmark_comparison(
        own_claim_contract={"dataset": "DomainQA", "metric": "Latency"},
        incoming_metrics=incoming,
        own_metrics=[{"dataset": "DomainQA", "metric": "Inference Latency (ms)", "value": 120.0}],
    )
    assert len(comp) == 1
    assert comp[0]["higher_is_better"] is False
    assert comp[0]["delta"] == -40.0
    assert comp[0]["warning"] is True
    assert comp[0]["warning_level"] == "critical"


def test_export_latex_booktabs():
    comparisons = [
        {
            "dataset": "DomainQA",
            "metric": "Exact Match",
            "our_value": 68.7,
            "competitor_value": 71.2,
            "delta": 2.5,
            "unit": "%",
            "warning": True,
        },
        {
            "dataset": "HotpotQA",
            "metric": "F1",
            "our_value": 74.3,
            "competitor_value": 72.8,
            "delta": -1.5,
            "unit": "%",
            "warning": False,
        },
    ]
    latex = ImpactService.export_latex_booktabs(comparisons)
    assert "\\begin{table}" in latex
    assert "\\toprule" in latex
    assert "\\midrule" in latex
    assert "\\bottomrule" in latex
    assert "\\textbf{+2.5%}" in latex
    assert "-1.5%" in latex
    assert "\\end{table}" in latex


def test_paper_out_schema_benchmark_comparison():
    paper = PaperOut(
        id="paper-1",
        title="Test Paper",
        benchmarkComparison=[
            {
                "dataset": "DomainQA",
                "metric": "Exact Match",
                "our_value": 68.7,
                "competitor_value": 71.2,
                "delta": 2.5,
                "unit": "%",
                "warning": True,
            }
        ],
        strategicFlags=["EMPIRICAL_DOMINANCE"],
        empiricalDominance=True,
    )
    dumped = paper.model_dump()
    assert "benchmarkComparison" in dumped
    assert len(dumped["benchmarkComparison"]) == 1
    assert dumped["benchmarkComparison"][0]["dataset"] == "DomainQA"
    assert dumped["strategicFlags"] == ["EMPIRICAL_DOMINANCE"]
    assert dumped["empiricalDominance"] is True


def test_empirical_dominance_tagging():
    # 1. Significant lead on accuracy (+3.5%)
    incoming = [{"dataset": "MMLU", "metric": "Accuracy", "value": 78.5, "unit": "%"}]
    comp = ImpactService.compute_benchmark_comparison(
        own_claim_contract={"dataset": "MMLU", "metric": "Accuracy"},
        incoming_metrics=incoming,
        own_metrics=[{"dataset": "MMLU", "metric": "Accuracy", "value": 75.0}],
    )
    assert len(comp) == 1
    assert comp[0]["delta"] == 3.5
    assert comp[0]["warning"] is True
    assert comp[0]["warning_level"] == "critical"
    assert comp[0]["empirical_dominance"] is True
    assert ImpactService.has_empirical_dominance(comp) is True

    # 2. Moderate lead (+1.0%)
    incoming_mod = [{"dataset": "MMLU", "metric": "Accuracy", "value": 76.0, "unit": "%"}]
    comp_mod = ImpactService.compute_benchmark_comparison(
        own_claim_contract={"dataset": "MMLU", "metric": "Accuracy"},
        incoming_metrics=incoming_mod,
        own_metrics=[{"dataset": "MMLU", "metric": "Accuracy", "value": 75.0}],
    )
    assert comp_mod[0]["delta"] == 1.0
    assert comp_mod[0]["warning"] is True
    assert comp_mod[0]["warning_level"] == "moderate"
    assert comp_mod[0]["empirical_dominance"] is False
    assert ImpactService.has_empirical_dominance(comp_mod) is False


def test_parse_table_with_multimodal_llm_fallback():
    from radar.services.manuscript_parser import parse_table_with_multimodal_llm
    # Without LLM client, gracefully returns None
    result = parse_table_with_multimodal_llm(b"fake_image_bytes", llm_client=None, caption="Table 1")
    assert result is None
