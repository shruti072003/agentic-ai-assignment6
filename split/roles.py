"""Assignment 6, Part B: the split's two roles, each a tool surface (slide 11).

- The investigator holds only reads: issues_read, code_search, code_read_file and
  code_run_tests, and a local return tool, return_finding. It reads text that strangers
  wrote, and it can write nothing.
- The writer holds only writes: issues_label, pulls_open and issues_comment. It never
  reads the issue or the code: it writes the finding the coordinator gives it.

A role's surface is a RoleTools: it offers the model only the role's schemas and
refuses any other name in code, before the registry. Every call it lets through goes
to the run's registry, so the hook, the policy and the keys hold for every agent.

Each RoleTools also records the facts of the worker's own calls (calls_used and
searched), exactly as its tool_call lines will show them, so that the return's facts
come from the run, never from what the model says it did.
"""

from __future__ import annotations

import json
import threading

from agent.loop import Bounds, run_loop
from harness.formats import normalize_path, parse_triage_comment
from harness.roles import arg, role_of, tool_for
from harness.schema import errors as schema_errors
from tools import ToolResult

INVESTIGATOR_ROLES = ("read_issue", "search", "read_file", "run_tests")
WRITER_ROLES = ("apply_label", "open_pr", "comment")
RETURN_TOOL = "return_finding"
MAX_BAD_RETURNS = 3          # a worker that breaks the contract this often is stopped, never repaired


class WorkerStopped(RuntimeError):
    """The coordinator stopped this worker (a cycle of waits, or a contract broken too often)."""


def refused(text: str) -> ToolResult:
    return ToolResult(text, True, 0.0)


def accepted(text: str) -> ToolResult:
    return ToolResult(text, False, 0.0)


class StoppableClient:
    """An agent's client that the coordinator can stop: the next model call raises."""

    def __init__(self, client):
        self.client = client
        self.stop_reason: str | None = None

    def stop(self, reason: str) -> None:
        self.stop_reason = reason

    def create(self, **kwargs):
        if self.stop_reason:
            raise WorkerStopped(self.stop_reason)
        return self.client.create(**kwargs)

    @property
    def total_tokens(self) -> int:
        return self.client.total_tokens

    def __getattr__(self, attr):
        return getattr(self.client, attr)


class RoleTools:
    """One role's tool surface: the registry's schemas for `roles` only, plus local tools.

    `local` maps a local tool's name to (schema, handler); a local tool never reaches the
    registry. `guard(name, args)` returns a reason to refuse a call, or None; it runs
    before the registry. A name outside the surface is refused here, so it can never
    count as a protocol error against the run."""

    def __init__(self, registry, roles: tuple[str, ...], local: dict | None = None, guard=None,
                 after=None):
        self.registry = registry
        self.allowed = {tool_for(r) for r in roles}
        self.local = dict(local or {})
        self.guard, self.after = guard, after
        self.calls: list[tuple[str, dict]] = []           # registry-role calls, as traced
        self.stopped: str | None = None
        self._lock = threading.Lock()

    @property
    def schemas(self) -> list[dict]:
        mine = [s for s in self.registry.schemas if s["name"] in self.allowed]
        return mine + [schema for schema, _ in self.local.values()]

    def call(self, name: str, args: dict, **kwargs):
        if name in self.local:
            return self.local[name][1](args)
        if self.stopped:
            result = refused(f"{self.stopped} Stop now: call no more tools.")
        elif name not in self.allowed:
            result = refused(f"{name} is not one of this role's tools. This role holds only "
                             f"{', '.join(sorted(self.allowed | set(self.local)))}.")
        else:
            reason = self.guard(name, args) if self.guard else None
            result = refused(reason) if reason else self.registry.call(name, args, **kwargs)
            if not reason and self.after:
                self.after(name, args, result)
        if role_of(name):                              # a course tool: its tool_call line counts
            with self._lock:
                self.calls.append((name, dict(args) if isinstance(args, dict) else {}))
        return result

    def facts(self) -> tuple[int, list[str]]:
        """(calls_used, searched): the number of this worker's calls to the course's tools,
        and every search query and read path among them, each once, in order."""
        query, path = arg("search", "query"), arg("read_file", "path")
        searched: list[str] = []
        with self._lock:
            calls = list(self.calls)
        for name, a in calls:
            role = role_of(name)
            value = a.get(query) if role == "search" else a.get(path) if role == "read_file" else None
            if value is not None and str(value) not in searched:
                searched.append(str(value))
        return len(calls), searched


# -- the investigator ------------------------------------------------------------------------------

def return_tool_schema(contract: dict) -> dict:
    """The return tool the model calls: the contract's fields the model may fill. searched
    and calls_used are left out: code writes them, from the worker's own calls."""
    props = contract["properties"]
    result = props["result"]
    return {
        "name": RETURN_TOOL,
        "strict": True,
        "description": (
            "Return your finding to the coordinator, once, when you have decided, and then stop. "
            "The coordinator acts only on what you return here, and a writer that never sees the "
            "issue or the code writes the label, the pull request and the comment from it. So "
            "say what you did not settle: an empty could_not_determine means you settled "
            "everything. The harness adds what you searched and how many calls you made from "
            "your own tool calls; do not report them."),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["result", "status", "not_searched", "could_not_determine"],
            "properties": {
                "result": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["issue", "label", "file", "evidence"],
                    "properties": {
                        "issue": {"type": "integer", "description": "The issue's number, such as 4410."},
                        "label": {"type": "string", "enum": [v for v in result["properties"]["label"]["enum"]
                                                             if v is not None],
                                  "description": "bug, feature, question or docs."},
                        "file": {"type": "string",
                                 "description": "The one file most responsible, relative to the repository "
                                                "root, such as src/config.ts; none for a question."},
                        "evidence": {"type": "string",
                                     "description": "One or two sentences: what in that file is wrong or must "
                                                    "change, and why it explains the issue. The pull request is "
                                                    "written from this."},
                    },
                },
                "status": {"type": "string", "enum": list(props["status"]["enum"]),
                           "description": "found: you read the file and it explains the issue. candidate: your "
                                          "best answer, not confirmed. out_of_budget: you ran out of steps "
                                          "before you could decide."},
                "not_searched": {"type": "array", "items": {"type": "string"},
                                 "description": "Files or places you know you did not look at."},
                "could_not_determine": {"type": "array", "items": {"type": "string"},
                                        "description": "What you could not settle; an empty list when nothing."},
            },
        },
    }


class Investigation:
    """One investigator's loop on one issue, and the return the coordinator receives."""

    def __init__(self, ctx, coordinator, issue: int, agent_id: str, contract: dict, prompt: str,
                 max_tokens: int, max_steps: int = 12, wall_clock_s: float = 180.0):
        self.ctx, self.coordinator, self.issue, self.agent_id = ctx, coordinator, int(issue), agent_id
        self.contract_schema, self.prompt = contract, prompt
        self.bounds = Bounds(max_steps, int(max_tokens), wall_clock_s)
        self.worker = ctx.agent(agent_id, parent=coordinator.id)
        self.client = StoppableClient(self.worker.client)
        self.tools = RoleTools(ctx.tools, INVESTIGATOR_ROLES,
                               local={RETURN_TOOL: (return_tool_schema(contract), self._on_return)})
        self.returned: dict | None = None
        self.bad_returns = 0

    def brief(self) -> str:
        return (f"Investigate issue #{self.issue} (call the tools with issue-{self.issue}). Find the one file "
                f"most responsible and its label, then call {RETURN_TOOL} once and stop.")

    def contract_from(self, given: dict) -> dict:
        """The return as the coordinator receives it: the model's fields as it sent them,
        never converted, and the facts of the run from code."""
        calls, searched = self.tools.facts()
        out = dict(given) if isinstance(given, dict) else {"result": given}
        out.pop("searched", None)
        out.pop("calls_used", None)
        out["searched"], out["calls_used"] = searched, calls
        return out

    def record(self, contract: dict) -> list[str]:
        errs = schema_errors(contract, self.contract_schema)
        self.coordinator.trace.event("worker_return", worker=self.agent_id, contract=contract,
                                     valid=not errs, errors=errs)
        return errs

    def _on_return(self, given: dict) -> ToolResult:
        if self.returned is not None:
            return refused("You have already returned your finding. Stop now: call no more tools.")
        contract = self.contract_from(given)
        errs = self.record(contract)        # valid means: fits the schema, and nothing else
        if errs:
            self.bad_returns += 1
            if self.bad_returns >= MAX_BAD_RETURNS:
                self.tools.stopped = f"Your return broke the contract {self.bad_returns} times, and is rejected."
                self.client.stop(self.tools.stopped)
                return refused(self.tools.stopped)
            return refused("Your return was rejected; it breaks the contract: " + "; ".join(errs[:4])
                           + f". Fix those fields and call {RETURN_TOOL} again.")
        self.returned = contract
        self.tools.stopped = "You have returned your finding."
        return accepted("Received. Your part is done: stop now, without calling any tool.")

    def run(self) -> dict:
        try:
            run_loop(self.client, self.tools, self.prompt, self.brief(), self.bounds, self.worker.trace)
        except WorkerStopped as exc:
            self.worker.trace.end("stopped", detail=str(exc))
        if self.returned is not None:
            return self.returned
        # No valid return: the coordinator records what it did receive, from code, so that it
        # can tell "nothing exists" from "the worker did not finish". It is never acted on.
        calls, searched = self.tools.facts()
        made = {"result": {"issue": self.issue, "label": None, "file": None, "evidence": ""},
                "status": "out_of_budget", "searched": searched, "not_searched": [],
                "calls_used": calls,
                "could_not_determine": ["label", "file",
                                        f"the investigator stopped without a valid return "
                                        f"({self.bad_returns} rejected)"]}
        self.record(made)
        return made


def usable(contract: dict | None, issue: int) -> bool:
    """A return the writer may act on: found or candidate, with a label and a file, for this issue."""
    if not contract or contract.get("status") not in ("found", "candidate"):
        return False
    result = contract.get("result") or {}
    return result.get("issue") == int(issue) and bool(result.get("label")) and bool(result.get("file"))


# -- the writer --------------------------------------------------------------------------------------

class WriteGuard:
    """The writer's writes, checked in code before the registry, against the finding it was
    briefed with and the version of that finding it read (compare-and-set).

    - every call names this issue;
    - the label is the finding's; a pull request only when the file is not none, naming
      the file; the comment's label and file lines are the finding's;
    - the order: the label, then the pull request, then the comment, last and once;
    - every write checks that the issue's record on the board is still at the version the
      writer read; the comment, the one write that cannot be taken back, is a
      compare-and-set on it, so a second comment, or one from a stale brief, is refused."""

    def __init__(self, board, key: str, version: int, agent_id: str, issue: int, label: str, file: str):
        self.board, self.key, self.version, self.agent = board, key, version, agent_id
        self.issue, self.label, self.file = int(issue), label, normalize_path(file) or "none"
        self.labelled = self.pr_opened = self.commented = False
        self._prior: str | None = None

    def _issue_ok(self, args: dict) -> str | None:
        from tools import ISSUE
        value = str((args or {}).get(arg("comment", "number"), ""))
        m = ISSUE.match(value.strip())
        if not m or int(m.group(1)) != self.issue:
            return f"This writer writes only issue-{self.issue}; the call names {value!r}."
        return None

    def __call__(self, name: str, args: dict) -> str | None:
        role = role_of(name)
        problem = self._issue_ok(args)
        if problem:
            return problem
        if self.board.version(self.key) != self.version:
            return (f"The finding for issue-{self.issue} changed after you were briefed (version "
                    f"{self.version}, now {self.board.version(self.key)}). Your write is refused; stop.")
        if role == "apply_label":
            given = str(args.get(arg("apply_label", "label"), "")).strip().lower()
            if given != self.label:
                return f"The finding's label is {self.label}, not {given!r}. Apply {self.label}."
            return None
        if role == "open_pr":
            if self.file == "none":
                return "The finding's file is none: open no pull request. Post the comment next."
            if not self.labelled:
                return f"Apply the label {self.label} first, with issues_label; then open the pull request."
            text = f"{args.get(arg('open_pr', 'title'), '')}\n{args.get(arg('open_pr', 'body'), '')}".lower()
            base = self.file.rsplit("/", 1)[-1].lower()
            if self.file.lower() not in text and base not in text:
                return f"The pull request must name the file {self.file} in its title or body."
            return None
        if role == "comment":
            if self.commented:
                return "This issue already has its comment. Stop now."
            if not self.labelled or (self.file != "none" and not self.pr_opened):
                todo = "the label" if not self.labelled else "the pull request"
                return f"The comment comes last: write {todo} first."
            label, file = parse_triage_comment(str(args.get(arg("comment", "body"), "")))
            if label != self.label or file != self.file:
                return (f"The comment's first two lines must be exactly 'label: {self.label}' and "
                        f"'file: {self.file}'; they say label {label!r} and file {file!r}.")
            prior, _ = self.board.read(self.agent, self.key)
            if not self.board.write(self.agent, self.key, json.dumps({"state": "commenting"}), self.version):
                return f"Another write changed issue-{self.issue}'s record first. Your comment is refused; stop."
            self._prior, self.version = prior, self.version + 1
            return None
        return None

    def after(self, name: str, args: dict, result) -> None:
        role = role_of(name)
        ok = not result.is_error
        if role == "apply_label" and ok:
            self.labelled = True
        elif role == "open_pr" and ok:
            self.pr_opened = True
        elif role == "comment":
            if ok:
                self.commented = True
                self.board.write(self.agent, self.key, json.dumps({"state": "commented"}), self.version)
                self.version += 1
            else:                      # the comment did not land: give the record back, at a new version
                self.board.write(self.agent, self.key, self._prior or "", self.version)
                self.version += 1


class Writing:
    """One writer's loop on one issue, from a finding it cannot check."""

    def __init__(self, ctx, coordinator, issue: int, agent_id: str, finding: dict, board, key: str,
                 version: int, prompt: str, max_tokens: int, max_steps: int = 6, wall_clock_s: float = 120.0):
        from ways.agent import Disclosing, disclosure_line
        self.issue, self.finding, self.prompt = int(issue), finding, prompt
        self.bounds = Bounds(max_steps, int(max_tokens), wall_clock_s)
        self.worker = ctx.agent(agent_id, parent=coordinator.id)
        self.client = StoppableClient(self.worker.client)
        self.guard = WriteGuard(board, key, version, agent_id, issue, finding["label"], finding["file"])
        self.surface = RoleTools(ctx.tools, WRITER_ROLES, guard=self.guard, after=self.guard.after)
        # The disclosure line goes on every comment here, in code, before the registry:
        # ways.agent.with_disclosure is the one function every comment passes.
        self.tools = Disclosing(self.surface, disclosure_line())

    def brief(self) -> str:
        f = self.finding
        file = normalize_path(f["file"]) or "none"
        lines = [f"Write the triage for issue #{self.issue} (call the tools with issue-{self.issue}).",
                 "", "The checked finding:", f"label: {f['label']}", f"file: {file}",
                 f"What to change: {f.get('evidence') or '(not given)'}"]
        if f.get("could_not_determine"):
            lines.append(f"Not settled by the investigation: {'; '.join(map(str, f['could_not_determine']))}")
        lines += ["", "Steps: issues_label, then " + ("pulls_open, then " if file != "none" else "")
                  + "issues_comment last. Then stop."]
        return "\n".join(lines)

    def run(self) -> None:
        try:
            run_loop(self.client, self.tools, self.prompt, self.brief(), self.bounds, self.worker.trace)
        except WorkerStopped as exc:
            self.worker.trace.end("stopped", detail=str(exc))
