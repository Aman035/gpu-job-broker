# Retrieval evaluation

P@k = share of the top k results whose id is relevant, averaged over questions that have relevant logs.
Abstain = questions with nothing relevant that correctly returned no results.
False abstain = questions with relevant logs that returned nothing.

| Pipeline | Given P@3 | Given P@5 | Given abstain | Given false abstain | Held-out P@3 | Held-out P@5 | Held-out abstain | Held-out false abstain | Log→log P@3 | Log→log P@5 |
|---|---|---|---|---|---|---|---|---|---|---|
| BM25 on the raw log | 0.667 | 0.567 | 3/6 | 0 | 0.614 | 0.516 | 0/5 | 1 | 0.643 | 0.477 |
| + keep signal lines only | 0.667 | 0.622 | 4/6 | 0 | 0.667 | 0.568 | 1/5 | 1 | 0.842 | 0.737 |
| + index the resolution text | 0.815 | 0.800 | 2/6 | 0 | 0.649 | 0.579 | 1/5 | 1 | 0.889 | 0.804 |
| + glossary query expansion | 0.981 | 0.933 | 2/6 | 0 | 0.947 | 0.895 | 1/5 | 0 | 0.942 | 0.902 |
| + rank by shared fix | 1.000 | 0.989 | 2/6 | 0 | 0.965 | 0.968 | 1/5 | 0 | 1.000 | 1.000 |
| + abstain when nothing matches (final) | 1.000 | 0.989 | 6/6 | 0 | 0.965 | 0.968 | 5/5 | 0 | 1.000 | 1.000 |

## Per question (final pipeline)

| Set | Id | Question | P@5 | Top 5 |
|---|---|---|---|---|
| given | Q01 | rank aborted communicator error | 1.0 | F030, F027, F025, F026, F028 |
| given | Q02 | loss turned to NaN after a few epochs | 1.0 | F024, F022, F019, F020, F021 |
| given | Q03 | can I run two jobs on one GPU | ok | (abstained) |
| given | Q04 | job crashed saving a checkpoint, disk is full | 1.0 | F033, F034, F031, F035, F036 |
| given | Q05 | my training job died, GPU memory exhausted | 1.0 | F004, F006, F001, F002, F003 |
| given | Q06 | job cannot download its container image, access denied | 0.8 | F009, F010, F011, F012, ~~F014~~ |
| given | Q07 | cannot read the training data, permission problem | 1.0 | F014, F016, F018, F013, F015 |
| given | Q08 | interruptible node was taken back and killed the job | 1.0 | F037, F041, F038, F039, F040 |
| given | Q09 | multi-GPU run hangs then times out in all-reduce | 1.0 | F030, F027, F025, F026, F028 |
| given | Q10 | no space left on device in scratch | 1.0 | F031, F035, F036, F033, F034 |
| given | Q11 | how do I reset my account password | ok | (abstained) |
| given | Q12 | where can I find the quarterly report | ok | (abstained) |
| given | Q13 | dataset file missing at startup | 1.0 | F014, F016, F018, F013, F015 |
| given | Q14 | node vanished mid run, termination notice | 1.0 | F038, F039, F037, F041, F040 |
| given | Q15 | access denied, the token has expired | 1.0 | F043, F045, F047, F046, F044 |
| given | Q16 | what is the maintenance window for the cluster | ok | (abstained) |
| given | Q17 | how do I rename my team | ok | (abstained) |
| given | Q18 | image pull keeps backing off with 401 | 1.0 | F007, F008, F012, F009, F010 |
| given | Q19 | container killed with exit code 137 | 1.0 | F006, F004, F911, F912, F001 |
| given | Q20 | torch complains driver too old for CUDA | 1.0 | F054, F049, F052, F050, F053 |
| given | Q21 | training blew up, gradients infinite | 1.0 | F024, F019, F020, F021, F022 |
| given | Q22 | libcudnn missing when starting the job | 1.0 | F049, F052, F054, F050, F053 |
| given | Q23 | how do I add a teammate to my group | ok | (abstained) |
| given | Q24 | token rejected, says expired | 1.0 | F045, F047, F043, F046, F044 |
| held-out | H01 | ran out of VRAM during the forward pass | 1.0 | F001, F002, F003, F005, F004 |
| held-out | H02 | process got OOM killed by the kernel | 1.0 | F004, F006, F001, F002, F003 |
| held-out | H03 | the run was killed because it used too much RAM loading data | 1.0 | F006, F004, F911, F912, F001 |
| held-out | H04 | training became unstable and the loss exploded | 1.0 | F024, F019, F020, F021, F022 |
| held-out | H05 | getting NaN values in the loss | 1.0 | F024, F023, F022, F019, F020 |
| held-out | H06 | collective operation timed out between GPUs | 1.0 | F027, F030, F025, F026, F028 |
| held-out | H07 | workers lost connection to each other during distributed training | 0.8 | F030, F027, ~~F901~~, F025, F026 |
| held-out | H08 | can't fetch the docker image, says unauthorized | 1.0 | F012, F009, F010, F011, F007 |
| held-out | H09 | registry login failed when starting the container | 1.0 | F012, F009, F010, F011, F007 |
| held-out | H10 | my API credentials are no longer valid | 0.8 | F043, F045, F047, F046, ~~F900~~ |
| held-out | H11 | authentication failed because the session expired | 0.8 | ~~F012~~, F045, F047, F046, F043 |
| held-out | H12 | cuda version incompatible with pytorch | 1.0 | F054, F049, F052, F050, F053 |
| held-out | H13 | shared library not found when importing torch | 1.0 | F049, F052, F054, F050, F053 |
| held-out | H14 | storage ran out while writing output | 1.0 | F031, F035, F036, F033, F034 |
| held-out | H15 | could not write the checkpoint, volume is full | 1.0 | F031, F035, F036, F033, F034 |
| held-out | H16 | training files are missing from the data folder | 1.0 | F014, F016, F018, F013, F015 |
| held-out | H17 | not allowed to open the labels file | 1.0 | F014, F016, F018, F013, F015 |
| held-out | H18 | spot instance was preempted | 1.0 | F037, F041, F038, F039, F040 |
| held-out | H19 | machine disappeared and heartbeats stopped | 1.0 | F040, F042, F037, F041, F038 |
| held-out | H20 | how much does a large node cost per hour | ok | (abstained) |
| held-out | H21 | who do I ask for more GPU quota | ok | (abstained) |
| held-out | H22 | how do I change the priority of my job | ok | (abstained) |
| held-out | H23 | what is the office wifi password | ok | (abstained) |
| held-out | H24 | schedule a meeting with the platform team | ok | (abstained) |
