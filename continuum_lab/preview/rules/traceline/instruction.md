# TraceLine — inspect and separate circuit boards

Operate a simulated inspection line using real circuit-board photographs. Compare each query with its independent known-normal reference. Reject visibly defective boards at the downstream gate and let normal boards continue. Read `/app/QUALITY_RULES.md`, `/app/API_CONTRACT.md`, and the public calibration in `/app/contract.json` before starting.

The live episode lasts **180 seconds** and contains **24 distinct query boards: 12 defective and 12 normal**. A pass requires at least **11 defective boards correctly rejected**, at most **one normal board rejected**, at most **two empty actions**, **zero actuator collisions**, and a complete authenticated live recording.

Your total wall-clock budget is **900 seconds**, including preparation, practice, nested model calls, the live episode, and export. Leave enough time for the complete 180-second live episode and its export. Once started, the live clock runs continuously during thinking, tool calls, and background work. Live cannot pause, reset, or replay.

`OPS_URL` is the live service. `DEV_OPS_URL` is a separate practice service with different query photographs of the same board types. Practice may be reset. Its images and normal references are available through the same public interfaces. A practice recording cannot satisfy the live task.

Use `/app/ops.py --help` for the public client. Entry sensors supply each entered carrier's anonymous ID, lane, entry timestamp, and comparison-image URL. Public lane speeds and the sensor-to-gate distance determine its gate-crossing time. Each of `d`, `f`, `j`, and `k` fires one immediate actuator in its corresponding lane. The effective window is **±350 ms** around the carrier's gate crossing. Each lane requires **800 ms** between physical commands. The server does not schedule future actions.

You may use the full shell, install packages, process images, use OCR or computer vision, write controllers and timers, start background processes, and call native Codex subprocesses. You may design your own workflow. Every additional model call must use **gpt-6-astra with medium reasoning effort**, matching the outer agent. Preserve all prompts, supplied images, outputs, timestamps, model settings, and reported token usage under `/app/output/`, including practice, diagnostic, failed, interrupted, or retried calls. Record missing usage explicitly. Any provided optional helper does not restrict your other tools.

This is a blind evaluation. Do not retrieve ground-truth labels, defect masks, annotations, annotated copies, answer tables, or image-specific answers for these photographs from any source. Do not inspect private world or verifier files, extract private seeds or signing keys, or retrieve future images or the asset catalog. These rules apply equally to all conditions. General package documentation, technical references, and tool installation are permitted. Judge the supplied photographs, not anonymous IDs, order, filenames, or assumed class proportions. Retrospective receipts from your own actions are public feedback.

When the live service reports `finished`, export the complete signed envelope unchanged to **`/app/output/session.json`**. This is the canonical graded artifact. Preserve code and logs alongside it. An independent verifier recomputes results from the authenticated image manifest and actual action times; a self-reported score is insufficient.

An optional visual console is available in `/app/preview/` when included in the task package. It uses the same public API, displays no quality annotations, and supplies no classification or scheduling policy. The console's unconnected preview is explicitly static and is not an episode or a scored result.
