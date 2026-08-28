"""Concrete ASR engine adapters.

Import these only from bootstrap-time registration; they pull in optional
third-party engines (e.g. ``sherpa_onnx``) that are not part of the base
install. The core resolves providers through ``asr.registry`` instead.
"""
