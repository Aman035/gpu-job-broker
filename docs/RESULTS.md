# Trace replay

`01a_trace.csv`: 494 rows, 13 identical rows collapsed as client retries, 2 rejected, 479 jobs replayed. Every figure is a mean over provider seeds 1-5.

- Rejected line 88: gpus must be one of [1, 2, 4, 8]
- Rejected line 393: gpus must be one of [1, 2, 4, 8]

Wait = minutes a job spent queued, summed over attempts. Costs in provider currency for the week.

## Policy comparison

| Run | p50 wait | p90 wait | p99 wait | Started ≤10 min | Total cost (± sd) | Idle share | Lost runs |
|---|---|---|---|---|---|---|---|
| fair share | 6.7 | 86.2 | 112.8 | 65% | 2,864 (± 42) | 26% | 13.8 |
| FIFO | 6.7 | 76.0 | 95.6 | 65% | 2,817 (± 65) | 25% | 14.0 |

## Per team

| Team | Jobs | fair share: p50 / p90 / p99 wait | FIFO: p50 / p90 / p99 wait | fair share: cost (idle) | FIFO: cost (idle) |
|---|---|---|---|---|---|
| forecast | 64 | 4.8 / 7.9 / 19.2 | 4.7 / 7.9 / 18.4 | 254 (38%) | 245 (38%) |
| nlp | 80 | 5.0 / 7.6 / 13.9 | 5.1 / 7.8 / 15.7 | 538 (27%) | 547 (28%) |
| platform | 24 | 0.0 / 13.1 / 76.6 | 4.4 / 59.1 / 88.9 | 414 (45%) | 376 (44%) |
| speech | 56 | 6.1 / 15.0 / 43.7 | 6.1 / 15.5 / 55.2 | 1,044 (7%) | 1,044 (7%) |
| vision | 255 | 34.2 / 99.5 / 125.7 | 34.2 / 86.5 / 101.8 | 614 (37%) | 605 (38%) |

## Sensitivity: idle timeout

| Run | p50 wait | p90 wait | p99 wait | Started ≤10 min | Total cost (± sd) | Idle share | Lost runs |
|---|---|---|---|---|---|---|---|
| idle timeout 0 min | 6.8 | 95.9 | 123.3 | 63% | 2,730 (± 88) | 24% | 7.4 |
| idle timeout 5 min | 6.7 | 86.2 | 112.8 | 65% | 2,864 (± 42) | 26% | 13.8 |
| idle timeout 10 min | 6.6 | 98.6 | 127.5 | 64% | 2,841 (± 44) | 26% | 17.4 |
| idle timeout 20 min | 6.3 | 100.2 | 128.1 | 65% | 3,121 (± 71) | 31% | 15.0 |
| idle timeout 30 min | 6.2 | 104.8 | 133.0 | 64% | 3,187 (± 32) | 32% | 15.2 |

## Sensitivity: interruptible nodes for low priority

| Run | p50 wait | p90 wait | p99 wait | Started ≤10 min | Total cost (± sd) | Idle share | Lost runs |
|---|---|---|---|---|---|---|---|
| fair share | 6.7 | 86.2 | 112.8 | 65% | 2,864 (± 42) | 26% | 13.8 |
| no interruptible | 6.4 | 88.7 | 111.8 | 65% | 3,077 (± 23) | 27% | 13.6 |

## Sensitivity: node size under slot pressure

| Run | p50 wait | p90 wait | p99 wait | Started ≤10 min | Total cost (± sd) | Idle share | Lost runs |
|---|---|---|---|---|---|---|---|
| fair share | 6.7 | 86.2 | 112.8 | 65% | 2,864 (± 42) | 26% | 13.8 |
| large nodes at 8/10 slots | 6.1 | 71.2 | 92.0 | 69% | 3,179 (± 32) | 34% | 11.0 |
| large nodes at 5/10 slots | 5.8 | 58.4 | 70.7 | 71% | 3,497 (± 46) | 40% | 5.0 |
