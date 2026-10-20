"""BPM and key detection (Essentia) and tagging (mutagen)."""
from dataclasses import dataclass

from . import naming


@dataclass
class Analysis:
    bpm: int
    key: str            # in the requested notation
    key_strength: float  # KeyExtractor confidence, 0..1; below ~0.55 is worth checking by ear


def analyze(path: str, bpm_range=(65, 150), key_notation="standard") -> Analysis:
    import essentia
    essentia.log.warningActive = False
    import essentia.standard as es

    audio = es.MonoLoader(filename=path, sampleRate=44100)()
    bpm = es.RhythmExtractor2013(method="multifeature")(audio)[0]
    key, scale, strength = es.KeyExtractor(profileType="bgate")(audio)
    return Analysis(naming.fold_bpm(bpm, *bpm_range), naming.key_label(key, scale, key_notation), float(strength))


def write_tags(path: str, a: Analysis) -> bool:
    """Write BPM + initial key. Returns False for formats we don't tag."""
    import mutagen
    from mutagen.easyid3 import EasyID3

    if "initialkey" not in EasyID3.valid_keys:
        EasyID3.RegisterTextKey("initialkey", "TKEY")
    f = mutagen.File(path, easy=True)
    if f is None:
        return False
    try:
        if f.tags is None:
            f.add_tags()
        f["bpm"] = str(a.bpm)
        f["initialkey"] = a.key
        f.save()
    except (KeyError, ValueError, mutagen.MutagenError):
        return False  # e.g. MP4 easy tags have no key field
    return True
