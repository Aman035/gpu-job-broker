# Retrieval: similar past failures

`POST /failures/search {query, k}` takes a free-text question or a failed
job's log and returns the most similar past failures, their fixes, a
suggested fix, and whether it abstained. Code: `src/broker/retrieval/`.
Full per-question results: [RETRIEVAL_RESULTS.md](RETRIEVAL_RESULTS.md)
(`broker-eval-retrieval` regenerates it).

## What the data asks for

- **Relevance means the same cause, not the same words.** `jwt expired while
  calling /v1/models` and `401 Unauthorized: token expired` share almost no
  tokens but have one cause.
- **Most of each log is noise.** Heartbeats, epoch counters and deprecation
  warnings appear in 20 to 37 of the 59 logs; the error is one line.
- **Everyday words for technical terms:** "disk is full" vs `No space left on
  device`, "blew up" vs `Gradient norm inf`.
- **Logs contain secrets** (`Authorization: Bearer …`, `SERVICE_PASSWORD=…`).
- **Six of the 24 questions have no answer** and should get none.
- **Traps:** three OOM logs have no recorded fix but are relevant; F900 and
  F901 are the same log with two different fixes.

## Pipeline and what each step buys

| Step | Given P@3 / P@5 | Held-out P@3 / P@5 | Log→log P@5 |
|---|---|---|---|
| BM25 on the raw log | 0.67 / 0.57 | 0.61 / 0.52 | 0.48 |
| + keep signal lines only | 0.67 / 0.62 | 0.67 / 0.57 | 0.74 |
| + index the resolution text | 0.81 / 0.80 | 0.65 / 0.58 | 0.80 |
| + glossary query expansion | 0.98 / 0.93 | 0.95 / 0.90 | 0.90 |
| + rank by shared fix | 1.00 / 0.99 | 0.97 / 0.97 | 1.00 |
| + abstain when nothing matches | 1.00 / 0.99 | 0.97 / 0.97 | 1.00 |

Abstention: 6 of 6 given and 5 of 5 held-out unanswerable questions return
nothing, and no answerable question is wrongly refused.

```mermaid
flowchart LR
    q["question or pasted log"] --> red["redact secrets"] --> sig["keep signal lines"] --> tok["tokenise"]
    tok --> hit{"glossary<br/>concept hit?"}
    hit -->|yes| exp["add glossary terms"] --> bm["BM25 over signal lines and fix text"]
    hit -->|no| strong{"strong literal<br/>match?"}
    strong -->|no| none["no match"]
    strong -->|yes| bm
    bm --> boost["boost logs that share a fix"] --> out["top k, suggested fix, ambiguity flag"]
```

1. **Redact first.** Secrets are masked before indexing, so they can never be
   returned or leak into an index.
2. **Signal lines, learned from the corpus.** A line whose template (digits
   masked) appears in more than 15% of logs is boilerplate; DEBUG lines are
   dropped. Nothing is hand-listed, so the same filter cleans a pasted log.
3. **Error-aware tokens.** Split `PermissionError` and `OOMKilled` (not
   `NaN`), keep short codes such as 137 and 401, drop step counts and shard
   ids, light stemming.
4. **Glossary expansion.** Nine failure concepts map the words people use to
   the words failures are logged in. Expansion terms weigh 0.4 so they help
   recall without outvoting what the user typed. This step is the largest
   single gain.
5. **Rank by shared fix.** Each log is boosted by the best score among logs
   that share its recorded fix. One strong match then lifts the same-cause
   logs that are worded differently. Logs without a fix stand alone, so the
   no-fix OOM logs are still found on their own text.
6. **Abstain.** A free-text question that hits no failure concept and has no
   strong literal match returns nothing. A wrong fix sent with confidence costs
   more than "no match".

The response also carries a **suggested fix**: a score-weighted vote over the
top matches, skipping logs with no fix, so a query that lands on F911 still
gets the OOM fix. It is flagged **ambiguous** when two top matches have
near-identical logs but different fixes (F900 and F901), instead of picking one
silently.

## How honest are these numbers

- **The given set is optimistic.** I wrote the glossary after reading those
  24 questions.
- **The held-out set is the better estimate.** I wrote its 24 paraphrases
  (`data/holdout_queries.jsonl`) before building the pipeline and did not tune
  on them; the four remaining misses there are left as they are. I wrote both
  the set and the pipeline, so your unseen questions are the real test.
- **Log→log** uses every log as the query, excluding itself, with relevance
  being the other logs of the same cause.

## Embeddings: measured, not shipped

A local embedding model (`bge-small-en-v1.5` via `fastembed`) scored
0.85 / 0.79 alone on the given questions. Merged with the lexical ranking by
reciprocal rank fusion
([Cormack et al., 2009](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)),
it matched the lexical pipeline on the held-out set and was slightly worse on
the given set (P@5 0.96 vs 0.99). Its similarity scores also overlap for
answerable and unanswerable questions (0.65 vs 0.66), so it does not help
abstention. Failures here are identified by exact error strings, which is
lexical retrieval's strength. Embeddings would add about 200 MB of
dependencies and a model download for no measured gain, so they are left out.

## Limits and next steps

- **Abstention depends on glossary coverage.** A real failure described in
  words the glossary lacks, with no literal log match, gets no answer. This is
  deliberate (refusing is safer than a wrong fix), but it is the main risk on
  unseen questions. Next step: grow the glossary from incident tickets, and
  revisit embeddings once there is enough data to show they help.
- **"Same fix" is an exact string match.** Real tickets word one fix many ways;
  fixes would need clustering first.
- **59 logs.** BM25 over an in-memory index is right at this size. At thousands
  of logs the same pipeline fits a search engine with these steps as analysers.
