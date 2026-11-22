# Checkpointed eval runs: each graded case is stored as it lands, so a run can resume.
# base.py holds the RunStore interface; run.py the payload, the resume identity, the
# error-origin tag and run_checkpointed (docs/specs/eval-run-checkpoint.md).
