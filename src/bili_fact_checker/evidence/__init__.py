"""Auditable evidence retrieval, extraction, assessment, and aggregation."""

from bili_fact_checker.evidence.core import aggregate_verdict
from bili_fact_checker.evidence.fetch import fetch_candidate
from bili_fact_checker.evidence.service import EvidenceService

__all__ = ["EvidenceService", "aggregate_verdict", "fetch_candidate"]
