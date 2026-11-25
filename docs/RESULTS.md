# Trace replay

`01a_trace.csv`: 494 rows, 14 duplicate submissions collapsed as client retries, 2 rejected, 478 jobs replayed. Every figure is a mean over provider seeds 1-5.

- Rejected line 88: gpus must be one of [1, 2, 4, 8]
- Rejected line 393: gpus must be one of [1, 2, 4, 8]

Wait = minutes a job spent queued, summed over attempts. Costs in provider currency for the week.

## Policy comparison

| Run | p50 wait | p90 wait | p99 wait | Started ≤10 min | Total cost (± sd) | Idle share | Lost runs |
|---|---|---|---|---|---|---|---|
| fair share | 6.7 | 103.3 | 132.9 | 64% | 2,823 (± 31) | 25% | 6.4 |
| FIFO | 6.6 | 85.3 | 109.8 | 64% | 2,844 (± 43) | 26% | 5.6 |

## Per team

| Team | Jobs | fair share: p50 / p90 / p99 wait | FIFO: p50 / p90 / p99 wait | fair share: cost (idle) | FIFO: cost (idle) |
|---|---|---|---|---|---|
| forecast | 63 | 4.8 / 7.9 / 21.6 | 4.8 / 7.9 / 20.6 | 236 (37%) | 258 (41%) |
| nlp | 80 | 5.1 / 7.9 / 16.6 | 5.2 / 7.8 / 13.9 | 537 (27%) | 553 (29%) |
| platform | 24 | 0.8 / 13.2 / 31.4 | 3.1 / 62.7 / 99.2 | 408 (45%) | 382 (45%) |
| speech | 56 | 6.0 / 13.9 / 41.1 | 6.0 / 13.0 / 67.4 | 1,038 (6%) | 1,044 (6%) |
| vision | 255 | 35.1 / 118.7 / 152.5 | 34.1 / 95.2 / 120.1 | 602 (37%) | 606 (38%) |

## Sensitivity: idle timeout

| Run | p50 wait | p90 wait | p99 wait | Started ≤10 min | Total cost (± sd) | Idle share | Lost runs |
|---|---|---|---|---|---|---|---|
| idle timeout 0 min | 6.8 | 96.0 | 123.3 | 63% | 2,739 (± 80) | 24% | 7.4 |
| idle timeout 5 min | 6.7 | 103.3 | 132.9 | 64% | 2,823 (± 31) | 25% | 6.4 |
| idle timeout 10 min | 6.6 | 118.5 | 151.3 | 63% | 2,932 (± 68) | 28% | 5.4 |
| idle timeout 20 min | 6.4 | 126.8 | 160.7 | 64% | 3,099 (± 47) | 31% | 4.6 |
| idle timeout 30 min | 6.3 | 123.5 | 157.4 | 63% | 3,132 (± 58) | 32% | 4.2 |

## Sensitivity: interruptible nodes for low priority

| Run | p50 wait | p90 wait | p99 wait | Started ≤10 min | Total cost (± sd) | Idle share | Lost runs |
|---|---|---|---|---|---|---|---|
| fair share | 6.7 | 103.3 | 132.9 | 64% | 2,823 (± 31) | 25% | 6.4 |
| no interruptible | 6.4 | 118.9 | 154.1 | 64% | 3,048 (± 25) | 26% | 0.0 |

## Sensitivity: node size under slot pressure

| Run | p50 wait | p90 wait | p99 wait | Started ≤10 min | Total cost (± sd) | Idle share | Lost runs |
|---|---|---|---|---|---|---|---|
| fair share | 6.7 | 103.3 | 132.9 | 64% | 2,823 (± 31) | 25% | 6.4 |
| large nodes at 8/10 slots | 6.1 | 75.0 | 102.0 | 68% | 3,159 (± 59) | 34% | 6.0 |
| large nodes at 5/10 slots | 5.8 | 58.4 | 70.7 | 71% | 3,500 (± 31) | 40% | 5.0 |
