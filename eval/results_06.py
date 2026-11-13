#!/usr/bin/env python3
"""Assignment 6, Part C: every measured run's outcome, from the environment only.

    python eval/results_06.py            # writes results/runs_06.jsonl and prints the table
    python eval/results_06.py --json     # also prints the arms, multiplier and constants for reports/LAB_06.json

One line per finished measured run (checks/FORMATS.md, section 14.7): a real backend,
on a task of fixtures/tasks6, of the agent, split or splus way. A run is finished when
its last line is run_end and no end line says quota or rate_limited. `passed` and
`parts` come from eval/check_task.py, which reads the tracker; `tokens` from
run_end.totals.total_tokens; `wall_s` from run_end.elapsed_s. No agent's message is
ever read for a pass.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.check_task import check_task  # noqa: E402
from harness import LAB  # noqa: E402
from harness.formats import ARMS, UNFINISHED  # noqa: E402
from harness.runner import is_free, is_trace  # noqa: E402
from harness.tasks import load_tasks  # noqa: E402
from harness.trace import read_trace  # noqa: E402

TASK_DIR = ROOT / "fixtures" / "tasks6"
OUT = ROOT / "results" / "runs_06.jsonl"
SWEEPS = 3


class Tracker:
    """The tracker's database and its archives read as one, for check_task's three reads,
    so a `make reset` between arms loses no run."""

    TABLES = ("comments", "prs", "labels")

    def __init__(self, root: Path = ROOT):
        self.rows = {t: {} for t in self.TABLES}
        files = [root / "tracker" / "tracker.db", *sorted((root / "tracker" / "archive").glob("*.db"))]
        for path in (p for p in files if p.exists()):
            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            try:
                for table in self.TABLES:
                    try:
                        for row in db.execute(f"SELECT * FROM {table} ORDER BY id"):
                            self.rows[table].setdefault(row["run_id"], []).append(dict(row))
                    except sqlite3.OperationalError:
                        continue
            finally:
                db.close()

    def comments(self, run_id, issue=None):
        return [r for r in self.rows["comments"].get(run_id, []) if issue is None or int(r["issue"]) == int(issue)]

    def labels(self, run_id, issue=None, active=True):
        return [r for r in self.rows["labels"].get(run_id, [])
                if (issue is None or int(r["issue"]) == int(issue)) and (not active or r.get("removed_at") is None)]

    def prs(self, run_id, branch=None, state="open"):
        return [r for r in self.rows["prs"].get(run_id, [])
                if (branch is None or r["branch"] == branch) and (state is None or r.get("state") == state)]


def measured_runs(tasks: dict) -> list[tuple[dict, dict]]:
    """(run_start, run_end) of every finished measured run, in file order."""
    out = []
    for path in sorted((ROOT / "runs").glob("*.jsonl")):
        if not is_trace(path):
            continue
        try:
            records = read_trace(path)
        except json.JSONDecodeError:
            continue
        start = records[0] if records and records[0].get("type") == "run_start" else None
        if not start or start.get("lab") != LAB or start.get("task") not in tasks or start.get("way") not in ARMS:
            continue
        if is_free(start.get("backend") or ""):
            continue
        ends = {r.get("terminal_reason") for r in records if r.get("type") == "end"}
        if records[-1].get("type") != "run_end" or ends & set(UNFINISHED):
            continue
        out.append((start, records[-1]))
    return out


def rows(tasks: dict) -> list[dict]:
    tracker = Tracker()
    out = []
    for start, end in measured_runs(tasks):
        task = tasks[start["task"]]
        checked = check_task(start["run_id"], task, tracker=tracker)
        out.append({"run_id": start["run_id"], "arm": ARMS[start["way"]], "way": start["way"],
                    "task": task.id, "kind": task.kind, "sweep": start.get("sweep"),
                    "passed": bool(checked["passed"]),
                    "parts": {str(k): bool(v) for k, v in checked["parts"].items()},
                    "tokens": int((end.get("totals") or {}).get("total_tokens") or 0),
                    "wall_s": float(end.get("elapsed_s") or 0.0),
                    "budget": start.get("budget"), "ceiling": start.get("ceiling")})
    return out


def mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def summarize(lines: list[dict]) -> dict:
    """The arms, as reports/LAB_06.json holds them (section 14.8)."""
    arms = {}
    for arm in ("S", "M", "S_plus"):
        arms[arm] = {}
        for kind in ("single", "batch"):
            mine = [r for r in lines if r["arm"] == arm and r["kind"] == kind]
            per_task, sweeps = {}, {}
            for r in mine:
                per_task[r["task"]] = per_task.get(r["task"], 0) + int(r["passed"])
                sweeps[r["sweep"]] = sweeps.get(r["sweep"], 0) + int(r["passed"])
            arms[arm][kind] = {
                "runs": len(mine), "passes": sum(int(r["passed"]) for r in mine),
                "per_task": dict(sorted(per_task.items())),
                "sweep_min": min(sweeps.values()) if sweeps else 0,
                "sweep_max": max(sweeps.values()) if sweeps else 0,
                "all_three": sum(1 for v in per_task.values() if v >= SWEEPS),
                "mean_tokens": round(mean(r["tokens"] for r in mine)),
                "mean_wall_s": round(mean(r["wall_s"] for r in mine), 1)}
    return arms


def constants() -> dict | None:
    seen = set()
    for path in sorted((ROOT / "runs").glob("*.jsonl")):
        if not is_trace(path):
            continue
        start = read_trace(path)[0]
        if start.get("lab") == LAB and start.get("way") in ARMS and not is_free(start.get("backend") or "") \
                and start.get("task") in {t.id for t in load_tasks(TASK_DIR)}:
            seen.add(json.dumps(start.get("constants"), sort_keys=True))
    if len(seen) > 1:
        print(f"WARNING: the runs carry {len(seen)} different sets of constants", file=sys.stderr)
    return json.loads(next(iter(seen))) if len(seen) == 1 else None


def per_task_spend(lines: list[dict]) -> dict:
    """Mean tokens per task and arm: whether S-plus's spend matched M's, task by task."""
    out = {}
    for r in lines:
        out.setdefault(r["task"], {}).setdefault(r["arm"], []).append(r["tokens"])
    return {t: {a: round(mean(v)) for a, v in arms.items()} for t, arms in sorted(out.items())}


def main(argv: list[str]) -> int:
    tasks = {t.id: t for t in load_tasks(TASK_DIR)}
    lines = rows(tasks)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as fh:
        for r in lines:
            fh.write(json.dumps(r) + "\n")
    arms = summarize(lines)
    print(f"wrote {len(lines)} runs to {OUT.relative_to(ROOT)}\n")
    print(f"{'arm':<7}{'kind':<8}{'passes':>8}{'sweeps':>9}{'all 3':>7}{'mean tok':>11}{'mean s':>8}")
    for arm, kinds in arms.items():
        for kind, s in kinds.items():
            if s["runs"]:
                print(f"{arm:<7}{kind:<8}{s['passes']:>4}/{s['runs']:<3}{s['sweep_min']:>5}-{s['sweep_max']:<3}"
                      f"{s['all_three']:>6}{s['mean_tokens']:>11,}{s['mean_wall_s']:>8}")
    multiplier = {k: (round(arms["M"][k]["mean_tokens"] / arms["S"][k]["mean_tokens"], 2)
                      if arms["S"][k]["mean_tokens"] and arms["M"][k]["mean_tokens"] else None)
                  for k in ("single", "batch")}
    print(f"\nmultiplier (M over S): {multiplier}")
    print("\nmean tokens per task, by arm:")
    for task, by in per_task_spend(lines).items():
        print(f"  {task:<10} " + "  ".join(f"{a} {by[a]:>9,}" for a in ("S", "M", "S_plus") if a in by))
    if "--json" in argv:
        print("\n" + json.dumps({"constants": constants(), "arms": arms, "multiplier": multiplier}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
