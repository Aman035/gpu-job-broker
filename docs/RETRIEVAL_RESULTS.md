# Retrieval evaluation

Each cell: P@3 / P@5 / unanswerable questions correctly refused (answerable questions wrongly refused).
P@k is the share of the top k results whose id is relevant, averaged over questions that have relevant logs.
Sets run from least to most independent of the pipeline; see `src/broker/retrieval/evaluate.py`.

| Pipeline | Given | Own held-out | Blind | Log→log P@3 / P@5 |
|---|---|---|---|---|
| BM25 on the raw log | 0.67 / 0.57 / 3 of 6 (0) | 0.61 / 0.52 / 0 of 5 (1) | 0.63 / 0.56 / 0 of 6 (0) | 0.64 / 0.48 |
| + keep signal lines only | 0.67 / 0.62 / 4 of 6 (0) | 0.67 / 0.57 / 1 of 5 (1) | 0.57 / 0.54 / 0 of 6 (0) | 0.84 / 0.74 |
| + index the resolution text | 0.81 / 0.80 / 2 of 6 (0) | 0.65 / 0.58 / 1 of 5 (1) | 0.74 / 0.67 / 0 of 6 (0) | 0.89 / 0.80 |
| + glossary query expansion | 0.98 / 0.93 / 2 of 6 (0) | 0.95 / 0.89 / 1 of 5 (0) | 0.98 / 0.93 / 0 of 6 (0) | 0.94 / 0.90 |
| + rank by shared fix | 1.00 / 0.99 / 2 of 6 (0) | 0.96 / 0.97 / 1 of 5 (0) | 1.00 / 0.98 / 0 of 6 (0) | 1.00 / 1.00 |
| + abstain unless a glossary concept is hit (v1) | 1.00 / 0.99 / 6 of 6 (0) | 0.96 / 0.97 / 5 of 5 (0) | 1.00 / 0.98 / 3 of 6 (0) | 1.00 / 1.00 |
| + abstain unless the question describes a failure (final) | 1.00 / 0.99 / 6 of 6 (0) | 0.96 / 0.97 / 5 of 5 (0) | 0.94 / 0.92 / 4 of 6 (1) | 1.00 / 1.00 |

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
| own held-out | H01 | ran out of VRAM during the forward pass | 1.0 | F001, F002, F003, F005, F004 |
| own held-out | H02 | process got OOM killed by the kernel | 1.0 | F004, F006, F001, F002, F003 |
| own held-out | H03 | the run was killed because it used too much RAM loading data | 1.0 | F006, F004, F911, F912, F001 |
| own held-out | H04 | training became unstable and the loss exploded | 1.0 | F024, F019, F020, F021, F022 |
| own held-out | H05 | getting NaN values in the loss | 1.0 | F024, F023, F022, F019, F020 |
| own held-out | H06 | collective operation timed out between GPUs | 1.0 | F027, F030, F025, F026, F028 |
| own held-out | H07 | workers lost connection to each other during distributed training | 0.8 | F030, F027, ~~F901~~, F025, F026 |
| own held-out | H08 | can't fetch the docker image, says unauthorized | 1.0 | F012, F009, F010, F011, F007 |
| own held-out | H09 | registry login failed when starting the container | 1.0 | F012, F009, F010, F011, F007 |
| own held-out | H10 | my API credentials are no longer valid | 0.8 | F043, F045, F047, F046, ~~F900~~ |
| own held-out | H11 | authentication failed because the session expired | 0.8 | ~~F012~~, F045, F047, F046, F043 |
| own held-out | H12 | cuda version incompatible with pytorch | 1.0 | F054, F049, F052, F050, F053 |
| own held-out | H13 | shared library not found when importing torch | 1.0 | F049, F052, F054, F050, F053 |
| own held-out | H14 | storage ran out while writing output | 1.0 | F031, F035, F036, F033, F034 |
| own held-out | H15 | could not write the checkpoint, volume is full | 1.0 | F031, F035, F036, F033, F034 |
| own held-out | H16 | training files are missing from the data folder | 1.0 | F014, F016, F018, F013, F015 |
| own held-out | H17 | not allowed to open the labels file | 1.0 | F014, F016, F018, F013, F015 |
| own held-out | H18 | spot instance was preempted | 1.0 | F037, F041, F038, F039, F040 |
| own held-out | H19 | machine disappeared and heartbeats stopped | 1.0 | F040, F042, F037, F041, F038 |
| own held-out | H20 | how much does a large node cost per hour | ok | (abstained) |
| own held-out | H21 | who do I ask for more GPU quota | ok | (abstained) |
| own held-out | H22 | how do I change the priority of my job | ok | (abstained) |
| own held-out | H23 | what is the office wifi password | ok | (abstained) |
| own held-out | H24 | schedule a meeting with the platform team | ok | (abstained) |
| blind | B01 | my training job keeps getting killed, looks like the gpu ran out of room when I bumped the batch size | 1.0 | F004, F006, F001, F002, F003 |
| blind | B02 | exit 137 again right after the data finishes loading, what do I do | 1.0 | F006, F004, F911, F912, F001 |
| blind | B03 | job is stuck at startup, says it cant fetch the container image, unauthorized | 1.0 | F012, F009, F010, F011, F007 |
| blind | B04 | docker says access denied when my job tries to grab the training image, did our registry login lapse? | 1.0 | F009, F010, F011, F012, F007 |
| blind | B05 | script crashes immediately saying it cant find my data files, path looks right to me | 1.0 | F014, F016, F018, F013, F015 |
| blind | B06 | PermissionError: [Errno 13] Permission denied: '/mnt/datasets/vision/labels.csv' | 1.0 | F013, F015, F017, F018, F014 |
| blind | B07 | loss blew up to nan after a few thousand steps, any idea how to stop it | 1.0 | F024, F022, F019, F020, F021 |
| blind | B08 | my model trained fine for a while then the loss just exploded and the run aborted | 1.0 | F022, F024, F019, F020, F021 |
| blind | B09 | multi gpu run hangs then dies, something about the gpus not talking to each other | 1.0 | F030, F027, F025, F026, F028 |
| blind | B10 | Watchdog caught collective operation timeout: WorkNCCL(OpType=ALLREDUCE) ran for 1800000 ms | 1.0 | F027, F030, F025, F026, F028 |
| blind | B11 | ran out of storage on the box while saving checkpoints, job crashed | 1.0 | F033, F034, F031, F035, F036 |
| blind | B12 | job died writing output, says the drive is full | 1.0 | F033, F034, F031, F035, F036 |
| blind | B13 | my cheap spot machine got yanked away in the middle of training | 0.0 | (abstained) |
| blind | B14 | node just vanished mid run, scheduler lost contact with it, do I need to resubmit? | 1.0 | F040, F042, F037, F041, F038 |
| blind | B15 | getting 401s from the auth service partway through my job, my credentials seem to have timed out | 1.0 | F045, F047, F044, F048, F046 |
| blind | B16 | calls to the models api start failing with an expired jwt after a day | 1.0 | F046, F043, F045, F047, F044 |
| blind | B17 | job crashes on import, some cuda library .so file missing | 0.6 | F049, F052, F054, ~~F014~~, ~~F016~~ |
| blind | B18 | pytorch complains the nvidia driver version is too old for the cuda it wants | 1.0 | F054, F049, F052, F050, F053 |
| blind | B19 | how much does an hour on the big 80GB memory GPUs cost us per team? | ok | (abstained) |
| blind | B20 | how do I add a new teammate to our project so they can submit jobs? | ok | (abstained) |
| blind | B21 | what is the max number of nodes I can request for a single job before needing approval? | ok | (abstained) |
| blind | B22 | how do I generate a personal access token for the CLI the first time? | MISS | F043, F045, F047, F046, F044 |
| blind | B23 | can I get more disk quota for our team bucket for next quarter, who approves it? | ok | (abstained) |
| blind | B24 | how do I set up early stopping and a learning rate schedule in our training template? | MISS | F022, F019, F020, F021, F024 |
