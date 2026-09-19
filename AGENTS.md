# Herdr customization

This project customizes [Herdr](https://herdr.dev/), primarily through plugins.
Keep changes focused and compatible with Herdr's documented APIs.
After each commit, install/link the latest local plugin for dogfooding with `herdr plugin link .` and verify it is enabled.

## Testing

Run `python3 -m unittest discover -s tests` from the repo root before finishing a task; all tests must pass.
Tests must not require a live Herdr server or socket (they run in isolated worktrees).

Reference:
- [Herdr docs](https://herdr.dev/docs/)
- [Plugin docs](https://herdr.dev/docs/plugins/)
