"""Provider package for stage-19A model analysis.

Only the deterministic mock provider is implemented in stage 19A. This package
does not connect to any real model service, does not access trading interfaces,
and does not modify formal Kline tables.
"""

from app.model_analysis.providers.mock import MockModelReviewProvider

__all__ = ["MockModelReviewProvider"]
