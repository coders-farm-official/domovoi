"""Train the phonemizer's out-of-dictionary model (letters -> ARPAbet) on CMUdict.

This is the script that made ``domovoi/phonemizer/data/g2p/g2p.int8.onnx``.
Run it through ``train-in-container.sh`` (same folder), which pins the
Python image, the wheels (by SHA-256) and the CPU threads; the README in
``domovoi/phonemizer/`` has the recorded hashes and metrics.

Data: CMUdict only (BSD-2-Clause style licence), first pronunciation of
each word. Nothing else goes in: no espeak output, no other dictionary.

Model: each letter is repeated 3x, a small Transformer encoder reads the
sequence, and a CTC head emits ARPAbet-with-stress tokens. One forward
pass, argmax and collapse to decode, so it ports to ONNX Runtime on
Android/Kotlin without a beam search or an autoregressive loop.

Determinism: seed 0 for torch and ``random``; the split and the batch
order come from that seed. The weights are bit-for-bit the same only on
the same torch build, thread count and CPU family (float reductions run
in a different order elsewhere); the script prints every output's SHA-256
so a rerun can be compared with the recorded ones.

usage: python -I train_g2p.py <cmudict.dict> <outdir> [epochs]
       (environment: THREADS, default 16; the recorded run used 14 and 40 epochs)

MIT licence, like the rest of Domovoi.
"""
import hashlib
import json
import math
import os
import random
import re
import sys
import time

import numpy as np  # noqa: F401  (torch wants it importable for the ONNX export)
import torch
import torch.nn as nn
import torch.nn.functional as F

SEED = 0
UPS = 3
MAXC = 40
LETTERS = "abcdefghijklmnopqrstuvwxyz'"
VOWELS = ["AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"]
CONS = ["B", "CH", "D", "DH", "F", "G", "HH", "JH", "K", "L", "M", "N", "NG", "P", "R", "S", "SH", "T",
        "TH", "V", "W", "Y", "Z", "ZH"]
PHONES = ["<blank>"] + CONS + [v + s for v in VOWELS for s in "012"]
P2I = {p: i for i, p in enumerate(PHONES)}
C2I = {c: i + 1 for i, c in enumerate(LETTERS)}  # 0 = pad


def load(path):
    data = {}
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if not line:
            continue
        w, *ph = line.split()
        if re.search(r"\(\d+\)$", w):
            continue  # first variant only
        if not re.fullmatch(r"[a-z][a-z']*", w) or len(w) > MAXC:
            continue
        if any(p not in P2I for p in ph):
            continue
        data[w] = ph
    return data


class Block(nn.Module):
    def __init__(self, d, h, ff, drop):
        super().__init__()
        self.h = h
        self.n1 = nn.LayerNorm(d)
        self.qkv = nn.Linear(d, 3 * d)
        self.o = nn.Linear(d, d)
        self.n2 = nn.LayerNorm(d)
        self.f1 = nn.Linear(d, ff)
        self.f2 = nn.Linear(ff, d)
        self.drop = nn.Dropout(drop)

    def forward(self, x, bias):
        B, T, D = x.shape
        q, k, v = self.qkv(self.n1(x)).reshape(B, T, 3, self.h, D // self.h).permute(2, 0, 3, 1, 4)
        a = torch.matmul(q, k.transpose(-1, -2)) / math.sqrt(D // self.h)
        if bias is not None:
            a = a + bias
        a = torch.softmax(a, dim=-1)
        y = torch.matmul(a, v).transpose(1, 2).reshape(B, T, D)
        x = x + self.drop(self.o(y))
        x = x + self.drop(self.f2(self.drop(F.gelu(self.f1(self.n2(x))))))
        return x


class G2P(nn.Module):
    def __init__(self, d=256, h=4, ff=1024, layers=4, drop=0.1):
        super().__init__()
        self.emb = nn.Embedding(len(LETTERS) + 1, d)
        self.pos = nn.Embedding(MAXC * UPS + 8, d)
        self.blocks = nn.ModuleList([Block(d, h, ff, drop) for _ in range(layers)])
        self.norm = nn.LayerNorm(d)
        self.head = nn.Linear(d, len(PHONES))

    def forward(self, chars, mask=None):
        # chars: [B, T] int64 (0 = pad). mask: [B, T] bool, True = real.
        x = torch.repeat_interleave(chars, UPS, dim=1)
        T = x.shape[1]
        pos = torch.arange(T, device=x.device).unsqueeze(0)
        h = self.emb(x) + self.pos(pos)
        bias = None
        if mask is not None:
            m = torch.repeat_interleave(mask, UPS, dim=1)
            bias = (~m).float().mul(-1e9)[:, None, None, :]
        for b in self.blocks:
            h = b(h, bias)
        return self.head(self.norm(h))  # [B, T*UPS, V]


def encode(w):
    return [C2I[c] for c in w]


def decode(logits_row, n):
    ids = logits_row[:n].argmax(-1).tolist()
    out, prev = [], 0
    for i in ids:
        if i != prev and i != 0:
            out.append(PHONES[i])
        prev = i
    return out


def edit(a, b):
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        p, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (x != y))
    return d[-1]


def evaluate(model, items, bs=512):
    model.eval()
    werr = perr = ptot = 0
    nostress_w = 0
    with torch.no_grad():
        for i in range(0, len(items), bs):
            chunk = items[i:i + bs]
            L = max(len(w) for w, _ in chunk)
            x = torch.zeros(len(chunk), L, dtype=torch.long)
            m = torch.zeros(len(chunk), L, dtype=torch.bool)
            for k, (w, _) in enumerate(chunk):
                x[k, :len(w)] = torch.tensor(encode(w))
                m[k, :len(w)] = True
            lg = model(x, m)
            for k, (w, ph) in enumerate(chunk):
                hyp = decode(lg[k], len(w) * UPS)
                e = edit(hyp, ph)
                perr += e
                ptot += len(ph)
                werr += e > 0
                nostress_w += [re.sub(r"\d", "", p) for p in hyp] != [re.sub(r"\d", "", p) for p in ph]
    model.train()
    return werr / len(items), perr / ptot, nostress_w / len(items)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(dict_path, outdir, epochs=30):
    torch.manual_seed(SEED)
    random.seed(SEED)
    torch.set_num_threads(int(os.environ.get("THREADS", "16")))
    data = load(dict_path)
    words = sorted(data)
    random.shuffle(words)
    n_test = int(len(words) * 0.02)
    n_dev = int(len(words) * 0.01)
    test, dev, train = words[:n_test], words[n_test:n_test + n_dev], words[n_test + n_dev:]
    os.makedirs(outdir, exist_ok=True)
    json.dump({"test": test, "dev": dev}, open(os.path.join(outdir, "split.json"), "w"))
    tr = [(w, data[w]) for w in train][:int(os.environ.get("LIMIT", "10000000"))]
    dv = [(w, data[w]) for w in dev]
    te = [(w, data[w]) for w in test]
    print(f"train {len(tr)} dev {len(dv)} test {len(te)}", flush=True)

    model = G2P()
    nparam = sum(p.numel() for p in model.parameters())
    print("params", nparam, flush=True)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=0.01)
    bs = 256
    steps_per_epoch = math.ceil(len(tr) / bs)
    total = steps_per_epoch * epochs
    warm = 1000
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / warm) * max(0.02, 0.5 * (1 + math.cos(math.pi * min(1.0, s / total)))))
    ctc = nn.CTCLoss(blank=0, zero_infinity=True)
    best = None
    step = 0
    log = []
    for ep in range(epochs):
        t0 = time.time()
        # length-bucketed batches
        tr.sort(key=lambda x: len(x[0]) + random.random() * 3)
        batches = [tr[i:i + bs] for i in range(0, len(tr), bs)]
        random.shuffle(batches)
        tot = 0.0
        for b in batches:
            L = max(len(w) for w, _ in b)
            x = torch.zeros(len(b), L, dtype=torch.long)
            m = torch.zeros(len(b), L, dtype=torch.bool)
            tgt = []
            tl = []
            for k, (w, ph) in enumerate(b):
                x[k, :len(w)] = torch.tensor(encode(w))
                m[k, :len(w)] = True
                tgt.extend(P2I[p] for p in ph)
                tl.append(len(ph))
            lg = model(x, m)
            lp = lg.log_softmax(-1).transpose(0, 1)  # T,B,V
            il = torch.tensor([len(w) * UPS for w, _ in b])
            loss = ctc(lp, torch.tensor(tgt), il, torch.tensor(tl))
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            step += 1
            tot += loss.item()
        wer, per, wer_ns = evaluate(model, dv)
        rec = {"epoch": ep + 1, "loss": tot / len(batches), "dev_wer": wer, "dev_per": per,
               "dev_wer_nostress": wer_ns, "sec": time.time() - t0}
        log.append(rec)
        print(json.dumps(rec), flush=True)
        if best is None or wer < best:
            best = wer
            torch.save(model.state_dict(), os.path.join(outdir, "g2p.pt"))
    model.load_state_dict(torch.load(os.path.join(outdir, "g2p.pt")))
    wer, per, wer_ns = evaluate(model, te)
    res = {"test_words": len(te), "test_wer": wer, "test_per": per, "test_wer_nostress": wer_ns,
           "params": nparam, "epochs": epochs, "seed": SEED, "log": log}
    print(json.dumps({k: v for k, v in res.items() if k != "log"}), flush=True)
    json.dump(res, open(os.path.join(outdir, "train_result.json"), "w"), indent=1)

    # export ONNX (batch 1, dynamic length; no mask needed for a single word)
    model.eval()

    class Export(nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, chars):
            return self.m(chars, None)

    dummy = torch.tensor([encode("hello")])
    onnx_path = os.path.join(outdir, "g2p.onnx")
    torch.onnx.export(Export(model), (dummy,), onnx_path, input_names=["chars"], output_names=["logits"],
                      dynamic_axes={"chars": {1: "T"}, "logits": {1: "T3"}}, opset_version=17)
    json.dump({"letters": LETTERS, "upsample": UPS, "phones": PHONES, "max_chars": MAXC},
              open(os.path.join(outdir, "g2p_vocab.json"), "w"), indent=1)
    print("exported", os.path.getsize(onnx_path), flush=True)

    # The shipped model: dynamic int8 quantization of the export (weights
    # int8, activations quantized at run time). 3.3 MB against 12.9 MB, the
    # same held-out word error rate to the third decimal.
    from onnxruntime.quantization import QuantType, quantize_dynamic

    q8 = os.path.join(outdir, "g2p.int8.onnx")
    quantize_dynamic(onnx_path, q8, weight_type=QuantType.QInt8)
    for name in ("g2p.pt", "g2p.onnx", "g2p.int8.onnx", "g2p_vocab.json"):
        print(f"sha256 {sha256(os.path.join(outdir, name))}  {name}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 30)
