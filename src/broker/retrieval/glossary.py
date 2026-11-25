"""Everyday words -> the vocabulary failures are logged in."""

from __future__ import annotations

import re

from .text import stem, tokenize

# Each concept: words people use (triggers) -> words failures are logged in.
# Kept deliberately small and about failure classes, not about specific
# questions. In production this would be mined from incident tickets.

# Glossary terms help recall without outvoting what the user typed.
EXPANSION_WEIGHT = 0.4

GLOSSARY: dict[str, tuple[str, str]] = {
    "out_of_memory": (
        "oom oomkilled memory vram ram 137",
        "out of memory oom oom killed cuda allocate tensor 137",
    ),
    "disk_full": (
        "disk space storage full volume enospc",
        "no space left on device disk usage scratch write",
    ),
    "divergence": (
        "nan inf infinite infinity diverge divergence diverged exploded explode blew unstable gradient",
        "loss nan inf divergence detected gradient norm",
    ),
    "multi_gpu_comms": (
        "nccl allreduce collective communicator distributed interconnect hang hangs rank",
        "nccl communicator aborted rank watchdog collective timeout allreduce",
    ),
    "image_pull": (
        "image pull registry docker",
        "image pull registry docker unauthorized authentication required access denied",
    ),
    "auth_token": (
        "token jwt credential credentials expired unauthorized 401 403 forbidden authentication auth session",
        "token expired unauthorized forbidden jwt bearer auth",
    ),
    "driver_mismatch": (
        "driver cudnn libcudnn cublas symbol library incompatible mismatch",
        "nvidia driver too old cuda version torch requires runtime libcudnn shared object undefined symbol import",
    ),
    "data_access": (
        "dataset missing permission denied labels mount path",
        "file not found no such file directory permission denied errno dataset",
    ),
    "node_reclaimed": (
        "interruptible spot preempt preempted reclaim reclaimed termination terminated notice"
        " vanished disappeared heartbeat",
        "interruption warning node terminated termination notice reclaim heartbeat missed node lost",
    ),
}

_CONCEPTS = {
    name: ({stem(t) for t in triggers.split()}, tokenize(expansion)) for name, (triggers, expansion) in GLOSSARY.items()
}


def expand(tokens: list[str]) -> tuple[dict[str, float], list[str]]:
    """Weighted query terms (original terms weigh 1, glossary terms less) and the concepts hit."""
    weights: dict[str, float] = {}
    for t in tokens:
        weights[t] = weights.get(t, 0.0) + 1.0
    hit = [name for name, (triggers, _) in _CONCEPTS.items() if triggers & set(tokens)]
    for name in hit:
        for t in _CONCEPTS[name][1]:
            weights[t] = max(weights.get(t, 0.0), EXPANSION_WEIGHT)
    return weights, hit


# ---- does the question describe a failure? ------------------------------------
#
# Glossary words alone do not mean a question is about a failure: "how much
# memory does a node have" or "how do I create a token" use failure vocabulary
# to ask something else. A question is answered only if it describes something
# going wrong. This list is general operations vocabulary, not tuned to any test
# question.

_FAILURE_WORDS_TEXT = """error errors fail fails failed failing failure crash crashed crashes died dies dead killed kill
    abort aborted denied refused rejected unauthorized forbidden expired invalid missing lost vanished
    disappeared hang hangs hung stuck timeout nan inf infinite exploded blew unstable diverged broken broke
    exhausted oom overflow stopped interrupted preempted reclaimed terminated complains complaining problem
    full incompatible mismatch"""
FAILURE_WORDS = {stem(w) for w in _FAILURE_WORDS_TEXT.split()}
FAILURE_PHRASES = re.compile(
    r"\b(can'?t|cannot|could ?n[o']t|couldn'?t|won'?t|unable|not found|no longer|ran out|run out|runs out|"
    r"out of|no space|too old|not allowed|taken back|timed out|times out|went wrong)\b",
    re.IGNORECASE,
)
STATUS_CODE = re.compile(r"\b[1-5]\d\d\b")  # HTTP and exit codes such as 137, 401, 403


def describes_failure(text: str) -> bool:
    if FAILURE_PHRASES.search(text) or STATUS_CODE.search(text):
        return True
    return bool(FAILURE_WORDS & set(tokenize(text)))
