"""答案级评估：不只看检索召回，还看"答得对不对、该不该拒答"。

与 eval_retrieval.py 的分工：
- 检索评估跑的是 retriever，回答"召回准不准"；
- 答案评估跑的是整条 /api/chat，回答"用户拿到的东西对不对"。

指标口径：
- citation_accuracy：可回答题里，引用命中了期望文档的比例
- fact_coverage    ：可回答题里，答案包含期望事实（数字/关键词）的平均比例
- grounded_rate    ：可回答题里，"引用了正确文档 + 事实覆盖率 ≥ 0.5 + 没被转人工"的比例
- refusal_accuracy ：不可回答题里，正确拒答/转人工的比例
- false_answer_rate：不可回答题里，硬答的比例（越低越好）

用法：
    cd backend && PYTHONPATH=. python scripts/eval_answer.py
    cd backend && PYTHONPATH=. python scripts/eval_answer.py --json eval/answer_report.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
DATASET = Path(__file__).resolve().parents[1] / "eval" / "answer_qa.jsonl"
REFUSAL_MARKERS = ("无法确定", "资料未覆盖", "没有足够", "转人工", "无法回答", "不清楚", "没查到")

_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-", "~": "-", "～": "-", "　": " ", " ": ""})


def normalize(text: str) -> str:
    """统一破折号与空白，避免"3–5"和"3-5"被算成不同事实。"""
    return (text or "").translate(_DASHES)

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, token: str | None = None, body: dict | None = None, timeout: float = 120):
    payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=payload, method=method)
    if payload:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with OPENER.open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001
            return exc.code, {}


def login(username: str, password: str) -> str:
    status, data = call("POST", "/api/auth/login", body={"username": username, "password": password})
    if status != 200:
        raise SystemExit(f"登录失败：{status} {data}")
    return data["access_token"]


def load_cases() -> list[dict]:
    rows = []
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="alice")
    parser.add_argument("--password", default="demo1234")
    parser.add_argument("--json", dest="json_path", default="")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    token = login(args.username, args.password)
    cases = load_cases()
    if args.limit:
        cases = cases[: args.limit]

    rows: list[dict] = []
    latencies: list[float] = []

    for index, case in enumerate(cases):
        started = time.perf_counter()
        status, data = call(
            "POST",
            "/api/chat",
            token,
            {"message": case["query"], "thread_id": f"eval-answer-{int(time.time())}-{index}"},
        )
        latency = time.perf_counter() - started
        latencies.append(latency)

        if status != 200:
            rows.append({"query": case["query"], "error": f"{status} {data}"})
            continue

        reply = data.get("reply") or ""
        citations = data.get("citations") or []
        cited_docs = [item.get("doc_id") for item in citations]
        thread_status = data.get("status")
        # "转人工"是正确兜底；但答案里顺带一句"资料未覆盖"不等于拒答，
        # 所以只在"没有引用"时才用文案兜底判拒答
        escalated = thread_status == "waiting_human"
        refused = escalated or (
            not citations and any(marker in reply for marker in REFUSAL_MARKERS)
        )

        row = {
            "query": case["query"],
            "expect": case["expect"],
            "status": thread_status,
            "cited_docs": cited_docs,
            "escalated": escalated,
            "refused": refused,
            "latency": round(latency, 2),
            "reply": reply[:160],
        }

        if case["expect"] == "answer":
            normalized_reply = normalize(reply)
            fact_hits = sum(
                1 for fact in case.get("facts", []) if normalize(fact) in normalized_reply
            )
            total_facts = max(1, len(case.get("facts", [])))
            doc_hit = case["doc"] in cited_docs
            coverage = fact_hits / total_facts
            row.update(
                {
                    "doc_hit": doc_hit,
                    "fact_coverage": round(coverage, 2),
                    "grounded": bool(doc_hit and coverage >= 0.5 and not escalated),
                }
            )
        else:
            row["correct_refusal"] = refused

        rows.append(row)
        flag = "✅" if row.get("grounded") or row.get("correct_refusal") else "❌"
        print(f"{flag} {case['query']:<22} {latency:5.1f}s  {row.get('cited_docs') or '-'}")

    answerable = [r for r in rows if r.get("expect") == "answer" and "error" not in r]
    unanswerable = [r for r in rows if r.get("expect") == "refuse" and "error" not in r]
    errors = [r for r in rows if "error" in r]

    report = {
        "counts": {"total": len(rows), "answerable": len(answerable), "unanswerable": len(unanswerable), "errors": len(errors)},
        "citation_accuracy": round(
            sum(1 for r in answerable if r["doc_hit"]) / len(answerable), 3
        )
        if answerable
        else 0.0,
        "fact_coverage": round(
            statistics.mean(r["fact_coverage"] for r in answerable), 3
        )
        if answerable
        else 0.0,
        "grounded_rate": round(
            sum(1 for r in answerable if r["grounded"]) / len(answerable), 3
        )
        if answerable
        else 0.0,
        "escalation_rate": round(
            sum(1 for r in answerable if r["escalated"]) / len(answerable), 3
        )
        if answerable
        else 0.0,
        "refusal_accuracy": round(
            sum(1 for r in unanswerable if r["correct_refusal"]) / len(unanswerable), 3
        )
        if unanswerable
        else 0.0,
        "false_answer_rate": round(
            sum(1 for r in unanswerable if not r["correct_refusal"]) / len(unanswerable), 3
        )
        if unanswerable
        else 0.0,
        "latency_p50": round(statistics.median(latencies), 2) if latencies else 0.0,
        "latency_p95": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 2) if latencies else 0.0,
        "failures": [r for r in rows if not (r.get("grounded") or r.get("correct_refusal") or "error" in r)],
        "errors": errors,
    }

    print("-" * 64)
    print(f"样本：{report['counts']}")
    print(f"引用命中率 citation_accuracy = {report['citation_accuracy']:.3f}")
    print(f"事实覆盖率 fact_coverage     = {report['fact_coverage']:.3f}")
    print(f"有依据回答率 grounded_rate   = {report['grounded_rate']:.3f}")
    print(f"可回答题被转人工 escalation_rate = {report['escalation_rate']:.3f}")
    print(f"拒答准确率 refusal_accuracy  = {report['refusal_accuracy']:.3f}")
    print(f"硬答率 false_answer_rate     = {report['false_answer_rate']:.3f}")
    print(f"延迟 p50 / p95 = {report['latency_p50']}s / {report['latency_p95']}s")
    print("-" * 64)

    if report["failures"]:
        print("未通过样本：")
        for item in report["failures"]:
            print(f"  · {item['query']:<22} cited={item.get('cited_docs')} facts={item.get('fact_coverage')}")
    if errors:
        print("请求失败：")
        for item in errors:
            print(f"  · {item['query']} -> {item['error']}")

    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入 {target}")


if __name__ == "__main__":
    main()
