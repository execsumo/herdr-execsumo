# Herdr Space-Aware Agents Plugin

A [Herdr](https://herdr.dev/) plugin that makes the lower-right-bar **Agents** section dynamically follow the space selected in the upper-right-bar **Spaces** section.

## Features

- **Space-Following Agent List**: When a space (workspace or git worktree) is selected in Herdr's Spaces section, only agents active in that space appear in the Agents list.
- **Dynamic Space Switching**: Switching between spaces automatically updates both the Agents list and its label to the selected space name.
- **Clean Empty & No-Selection States**:
  - If a space has no running agents, the Agents list cleanly displays empty.
  - If no space is selected or all workspaces are closed, the list gracefully handles the absence of selection without error.
- **Startup Hook (`[[startup]]`)**: Automatically re-applies the space-aware view projection on server boot or session restore.
- **Event Hooks (`[[events]]`)**: Listens to `workspace.focused`, `workspace.created`, and `workspace.closed` to ensure the projection remains synchronized.
- **Actions (`[[actions]]`)**: Provides user-invocable actions:
  - `toggle`: Flip between space-aware filtering and viewing all agents across spaces.
  - `enable`: Explicitly enable space-aware filtering.
  - `disable`: Explicitly disable filtering to view all agents.
  - `status`: Output current filter status, active spaces, and agents in JSON format.
- **Interactive Popup Pane (`[[panes]]`)**: Opens a modal popup dashboard showing the current space, agents inside it, other spaces, and quick keyboard hotkeys (`t` to toggle, `q` to close).
- **Zero External Dependencies**: Built purely with the Python 3 standard library for fast startup and cross-platform compatibility (Linux, macOS, Windows).

---

## Installation & Linking

To link this plugin locally into your Herdr installation:

```bash
herdr plugin link .
```

Verify that the plugin is installed and active:

```bash
herdr plugin list
```

---

## Usage

### Actions

You can trigger actions from the Herdr command line, keybindings, or action menu:

```bash
# Toggle between space-aware and unfiltered view
herdr plugin action invoke herdr-space-aware-agents.toggle

# Enable space-aware filtering
herdr plugin action invoke herdr-space-aware-agents.enable

# Disable space-aware filtering (view all agents)
herdr plugin action invoke herdr-space-aware-agents.disable

# View status
herdr plugin action invoke herdr-space-aware-agents.status
```

### Interactive Status Popup Pane

Herdr supports opening the plugin's status dashboard as a modal popup:

```bash
herdr plugin pane open --plugin herdr-space-aware-agents --entrypoint status
```

In the popup:
- Press `t` to toggle space filtering.
- Press `e` to enable filtering.
- Press `d` to disable filtering.
- Press `q` or `Esc` to close the popup.

---

## How It Works

1. **Declarative Agent Projection (`agent.view.set`)**:
   The plugin configures Herdr's built-in agent projection engine via the socket API. The label is refreshed from the focused space whenever Herdr emits a workspace event:
   ```json
   {
     "method": "agent.view.set",
     "params": {
       "source": "plugin:herdr-space-aware-agents",
       "label": "herdr-execsumo",
       "filter": {
         "op": "eq",
         "field": "workspace_id",
         "value": {
           "context": "current_workspace_id"
         }
       }
     }
   }
   ```
2. **Context Binding**:
   Herdr's client shell dynamically binds `{ "context": "current_workspace_id" }` to the space currently focused by the user. As the user navigates between spaces in the upper Spaces bar, the Agents panel updates instantaneously without polling.
3. **State Persistence**:
   Plugin state is saved under `HERDR_PLUGIN_STATE_DIR` (e.g. `~/.config/herdr/plugins/state/herdr-space-aware-agents/state.json`). When Herdr starts or restores a session, the `[[startup]]` hook reads this state and reinstalls the projection.

---

## Configuration

Custom configuration can be placed in `config.json` inside the plugin's config directory (`HERDR_PLUGIN_CONFIG_DIR`, typically `~/.config/herdr/plugins/config/herdr-space-aware-agents/config.json`):

```json
{
  "default_enabled": true,
  "label": "Spaces"
}
```

---

## Testing

Run the automated test suite with Python's built-in unittest runner:

```bash
python3 -m unittest discover tests
```
