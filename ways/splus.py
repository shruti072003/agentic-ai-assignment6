"""Assignment 6, Part C: arm S-plus, one agent given what the split actually spent.

Slide 16's fair rival. `run.py --match split` sets ctx.budget to arm M's mean spend on
this task. One agent spends it as one agent can: further attempts with read-only
tools, each in a fresh context, one after another (nothing runs at the same time, and
there are no roles), until it has spent about its budget. Then it chooses an answer by
something it can compute without the known answers, a vote over its attempts, and
writes only that answer, once: the label, the draft pull request when the file is not
none, and the comment last, with the disclosure line added in code.

The budget: each issue gets an equal share of what is left, recomputed before each
issue, so an issue that needed less leaves more for the next. Within an issue, a new
attempt starts only while the issue has spent under ATTEMPT_UNTIL of its share and an
attempt of the usual cost still fits under OVERSHOOT of it. Each attempt's own bound
is what is left of the issue's share (up to OVERSHOOT); a note in its tool results
tells it to return once it has used NOTE_AT of its bound.

The attempts are ctx.agent's (attempt-<k>-<n>, parent splus), so each loop has its own
client and trace; the writes are the root agent's own calls (splus).
"""

from __future__ import annotations

import json
from collections import Counter

from harness.formats import normalize_path
from harness.roles import arg, tool_for
from split.roles import INVESTIGATOR_ROLES, RETURN_TOOL, Investigation, RoleTools, return_tool_schema, usable
from tools import ToolResult
from ways.agent import disclosure_line, with_disclosure
from ways.split import load_contract

ROOT_AGENT = "splus"
MAX_ATTEMPTS = 8            # per issue: a cap so that a cheap issue cannot loop forever
ATTEMPT_UNTIL = 0.85        # start another attempt while the issue has spent less than this of its share
OVERSHOOT = 1.15            # ... and an attempt of the usual cost still fits under this of it
NOTE_AT = 0.6               # an attempt is told to return once it has used this much of its bound
MIN_ATTEMPT_TOKENS = 8000   # an attempt with less than this has no room to read and return


class NotedTools(RoleTools):
    """An attempt's read-only surface; past NOTE_AT of its bound, each result says to return now."""

    def __init__(self, *args, client=None, bound: int = 0, **kwargs):
        super().__init__(*args, **kwargs)
        self.client, self.bound = client, bound

    def call(self, name, args, **kwargs):
        result = super().call(name, args, **kwargs)
        if name in self.local or self.client is None or not self.bound:
            return result
        used = self.client.total_tokens
        if used >= NOTE_AT * self.bound:
            note = (f"\n\n[Note from the harness: this attempt has used {used:,} of its {self.bound:,} tokens. "
                    f"Call {RETURN_TOOL} with your best answer now.]")
            return ToolResult(result.content + note, result.is_error, result.latency_s, result.key)
        return result


class Attempt(Investigation):
    """One attempt: the investigator's read-only loop and return tool, under one agent."""

    def __init__(self, ctx, root, issue, agent_id, contract, prompt, max_tokens, number):
        super().__init__(ctx, root, issue, agent_id, contract, prompt, max_tokens=max_tokens)
        self.number = number
        self.tools = NotedTools(ctx.tools, INVESTIGATOR_ROLES,
                                local={RETURN_TOOL: (return_tool_schema(contract), self._on_return)},
                                client=self.client, bound=int(max_tokens))

    def record(self, contract: dict) -> list[str]:
        from harness.schema import errors
        errs = errors(contract, self.contract_schema)
        self.coordinator.trace.event("attempt_return", attempt=self.agent_id, number=self.number,
                                     issue=self.issue, contract=contract, valid=not errs, errors=errs,
                                     tokens=self.client.total_tokens)
        return errs

    def brief(self) -> str:
        return (f"Triage issue #{self.issue} (call the tools with issue-{self.issue}): find the one file most "
                f"responsible and its label, then call {RETURN_TOOL} once and stop.")


def vote(answers: list[dict]) -> dict | None:
    """The answer most attempts gave, as (label, file); a tie goes to the answer with more
    found statuses, then to the earliest. None when no attempt gave a usable answer."""
    if not answers:
        return None
    key = lambda a: (a["result"]["label"], normalize_path(a["result"]["file"]) or "none")
    counts = Counter(key(a) for a in answers)
    found = Counter(key(a) for a in answers if a["status"] == "found")
    order = {k: i for i, k in reversed(list(enumerate(key(a) for a in answers)))}
    best = max(counts, key=lambda k: (counts[k], found[k], -order[k]))
    support = [a for a in answers if key(a) == best]
    return {"label": best[0], "file": best[1], "votes": counts[best], "of": len(answers),
            "evidence": next((a["result"].get("evidence") for a in support if a["status"] == "found"),
                             support[0]["result"].get("evidence") or "")}


class OneAgent:
    def __init__(self, ctx):
        self.ctx = ctx
        self.root = ctx.agent(ROOT_AGENT)
        self.contract = load_contract()
        self.prompt = ctx.prompt("splus_attempt.md")
        self.issues = list(ctx.issues)
        from agent.loop import load_bounds
        self.budget = int(ctx.budget) if ctx.budget else load_bounds().max_tokens * len(self.issues)
        self.spent = 0                       # every attempt's tokens so far, this run

    def triage(self, issue: int, share: int) -> dict | None:
        answers, costs, spent = [], [], 0
        for k in range(1, MAX_ATTEMPTS + 1):
            usual = sum(costs) / len(costs) if costs else 0
            room = int(OVERSHOOT * share) - spent
            if k > 1 and (spent >= ATTEMPT_UNTIL * share or spent + usual > OVERSHOOT * share):
                break
            bound = min(share - spent if k == 1 else room, room)
            if bound < MIN_ATTEMPT_TOKENS:
                break
            attempt = Attempt(self.ctx, self.root, issue, f"attempt-{k}-{issue}", self.contract, self.prompt,
                              max_tokens=bound, number=k)
            returned = attempt.run()
            cost = attempt.client.total_tokens
            costs.append(cost)
            spent += cost
            self.spent += cost
            if usable(returned, issue):
                answers.append(returned)
        chosen = vote(answers)
        self.root.trace.event("vote", issue=issue, attempts=len(costs), usable=len(answers), share=share,
                              spent=spent, chosen=chosen)
        return chosen

    def write(self, issue: int, chosen: dict) -> None:
        """The chosen answer, written once: the label, the pull request, the comment last."""
        number = f"issue-{issue}"
        label, file = chosen["label"], chosen["file"]
        evidence = str(chosen.get("evidence") or "").strip()
        self.root.call_tool(0, tool_for("apply_label"), {arg("apply_label", "number"): number,
                                                         arg("apply_label", "label"): label})
        if file != "none":
            self.root.call_tool(0, tool_for("open_pr"), {
                arg("open_pr", "number"): number,
                arg("open_pr", "branch"): f"repomind/issue-{issue}",
                arg("open_pr", "title"): f"Issue {issue}: change {file}"[:120],
                arg("open_pr", "body"): f"File: {file}\n\n{evidence[:3000]}"})
        body = f"label: {label}\nfile: {file}\n\n{evidence[:1500]}".rstrip()
        self.root.call_tool(0, tool_for("comment"), {arg("comment", "number"): number,
                                                     arg("comment", "body"): with_disclosure(body, disclosure_line())})

    def run(self) -> None:
        self.root.trace.event("budget", budget=self.budget, ceiling=self.ctx.ceiling,
                              matched_to=getattr(self.ctx, "matched_to", None), issues=self.issues)
        chosen = {}
        for i, issue in enumerate(self.issues):            # one after another: nothing runs at the same time
            share = max(0, (self.budget - self.spent) // (len(self.issues) - i))
            chosen[issue] = self.triage(issue, share)
        for issue in self.issues:                           # the writes, once each, after every choice
            if chosen[issue]:
                self.write(issue, chosen[issue])
        self.ctx.trace.end("completed", detail=f"spent {self.spent:,} of {self.budget:,}; "
                                               f"{sum(1 for c in chosen.values() if c)} of {len(self.issues)} written")


def run(ctx) -> None:
    OneAgent(ctx).run()


__all__ = ["run", "vote", "OneAgent"]
