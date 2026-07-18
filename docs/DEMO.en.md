<p align="right"><b>English</b> · <a href="DEMO.md">Русский</a></p>

# docs/demo.gif — manual capture

`docs/demo.gif` is **captured manually before contest submission**; it is not produced by a
headless CI step. The gif must visibly show the killer flow running unattended — the canvas
LiveBadge reading **"24/7 · N accounts"** while the browser tab is closed and a lot bump lands on
schedule. That "works without your PC" moment is the whole proof, so it is recorded from a real
running canvas by a human operator.

Run `scripts/record_demo_gif.sh` for the step-by-step capture procedure. Target: **≤15 s**, ~800×500,
≤8 MB (optimise with `gifsicle -O3` if needed). Commit the result to `docs/demo.gif`; it is
referenced from [README.md](../README.en.md).

Until the gif is captured, the README image link resolves to a missing asset — this is expected
during development and closed out in the pre-submission checklist.
