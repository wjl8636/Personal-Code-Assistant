"""Skill 自进化消融评测管道的测试 —— case 校验 / 判分 / 汇总 / run_case 配对。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalcode.eval.eval_skill_evolution import (
    extract_answer,
    load_cases,
    score_answer,
    summarize,
)


def _cases_path(tmp_path: Path) -> Path:
    p = tmp_path / "cases.jsonl"
    p.write_text("\n".join([
        '{"task_id":"skill/1","prompts":["task1","task2"],"answers":["A1","A2"],"feedback":"use parser"}',
        '{"task_id":"skill/2","prompts":["task1","task2"],"answers":["B1","B2"],"feedback":"strip cells"}',
    ]), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# load_cases
# ---------------------------------------------------------------------------

class TestLoadCases:
    def test_loads_valid(self, tmp_path: Path) -> None:
        cases = load_cases(_cases_path(tmp_path))
        assert len(cases) == 2
        assert cases[0]["task_id"] == "skill/1"
        assert cases[0]["prompts"] == ["task1", "task2"]
        assert cases[0]["answers"] == ["A1", "A2"]
        assert cases[0]["feedback"] == "use parser"

    def test_limit(self, tmp_path: Path) -> None:
        cases = load_cases(_cases_path(tmp_path), limit=1)
        assert len(cases) == 1

    def test_duplicate_task_id_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "dup.jsonl"
        p.write_text(
            '{"task_id":"x","prompts":["a","b"],"answers":["1","2"],"feedback":"f"}\n'
            '{"task_id":"x","prompts":["a","b"],"answers":["1","2"],"feedback":"f"}\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="unique task_id"):
            load_cases(p)

    def test_missing_feedback_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.jsonl"
        p.write_text('{"task_id":"x","prompts":["a","b"],"answers":["1","2"]}\n', encoding="utf-8")
        with pytest.raises(ValueError, match="feedback"):
            load_cases(p)

    def test_short_prompts_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.jsonl"
        p.write_text('{"task_id":"x","prompts":["a"],"answers":["1","2"],"feedback":"f"}\n', encoding="utf-8")
        with pytest.raises(ValueError, match="prompts"):
            load_cases(p)


# ---------------------------------------------------------------------------
# extract_answer / score_answer
# ---------------------------------------------------------------------------

class TestScoring:
    def test_extract_final_answer(self) -> None:
        assert extract_answer("some text\nFINAL_ANSWER: fl\n") == "fl"
        assert extract_answer("no marker here") == "no marker here"

    def test_score_normalized(self) -> None:
        assert score_answer("  fl ", ["fl"])
        assert score_answer("Fl", ["fl"])  # casefold
        assert not score_answer("flower", ["fl"])

    def test_score_exact(self) -> None:
        assert score_answer("fl", ["fl"], mode="exact")
        assert score_answer(" fl ", ["fl"], mode="exact")  # exact 去首尾空白
        assert not score_answer("Fl", ["fl"], mode="exact")  # exact 不折叠大小写


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

class TestSummarize:
    def _rows(self) -> list[dict]:
        return [
            # skill/1: both correct
            {"task_id": "skill/1", "condition": "no_evolve", "correct": True, "status": "completed", "skills_created": 0, "main_input_tokens": 10, "main_output_tokens": 5},
            {"task_id": "skill/1", "condition": "evolve", "correct": True, "status": "completed", "skills_created": 1, "main_input_tokens": 20, "main_output_tokens": 8},
            # skill/2: evolve only
            {"task_id": "skill/2", "condition": "no_evolve", "correct": False, "status": "completed", "skills_created": 0, "main_input_tokens": 9, "main_output_tokens": 4},
            {"task_id": "skill/2", "condition": "evolve", "correct": True, "status": "completed", "skills_created": 1, "main_input_tokens": 18, "main_output_tokens": 7},
        ]

    def test_four_buckets(self) -> None:
        summary = summarize(self._rows())
        assert summary["cases"] == 2
        assert summary["paired_outcomes"] == {"both_correct": 1, "evolve_only": 1, "no_evolve_only": 0, "both_wrong": 0}
        assert summary["accuracy_delta_percentage_points"] == 50.0  # (1-0)/2*100
        assert summary["skills_created"] == 2

    def test_evolve_only_bucket(self) -> None:
        rows = self._rows()
        # 只保留 skill/2 的单条
        rows = [r for r in rows if r["task_id"] == "skill/2"]
        summary = summarize(rows)
        assert summary["cases"] == 1
        assert summary["paired_outcomes"]["evolve_only"] == 1
        assert summary["accuracy_delta_percentage_points"] == 100.0

    def test_incomplete_pair_rejected(self) -> None:
        rows = [self._rows()[0]]  # 只有 no_evolve，缺 evolve
        with pytest.raises(ValueError, match="incomplete pair"):
            summarize(rows)

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="no evaluation results"):
            summarize([])