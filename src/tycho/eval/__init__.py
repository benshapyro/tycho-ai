"""Scoring speech recognition and understanding against recorded phrases."""

from tycho.eval.wer import ClipScore, normalize, score_clip, word_errors

__all__ = ["ClipScore", "normalize", "score_clip", "word_errors"]
