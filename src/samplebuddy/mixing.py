"""Pure-numpy post-processing that keeps stems summing back to the mix.

Arrays are (samples, channels)."""
import numpy as np


def repair_split(inst, parts):
    """Make Demucs parts (Drums, Bass, Other, residue) track the instrumental they came from.

    1) audio-separator rescales any stem whose peak exceeds 1.0 on its own, so refit each part's gain.
    2) Demucs occasionally invents ms-long bursts absent from its input. Where the parts stop
       summing to the instrumental, move the error into the loudest part with a 10 ms crossfade,
       which cancels the burst. Normal passages are below the threshold and left untouched.
    Returns the repaired parts as a new list."""
    win = np.hanning(441)

    def fit(mask):
        A = np.stack([p[mask].ravel() for p in parts], axis=1)
        return np.linalg.lstsq(A, inst[mask].ravel(), rcond=None)[0]

    def find_bad(g):
        err = inst - sum(p * gi for p, gi in zip(parts, g))
        e = np.abs(err).max(axis=1)
        return e > max(0.05 * np.abs(inst).max(), 8 * np.sqrt(np.mean(err ** 2)))

    everything = np.ones(len(inst), bool)
    g = fit(everything)
    bad = find_bad(g)
    if bad.any():  # refit without the bursts so they don't bias the gains
        near_bad = np.convolve(bad.astype(float), win, mode="same") > 0
        g = fit(~near_bad)
        bad = find_bad(g)
    parts = [p * gi for p, gi in zip(parts, g)]
    err = inst - sum(parts)
    if bad.any():
        w = np.clip(np.convolve(bad.astype(float), win, mode="same") / win.max(), 0, 1)[:, None]
        edges = np.flatnonzero(np.diff(np.concatenate([[0], w[:, 0] > 0, [0]])))
        for a, b in zip(edges[::2], edges[1::2]):
            k = max(range(len(parts)), key=lambda j: np.sum(parts[j][a:b] ** 2))
            parts[k][a:b] += w[a:b] * err[a:b]
    return parts


def shared_gain(mix, vocals, instrumental, stems, ceiling=0.999):
    """One gain for every stem of a song: least-squares fit of Vocals+Instrumental to the mix,
    lowered for all stems together if any stem would clip (keeps relative levels intact)."""
    est = vocals + instrumental
    g = float(np.sum(mix * est) / np.sum(est * est))
    return min(g, ceiling / max(np.abs(s).max() for s in stems))
