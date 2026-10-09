"""Skill 自进化消融评测：同题「无自进化 vs 有自进化」配对 A/B。

对每个 case 跑两遍，唯一区别是否启用 Skill 自进化：
- `no_evolve`（A）：不注册 skill 工具、不注入反馈，模型只能靠通用工具硬做 2 轮。
- `evolve`（B）：注册 skill_create/skill_evolve，第 1 轮后注入 case.feedback 作为
  演化信号（模拟 pending window 的下一轮反馈），让模型沉淀 SKILL.md 并在第 2 轮
  同类任务复用。

指标：同题正确率四桶（both_correct / evolve_only / no_evolve_only / both_wrong）、
accuracy_delta_percentage_points、skills_created（B 组 usage.jsonl create 数）。

独立 CLI（需要 API key，不进 CI）；`--validate-only` 只校验 case 不发请求。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any

from personalcode.config import ProviderConfig, load_config
from personalcode.conversation import ConversationManager
from personalcode.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)

FINAL_ANSWER = __import__("re").compile(r"(?im)^\s*FINAL_ANSWER\s*[:：]\s*(.*?)\s*$")


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def load_cases(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """加载并校验 case。每个 case 必须有 task_id / prompts(list≥2) / answers(list≥2) / feedback。"""
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    else:
        rows = json.loads(raw)
        if isinstance(rows, dict):
            rows = rows.get("cases")
    if not isinstance(rows, list):
        raise ValueError("cases file must contain a JSON array or JSONL objects")

    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"case {index + 1} is not an object")
        case_id = str(row.get("task_id") or "").strip()
        if not case_id or case_id in seen:
            raise ValueError(f"case {index + 1} needs a unique task_id")
        seen.add(case_id)
        prompts = row.get("prompts")
        if not isinstance(prompts, list) or len(prompts) < 2 or any(not isinstance(p, str) or not p.strip() for p in prompts):
            raise ValueError(f"case {case_id} needs prompts (list, >=2 non-empty)")
        answers = row.get("answers")
        if not isinstance(answers, list) or len(answers) < 2 or any(a is None or str(a).strip() == "" for a in answers):
            raise ValueError(f"case {case_id} needs answers (list, >=2 non-empty)")
        feedback = str(row.get("feedback") or "").strip()
        if not feedback:
            raise ValueError(f"case {case_id} needs a reusable feedback string")
        cases.append({
            "task_id": case_id,
            "prompts": [str(p) for p in prompts],
            "answers": [str(a).strip() for a in answers],
            "feedback": feedback,
        })
        if limit is not None and len(cases) >= limit:
            break
    if not cases:
        raise ValueError("no cases selected")
    return cases


def extract_answer(output: str) -> str:
    matches = FINAL_ANSWER.findall(output or "")
    return (matches[-1] if matches else str(output or "")).strip()


def _normalized_answer(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text)).casefold().split())


def score_answer(prediction: str, answers: list[str], *, mode: str = "normalized") -> bool:
    if not prediction:
        return False
    normalize = (lambda text: str(text).strip()) if mode == "exact" else _normalized_answer
    return normalize(prediction) in {normalize(answer) for answer in answers}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """配对四桶汇总。rows 是 results 列表，每个 case 有 no_evolve / evolve 两条。"""
    if not rows:
        raise ValueError("no evaluation results")
    paired: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        pair = paired.setdefault(row["task_id"], {})
        if row["condition"] in pair:
            raise ValueError(f"duplicate result: {row['task_id']} {row['condition']}")
        pair[row["condition"]] = row
    counts = {"both_correct": 0, "evolve_only": 0, "no_evolve_only": 0, "both_wrong": 0}
    skills_created = 0
    for task_id, pair in paired.items():
        if set(pair) != {"no_evolve", "evolve"}:
            raise ValueError(f"incomplete pair: {task_id}")
        on = bool(pair["evolve"]["correct"])
        off = bool(pair["no_evolve"]["correct"])
        bucket = "both_correct" if on and off else "evolve_only" if on else "no_evolve_only" if off else "both_wrong"
        counts[bucket] += 1
        skills_created += int(pair["evolve"].get("skills_created", 0))
    n = len(paired)

    def cond_totals(condition: str) -> dict[str, Any]:
        selected = [r for r in rows if r["condition"] == condition]
        correct = counts["both_correct"] + (counts["evolve_only"] if condition == "evolve" else counts["no_evolve_only"])
        return {
            "correct": correct,
            "accuracy": correct / n,
            "main_input_tokens": sum(int(r.get("main_input_tokens", 0)) for r in selected),
            "main_output_tokens": sum(int(r.get("main_output_tokens", 0)) for r in selected),
            "statuses": {s: sum(r["status"] == s for r in selected) for s in sorted({r["status"] for r in selected})},
        }

    return {
        "cases": n,
        "paired_outcomes": counts,
        "skills_created": skills_created,
        "no_evolve": cond_totals("no_evolve"),
        "evolve": cond_totals("evolve"),
        "accuracy_delta_percentage_points": 100 * (counts["evolve_only"] - counts["no_evolve_only"]) / n,
    }


def _build_agent(case: dict[str, Any], condition: str, *, provider: ProviderConfig, work_dir: str):
    """构造评测用 Agent。

    no_evolve：普通 Agent（不注册 skill 工具，不注入 feedback）。
    evolve：注册 skill_create/skill_evolve，注入 case.feedback。
    """
    from personalcode.agent import Agent
    from personalcode.client import create_client
    from personalcode.memory.instructions import load_instructions
    from personalcode.tools import create_default_registry
    from personalcode.tools.impl.tool_search import ToolSearchTool

    client = create_client(provider)
    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(work_dir, deny_write=PathSandbox.without_skill_deny()),
        rule_engine=RuleEngine(
            user_rules_path=Path.home() / ".personalcode" / "permissions.yaml",
            project_rules_path=Path(work_dir) / ".personalcode" / "permissions.yaml",
            local_rules_path=Path(work_dir) / ".personalcode" / "permissions.local.yaml",
        ),
        mode=PermissionMode.BYPASS,
    )
    registry = create_default_registry()
    registry.register(ToolSearchTool(registry, protocol=provider.protocol))

    if condition == "evolve":
        from personalcode.skills.loader import SkillLoader
        from personalcode.tools.skill_create import SkillCreateTool
        from personalcode.tools.skill_evolve import SkillEvolveTool

        skill_loader = SkillLoader(work_dir)
        skill_loader.load_all()
        create_tool = SkillCreateTool()
        create_tool.set_loader(skill_loader)
        create_tool.set_work_dir(work_dir)
        registry.register(create_tool)
        evolve_tool = SkillEvolveTool()
        evolve_tool.set_loader(skill_loader)
        evolve_tool.set_work_dir(work_dir)
        registry.register(evolve_tool)

    agent = Agent(
        client=client,
        registry=registry,
        protocol=provider.protocol,
        work_dir=work_dir,
        permission_checker=checker,
        context_window=provider.get_context_window(),
        instructions_content=load_instructions(work_dir),
    )
    if condition == "evolve":
        agent.set_skill_loader(skill_loader)
    return agent


async def _run_case(
    case: dict[str, Any],
    condition: str,
    *,
    provider: ProviderConfig,
    output_dir: Path,
    work_dir: str,
    score_mode: str,
) -> dict[str, Any]:
    """跑一个 case 的一遍（no_evolve 或 evolve），返回结果行。"""
    run_dir = output_dir / f"case-{hashlib.sha256(case['task_id'].encode('utf-8')).hexdigest()[:16]}" / condition
    run_dir.mkdir(parents=True, exist_ok=True)

    conv = ConversationManager()
    agent = _build_agent(case, condition, provider=provider, work_dir=work_dir)

    outputs: list[str] = []
    status = "completed"
    error = ""
    try:
        for index, prompt in enumerate(case["prompts"]):
            # evolve 条件：第 1 轮跑完后、第 2 轮前，真正触发一次在线 Skill 自进化。
            # 构造 ready window（当前轮 + feedback 作为演化信号），走 online_ingest，
            # 让 Extractor/Maintainer 沉淀或演化 SKILL.md——这才是消融要测的机制。
            if condition == "evolve" and index == 1:
                window_messages = [
                    {"role": m.role, "content": m.content}
                    for m in list(conv.history)[-6:]
                    if m.role in ("user", "assistant") and m.content
                ]
                window_messages.append({"role": "user", "content": case["feedback"]})
                await agent._run_online_skill_evolution({
                    "messages": window_messages,
                    "retrieved_reference": None,
                })
                # 把 feedback 也作为用户消息注入对话，让模型知道这条规则
                conv.add_user_message(case["feedback"])
                # 提示模型：如已沉淀可复用 skill 请先加载
                conv.add_system_reminder(
                    "If a reusable skill was just created from the previous feedback, "
                    "call LoadSkill with that skill's name before answering, and follow it."
                )
            text = await agent.run_to_completion(prompt, conv)
            outputs.append(text)
    except Exception as exc:
        status, error = "error", f"{type(exc).__name__}: {exc}"

    # 每轮独立判分：第 1 轮看 prompts[0]/answers[0]，第 2 轮看 prompts[1]/answers[1]
    per_turn_correct: list[bool] = []
    for i in range(min(len(outputs), len(case["prompts"]))):
        prediction = extract_answer(outputs[i])
        per_turn_correct.append(score_answer(prediction, [case["answers"][i]], mode=score_mode))

    # 第 2 轮（复用轮）正确是主指标
    final_correct = per_turn_correct[-1] if per_turn_correct else False

    # 统计 evolve 组沉淀的 skill 数
    skills_created = 0
    if condition == "evolve":
        from personalcode.skills.evolution import load_skill_stats

        stats = load_skill_stats(work_dir)
        skills_created = sum(1 for item in stats.values() if item.get("created", 0) > 0)

    trace = {
        "messages": conv.history,
        "outputs": outputs,
        "per_turn_correct": per_turn_correct,
    }
    _write_json(run_dir / "trace.json", trace)

    row = {
        "task_id": case["task_id"],
        "condition": condition,
        "status": status,
        "error": error,
        "output": outputs[-1] if outputs else "",
        "answers": case["answers"],
        "correct": status == "completed" and final_correct,
        "per_turn_correct": per_turn_correct,
        "score_mode": score_mode,
        "skills_created": skills_created,
        "main_input_tokens": agent.total_input_tokens,
        "main_output_tokens": agent.total_output_tokens,
    }
    _write_json(run_dir / "result.json", row)
    return row


async def main_async(args: argparse.Namespace) -> None:
    cases = load_cases(args.cases, limit=args.limit)
    if args.validate_only:
        print(f"Validated {len(cases)} cases; no model calls made.")
        return

    config = load_config()
    if not config.providers:
        raise ValueError("no provider configured in .personalcode/config.yaml")
    provider = config.providers[0]
    # 允许 CLI 覆盖
    if args.model:
        provider.model = args.model
    if args.api_base:
        provider.base_url = args.api_base
    if args.api_key:
        provider.api_key = args.api_key

    work_dir = str(Path.cwd())
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)

    _write_json(output_dir / "config.json", {
        "cases_file": str(args.cases.resolve()),
        "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "case_ids": [c["task_id"] for c in cases],
        "model": provider.model,
        "score_mode": args.score_mode,
        "note": "Same cases run in both conditions. evolve group gets feedback injected and skill tools registered; no_evolve does not.",
    })

    rows: list[dict[str, Any]] = []
    with (output_dir / "results.jsonl").open("w", encoding="utf-8") as results:
        for index, case in enumerate(cases):
            conditions = ("no_evolve", "evolve") if index % 2 == 0 else ("evolve", "no_evolve")
            for condition in conditions:
                row = await _run_case(case, condition, provider=provider, output_dir=output_dir, work_dir=work_dir, score_mode=args.score_mode)
                rows.append(row)
                results.write(json.dumps(row, ensure_ascii=False) + "\n")
                results.flush()
                print(f"{case['task_id']} {condition}: {row['status']}, correct={row['correct']}, skills_created={row['skills_created']}")
    summary = summarize(rows)
    _write_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paired BearCode-style Skill evolution ablation evaluation")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-base", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--score-mode", choices=["exact", "normalized"], default="normalized")
    return parser.parse_args()


def main() -> None:
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
