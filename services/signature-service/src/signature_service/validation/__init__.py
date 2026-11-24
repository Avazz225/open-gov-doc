"""Validation of signatures on incoming documents (ADR 0248, Phase 92).

`interface` holds the format-agnostic contract and result model, `pades` the
first validator, `fetcher` the fenced outbound HTTP client, `trust` the
anchor handling and `worker` the bounded execution.
"""
