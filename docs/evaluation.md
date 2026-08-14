# Offline evaluation

The 1.0 gate is the pytest regression file `tests/test_regression.py`. It covers:

- valid vs fabricated transcript anchors
- supporting evidence → `supported`
- conflicting evidence → `disputed`
- fetch failure and poisoned snippets → `insufficient_evidence`
- blocked URLs → `page_blocked`
- provider unavailable → `search_unavailable`

Run:

```bash
pytest tests/test_regression.py tests/test_evidence_contract.py tests/test_safe_fetch.py -q
```

There is no calibrated “truth percentage”. Live provider evals are optional and
must not be a required CI gate.
