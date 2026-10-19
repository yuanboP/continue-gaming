# Realtime workflow

Use the shared native broker and public DEV images. The workflow profiles four cohorts of four calls, compares initial and single_frame candidates in development simulation, then freezes a selected configuration. This is not a guarantee of success. The world keeps running during live inference.

    python /app/model_broker.py serve
    python /app/workflow.py profile --run-id calibration
    python /app/workflow.py simulate --config initial --run-id initial-dev
    python /app/workflow.py simulate --config single_frame --run-id single-dev
    python /app/workflow.py freeze --config initial --run-id chosen

Choose initial or single_frame from actual development evidence. Create a self-contained /app/output/controller/solve.sh that starts the broker if needed and invokes the live workflow. Commit it with `python /app/game.py freeze --controller /app/output/controller`, then run `python /app/workflow.py live --freeze-run-id chosen --run-id official`. Do not begin the live game separately: the workflow starts it once. Save the unchanged signed export as /app/output/session.json, along with all decisions, timings and usage. Preserve failed attempts; do not retry official play.
