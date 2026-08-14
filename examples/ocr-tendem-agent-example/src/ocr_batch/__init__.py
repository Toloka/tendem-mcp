"""Batch document OCR with a human expert as the fallback.

The model reads what it can; anything it is not confident about goes to a
vetted human through `langchain_tendem`, and both land in the same CSV.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
