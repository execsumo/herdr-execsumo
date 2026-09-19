# Space-Aware Agents

A [Herdr](https://herdr.dev/) plugin that keeps the Agents sidebar focused on what you're working on.

## What you get

- **Agents follow your space.** Select a space and the Agents list shows only the agents in it. Switch spaces and the list follows.
- **See who spawned whom.** Agents started by another agent are nested under it (`├─`, `└─`).
- **Know which agent is in charge.** The spawning agent is labeled **orchestrator**, so you talk to the lead instead of dropping into one of its subagents by mistake.
- **Collapse the noise.** Fold an orchestrator's subagents away (`▸ +3`) or hide every subagent at once.
- **Background work at a glance.** Claude panes show how many in-process subagents are running (`2 subagents`).

## Keys

| Key | Does |
|---|---|
| `cmd+e` | Collapse or expand the focused orchestrator's subagents |
| `cmd+shift+e` | Hide or show all subagents |

Also available from the action menu: **toggle / enable / disable** space filtering, and **status**.

## Install

```bash
herdr plugin link .
```

Then add the keys and sidebar row to `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "cmd+e"
type = "plugin_action"
command = "herdr-space-aware-agents.collapse"

[[keys.command]]
key = "cmd+shift+e"
type = "plugin_action"
command = "herdr-space-aware-agents.subagents"

[ui.sidebar.agents]
rows = [
  ["state_icon", { token = "$tree", dim = true }, "agent",
   { token = "$role", rules = [{ equals = "orchestrator", fg = "#f9e2af", bold = true }, { equals = "subagent", dim = true }] },
   { token = "$collapsed", fg = "#89b4fa" }, { token = "$subagents", fg = "#94e2d5" }],
  ["workspace", "tab"],
]
```

Agents launched through the `delegate` skill are nested automatically.
