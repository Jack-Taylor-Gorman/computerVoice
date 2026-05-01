"""DeepFilterNet3 conditional denoise (CPU, MIT)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _get_model():
    from df.enhance import init_df
    return init_df()


def denoise(wav_path: Path, out_path: Path) -> Path:
    from df.enhance import enhance, load_audio, save_audio
    model, df_state, _ = _get_model()
    audio, _ = load_audio(str(wav_path), sr=df_state.sr())
    enhanced = enhance(model, df_state, audio)
    save_audio(str(out_path), enhanced, df_state.sr())
    return out_path
