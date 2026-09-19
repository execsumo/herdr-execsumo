# Herdr customization

This project customizes [Herdr](https://herdr.dev/), primarily through plugins.
Keep changes focused and compatible with Herdr's documented APIs.
After each commit, install/link the latest local plugin for dogfooding with `herdr plugin link .` and verify it is enabled.

## Testing

Run `python3 -m unittest discover -s tests` from the repo root before finishing a task; all tests must pass.
Tests must not require a live Herdr server or socket (they run in isolated worktrees).

## Architecture

- `main.py` — entrypoints (`--startup`, `--event`, `--action <id>`, `--pane`), socket client, state, and `SpaceAwareController`.
- `agent_tree.py` — pure lineage/tree logic (no I/O); keep it that way so it stays unit-testable.
- `herdr-plugin.toml` — manifest. Herdr validates event names at link time.

### Agent view

One declarative `agent.view.set` projection owned by `plugin:herdr-space-aware-agents`:

- Filter: `all` of `workspace_id == {"context": "current_workspace_id"}` (when space filtering is enabled) and `{"op": "not", "filter": {"op": "exists", "field": {"token": "hidden"}}}` (when the tree is enabled). A single filter is sent bare; neither → `agent.view.clear`.
- Sort (tree enabled): `workspace_order`, `{"token": "tree_key"}`, `tab_order`, `pane_order`. This overrides the user's `ui.agent_panel_sort`.
- Label: the focused space's name. Views don't survive a server restart; the `[[startup]]` hook reapplies from state.
- Workspace events reapply the view (label); pane events (`pane.closed`, `pane.agent_detected`, `pane.moved`, `tab.moved`) only refresh tree tokens.

### Agent tree

Herdr has no parent/child relationship between panes, so spawners report it:

```bash
parent_term=$(herdr pane get "$HERDR_PANE_ID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["pane"]["terminal_id"])')
herdr pane report-metadata "$CHILD_PANE" --source delegation --token parent_term="$parent_term"
herdr plugin action invoke herdr-space-aware-agents.refresh
```

- Use `terminal_id`, not pane ids: pane ids compact when panes close. A `parent_pane` tag is accepted only when no lineage entry exists yet for that child.
- Lineage persists in `$HERDR_PLUGIN_STATE_DIR/tree.json` (`lineage`, `collapsed`, `hide_subagents`) because pane tokens are lost on server restart. Entries carry both sides' `agent_session` keys; `rebind_and_prune` reattaches them to new terminals after restore and drops entries unseen for 24h.
- `refresh_tree` reports only changed tokens, under a `fcntl` lock (hooks can run concurrently). State files are written via per-PID temp files for the same reason.
- Tokens written on every agent pane: `tree_key` (per-workspace sort key, `0000.0001…`), `tree` (`▾`/`▸` on roots with children, `├─`/`└─` on children; nested pads use U+2800 because Herdr trims leading whitespace), `role` (`orchestrator`/`subagent`), `collapsed` (`+N`), `hidden` (`1`).
- `collapse` acts on `focused_pane_id` from `HERDR_PLUGIN_CONTEXT_JSON`: toggles that node if it has children, else its parent; if neither, flips all orchestrators in the workspace. Clicking sidebar rows cannot trigger plugin code, and re-selecting a focused pane emits no event, so expand/collapse is key-driven by design.
- `$subagents` is not written by this plugin: `herdr-subagent-count.sh` (herdr skill, Claude `SubagentStart`/`SubagentStop`/`SessionStart`/`SessionEnd` hooks, source `claude-subagents`) counts in-process Agent-tool subagents.

### Config

`$HERDR_PLUGIN_CONFIG_DIR/config.json`: `default_enabled` (space filtering, default `true`), `label` (fallback view label, default `Spaces`), `tree_enabled` (default `true`; `false` restores Herdr's own ordering).

Reference:
- [Herdr docs](https://herdr.dev/docs/)
- [Plugin docs](https://herdr.dev/docs/plugins/)
