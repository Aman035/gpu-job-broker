# Job Broker

A shared service that places GPU jobs from ML teams onto rented cloud nodes,
starts and stops nodes to control cost, and reports cost per team. It also
contains a retrieval endpoint that finds similar past job failures.

Design decisions and their reasoning are in [docs/DECISIONS.md](docs/DECISIONS.md).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
broker-serve --data-dir var          # API on http://127.0.0.1:8000, docs at /docs
```

State lives in `var/broker.db` and `var/provider.db`. Stop and restart the
server with the same `--data-dir` and nothing is lost: simulated time resumes
where it stopped and provider nodes keep billing in between.

## Trace replay

```bash
broker-replay --out docs/RESULTS.md   # about 20 seconds; --quick skips sensitivity runs
```

Full tables: [docs/RESULTS.md](docs/RESULTS.md). Each figure is a mean over 5
provider seeds, because one seed is noisy (p90 wait moves by about 40 minutes
between seeds).

What the replay shows:

- **Fair share moves the wait onto the team causing it.** On 2025-03-05
  `vision` submits 167 jobs in under 3 hours. Under FIFO, `platform`'s p90
  wait is 59 minutes; under fair share it is 13. `vision`'s own p90 rises from
  87 to 100 minutes. The overall p90 looks worse under fair share only because
  `vision` is 53% of all jobs. Cost is the same within noise.
- **Most jobs start in about the boot time.** Median wait is 7 minutes, and
  65% of jobs start within the ML engineers' 10 minutes. Every team except
  `vision` has a p90 of 8 to 15 minutes; the misses are cold starts plus the
  burst.
- **Idle share is 26%,** against Finance's "about a third". It is reported
  per team, so the teams that cause it can see it.
- **The burst wait is a node-slot problem, not a GPU problem.** The burst
  needs about 95 GPU-hours over 3 hours; 10 slots could supply 240. Arrivals
  trickle in, so the slots fill with small nodes. Launching only large nodes
  once 8 of 10 slots are used cuts p90 from 86 to 71 minutes for 11% more
  cost. It is available but off by default (see DECISIONS.md, section 5).
- **A longer idle timeout does not buy shorter waits.** 20 to 30 minutes adds
  about 10% cost with no wait improvement, so the default is 5 minutes.
- **Interruptible nodes for `low` jobs save about 7%** of the bill with no
  measurable effect on waits.

## Failure retrieval

```bash
broker-eval-retrieval      # P@3 / P@5 and abstention on all test sets, per pipeline step
```

`POST /failures/search` returns similar past failures for a question or a
pasted log. On the 24 given questions it scores **P@3 1.00, P@5 0.99**, and it
returns nothing for all 6 questions with no relevant log. On 24 held-out
paraphrases written before the pipeline was built it scores **0.97 / 0.97**.
Design, ablation and limits: [docs/RETRIEVAL.md](docs/RETRIEVAL.md).

## HTTP API

Times are ISO 8601 UTC. Simulated time moves only through `POST /clock/advance`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/jobs` | Submit `{team, gpus, estimated_minutes, priority}`. Optional `Idempotency-Key` header: a repeat returns the original job with `200`; the same key with a different body returns `409`. New jobs return `201`. |
| `GET` | `/jobs/{id}` | One job. |
| `GET` | `/jobs?status=&team=` | Jobs, newest first. |
| `POST` | `/jobs/{id}/cancel` | Cancel a queued or running job. Cancelling twice is a no-op; cancelling a finished job is `409`. |
| `POST` | `/jobs/{id}/complete` | Report `{outcome: succeeded \| failed}` for a running job. |
| `GET` | `/queue` | Waiting jobs, in the order the scheduler will consider them. |
| `GET` | `/nodes` | Live nodes with their jobs and GPU use. |
| `GET` | `/reports/cost?start=&end=` | GPU-hours and cost per team for a window, split into used and idle cost. |
| `GET` | `/clock` | Current simulated time. |
| `POST` | `/clock/advance` | `{seconds}`: process every event up to that point. |
| `POST` | `/failures/search` | `{query, k}`: similar past failures for a question or a pasted log, with their fixes, a suggested fix and whether it abstained. |

Errors: `422` invalid input, `404` unknown job, `409` invalid state change or
idempotency conflict.
