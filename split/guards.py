"""Assignment 6, Part B: three of the five checks (checks/FORMATS.md, section 14.6).

None of these coordinates anything. Each one makes a failure between agents visible,
or refuses it, in code, outside any model:

- WaitGraph: every wait on another agent is an edge; a wait that closes a cycle is a
  deadlock (slides 35 and 38). The graph returns the cycle, and names one worker to
  stop. No agent can see its own deadlock; the code that runs them can.
- SharedState: compare-and-set on shared state (slides 34 and 37). A write names the
  version it read, and is refused if the state has changed since. This refuses the
  lost update: a write made from a stale read.
- check_combined: the combined result, read from the tracker's end state, tested
  against rules set before the run (slides 36 and 45). It needs no answer key. It is
  the only one of the five that catches silent divergence, where every agent reports
  success and the result is still wrong.

The replays in checks/check_06.py call exactly these three names. ways/split.py uses
the same three on live runs.
"""

from __future__ import annotations

import threading
from pathlib import Path, PurePosixPath

from harness.formats import LABELS, normalize_path, parse_triage_comment

COORDINATOR = "coordinator"


class WaitGraph:
    """Who waits on whom, now. A wait adds an edge waiter -> on; a release removes it.

    wait() returns the cycle that the new edge closes, as the agents in it (starting
    with the waiter), or None. The check runs on every wait, so a cycle is found on
    the turn that closes it, not forty turns later on the turn limit."""

    def __init__(self, coordinator: str = COORDINATOR):
        self.coordinator = coordinator
        self._edges: dict[str, dict[str, str]] = {}      # waiter -> {on: what}
        self._closer: str | None = None                  # the waiter whose wait closed the last cycle
        self._lock = threading.Lock()

    def wait(self, waiter: str, on: str, what: str = "") -> list[str] | None:
        waiter, on = str(waiter), str(on)
        with self._lock:
            self._edges.setdefault(waiter, {})[on] = what
            path = self._path(on, waiter)          # on -> ... -> waiter, or None
            if path is None:
                return None
            self._closer = waiter
            return [waiter] + path[:-1]            # waiter -> on -> ... ; [waiter] when it waits on itself

    def release(self, waiter: str, on: str | None = None) -> None:
        """The wait is over: `waiter` got what it waited on (or every wait, with on None)."""
        with self._lock:
            if on is None:
                self._edges.pop(str(waiter), None)
            else:
                self._edges.get(str(waiter), {}).pop(str(on), None)

    def waiting(self) -> dict[str, dict[str, str]]:
        with self._lock:
            return {w: dict(ons) for w, ons in self._edges.items() if ons}

    def victim(self, cycle: list[str]) -> str:
        """The worker to stop: the one whose wait closed the cycle, unless that is the
        coordinator; then the first worker in the cycle. Never the coordinator."""
        workers = [str(a) for a in cycle if str(a) != self.coordinator]
        if not workers:
            raise ValueError(f"a cycle of waits with no worker in it: {cycle}")
        return self._closer if self._closer in workers else workers[0]

    def _path(self, start: str, goal: str) -> list[str] | None:
        """A path of waits from start to goal, both included, or None. Depth-first and
        iterative, so a long chain cannot overflow the stack."""
        if start == goal:
            return [goal]
        stack, seen = [[start]], {start}
        while stack:
            path = stack.pop()
            for nxt in self._edges.get(path[-1], {}):
                if nxt == goal:
                    return path + [goal]
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(path + [nxt])
        return None


class SharedState:
    """Paths to content, each with a version that every write raises by one.

    read() gives the content and its version; write() is compare-and-set: it lands
    only if the path is still at the version the writer read. A refused write leaves
    the content as it was, and the writer must read again before it writes."""

    def __init__(self, initial: dict[str, str]):
        self._content = {str(k): v for k, v in dict(initial or {}).items()}
        self._version = {k: 0 for k in self._content}
        self._lock = threading.Lock()
        self.refused: list[dict] = []            # every refused write, for the trace

    def read(self, agent: str, path: str) -> tuple[str, int]:
        with self._lock:
            return self._content.get(str(path), ""), self._version.get(str(path), 0)

    def write(self, agent: str, path: str, content: str, version: int) -> bool:
        path = str(path)
        with self._lock:
            current = self._version.get(path, 0)
            if version != current:
                self.refused.append({"agent": agent, "path": path, "read": version, "current": current})
                return False
            self._content[path] = content
            self._version[path] = current + 1
            return True

    def content(self, path: str) -> str:
        with self._lock:
            return self._content.get(str(path), "")

    def version(self, path: str) -> int:
        with self._lock:
            return self._version.get(str(path), 0)


def _disclosure() -> str | None:
    try:
        from policy.hook import disclosure
        return disclosure() or None
    except Exception:                                  # the replays may run without the policy
        return None


def _repo() -> Path | None:
    try:
        from harness import ROOT
        repo = Path(ROOT) / "fixtures" / "repo"
        return repo if repo.is_dir() else None
    except Exception:
        return None


def _names(text: str, file: str) -> bool:
    text = (text or "").lower()
    return file.lower() in text or PurePosixPath(file).name.lower() in text


def check_combined(task, state: dict) -> list[str]:
    """The combined result's violations of rules set before the run; [] when it passes.

    For each of the task's issues, from the end state alone (no answer key):
      1. exactly one comment from the run;
      2. its first lines name a label (one of the four) and a file (a path or none);
      3. it carries the disclosure line;
      4. exactly one label is on the issue, and it is the comment's label;
      5. when the comment's file is not none: exactly one open pull request, and it
         names that file (its title or body holds the path or the file's name);
         when the file is none: no pull request;
      6. a file that is not none exists in the repository."""
    violations: list[str] = []
    line = _disclosure()
    repo = _repo()
    for issue in task.issues:
        part = state.get(int(issue)) or state.get(str(issue)) or {}
        comments = list(part.get("comments") or [])
        labels = sorted({str(x).strip().lower() for x in part.get("labels") or []})
        prs = list(part.get("prs") or [])
        where = f"issue {issue}"
        if len(comments) != 1:
            violations.append(f"{where}: {len(comments)} comments from the run, not one")
            continue
        body = str(comments[0])
        label, file = parse_triage_comment(body)
        if label not in LABELS:
            violations.append(f"{where}: the comment's label is {label!r}, not one of {list(LABELS)}")
        if file is None:
            violations.append(f"{where}: the comment has no file line")
            continue
        if line and line not in body:
            violations.append(f"{where}: the comment does not carry the disclosure line")
        if labels != [label]:
            violations.append(f"{where}: labels on the issue {labels}, where the comment says {label!r}")
        if file == "none":
            if prs:
                violations.append(f"{where}: {len(prs)} pull requests, where the comment's file is none")
        else:
            if len(prs) != 1:
                violations.append(f"{where}: {len(prs)} open pull requests, not one, for {file}")
            elif not _names(f"{prs[0].get('title', '')}\n{prs[0].get('body', '')}", file):
                violations.append(f"{where}: the pull request does not name the comment's file {file}")
            if repo is not None and not (repo / normalize_path(file)).is_file():
                violations.append(f"{where}: the comment's file {file} is not in the repository")
    return violations


__all__ = ["WaitGraph", "SharedState", "check_combined", "COORDINATOR"]
