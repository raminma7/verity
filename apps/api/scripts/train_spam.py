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

The corpus's legitimate mail is personal and mailing-list mail, so support vocabulary (order, refund, invoice,
price, discount, free trial) otherwise reads as spam. scripts/support_ham.txt holds legitimate support email
written for this project, and scripts/support_spam.txt the phishing, fake invoices and cold pitches that use the
same words. Both are weighted up and 20% of each is held out; the script prints how the held-out support mail
and support-styled spam would be filed. An optional second argument sets that weight.
"""

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.mail.parse import parse  # noqa: E402
from app.services.mail.spam_model import DIM, HAM_AT, SPAM_AT, WEIGHTS, features, hashed, sigmoid  # noqa: E402

SPAM_DIRS = ("spam", "spam_2")
HAM_DIRS = ("easy_ham", "easy_ham_2", "hard_ham")
EPOCHS = 12
L2 = 1e-6
KEEP = 40000
SUPPORT_HAM = Path(__file__).with_name("support_ham.txt")
SUPPORT_SPAM = Path(__file__).with_name("support_spam.txt")
SUPPORT_WEIGHT = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0  # each written message counts like this many corpus ones

Example = tuple[list[int], int, float]


def load_support() -> list[Example]:
    out = []
    for label, path in ((0, SUPPORT_HAM), (1, SUPPORT_SPAM)):
        for line in path.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            subject, body = line.split("\t", 1)
            out.append((hashed(features(subject, body, None, "sender@example.org")), label, SUPPORT_WEIGHT))
    return out


def load(root: Path) -> list[Example]:
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
                out.append((hashed(features(p.subject, p.text, p.html, p.from_addr)), label, 1.0))
    return out


PRIOR = 0.2  # the bias is fixed at this spam rate, so a message with little evidence leans legitimate


def train(data: list[Example], seed: int = 7) -> tuple[float, dict[int, float]]:
    rnd = random.Random(seed)
    w: dict[int, float] = {}
    bias = math.log(PRIOR / (1 - PRIOR))
    pos = sum(y for _, y, _ in data)
    # Balance the classes: spam is about a third of the corpus.
    cw = {1: len(data) / (2 * pos), 0: len(data) / (2 * (len(data) - pos))}
    order = list(range(len(data)))
    for epoch in range(EPOCHS):
        rnd.shuffle(order)
        lr = 0.2 / (1 + epoch)
        for i in order:
            idx, y, weight = data[i]
            z = bias + sum(w.get(j, 0.0) for j in idx)
            g = (sigmoid(z) - y) * cw[y] * weight
            for j in idx:
                wj = w.get(j, 0.0)
                w[j] = wj - lr * (g + L2 * wj)
    return bias, w


def evaluate(bias: float, w: dict[int, float], data, at: float) -> dict:
    tp = fp = fn = tn = 0
    for idx, y, _ in data:
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


def flagged(bias: float, w: dict[int, float], data: list[Example]) -> dict:
    ps = [sigmoid(bias + sum(w.get(j, 0.0) for j in idx)) for idx, _, _ in data]
    return {
        "messages": len(ps),
        "possible_spam": round(sum(p > HAM_AT for p in ps) / max(1, len(ps)), 4),
        "sent_to_spam": round(sum(p >= SPAM_AT for p in ps) / max(1, len(ps)), 4),
    }


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    data = load(root)
    support = load_support()
    rnd = random.Random(11)
    spam = [d for d in data if d[1]]
    ham = [d for d in data if not d[1]]
    rnd.shuffle(spam)
    rnd.shuffle(ham)
    s_ham = [d for d in support if not d[1]]
    s_spam = [d for d in support if d[1]]
    rnd.shuffle(s_ham)
    rnd.shuffle(s_spam)
    cut_s, cut_h, cut_a, cut_b = len(spam) // 5, len(ham) // 5, len(s_ham) // 5, len(s_spam) // 5
    test = spam[:cut_s] + ham[:cut_h]
    trainset = spam[cut_s:] + ham[cut_h:] + s_ham[cut_a:] + s_spam[cut_b:]
    print(f"{len(spam)} spam, {len(ham)} legitimate; written: {len(s_ham)} support, {len(s_spam)} support-styled spam")
    bias, w = train(trainset)
    for at in (0.5, 0.95):
        print("held out:", evaluate(bias, w, test, at))
    print("held-out support mail:", flagged(bias, w, s_ham[:cut_a]))
    print("held-out support-styled spam:", flagged(bias, w, s_spam[:cut_b]))
    data = data + support
    bias, w = train(data)
    kept = dict(sorted(w.items(), key=lambda kv: -abs(kv[1]))[:KEEP])
    pruned = {str(k): round(v, 4) for k, v in kept.items() if abs(v) >= 0.005}
    print("pruned model on held out:", evaluate(bias, {int(k): v for k, v in pruned.items()}, test, 0.95), "(trained on it: optimistic)")
    WEIGHTS.write_text(
        json.dumps(
            {
                "dim": DIM,
                "bias": round(bias, 4),
                "trained_on": "Apache SpamAssassin public corpus (spam, spam_2; easy_ham, easy_ham_2, hard_ham), scripts/support_*.txt",
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
