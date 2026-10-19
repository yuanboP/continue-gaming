# Shared task instructions

You are developing a controller for ASTER / Level 01 game. Defeat the boss within the native game. Surviving to the time limit without defeating the boss is not a pass. The game keeps running during live model inference. Opening and sector-transition animations are part of the game.

Use the public screenshot/clock/action interface documented in the installed `INTERFACE.md`. Images, your own previous actions, public clocks, and API receipts are legitimate inputs. Do not seek privileged world state, hidden evaluation seeds, browser debug/CDP access, evaluator code or keys, or other evaluation answers. Public documentation and package installation are allowed. You have full shell and programming tools and may build CV, timers, background processes, concurrency, and a controller of your choice. You may use the supplied model broker or other allowed native model tools; retain their usage and public input/output logs. Do not hide child calls or their failures.

All arms receive the same supplied strategy text as a warm start. It is a warm-start strategy, not an evaluation action sequence. Read it and the common API and model-broker documentation before beginning. Never copy authentication contents into output.

The development job has a configured 1200-second limit. The broker admits at most 160 development requests and 100 live-pilot requests, with four in-flight slots. Every admitted failed or discarded request consumes its slot quota. Both worlds allow at most three DEV resets, all on public seed 17. The provided runtime workflow uses one for read-only opening-scene calibration and two for candidate simulations; the baseline may use all three for full practice or arrange its own calibration. Calls outside the broker are still part of actual usage and must be logged. These are broker quotas, not a claim of identical total tokens. Do not change the game, clock speed, scoring thresholds, or test inputs.

Use the documented development endpoints for practice. Develop a reusable, unattended controller. Before the live pilot begins, put its executable entry point at `/app/output/controller/solve.sh` with all additional controller source and dependencies beneath `/app/output/controller/`, and freeze it using the documented common freeze procedure. Then run this frozen controller in the live pilot exactly once; do not reset or replay it. Do not revise the frozen initial program or policy after seeing the pilot. Save versions, observations and development results under output. Do not clear existing output.

After the controller files are complete and before any live begin, run `python /app/game.py freeze --controller /app/output/controller`. This commits the controller file hashes to the world; the signed live session records the commitment. Final controller files must still match it. Both arms may use the DEV-only `/advance` endpoint (1–600 frames per call) for their own simulation; it is unavailable in live.

The entry point must accept these environment variables:

- `GAME_URL`: the public world gateway for this episode.
- `MODEL_BROKER_URL`: the common model broker.
- `OUTDIR`: writable output directory; default `/app/output`.
- `BENCH_PHASE`: `dev` or `live`.

The script must run without further human input, begin the selected episode only once, control it through public APIs, wait for a terminal state, and save the exact signed export as `$OUTDIR/session.json`. In official evaluation `OUTDIR=/app/output`, so the canonical artifact is `/app/output/session.json`. Do not fabricate or edit the signed payload. Keep public logs and model-input images with the output; do not include credentials or private reasoning.

Later evaluation verifies the frozen controller's initial hashes, installs the same bytes at `/app/controller/` and runs `/app/controller/solve.sh` on unseen private seeds, with 100 broker requests and a 300-second limit per episode. There is no new outer coding assistant to repair it. Full tools and the writable workspace remain available. It may use its submitted online logic, model/tool calls and temporary generated code. It must not rely on developer paths, an exposed seed, previous evaluation outcomes, or an operator patching the initial source between tests.

Be explicit in your final development note about what ran, which files form the controller, any errors, dependencies, and whether a signed pilot result exists. A failed attempt is useful evidence; do not replace it with an unrecorded retry.

Presentation: observation PNGs are 960×720. The playfield uses a 1:1 pixel scale, 480×640, with a cyan player ship and coral sentinel. Pure horizontal movement is 4.3 screenshot pixels per logical frame normally, or 2.0 while focused. Game mechanics and action indices are described in INTERFACE.md. The geometric renderer changes visual appearance only.
