"""Trains the base spam model offline and writes app/services/mail/spam_base.json. Never runs in CI.

Usage (from apps/api, with the corpus unpacked somewhere outside the repository):

    .venv/bin/python scripts/train_spam.py /path/to/corpus

The corpus is the Apache SpamAssassin public corpus (https://spamassassin.apache.org/old/publiccorpus/):
spam/ and spam_2/ are spam; easy_ham/, easy_ham_2/ and hard_ham/ are legitimate. It has no formal licence: it
is offered "as a service to spam filter developers", and copyright of each message stays with its sender. So
only learned weights over hashed features are shipped, never message text, and the CI test uses examples
written for this project instead of corpus messages.

Messages are read with the app's own parser and feature function, so training sees exactly what production
sees. 20% is held out (stratified, fixed seed) and precision and recall are printed; then the model is
retrained on everything and pruned to its strongest weights.
"""

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.mail.parse import parse  # noqa: E402
from app.services.mail.spam_model import DIM, WEIGHTS, features, hashed, sigmoid  # noqa: E402

SPAM_DIRS = ("spam", "spam_2")
HAM_DIRS = ("easy_ham", "easy_ham_2", "hard_ham")
EPOCHS = 12
L2 = 1e-6
KEEP = 40000


def load(root: Path) -> list[tuple[list[int], int]]:
    out = []
    for label, dirs in ((1, SPAM_DIRS), (0, HAM_DIRS)):
        for d in dirs:
            for f in sorted((root / d).glob("*")):
                if f.name.startswith("cmds") or not f.is_file():
                    continue
                try:
                    p = parse(f.read_bytes())
                except Exception:  # noqa: BLE001 - skip what the parser can't read
                    continue
                out.append((hashed(features(p.subject, p.text, p.html, p.from_addr)), label))
    return out


PRIOR = 0.2  # the bias is fixed at this spam rate, so a message with little evidence leans legitimate


def train(data: list[tuple[list[int], int]], seed: int = 7) -> tuple[float, dict[int, float]]:
    rnd = random.Random(seed)
    w: dict[int, float] = {}
    bias = math.log(PRIOR / (1 - PRIOR))
    pos = sum(y for _, y in data)
    # Balance the classes: spam is about a third of the corpus.
    cw = {1: len(data) / (2 * pos), 0: len(data) / (2 * (len(data) - pos))}
    order = list(range(len(data)))
    for epoch in range(EPOCHS):
        rnd.shuffle(order)
        lr = 0.2 / (1 + epoch)
        for i in order:
            idx, y = data[i]
            z = bias + sum(w.get(j, 0.0) for j in idx)
            g = (sigmoid(z) - y) * cw[y]
            for j in idx:
                wj = w.get(j, 0.0)
                w[j] = wj - lr * (g + L2 * wj)
    return bias, w


def evaluate(bias: float, w: dict[int, float], data, at: float) -> dict:
    tp = fp = fn = tn = 0
    for idx, y in data:
        p = sigmoid(bias + sum(w.get(j, 0.0) for j in idx))
        guess = 1 if p >= at else 0
        tp += guess and y
        fp += guess and not y
        fn += (not guess) and y
        tn += (not guess) and (not y)
    return {
        "threshold": at,
        "precision": round(tp / max(1, tp + fp), 4),
        "recall": round(tp / max(1, tp + fn), 4),
        "false_positive_rate": round(fp / max(1, fp + tn), 4),
    }


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    data = load(root)
    rnd = random.Random(11)
    spam = [d for d in data if d[1]]
    ham = [d for d in data if not d[1]]
    rnd.shuffle(spam)
    rnd.shuffle(ham)
    cut_s, cut_h = len(spam) // 5, len(ham) // 5
    test, trainset = spam[:cut_s] + ham[:cut_h], spam[cut_s:] + ham[cut_h:]
    print(f"{len(spam)} spam, {len(ham)} legitimate; training on {len(trainset)}, holding out {len(test)}")
    bias, w = train(trainset)
    for at in (0.5, 0.95):
        print("held out:", evaluate(bias, w, test, at))
    bias, w = train(data)
    kept = dict(sorted(w.items(), key=lambda kv: -abs(kv[1]))[:KEEP])
    pruned = {str(k): round(v, 4) for k, v in kept.items() if abs(v) >= 0.005}
    print("pruned model on held out:", evaluate(bias, {int(k): v for k, v in pruned.items()}, test, 0.95), "(trained on it: optimistic)")
    WEIGHTS.write_text(
        json.dumps(
            {
                "dim": DIM,
                "bias": round(bias, 4),
                "trained_on": "Apache SpamAssassin public corpus (spam, spam_2; easy_ham, easy_ham_2, hard_ham)",
                "messages": len(data),
                "w": pruned,
            },
            separators=(",", ":"),
        )
    )
    print(f"wrote {WEIGHTS} ({WEIGHTS.stat().st_size // 1024} KB, {len(pruned)} weights)")
    assert math.isfinite(bias)


if __name__ == "__main__":
    main()
