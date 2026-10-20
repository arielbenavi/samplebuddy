"""Filename convention: "<BPM> BPM <Key> - <original name>", stems as "<name> (<Stem>)"."""
import re

STEM_NAMES = ("Vocals", "Drums", "Bass", "Other", "Instrumental")

# Matches both notations we write: "92 BPM Am - ..." and "92 BPM 8A - ..."
PREFIX_RE = re.compile(r"^(?P<bpm>\d{2,3}) BPM (?P<key>[A-G][#b]?m?|1[0-2][AB]|[1-9][AB]) - (?P<rest>.+)$")

# Camelot wheel, keyed by the names KeyExtractor emits (it uses sharps and "Ab", "Bb", "Eb")
_CAMELOT = {
    "C": "8B", "G": "9B", "D": "10B", "A": "11B", "E": "12B", "B": "1B", "F#": "2B",
    "C#": "3B", "Ab": "4B", "Eb": "5B", "Bb": "6B", "F": "7B",
    "Am": "8A", "Em": "9A", "Bm": "10A", "F#m": "11A", "C#m": "12A", "Abm": "1A", "G#m": "1A",
    "Ebm": "2A", "D#m": "2A", "Bbm": "3A", "A#m": "3A", "Fm": "4A", "Cm": "5A", "Gm": "6A", "Dm": "7A",
    "Db": "3B", "Gb": "2B", "D#": "5B", "G#": "4B", "A#": "6B",
}


def key_label(key: str, scale: str, notation: str = "standard") -> str:
    """'A', 'minor' -> 'Am' (standard) or '8A' (camelot)."""
    std = key + ("m" if scale == "minor" else "")
    return _CAMELOT.get(std, std) if notation == "camelot" else std


def fold_bpm(bpm: float, lo: float, hi: float) -> int:
    """Halve/double into [lo, hi] to undo half/double-time detections."""
    while bpm > hi:
        bpm /= 2
    while bpm < lo:
        bpm *= 2
    return round(bpm)


def prefixed(stem: str, bpm: int, key: str) -> str:
    return f"{bpm} BPM {key} - {stem}"


def parse_prefix(stem: str):
    """Return (bpm, key, rest) if the name already carries our prefix, else None."""
    m = PREFIX_RE.match(stem)
    return (int(m["bpm"]), m["key"], m["rest"]) if m else None


def stems_dir_name(stem: str) -> str:
    return f"{stem} [Stems]"


def stem_file_name(stem: str, part: str) -> str:
    return f"{stem} ({part}).flac"
