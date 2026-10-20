"""Two-pass stem separation (the Ultimate Vocal Remover engine, via audio-separator).

Pass 1: BS-Roformer (viperx 1297) -> Vocals + Instrumental.
Pass 2: Demucs htdemucs_ft on the Instrumental -> Drums, Bass, Other.
Output is 24-bit FLAC; all stems of a song share one gain so they sum back to the mix."""
import logging
import os
import subprocess
import tempfile

import numpy as np
import soundfile as sf

from . import mixing, naming

VOCAL_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
SPLIT_MODEL = "htdemucs_ft.yaml"


class StemSplitter:
    """Loads both models once; call split() per file."""

    def __init__(self, model_dir: str):
        from audio_separator.separator import Separator

        self._tmp = tempfile.TemporaryDirectory(prefix="samplebuddy-")
        self.tmp = self._tmp.name

        def load(model):
            # normalization_threshold=1.0: only scales a stem that would clip (we feed -6 dB so it shouldn't)
            s = Separator(output_dir=self.tmp, output_format="FLAC", model_file_dir=model_dir,
                          normalization_threshold=1.0, log_level=logging.WARNING)
            s.load_model(model_filename=model)
            return s

        self.vocal_sep, self.split_sep = load(VOCAL_MODEL), load(SPLIT_MODEL)

    def close(self):
        self._tmp.cleanup()

    def _t(self, name):
        return os.path.join(self.tmp, f"{name}.flac")

    def split(self, path: str, out_dir: str, name: str, on_phase=lambda phase: None) -> list[str]:
        mix, sr = _read(path, self._t("decoded"))
        on_phase("separating vocals")
        # Feed it -6 dB at 24-bit (output bit depth follows input) so the per-stem normalizer never fires
        sf.write(self._t("in"), mix * 0.5, sr, subtype="PCM_24")
        self.vocal_sep.separate(self._t("in"), {"Vocals": "Vocals", "Instrumental": "Instrumental"})
        on_phase("splitting drums, bass, other")
        self.split_sep.separate(self._t("Instrumental"),
                                {"Drums": "Drums", "Bass": "Bass", "Other": "Other", "Vocals": "residue"})

        raw = {k: sf.read(self._t(k), always_2d=True)[0]
               for k in ("Vocals", "Instrumental", "Drums", "Bass", "Other", "residue")}
        n = min(len(mix), *(len(v) for v in raw.values()))
        raw = {k: v[:n] for k, v in raw.items()}
        drums, bass, other, res = mixing.repair_split(
            raw["Instrumental"], [raw["Drums"], raw["Bass"], raw["Other"], raw["residue"]])
        stems = {
            "Vocals": raw["Vocals"],
            "Drums": drums,
            "Bass": bass,
            # Demucs's "vocals" from an instrumental isn't vocals; keep it so D+B+O == Instrumental
            "Other": other + res,
            "Instrumental": raw["Instrumental"],
        }
        g = mixing.shared_gain(mix[:n], stems["Vocals"], stems["Instrumental"], stems.values())

        on_phase("writing stems")
        os.makedirs(out_dir, exist_ok=True)
        written = []
        for part in naming.STEM_NAMES:
            dest = os.path.join(out_dir, naming.stem_file_name(name, part))
            sf.write(dest, stems[part] * g, sr, subtype="PCM_24")
            written.append(dest)
        for k in ("decoded", "in", "Vocals", "Instrumental", "Drums", "Bass", "Other", "residue"):
            if os.path.exists(self._t(k)):
                os.remove(self._t(k))
        return written


def _read(path, decoded_path):
    """Read any audio file; fall back to ffmpeg for formats libsndfile can't open (m4a, etc.)."""
    try:
        data, sr = sf.read(path, always_2d=True)
    except (sf.LibsndfileError, RuntimeError):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-c:a", "flac",
                        "-sample_fmt", "s32", decoded_path], check=True)
        data, sr = sf.read(decoded_path, always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    return data, sr
