# 0006 · Topology: RepoMind stays one agent

- **Status:** decided. We measured the split and recorded it. We did not merge it.
- **Date:** 2026-11-12
- **Context:** Assignment 6, Lecture 6.
  - The split is on the branch `lab-06-split`, at the tag `lab-06-split-final`.
  - The measurements are in `runs/`, `tracker/` and `results/runs_06.jsonl` on `main`.
  - The report is `reports/LAB_06.md`, and the data is `reports/LAB_06.json`.

## The question

The daily task of RepoMind has five steps:

1. Read an issue.
2. Find the file that causes the issue.
3. Apply the label.
4. Open a draft pull request, if a file causes the issue.
5. Write the triage comment last.

Must several agents do this task, or one agent? We ask this question for two kinds of
task: one issue, and a batch of five issues with no relation, in one run.

## What we tested

- **The split (arm M)**
  - A coordinator in code (no model) and two roles. We split the roles by their tools.
  - One investigator for each issue. It has only the read tools and a return tool.
  - One writer for each issue. It has only `issues_label`, `pulls_open` and
    `issues_comment`.
  - On a batch, the five issues operate at the same time.
  - The investigator's return is a structured contract. Code validates it.
  - The five checks of slide 47 are in the code: the contract, deadlock detection, the
    combined check, compare-and-set on shared writes, and one trace.
- **The single agent (arm S)** This is `ways/agent.py`: one loop with all tools. It
  has the same ceiling for each task as arm M.
- **One agent with the tokens of the split (arm S-plus)** This is `ways/splus.py`.
  - It gets the average tokens that arm M used on each task.
  - It makes attempts with read tools only. Each attempt has a new context, and the
    attempts operate one after the other.
  - Then it votes on the answers of its attempts. It writes the chosen answer one time.

## The gates, and the prediction (Part A)

| Gate (slide 46) | One issue | A batch of five |
|---|---|---|
| 1. The parts work mostly without talking | pass | pass |
| 2. Each part's result can be checked on its own | pass | pass |
| 3. A team buys what one agent cannot get more cheaply | fail (No two agents operate at the same time. One issue fits in one context window. The hook already keeps the reads separate from the writes) | pass: parallelism |
| 4. The result is worth the multiplier | not reached | pass (predicted) |

I committed the prediction before the first run of the split
(`reports/PREDICTIONS_06.md`, commit `6654c54`):

- **Single tasks:** the split uses approximately 1.1× the tokens of the single agent.
  It gets approximately the same passes. Arm S-plus does as well as the split.
- **Batch tasks:** the split uses approximately 0.5× the tokens. It gets more passes
  (approximately 3 of 6, compared with 1 of 6). Arm S-plus does approximately as well.
  Thus the split keeps only its wall-clock time.

The prediction was correct for the tokens and for S-plus. It was not correct for the
batch passes: no arm passed a batch.

## What we found (Part C)

**The five constants** were the same in all 72 runs:

| Constant | Value |
|---|---|
| Model | `gpt-4.1-mini` |
| Tools | `schemas_sha` `703c0002892f27ec` |
| Budget | B = 250,000 tokens for each issue (1,250,000 for each batch). S and M had the same ceiling for each task. S-plus had the average tokens of M |
| Evaluator | `checks_sha` `55abcd67e6a3b208`, with the reviewer `scripted:approve-all` |
| Task set | the eight tasks of `fixtures/tasks6/` |

All arms ran with `--jobs 1`.

| Arm | Kind | Passes | Sweeps | Average tokens | Average wall-clock time |
|---|---|---|---|---|---|
| S | single | 13 / 18 | 4 – 5 | 41,107 | 7.8 s |
| M | single | 14 / 18 | 4 – 5 | 36,116 | 13.0 s |
| S-plus | single | 13 / 18 | 4 – 5 | 32,788 | 9.6 s |
| S | batch | 0 / 6 (20 / 30 issues) | 0 – 0 | 305,055 | 31.4 s |
| M | batch | 0 / 6 (17 / 30 issues) | 0 – 0 | 161,351 | 17.4 s |
| S-plus | batch | 0 / 6 (18 / 30 issues) | 0 – 0 | 160,989 | 43.8 s |

**My multiplier** is the tokens of arm M divided by the tokens of arm S. It is **0.88×
on one issue, and 0.53× on a batch.**

- The lecture gives "approximately 15×". Anthropic reported this number for
  multi-agent systems, compared with a chat, for typical use on its own work. The same
  source gives approximately 4× for one agent.
- My number compares one design with one agent, on one set of eight tasks.
- My number is less than 1 for three reasons:
  1. The coordinator makes no model calls.
  2. Each worker sends only the tools of its own role, and only its own issue.
  3. On a batch, the context of the single agent keeps all earlier issues.

**Slide 18's three answers:**

| Question | One issue | A batch of five |
|---|---|---|
| Did the split spend more? | No (0.88×) | No (0.53×) |
| Does spending more raise the score? | No. One agent at 41,107 tokens and at 32,788 tokens passed 13 / 18 both times | No. Two times the tokens (S compared with S-plus) gave 20 issues of 30, compared with 18. Both passed 0 tasks |
| Given the same budget, did one agent do as well? | Yes. 13 / 18 compared with 14 / 18. This is in the 4-to-5 sweep range of all arms | Yes for the score (0 / 6 each; 18 issues of 30 compared with 17). No for the wall-clock time (43.8 s compared with 17.4 s) |

What these answers show:

- The split used fewer tokens. Thus the tokens are not the cause of its results.
- But the split had no win in score.
- The decrease in tokens comes from new, small contexts. One agent can get these at a
  lower cost (slide 12). Arm S-plus used the same tokens and got the same score.
- Only the team gave less wall-clock time on a batch: 17.4 s, compared with 31.4 s and
  43.8 s.
- This gain was not the same on the two batches. On `batch-a`, the split was not faster
  (19.4 s, compared with 20.3 s), because a run waits for its slowest issue.
- On one issue, the split is slower (13.0 s, compared with 7.8 s).

**The failures.** 10 arm M runs failed. In all 10, no anomaly occurred between the
agents: no lost update, no write skew, no deadlock, and no misalignment.

- **Nine runs.** One investigator made an error in its own finding. For example, it
  returned a question as a bug, or it named a code file for a docs issue. The writer
  wrote the finding correctly. The MAST category is task verification.
  - No check finds if a finding is true before the comment.
  - The combined check cannot find it. A wrong finding that the writer writes correctly
    gives an end state that agrees with itself.
- **One run.** The investigator got to its step limit and did not return. The MAST
  category is system design: only the prompt holds the stopping rule.

The same difficult issues (4403, 4412, 4420, 4426, 4431) caused failures in all arms.
The limit is the investigation, not the topology.

## The decision

- **One issue: one agent.**
  - Gate 3 gave this decision, and the measurement agrees.
  - The split passed one more run of 18. This difference is in the normal variation.
  - The split was 1.7× slower.
  - One agent with the same tokens did as well.
- **A batch of five: one agent.**
  - With the same budget, the split got no better score than one agent: not for tasks,
    and not for issues.
  - The split gave only less wall-clock time. No person waits on the daily batch. It
    takes 30 to 45 seconds, compared with approximately 17 seconds for the split.
  - The decrease in tokens is real. But it comes from a new context for each issue, and
    one agent can do this.
  - If we run batches, this is the change that costs less: one agent with a new context
    for each issue, as in arm S-plus.
- **No part of the work is split.**
  - RepoMind stays one agent in this repository.
  - We did not merge the branch `lab-06-split`.

## The condition that opens the decision again

Open this decision again if a batch run shows that a split is better than one agent,
with the same measured budget. Either of these two conditions is sufficient.

> **Condition 1.**
>
> - The task set has a minimum of two batches. Each batch has five separate issues.
> - **Each issue has its own test**: a grounded check for the issue, and a failing test
>   that the fix must pass.
> - The test has three sweeps, and the five constants stay the same.
> - **Arm M passes a minimum of 2 more batch runs of 6 than arm S-plus.** Arm S-plus has
>   the measured average tokens of arm M, in a 25% limit.
> - Arm M passes no fewer issues than arm S-plus.
>
> **Condition 2.**
>
> - The daily batch gets a wall-clock time limit. The limit is less than the measured
>   batch time of one agent (here, 31.4 s for S and 43.8 s for S-plus).
> - The split meets this limit with no fewer passes than S-plus.

To test the conditions, do these steps:

1. Run `python run.py --tasks <set> --way agent --budget 250000 --runs 3`.
2. Run `python run.py --tasks <set> --way split --budget 250000 --runs 3`.
3. Run `python run.py --tasks <set> --way splus --match split --runs 3`.
4. Run `python eval/results_06.py`.

**Does my batch result answer the condition of slide 49?** Slide 49 names a batch of
separate issues that operate at the same time, each issue with its own test. My result
answers this condition only in part:

- **What my batches have:** separate issues that operate at the same time. Each issue
  has its own grounded check.
- **What my batches do not have:** a test for each issue.
- **For speed, the answer is clear.** The parallelism shows on one batch and not on the
  other.
- **For score, the result cannot decide.**
  - All arms passed 0 of 6 batches. This is a floor effect.
  - A batch passes only when all five issues pass, and the same four difficult issues
    failed in all arms.
- **What is missing is the test for each issue.** The condition of slide 49 adds it, and
  my investigators did not have it. A test is a fact that a check can examine, and it
  can find the nine wrong findings that caused arm M to fail.
- **This task set cannot show a better score for the split** until the accuracy for each
  issue is high enough that some arm passes batches.

## What we lose with each choice

**If we split, we lose:**

- **A seam with no check:** The writer knows only what the return says, and it cannot
  check the return. Thus a wrong finding goes directly to a public comment. Nine of the
  ten failures had this cause.
- **Speed on one issue:** 13.0 s, compared with 7.8 s, because two agents operate in
  sequence.
- **Simplicity:** We must build, test and maintain more parts: a contract, a wait graph,
  a board with compare-and-set, threads, and a trace for many agents. In 24 live runs,
  none of these guards had to act. Thus we pay for protection against failures that
  this task does not cause.
- **One place to read a run:** The data of a failure is in the lines of several agents.

**If we do not split, we lose:**

- **Wall-clock time on a batch:** approximately 17 s for five issues, compared with 31 to
  44 s.
- **Structural separation:** between the reader of unknown text and the write tools.
  But the autonomy hook (Assignment 5) and the planned way already give one agent most
  of this protection.
- **The explicit contract at the hand-off**, with `could_not_determine`. But one agent
  can use the same structured report with no second agent.

ARCHITECTURE.md did not change: RepoMind stays one agent, and this is the result of this
decision.
