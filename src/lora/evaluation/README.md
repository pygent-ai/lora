# Evaluation

Owns cases, scoring, failure analysis, regression runs, and generated-test registration.

- `case.py`: case lifecycle.
- `evaluator.py`: evaluation results.
- `analysis.py`: failure/root-cause analysis.
- `regression.py`: regression manifests and execution.
- `test_generation.py`: generated-test workflow.

Evaluation may invoke the runtime through its narrow case-run entry point; runtime orchestration should not absorb evaluation policy.
