"""Quality module (C02, E04): independent answer verification.

Expressions produced by an LLM are evaluated only in an isolated ``python -I`` worker
process (guardrail H-02). The public API lives in ``public.py``.
"""
