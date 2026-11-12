# Assignment 6 · Predictions, before any run of the split

Written on `main` before any run of the split. After this commit this file is not
changed. Model: OpenAI gpt-4.1-mini, the same for every arm. B = 250,000 tokens per
issue (`agent/bounds.yaml`), so a batch gets 1,250,000.

The split I will build: 
- a coordinator in code, which is not a model, 
- one investigator per issue, which holds only the read tools and a return tool
- one writer per issue, which holds only `issues_label`, `pulls_open` and `issues_comment`. 

In a batch, all the five investigators will run at the same time.

## Single tasks (one issue)

- **Tokens:** The split will spend about 1.1× what the single agent spends per
  task, with a range of 0.9× to 1.4×. The investigator sends fewer tool schemas than
  the single agent, but the writer is a second context, and it reads the issue's
  finding again.
- **Passes:** About the same. The single agent (arm S) and the split (arm M) will be
  within 2 passes of each other out of 18. If one is ahead, I expect arm S to be
  ahead, because the writer only knows what the return tells it, so a return that loses detail
  can cost the split an issue.
- **Arm S-plus:** Yes, one agent given the split's spend will do as well as the
  split, within 2 passes out of 18. On one issue the split buys nothing that a second
  attempt by one agent does not also buy.

## Batch tasks (five issues in one run)

- **Tokens:** The split will spend less than the single agent: about **0.5×**,
  with a range of 0.3× to 0.8×. The single agent carries all five issues' reads in one
  context, so every later step sends the earlier issues again. Each of the split's
  investigators sees only its own issue.
- **Passes:** The split will pass more batch runs than the single agent: about 3 of 6
  against about 1 of 6. A batch passes only when all five issues pass, and the single
  agent's long context makes a late issue more likely to go wrong or to run out of
  steps.
- **Arm S-plus:** Yes, one agent given the split's (smaller) spend will do about as
  well as the split, within 1 pass out of 6, because it also gives each issue a fresh
  context. What the split keeps is wall-clock: about 3× faster on a batch, because
  its five investigators run at the same time. If that holds, the design buys speed,
  not accuracy.

## What would show me wrong

- If arm M passes clearly more single tasks than arm S and arm S-plus, the split buys
  something on one issue that I did not expect.
- If arm S-plus passes clearly fewer batch runs than arm M at the same spend, the split's
  design (not only its fresh contexts) is what wins the batch.
- If the split's batch wall-clock is not clearly lower than arm S's, parallelism did
  not show up, and the batch case for a split is gone.
