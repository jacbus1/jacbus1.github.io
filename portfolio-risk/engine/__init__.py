"""Deterministic allocation and risk calculations."""

from .model import BASE_WEIGHTS, expected_tail_loss, risk_snapshot

__all__ = ["BASE_WEIGHTS", "expected_tail_loss", "risk_snapshot"]
