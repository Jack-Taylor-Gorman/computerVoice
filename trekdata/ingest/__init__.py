"""Audio ingest pipeline steps, one module per stage.

DAG: decode → vad → transcribe → align → loudness → snr → denoise(conditional)
     → embed → phoneme → archetype_tag → ready_for_label
"""
