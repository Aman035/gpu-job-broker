# Trace replay

`01a_trace.csv`: 494 rows, 13 identical rows collapsed as client retries, 2 rejected, 479 jobs replayed. Every figure is a mean over provider seeds 1-5.

- Rejected line 88: gpus must be one of [1, 2, 4, 8]
- Rejected line 393: gpus must be one of [1, 2, 4, 8]

Wait = minutes a job spent queued, summed over attempts. Costs in provider currency for the week.

## Policy comparison

| Run | p50 wait | p90 wait | p99 wait | Started ≤10 min | Total cost (± sd) | Idle share | Lost runs |
|---|---|---|---|---|---|---|---|
| fair share | 6.6 | 103.2 | 132.9 | 64% | 2,822 (± 41) | 25% | 6.2 |
| FIFO | 6.7 | 85.3 | 109.8 | 64% | 2,866 (± 21) | 26% | 6.6 |

## Per team

| Team | Jobs | fair share: p50 / p90 / p99 wait | FIFO: p50 / p90 / p99 wait | fair share: cost (idle) | FIFO: cost (idle) |
|---|---|---|---|---|---|
| forecast | 64 | 4.9 / 7.9 / 21.6 | 4.9 / 9.6 / 20.7 | 234 (36%) | 259 (39%) |
| nlp | 80 | 5.1 / 7.8 / 16.6 | 5.2 / 7.8 / 13.7 | 540 (27%) | 557 (29%) |
| platform | 24 | 0.8 / 13.2 / 31.4 | 3.0 / 62.7 / 99.2 | 410 (45%) | 392 (45%) |
| speech | 56 | 6.0 / 13.9 / 41.6 | 6.1 / 13.2 / 62.5 | 1,040 (6%) | 1,041 (7%) |
| vision | 255 | 34.1 / 118.7 / 152.5 | 34.1 / 95.2 / 120.1 | 598 (36%) | 617 (39%) |

## Sensitivity: idle timeout

| Run | p50 wait | p90 wait | p99 wait | Started ≤10 min | Total cost (± sd) | Idle share | Lost runs |
|---|---|---|---|---|---|---|---|
| idle timeout 0 min | 6.8 | 95.9 | 123.3 | 63% | 2,730 (± 88) | 24% | 7.4 |
| idle timeout 5 min | 6.6 | 103.2 | 132.9 | 64% | 2,822 (± 41) | 25% | 6.2 |
| idle timeout 10 min | 6.5 | 118.4 | 151.3 | 64% | 2,910 (± 45) | 27% | 5.2 |
| idle timeout 20 min | 6.4 | 126.8 | 160.7 | 64% | 3,104 (± 45) | 31% | 4.6 |
| idle timeout 30 min | 6.3 | 123.5 | 157.4 | 63% | 3,135 (± 51) | 32% | 4.2 |

## Sensitivity: interruptible nodes for low priority

| Run | p50 wait | p90 wait | p99 wait | Started ≤10 min | Total cost (± sd) | Idle share | Lost runs |
|---|---|---|---|---|---|---|---|
| fair share | 6.6 | 103.2 | 132.9 | 64% | 2,822 (± 41) | 25% | 6.2 |
| no interruptible | 6.4 | 118.8 | 154.1 | 64% | 3,050 (± 26) | 26% | 0.0 |

## Sensitivity: node size under slot pressure

| Run | p50 wait | p90 wait | p99 wait | Started ≤10 min | Total cost (± sd) | Idle share | Lost runs |
|---|---|---|---|---|---|---|---|
| fair share | 6.6 | 103.2 | 132.9 | 64% | 2,822 (± 41) | 25% | 6.2 |
| large nodes at 8/10 slots | 6.1 | 74.9 | 102.0 | 68% | 3,159 (± 54) | 34% | 5.8 |
| large nodes at 5/10 slots | 5.8 | 58.4 | 70.7 | 71% | 3,497 (± 46) | 40% | 5.0 |
