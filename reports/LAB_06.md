# Assignment 6 · Split it, measure it, decide

Model: OpenAI gpt-4.1-mini. The API served `gpt-4.1-mini-2025-04-14`. All runs used
this model.

## Part A · The gates, before you build

**The roles I considered.** I split the roles by their tools, not by their names or
instructions (slide 11).

- **Coordinator.** This is code, not a model. It gets the task from the runner. It
  starts the workers. It validates each return and records each wait. It does the
  combined check. It has no tools that a model can select.
- **Investigator** (one for each issue). Its tools are `issues_read`, `code_search`,
  `code_read_file`, `code_run_tests`, and a local return tool. It reads text from
  unknown persons. It cannot write.
- **Writer** (one for each issue). Its tools are `issues_label`, `pulls_open` and
  `issues_comment`. It does not read the issue text. It reads only the finding that the
  coordinator gives it.
- **A reviewer: I considered it, and I did not use it.** A reviewer checks the
  investigator's finding before the writer starts. I did not use it, for two reasons:
  - It is the same model, so it makes the same errors as the investigator. Thus its
    agreement is weak evidence (slides 11 and 41).
  - It must read all that the investigator read. This is the most expensive type of
    communication.

  The set "triager, patch author, reviewer" usually fails gate 1.

**What each would need from the others mid-task.**

| Two roles | What one role needs from the other during the task | How it gets it | The cost |
|---|---|---|---|
| Coordinator → investigator | The issue number and the brief. After the start, it needs nothing | Through the coordinator, one time, at the start | No loss. The brief is short and complete |
| Investigator → coordinator | The finding: the label, the file, the change, and the items that it did not find | Through the coordinator, between rounds, as one structured return (`split/contract.schema.json`) | An item that the return does not include is lost. The writer does not see the files that the investigator read. It sees only the `evidence` sentence |
| Coordinator → writer | The validated finding (label, file, evidence) and the issue number | Through the coordinator, between rounds | The writer cannot check the finding. It can only write it correctly |
| Investigator ↔ writer | Nothing during the task. The writer starts only after the investigator returns | No direct channel | No cost during the run. The hand-off is a seam: a wrong finding goes through it with no change |
| Writer → coordinator | The confirmation that the three writes are done | The coordinator does not read the writer's words. It reads the end state from the tracker | One read of the tracker for each issue |
| Investigator ↔ investigator (batch only) | Nothing. The five issues are not related, and they are in different files | No channel | No cost |
| Writer ↔ writer (batch only) | Nothing. Each writer writes only to its own issue | No channel. Compare-and-set on the record of each issue makes sure that only one writer writes an issue | No cost. A retry can start a second writer on the same issue, but the compare-and-set refuses it |
| Reviewer ↔ investigator (not used) | During the task, the reviewer needs all that the investigator read, to judge the finding | Full records of the work, or messages during the work | Full records fill the reviewer's context window. Messages add turns and a place to wait (slide 14) |

- **For one issue:** one investigator starts, and then one writer starts. The only arrow
  is the hand-off of the finding, through the coordinator, between rounds. No two
  agents operate at the same time. The investigator returns the finding. It cannot say
  if its file is correct. It can say only if it read the file, and which items it did
  not find. The coordinator does not trust the writer's return. It reads the tracker.
- **For a batch of five:** five investigators operate at the same time. Each writer
  starts after its own investigator stops. No arrow goes from one issue to a different
  issue. Each investigator returns its own finding. It can say nothing about the other
  four issues. The only shared state is the tracker, and each issue's part of the
  tracker has one writer.

**The sketch:**

```
                       ┌──────────────────────────────┐
     runner ──task───▶ │ coordinator (code, no model) │ ◀── reads the tracker at the end:
                       └──────────────────────────────┘     the combined check
             brief │  ▲ return (contract)        finding │  ▲ (it trusts no words: it reads the tracker)
                   ▼  │ validated, traced                ▼  │
          ┌──────────────────────┐             ┌──────────────────────┐
          │ investigator-<n>     │             │ writer-<n>           │
          │ issues_read          │             │ issues_label         │
          │ code_search          │   (no       │ pulls_open           │
          │ code_read_file       │ ── arrow ── │ issues_comment       │──▶ tracker
          │ code_run_tests       │  mid-task)  │ (disclosure in code) │    (CAS for each issue)
          │ return_finding       │             └──────────────────────┘
          └──────────────────────┘

  One issue: one of each role, in sequence. The investigator starts first, then the writer.
  A batch:   five investigators operate at the same time (4410 · 4412 · 4414 · 4420 · 4423).
             Each one then has its own writer. No arrow goes between issues.
  All arrows go through the coordinator, between rounds. Only the coordinator waits,
  and it waits only on its own workers.
```

**The four gates** (slide 46), in sequence. I stopped at the first "no".

| Gate | One issue | Why | A batch of five | Why |
|---|---|---|---|---|
| 1. The parts work mostly without talking | pass | First the investigator finds, then the writer writes. There is one hand-off of a small finding, between rounds, through the coordinator. The roles do not talk during the task. | pass | The five issues are not related, and they are in different files. No issue needs data from a different issue. In each issue, there is only one hand-off. |
| 2. Each part's result can be checked on its own | pass | The tracker shows the writes. You can compare them with the finding, with no answer key. You can check the shape of the finding (the contract) and its facts (that it read the file that it names). This check does not replace a check of the full result. | pass | Each issue has its own check: one comment, its label, and its pull request. The combined check reads all five issues from the tracker. |
| 3. A team buys what one agent cannot get more cheaply | **fail** | No two agents operate at the same time, so the team gives no speed. One issue easily fits in one context window (approximately 12 steps), so the team gives no more room. The split by tools is real: the reader of unknown text has no write tool. But the hook from Assignment 5 and the planned way give one agent the same protection at a lower cost. | pass | Parallelism: five investigators operate at the same time. One agent cannot do this. Room: one agent on five issues keeps the reads of all earlier issues in its context window. One agent can get more room at a lower cost, with a new context for each issue (slide 12). But it cannot get the speed. |
| 4. The result is worth the multiplier | not_reached | — | pass (predicted) | I predict that the split uses fewer tokens than one agent on a batch (approximately 0.5×, `PREDICTIONS_06.md`). The reason: the single context of one agent becomes larger with each of the five issues. If the split uses fewer tokens and is faster, the multiplier is worth it. Part C measures this. This verdict is a prediction, not a result. |

- **The benefit I am trying to buy, and the condition it needs:** the benefit is
  **parallelism** on the batch. This means less wall-clock time, because five
  investigators operate at the same time. Its condition: the parts must be fully
  independent. Thus the five issues must not be related, each issue must be in its own
  file, and each issue must have one writer.

  A second benefit comes with parallelism: **context isolation**. The context window of
  each investigator holds only its own issue. Its condition is little communication, and
  the hand-off with one return gives this. But one agent can get this benefit at a lower
  cost, with a new context for each issue (slide 12). Arm S-plus tests this.

  The split by tools is a side effect. It is not the benefit that I buy. It is strong for
  safety, but the hook already gives this safety.
- **Your prediction, as committed in `reports/PREDICTIONS_06.md`, and its commit:**
  - **Single tasks:** the split uses approximately 1.1× the tokens of the single agent,
    and it gets approximately the same passes. Arm S-plus does as well as the split.
  - **Batch tasks:** the split uses approximately 0.5× the tokens of the single agent,
    and it gets more passes (approximately 3 of 6, compared with 1 of 6). Arm S-plus
    does approximately as well as the split. Thus the split keeps only its wall-clock
    time, which is approximately 3× faster.

  The commit is `6654c54` ("Part A Predictions"), on `main`, before the first run of the
  split.

## Part B · The split

These files are on `lab-06-split` only:

- `ways/split.py` (the coordinator)
- `split/roles.py` (the two roles)
- `split/contract.schema.json` and `split/guards.py`
- `prompts/split_investigator.md` and `prompts/split_writer.md`

The answers to the questions of Part B are as follows.

- **The coordinator and the roles, and the tools each holds:**
  - **Coordinator** (`ways/split.py`, agent `coordinator`, parent none). It is code, with
    no model and no tools that a model selects. For each issue, it does these steps:
    1. It starts an investigator and waits on it.
    2. It validates the return.
    3. If it cannot use the return, it starts one new investigator from the reserve.
    4. It writes the finding to the record of the issue.
    5. It starts a writer and waits on it.

    On a batch, the five issues operate at the same time, one thread for each issue.
    After the last write, the coordinator reads the tracker and does the combined check.
  - **Investigator** (`investigator-<n>`, parent `coordinator`). Its tools are
    `issues_read`, `code_search`, `code_read_file`, `code_run_tests`, and the local tool
    `return_finding`. The local tool does not go to the registry.
  - **Writer** (`writer-<n>`, parent `coordinator`). Its tools are `issues_label`,
    `pulls_open` and `issues_comment`. Its brief contains only the checked finding. It
    does not read the issue or the code.
  - **The tool surface of a role** is a `RoleTools` object (`split/roles.py`). It gives
    the model only the schemas of that role. It refuses all other tool names in code,
    before the registry. Each call that it accepts goes to the registry of the run. Thus
    the hook, the policy and the idempotency keys apply to all agents.
  - **The disclosure line:** code adds it to each comment. The writer's tools are in
    `ways.agent.Disclosing`. Thus each comment goes through `with_disclosure` before the
    registry.
- **Why they split by tool surface:** a split by tools changes the errors that each
  agent *can* make. A split by name (the same tools, different instructions) changes
  only the instructions (slide 11).
  - The investigator reads text from unknown persons, and it has no write tool. Thus an
    instruction that an attacker puts in an issue cannot make the investigator apply a
    label, open a pull request, or write a comment.
  - The writer can write, but it does not see the issue text. A guard in code
    (`WriteGuard`) refuses each write that is different from the checked finding.

  The two roles have different requirements: one reads and one writes. The roles do not
  need the same finding during the task, because the writer starts only after the
  finding exists.
- **The return contract, field by field.** The contract is `split/contract.schema.json`.
  The coordinator validates each return with `harness.schema.errors`. It records each
  return as a `worker_return` line before it uses the return.

  | Field | Type | Source |
  |---|---|---|
  | `result` | object: `issue` (integer), `label` (one of the four labels, or null), `file` (a path, `none`, or null), `evidence` (string: the change in that file, and the reason) | the return tool of the model |
  | `status` | `found`, `candidate` or `out_of_budget` | the return tool of the model. Code writes `out_of_budget` when the worker stops with no valid return |
  | `searched` | list of strings: each search query and each path that the worker read, one time each | **code**, from the worker's own calls (`RoleTools.facts`) |
  | `not_searched` | list of strings | the return tool of the model |
  | `calls_used` | integer: the number of calls that the worker made to the course's tools before it returned | **code**, from the worker's own calls |
  | `could_not_determine` | list of strings. It is empty when the worker found all items | the return tool of the model |

  - **The schema:** it allows no other fields. An `anyOf` rule makes a `found` or
    `candidate` return name a label and a file. Only `out_of_budget` can leave them null.
  - **The model cannot write the facts:** the return tool that the model sees has no
    `searched` or `calls_used` field. The tool is "strict", so the API makes the model
    obey its shape.
  - **A bad return:** if a return breaks the schema, the coordinator records it with
    `valid: false` and its errors. It rejects the return and does not repair it. It
    tells the worker which fields are wrong and asks again. After 3 rejections, it stops
    the worker.
  - **No valid return:** if a worker stops with no valid return, code makes an
    `out_of_budget` return for it. Its `could_not_determine` names the label and the
    file. Thus the coordinator can see the difference between "no answer exists" and
    "the worker did not finish". The coordinator records this return, but it does not
    write it.
- **Each worker's share of the budget, and what is held back:**
  - `ctx.budget` is the budget of the run: B = 250,000 tokens for each issue, and 5B for
    a batch.
  - Each issue gets an equal share, `ctx.budget / n`. From this share, the investigator
    gets 55% (137,500), and the writer gets 15% (37,500). The coordinator keeps 30%
    (75,000) for one new investigator, if it cannot use the first return.
  - Each share is the `max_tokens` limit of the loop of that agent. Code compares the
    limit with the agent's own client before each model call.
  - The clients of all agents count against the one ceiling of the run (`ctx.ceiling`).
    Thus the total of the run cannot go above the ceiling.
  - Each run records its shares on its `budget_shares` line.
  - On the development issues, one issue used approximately 25,000 to 30,000 tokens in
    total. This is much less than its share.
- **The five checks, where each is in my code:**
  1. *A return contract that the code checks:* `Investigation.record` and
     `Investigation._on_return`, in `split/roles.py`. The trace records a rejected return
     with `valid: false` and its errors.
  2. *Deadlock detection:* `WaitGraph`, in `split/guards.py`. The trace records each wait
     as a `wait` line, and the coordinator gives it to the graph (`Coordinator.wait`).
     - If a wait closes a cycle, the graph returns the cycle.
     - `victim` names one worker to stop. It never names the coordinator.
     - The coordinator writes `worker_stopped` and stops the client of that worker. The
       next model call of that worker then raises an error.

     In the recorded deadlock, the graph finds the cycle at turn 1. In my split, only
     the coordinator waits, so no cycle can occur. The guard is there to show a cycle if
     one occurs.
  3. *A check of the combined result:* `check_combined`, in `split/guards.py`.
     `Coordinator.run` calls it after the last write, on `end_state(ctx.tracker, ...)`.
     The trace records the result as `combined_check`. The rules are set before the
     run, and they need no answer key. For each issue:
     - one comment, with a label line, a file line and the disclosure line;
     - exactly that label on the issue;
     - one pull request that names the file, if the file is not none, and no pull
       request if it is none;
     - the file is in the repository.
  4. *Idempotency and compare-and-set on shared writes:* `SharedState`, in
     `split/guards.py`, with one record for each issue (`Coordinator.board`).
     - The coordinator writes the finding at the version that it read. The writer's
       brief gives the new version.
     - `WriteGuard` (`split/roles.py`) refuses all writes if the record changed after
       the brief.
     - The comment is the one write that you cannot reverse. It is itself a
       compare-and-set on the record. Thus the guard refuses a second comment, and a
       comment from an old brief.
     - The registry's keys from Assignment 3 also stay in use, so a retry writes only
       one time.
  5. *One trace for all agents:* each agent comes from `ctx.agent(...)`. Thus each
     `model_call`, `tool_call`, `wait`, `worker_return` and `agent_end` line of an agent
     has its ID and the ID of its parent. The tokens of each agent come from its own
     client. The coordinator makes no model calls, so the tokens of the workers add up
     to the total of the run.
- **Tried before measuring:** I ran `python run.py --way split --set dev --budget 250000`.
  - Both development issues passed the grounded check: 4191 (docs, `README.md`) and 4192
    (bug, `src/components/ExportButton.tsx`).
  - The runs used 29,929 and 24,602 tokens. All returns were valid. The combined check
    passed.
  - `python checks/check_06.py --here` gave: deadlock 8/8, lost update 8/8, combined
    result 8/8.

## Part C · Measured

**The five constants** (slide 59). These values were the same in the `run_start` lines of
all 72 runs (`make check-06`: "Constants recorded" 6/6).

| Constant | Value |
|---|---|
| Model | `gpt-4.1-mini` (OpenAI). The API served `gpt-4.1-mini-2025-04-14` |
| Tools | `schemas_sha` `703c0002892f27ec`: the ten definitions of the registry, in the registry's sequence. Each role uses some of these tools. The investigator's return tool is local to the split |
| Budget | B = 250,000 tokens for each issue (`agent/bounds.yaml`), and 1,250,000 for each batch. Arms S and M had the same ceiling for each task. Arm S-plus had the average tokens of arm M on each task (`--match split`), below the ceiling of arm M |
| Evaluator | `checks_sha` `55abcd67e6a3b208` (`eval/check_task.py`, `eval/check_triage.py`, and the answers). The reviewer was `scripted:approve-all` |
| Task set | `batch-a`, `batch-b`, `one-4402`, `one-4403`, `one-4405`, `one-4406`, `one-4409`, `one-4413` |

- All arms ran with `--jobs 1`, so only one run operated at a time. Thus the wall-clock
  time of a run does not include waits from the rate limits of a different run.
- No run stopped with `rate_limited` or `quota`.
- In total, there were 72 runs. They used approximately 5.7M tokens and cost $1.00.

**The results.** The source is `results/runs_06.jsonl`, from `eval/results_06.py`. The
passes come from the grounded check on the tracker. The tokens and the wall-clock times
come from the `run_end` lines.

| Arm | Kind | Passes / runs | Lowest and highest sweep | Tasks that passed all three times | Average tokens | Average wall-clock time (s) |
|---|---|---|---|---|---|---|
| S | single | 13 / 18 | 4 – 5 | 3 of 6 | 41,107 | 7.8 |
| S | batch | 0 / 6 | 0 – 0 | 0 of 2 | 305,055 | 31.4 |
| M | single | 14 / 18 | 4 – 5 | 4 of 6 | 36,116 | 13.0 |
| M | batch | 0 / 6 | 0 – 0 | 0 of 2 | 161,351 | 17.4 |
| S-plus | single | 13 / 18 | 4 – 5 | 3 of 6 | 32,788 | 9.6 |
| S-plus | batch | 0 / 6 | 0 – 0 | 0 of 2 | 160,989 | 43.8 |

Passes for each task (of 3):

| Task | S | M | S-plus |
|---|---|---|---|
| one-4402 | 2 | 3 | 3 |
| one-4403 | 0 | 0 | 0 |
| one-4405 | 3 | 2 | 3 |
| one-4406 | 3 | 3 | 2 |
| one-4409 | 2 | 3 | 2 |
| one-4413 | 3 | 3 | 3 |
| batch-a | 0 | 0 | 0 |
| batch-b | 0 | 0 | 0 |

No batch run passed in any arm. Thus the issues in the batches give more data than the
batch tasks. This table shows the issues that passed in the batch runs (of 3 for each
issue; from the `parts` of the grounded check):

| Issue | 4410 | 4412 | 4414 | 4420 | 4423 | 4424 | 4426 | 4429 | 4431 | 4434 | Total |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S | 3 | 3 | 1 | 0 | 3 | 3 | 2 | 3 | 0 | 2 | 20 / 30 |
| M | 3 | 0 | 3 | 0 | 3 | 3 | 0 | 3 | 0 | 2 | 17 / 30 |
| S-plus | 3 | 0 | 2 | 2 | 3 | 3 | 0 | 3 | 0 | 2 | 18 / 30 |

The average wall-clock time for each batch task:

| Task | S | M | S-plus |
|---|---|---|---|
| `batch-a` | 20.3 s | 19.4 s | 44.6 s |
| `batch-b` | 42.4 s | 15.4 s | 42.9 s |

- **Your multiplier (M over S), single and batch, beside the lecture's 15×:** the
  multiplier is **0.88× on single tasks and 0.53× on batches**. Thus the split used
  *fewer* tokens than one agent.
  - **The lecture's "approximately 15×":** this is the number that Anthropic gives for
    multi-agent systems, compared with a chat, for typical use on its own work. The same
    source gives approximately 4× for one agent, compared with a chat. Thus its
    multi-agent system uses approximately 3.75× the tokens of one agent.
  - **My number:** it compares one design with one agent, on one set of eight tasks.
    Both arms ran on the same tasks.
  - **Why it is less than 1:** there are three reasons.
    1. The coordinator is code, and it makes no model calls.
    2. Each worker sends only the tool schemas of its own role, and only its own issue.
    3. On a batch, the single agent has one context. That context keeps the reads of all
       earlier issues, and it sends them again at each later step.

    Most of the decrease on a batch comes from the third reason.
- **Whether S-plus's spend matched M's in fact, task by task:** this table divides the
  average tokens of S-plus by the average tokens of M.

  | Task | S-plus | M | S-plus / M |
  |---|---|---|---|
  | `batch-a` | 157,523 | 154,346 | 1.02 |
  | `batch-b` | 164,455 | 168,356 | 0.98 |
  | `one-4402` | 35,043 | 36,535 | 0.96 |
  | `one-4403` | 15,845 | 24,898 | 0.64 |
  | `one-4405` | 61,062 | 55,355 | 1.10 |
  | `one-4406` | 25,568 | 29,812 | 0.86 |
  | `one-4409` | 44,843 | 49,947 | 0.90 |
  | `one-4413` | 14,367 | 20,153 | 0.71 |

  - For each kind of task, the tokens matched: 0.91 on single tasks and 1.00 on
    batches. This is in the 25% limit of the checker ("Budget" 10/10).
  - On 4403 and 4413, S-plus used fewer tokens than M. There, one attempt used
    approximately 70% of the tokens of arm M. A second attempt did not fit below 115% of
    them, so the budget rule stopped S-plus.
  - Thus, on these two tasks, S-plus had a smaller budget than arm M in fact. But it got
    the same passes as arm M (0 of 3 and 3 of 3).

**Slide 18's three questions**, for each kind of task.

| Question | One issue | A batch of five |
|---|---|---|
| Did the split spend more? | **No.** It used 36,116 tokens for each task, compared with 41,107: 0.88× | **No.** It used 161,351, compared with 305,055: 0.53× |
| Does spending more raise the score? | **No.** One agent at two budgets: S used 41,107 tokens and passed 13/18. S-plus used 32,788 and passed 13/18. The split used 36,116 (between them) and passed 14/18. The sweep range of all three arms is the same: 4 to 5 | **No.** S used 305,055 and passed 0/6 tasks (20/30 issues). S-plus used 160,989 and passed 0/6 (18/30). Two times the tokens gave two more issues of thirty. This difference is in the normal variation from run to run |
| Given the same budget, did one agent do as well? | **Yes.** S-plus used the tokens of arm M and passed 13/18, compared with 14/18. The difference is one run, and the sweep range is the same (4 to 5) | **Yes, for the score:** 0/6 compared with 0/6, and 18/30 issues compared with 17/30. **No, for the wall-clock time:** S-plus used 43.8 s for each batch, compared with 17.4 s |

- **How slide 18 reads the 90.2%, applied to my results:**
  - On both kinds of task, the answer to the first question is "no". Thus the tokens
    cannot be the cause of a win by the split.
  - But the split had no win in score. On single tasks, 14 compared with 13 is one run
    of 18, and the sweeps of all arms go from 4 to 5. On batches, no arm passed a task.
  - The only measured effect of the design is the wall-clock time on a batch: 17.4 s,
    compared with 31.4 s (S) and 43.8 s (S-plus). This is the parallelism that I tried
    to buy.
  - This effect is not the same on the two batches. On `batch-a`, the split was not
    faster than one agent (19.4 s, compared with 20.3 s), because the run waits for its
    slowest issue. On `batch-b`, it was 2.8× faster.
  - On a single issue, the split is slower (13.0 s, compared with 7.8 s), because its
    two agents operate in sequence.
- **What I would have claimed from arms S and M alone, and what I claim now:**
  - **From S and M only, my claim is:** "The split is better and costs less. On single
    tasks, it gets one more pass with 12% fewer tokens. On batches, it uses half the
    tokens and almost half the wall-clock time."
  - **With S-plus in the table, this claim is not correct.** One agent with the tokens of
    the split, and a new context for each attempt, gets the same score.
  - Thus the decrease in tokens comes from new contexts, not from the team. One agent
    can get new contexts at a lower cost (slide 12).
  - The team adds only wall-clock time on a batch. It adds no accuracy.
  - The accuracy limit is the same in all arms: the investigation of four difficult
    issues (4412, 4420, 4426, 4431) and of the question 4403.

**Every arm M run that failed.** 10 of 24 runs failed. I read each failure from its
trace, not from the report of an agent.

- In each failed run, the only shared state was the record of each issue on the board,
  and the part of the tracker for that issue. Each had one writer.
- The coordinator waited only on its own workers, so no cycle of waits occurred.
- In all 24 arm M runs, the traces show 0 invalid returns, 0 writes that a writer's
  guard or the compare-and-set refused, and 0 stopped workers. Thus, on live runs, the
  guards did not have to act. The replays show that the guards operate correctly.
- In each failed run, the writer wrote the finding that it got, correctly.

| Run | What each agent read, wrote and waited on | The first error | Anomaly | MAST category |
|---|---|---|---|---|
| `split-batch-a-s01-2fee6b` | `investigator-4412` read the issue and `src/api/health.ts`. It returned `found`, bug, `health.ts`. `investigator-4420` read `errors.ts` and `auth.ts`. It returned `found`, docs, `src/api/auth.ts`. The writers wrote both findings. The coordinator waited on each worker in turn | `investigator-4412` returned a question as a bug in a code file. Its `could_not_determine` was empty, so the return showed no doubt | none | task_verification |
| `split-batch-a-s02-cc3f62` | The same reads. 4412: bug, `health.ts`. 4420: feature, `auth.ts`. The writers wrote both | The return of `investigator-4412`: it returned a question as a bug | none | task_verification |
| `split-batch-a-s03-1cd4bd` | The same reads. 4412: bug, `health.ts`. 4420: question, none. The writers wrote both | The return of `investigator-4412`: it returned a question as a bug | none | task_verification |
| `split-batch-b-s01-a44162` | `investigator-4426` read `src/util/money.ts` and returned bug. `investigator-4431` read `scheduler.ts`, `exportUtils.ts` *and* `src/util/strings.ts` (the correct file). It returned bug, `exportUtils.ts`. The writers wrote both | `investigator-4426` returned a question as a bug in `money.ts` | none | task_verification |
| `split-batch-b-s02-2208fb` | 4426: question, but with the file `money.ts`. Thus the writer opened a pull request for a question. 4431: bug, `exportUtils.ts`, after it read `strings.ts` | `investigator-4426` gave the label question with a file. Its own brief says that a question has the file none | none | task_verification |
| `split-batch-b-s03-125698` | 4426: bug, `src/ingest/parser.ts`. 4431: bug, `exportUtils.ts`. `investigator-4434` made 11 searches and read no file. It stopped at the 12-step limit. Its new retry did the same. The coordinator wrote nothing for 4434, and the combined check failed it | The wrong return of `investigator-4431`, at 7.8 s. It came before the return of 4426 (8.2 s) and the turn limit of 4434 (9.9 s) | none | task_verification |
| `split-one-4403-s01-59cbec` | `investigator-4403` searched for the BOM and read `src/export/csv.ts`. It returned feature, `csv.ts`. The writer wrote it | The investigator read a question about the CSV BOM as a change to `csv.ts`. Arm S made the same error, 3 of 3 times | none | task_verification |
| `split-one-4403-s02-7a2367` | The same. It returned bug, `csv.ts`. The writer wrote it | The same | none | task_verification |
| `split-one-4403-s03-73ed09` | The same. It returned feature, `csv.ts`. The writer wrote it | The same | none | task_verification |
| `split-one-4405-s03-a74796` | `investigator-4405` read `ExportButton.tsx` (the correct file) at step 7. Then it searched more until the 12-step limit, and it did not return. The new retry did the same. The coordinator wrote nothing, and the combined check failed the run | The stopping rule of the investigator. The rule "return by your ninth step" is only in the prompt. No code makes the worker return a candidate before its step limit | none | system_design |

- **Why these MAST categories:**
  - **The nine `task_verification` runs.** In each, one agent made an error in its own
    part. No agent checked the finding before the comment, and you cannot reverse a
    comment.
    - The contract checks the shape of the return and its facts (what the worker read).
      It does not check if the finding is correct.
    - The combined check checks that the end state agrees with itself. It does not check
      if it is true. It passed all nine runs, because a wrong finding that the writer
      writes correctly gives an end state that agrees with itself.
    - Only a check with evidence (for example, a failing test) can find these errors. A
      second model of the same type makes the same error (slide 41).
  - **The one `system_design` run.** It failed because of the stopping rule that I set: a
    step limit with no forced return. The retry from the reserve did the same.

## Part D · The decision

- **One issue: one agent.**
  - Gate 3 failed on paper, and the measurement agrees.
  - The split passed 14 of 18, compared with 13. This is in the 4-to-5 sweep range of
    all arms.
  - The split used 1.7× the wall-clock time.
  - One agent with the tokens of the split (S-plus) passed 13 of 18.
- **A batch of five: one agent.**
  - With the same budget, the split got no better score than one agent. All arms passed
    0 of 6 batches. The split passed 17 of 30 issues, and S-plus passed 18.
  - The split gave only less wall-clock time: 17.4 s, compared with 31.4 s and 43.8 s.
    No person waits on the daily batch.
  - The decrease in tokens comes from a new context for each issue. One agent can have
    this with no team.
  - No part of the work is split.
- **The condition that would reopen it:** two conditions can open the decision again.
  - **Condition 1:**
    - The task set has a minimum of two batches. Each batch has five separate issues.
    - Each issue has its own test: a grounded check, and a failing test that the fix
      must pass.
    - The test has three sweeps, and the five constants stay the same.
    - Arm M passes a minimum of 2 more batch runs of 6 than arm S-plus. S-plus has the
      measured tokens of arm M, in a 25% limit. Arm M also passes no fewer issues.
  - **Condition 2:**
    - The daily batch gets a wall-clock time limit. The limit is less than the measured
      batch time of one agent: 31.4 s for S, 43.8 s for S-plus.
    - The split meets this limit with no fewer passes.

  My batch result answers the speed part of the condition of slide 49. It does not
  answer the score part, for two reasons:
  - All arms failed all batches on the same difficult issues. This is a floor effect.
  - My issues did not have their own tests.
- **The record:** `decisions/0006-topology.md`, with the `CHANGES.md` entry.
  `ARCHITECTURE.md` did not change, and this is the result.

## AI assistance

**AI Tool Used:** Claude Code

I led the design and development of the split, using Claude Code to assist with implementation. I defined the coordinator's behavior in `ways/split.py` and the responsibilities of the investigator and writer roles, including their tool surfaces in `split/roles.py`. I also reviewed the implementation to make sure it followed the intended design.

I wrote the initial drafts of the report sections and prepared `CHANGES.md`, `ARCHITECTURE.md`, and `decisions/0006-topology.md`. I made the decisions about the proposed policy placements and documented the reasoning behind them. I then used Claude Code to review the documents for grammar and clarity and to check whether the proposed placements were consistent with the implementation.

Finally, I checked the reported results against `results/runs_06.jsonl` and the `make-check06` output. I also reviewed the line references in `ARCHITECTURE.md`.
