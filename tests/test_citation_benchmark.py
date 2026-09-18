"""Tests for the deterministic citation-check benchmark harness."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _benchmark_module():
    path = Path(__file__).parents[1] / "scripts" / "eval_citation_checks.py"
    spec = importlib.util.spec_from_file_location("eval_citation_checks", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_layer1_metrics_computes_confusion_metrics():
    module = _benchmark_module()
    metrics = module.layer1_metrics(
        [
            {"expected_real": True, "verified": True},
            {"expected_real": True, "verified": False},
            {"expected_real": False, "verified": True},
            {"expected_real": False, "verified": False},
        ]
    )

    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 1
    assert metrics["false_negative"] == 1
    assert metrics["true_negative"] == 1
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5
    assert metrics["f1"] == 0.5
    assert metrics["false_positive_rate"] == 0.5


def test_layer2_metrics_uses_category_distance_for_weighted_accuracy():
    module = _benchmark_module()
    metrics = module.layer2_metrics(
        [
            {"expected_category": "supported", "predicted_category": "supported"},
            {
                "expected_category": "supported",
                "predicted_category": "partially_supported",
            },
            {
                "expected_category": "supported",
                "predicted_category": "unsupported",
            },
            {"expected_category": "supported", "predicted_category": "uncertain"},
        ]
    )

    assert metrics["accuracy"] == 0.25
    assert metrics["weighted_accuracy"] == 0.5


def test_benchmark_main_runs_offline():
    module = _benchmark_module()

    assert module.main([]) == 0
