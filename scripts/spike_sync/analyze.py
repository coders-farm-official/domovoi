"""Room-sync spike: offset analysis of two client logs.

Per callback a client logged dac_sys (the API's reported DAC time of the
buffer's first frame, true host clock) and pos (the server sample index it
put there). The stamped time of that sample is stream_t0 + pos/fs on the
server clock, which on this PC is the same perf_counter clock, so:

    error = dac_sys - (stream_t0 + pos/fs)      (>0: late)

"raw" uses the reported DAC time as is. "fit" replaces it with a sliding
linear fit of DAC time against frames consumed: the hardware consumes
frames at a steady rate, so the fit removes the API's report jitter.
Device-to-device offset = error_A - error_B on a common 50 ms grid.

Usage: python analyze.py a.npz b.npz [--warmup 15]
"""
import argparse

import numpy as np


def load(path):
    z = np.load(path)
    log = z["log"]
    fs, t0 = float(z["fs"]), float(z["stream_t0"])
    dac, pos, frames, action = log[:, 0], log[:, 1], log[:, 2], log[:, 3]
    sched = t0 + pos / fs
    raw = (dac - sched) * 1000
    consumed = np.concatenate([[0], np.cumsum(frames)[:-1]])
    fit = np.empty_like(dac)
    win = 10.0
    edges = np.arange(dac[0], dac[-1] + win, win)
    for lo in edges:
        m = (dac >= lo) & (dac < lo + win)
        if m.sum() < 10:
            fit[m] = dac[m]
            continue
        wide = (dac >= lo - win / 2) & (dac < lo + 1.5 * win)
        p = np.polyfit(consumed[wide], dac[wide], 1)
        fit[m] = np.polyval(p, consumed[m])
    fitted = (fit - sched) * 1000
    return dict(dac=dac, raw=raw, fit=fitted, action=action, est=z["est"],
                skew=float(z["skew_ppm"]), off=float(z["offset_ms"]),
                underruns=int(z["underruns"]), fs=fs, path=path)


def stats(x):
    a = np.abs(x)
    return f"median {np.median(a):.3f}  p95 {np.percentile(a, 95):.3f}  max {a.max():.3f} ms"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--warmup", type=float, default=15)
    o = ap.parse_args()
    A, B = load(o.a), load(o.b)
    start = max(A["dac"][0], B["dac"][0]) + o.warmup
    end = min(A["dac"][-1], B["dac"][-1])
    grid = np.arange(start, end, 0.05)
    print(f"window {end - start:.0f} s after {o.warmup:.0f} s warm-up")
    for c in (A, B):
        m = c["dac"] >= start
        dur = c["dac"][m][-1] - c["dac"][m][0]
        ins, drp, jmp = (c["action"][m] == 1).sum(), (c["action"][m] == -1).sum(), (c["action"][m] == 9).sum()
        e = c["est"]
        em = e[:, 0] >= start
        print(f"\n{c['path']}  skew {c['skew']:+.0f} ppm, offset {c['off']:+.0f} ms, underruns {c['underruns']}")
        print(f"  play error raw : {stats(c['raw'][m])}   (mean {c['raw'][m].mean():+.3f})")
        print(f"  play error fit : {stats(c['fit'][m])}   (mean {c['fit'][m].mean():+.3f})")
        print(f"  corrections    : {ins} inserts, {drp} drops, {jmp} jumps; net "
              f"{(drp - ins) / dur / c['fs'] * 1e6:+.1f} ppm "
              f"({(ins + drp) / dur:.1f}/s)")
        print(f"  clock estimate : error {stats(e[em, 1])}; rate est {np.median(e[em, 2]):+.1f} ppm")
    for key in ("raw", "fit"):
        d = np.interp(grid, A["dac"], A[key]) - np.interp(grid, B["dac"], B[key])
        print(f"\nA-B offset ({key}): {stats(d)}   (mean {d.mean():+.3f})")
        mins = ((grid - start) // 60).astype(int)
        per = [f"{np.percentile(np.abs(d[mins == k]), 95):.2f}" for k in np.unique(mins)]
        print(f"  p95 per minute: {' '.join(per)}")


if __name__ == "__main__":
    main()
