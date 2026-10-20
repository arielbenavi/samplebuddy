import numpy as np

from samplebuddy import mixing

SR = 44100


def _snr(ref, est):
    return 20 * np.log10(np.std(ref) / np.std(ref - est))


def _parts(seed=0, seconds=4):
    rng = np.random.default_rng(seed)
    t = np.arange(SR * seconds) / SR
    drums = (rng.standard_normal((len(t), 2)) * (np.sin(2 * np.pi * 2 * t) > 0.9)[:, None]) * 0.2
    bass = np.stack([np.sin(2 * np.pi * 55 * t)] * 2, axis=1) * 0.2
    other = np.stack([np.sin(2 * np.pi * 440 * t), np.sin(2 * np.pi * 660 * t)], axis=1) * 0.1
    res = rng.standard_normal((len(t), 2)) * 0.005
    return drums, bass, other, res


def test_repair_undoes_per_stem_rescale():
    d, b, o, r = _parts()
    inst = d + b + o + r
    fixed = mixing.repair_split(inst, [d * 0.3, b, o, r])  # drums silently normalized down
    assert _snr(inst, sum(fixed)) > 60
    assert np.allclose(fixed[0], d, atol=1e-6)


def test_repair_removes_invented_click():
    d, b, o, r = _parts(1)
    inst = d + b + o + r
    clicky = d.copy()
    clicky[SR + 100:SR + 130] += 0.9  # 30-sample burst not in the input
    fixed = mixing.repair_split(inst, [clicky, b, o, r])
    assert np.abs(fixed[0][SR + 90:SR + 140]).max() < 0.3
    assert _snr(inst, sum(fixed)) > 25
    # far from the click the drums are untouched
    assert np.allclose(fixed[0][: SR // 2], d[: SR // 2], atol=1e-3)


def test_shared_gain_and_clip_guard():
    d, b, o, r = _parts(2)
    vocals = np.stack([np.sin(np.arange(len(d)) / 30.0)] * 2, axis=1) * 0.1
    inst = (d + b + o + r) * 0.3  # quiet enough that the fitted 2x gain doesn't clip
    mix = vocals + inst
    g = mixing.shared_gain(mix, vocals * 0.5, inst * 0.5, [vocals * 0.5, inst * 0.5])
    assert abs(g - 2.0) < 1e-6
    # if the fitted gain would clip a stem, every stem is lowered together
    g = mixing.shared_gain(mix * 3, vocals, inst, [vocals, inst])
    assert np.abs(vocals * g).max() <= 0.999 + 1e-9 and np.abs(inst * g).max() <= 0.999 + 1e-9
