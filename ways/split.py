"""Assignment 6, Part B: arm M, the split. On lab-06-split only; never merged.

A coordinator in code (no model) and two roles, split by tool surface (split/roles.py):

    coordinator ──brief──▶ investigator-<n>   reads only; returns a finding (the contract)
                ◀─return──                    validated against split/contract.schema.json
                ──finding─▶ writer-<n>        writes only: label, draft PR, comment last
    coordinator ──reads the tracker──▶ the combined check

Each issue is one pipeline: its investigator, then (if the return is not usable) one
fresh investigator from the reserve, then its writer. On a batch, the five pipelines
run at the same time, one thread each. Nothing passes between issues.

The five checks (slide 47), and where each is:
  1. the return contract, validated on every return: split/roles.py, Investigation.record
  2. deadlock detection: every wait goes to a WaitGraph (split/guards.py); a cycle stops a worker
  3. the combined result, read from the tracker after the last write: split/guards.check_combined
  4. compare-and-set on shared writes: each issue's record on a SharedState board, which the
     writer's every write checks and its comment sets (split/roles.WriteGuard); the
     registry's idempotency keys stay under it
  5. one trace: every agent comes from ctx.agent, so its lines carry its ID and its parent's

The budget: ctx.budget is the whole run's; each issue gets an equal share, and within
it the investigator gets INVESTIGATOR_SHARE, the writer WRITER_SHARE, and the rest is
held back for one fresh investigator when the first one's return is not usable. Every
agent's client counts against the run's one ceiling (ctx.ceiling).
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.loop import load_bounds
from harness.backends import DailyQuotaReached, RateLimited
from harness.client import BudgetExceeded
from harness.tasks import end_state
from split.guards import COORDINATOR, SharedState, WaitGraph, check_combined
from split.roles import Investigation, Writing, usable

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "split" / "contract.schema.json"
INVESTIGATOR_SHARE = 0.55
WRITER_SHARE = 0.15
RESERVE_SHARE = 1.0 - INVESTIGATOR_SHARE - WRITER_SHARE    # one fresh investigator, if needed
# What ends a run for the runner, in the order a worker's exception is re-raised.
INFRASTRUCTURE = (DailyQuotaReached, RateLimited, BudgetExceeded)


def load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text())


class Coordinator:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lead = ctx.agent(COORDINATOR)
        self.contract = load_contract()
        self.graph = WaitGraph(COORDINATOR)
        self.issues = list(ctx.issues)
        self.board = SharedState({self.key(n): json.dumps({"state": "open"}) for n in self.issues})
        per_issue = (ctx.budget // len(self.issues)) if ctx.budget else load_bounds().max_tokens
        self.shares = {"per_issue": int(per_issue),
                       "investigator": int(per_issue * INVESTIGATOR_SHARE),
                       "writer": int(per_issue * WRITER_SHARE),
                       "reserve": int(per_issue * RESERVE_SHARE)}
        self.workers: dict[str, object] = {}       # agent ID -> its Investigation or Writing
        self.investigator_prompt = ctx.prompt("split_investigator.md")
        self.writer_prompt = ctx.prompt("split_writer.md")

    @staticmethod
    def key(issue: int) -> str:
        return f"issue-{int(issue)}"

    # -- waits: every wait is traced and given to the graph, which may stop a worker -----------
    def wait(self, on: str, what: str, turn: int) -> None:
        self.lead.trace.event("wait", waiter=COORDINATOR, on=on, what=what, turn=turn)
        cycle = self.graph.wait(COORDINATOR, on, what)
        if cycle:
            victim = self.graph.victim(cycle)
            self.lead.trace.event("worker_stopped", worker=victim, reason=f"a cycle of waits: {' -> '.join(cycle)}")
            worker = self.workers.get(victim)
            if worker is not None:
                worker.client.stop(f"stopped by the coordinator: a cycle of waits {cycle}")

    def done_waiting(self, on: str) -> None:
        self.graph.release(COORDINATOR, on)

    # -- one issue's pipeline ---------------------------------------------------------------------
    def investigate(self, issue: int, agent_id: str, tokens: int, turn: int) -> dict:
        job = Investigation(self.ctx, self.lead, issue, agent_id, self.contract, self.investigator_prompt,
                            max_tokens=tokens)
        self.workers[agent_id] = job
        self.wait(agent_id, "a finding", turn)
        try:
            return job.run()
        finally:
            self.done_waiting(agent_id)

    def pipeline(self, issue: int) -> dict:
        returned = self.investigate(issue, f"investigator-{issue}", self.shares["investigator"], 0)
        if not usable(returned, issue) and self.shares["reserve"] > 0:
            self.lead.trace.event("retry", issue=issue, reason=f"the return was {returned.get('status')!r} "
                                  "with no usable finding; one fresh investigator, from the reserve")
            returned = self.investigate(issue, f"investigator-{issue}-r2", self.shares["reserve"], 1)
        if not usable(returned, issue):
            self.lead.trace.event("issue_unwritten", issue=issue,
                                  reason="no usable finding: nothing is written for this issue")
            return {"issue": issue, "written": False}
        finding = {**returned["result"], "could_not_determine": returned.get("could_not_determine") or [],
                   "status": returned["status"]}
        key = self.key(issue)
        _, version = self.board.read(COORDINATOR, key)
        if not self.board.write(COORDINATOR, key, json.dumps(finding), version):
            self.lead.trace.event("issue_unwritten", issue=issue, reason="the issue's record changed first")
            return {"issue": issue, "written": False}
        writer_id = f"writer-{issue}"
        job = Writing(self.ctx, self.lead, issue, writer_id, finding, self.board, key, version + 1,
                      self.writer_prompt, max_tokens=self.shares["writer"])
        self.workers[writer_id] = job
        self.wait(writer_id, "the label, the pull request and the comment", 2)
        try:
            job.run()
        finally:
            self.done_waiting(writer_id)
        return {"issue": issue, "written": True}

    # -- the run ---------------------------------------------------------------------------------------
    def run(self) -> None:
        self.lead.trace.event("budget_shares", budget=self.ctx.budget, ceiling=self.ctx.ceiling,
                              issues=self.issues, **self.shares)
        failures: list[BaseException] = []
        with ThreadPoolExecutor(max_workers=len(self.issues), thread_name_prefix="split") as pool:
            futures = [pool.submit(self.pipeline, n) for n in self.issues]
            for f in futures:
                try:
                    f.result()
                except BaseException as exc:          # kept, and re-raised once every issue has stopped
                    failures.append(exc)
        stopped_by_vendor = next((e for e in failures if isinstance(e, (DailyQuotaReached, RateLimited))), None)
        if stopped_by_vendor is not None:
            raise stopped_by_vendor                   # the runner marks the run unfinished; it is redone
        # The combined result, from the tracker, never from what any worker said.
        state = end_state(self.ctx.tracker, self.ctx.run_id, self.ctx.task)
        violations = check_combined(self.ctx.task, state)
        self.lead.trace.event("combined_check", passed=not violations, violations=violations)
        if failures:
            raise next((e for e in failures if isinstance(e, BudgetExceeded)), failures[0])
        self.ctx.trace.end("completed", detail="combined check " + ("passed" if not violations else
                                                                     f"failed: {len(violations)} violations"))


def run(ctx) -> None:
    Coordinator(ctx).run()


__all__ = ["run", "Coordinator", "load_contract", "INVESTIGATOR_SHARE", "WRITER_SHARE", "RESERVE_SHARE"]
