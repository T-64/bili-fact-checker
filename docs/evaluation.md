# Offline evaluation

The 1.0 gate is the pytest regression file `tests/test_regression.py`. It covers:

- valid vs fabricated transcript anchors
- supporting evidence → `supported`
- conflicting evidence → `disputed`
- fetch failure and poisoned snippets → `insufficient_evidence` when prior is off
- blocked URLs → `page_blocked`
- provider unavailable → `search_unavailable`
- model prior is covered in `tests/test_evidence_service.py` and
  `tests/test_pipeline_v1.py`; regression tests keep prior off so snippets
  still cannot become evidence

Run:

```bash
pytest tests/test_regression.py tests/test_evidence_contract.py tests/test_safe_fetch.py -q
```

There is no calibrated “truth percentage”. Live provider evals are optional and
must not be a required CI gate.
