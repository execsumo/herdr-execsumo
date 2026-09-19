#!/usr/bin/env python3
"""Herdr plugin: Space-Aware Agents List.

Makes the lower-right-bar Agents section follow the space selected in the
upper-right-bar Spaces section. When a space is selected, only agents active
in that space appear in the Agents list; switching spaces updates the list
automatically; empty and no-selection states are handled cleanly.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import agent_tree

DEFAULT_PLUGIN_ID = "herdr-space-aware-agents"
DEFAULT_LABEL = "Spaces"
SOCKET_TIMEOUT = 3.0
ACTIONS = ["toggle", "enable", "disable", "status", "collapse", "subagents", "refresh"]


class HerdrClient:
    """Communicates with the Herdr server over the socket API or CLI fallback."""

    def __init__(self, socket_path: Optional[str] = None, bin_path: Optional[str] = None) -> None:
        self.socket_path = (
            socket_path
            or os.environ.get("HERDR_SOCKET_PATH")
            or os.path.expanduser("~/.config/herdr/herdr.sock")
        )
        self.bin_path = bin_path or os.environ.get("HERDR_BIN_PATH") or "herdr"
        self._seq = 0

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send a JSON-RPC request to the Herdr API socket and return result."""
        self._seq += 1
        rid = f"space_aware_{os.getpid()}_{self._seq}_{int(time.time() * 1000)}"
        payload = {"id": rid, "method": method, "params": params or {}}
        raw_req = json.dumps(payload).encode("utf-8") + b"\n"

        # Try Unix domain socket (or Windows named pipe fallback)
        if sys.platform != "win32" or not self.socket_path.startswith("\\\\"):
            if not os.path.exists(self.socket_path):
                raise RuntimeError(f"Herdr socket not found at {self.socket_path}")

            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(SOCKET_TIMEOUT)
                sock.connect(self.socket_path)
                sock.sendall(raw_req)
                buf = b""
                while True:
                    nl = buf.find(b"\n")
                    if nl >= 0:
                        line, buf = buf[:nl], buf[nl + 1 :]
                        msg = json.loads(line.decode("utf-8"))
                        if msg.get("id") != rid:
                            continue
                        if "error" in msg:
                            raise RuntimeError(f"{method} error: {msg['error']}")
                        return msg.get("result", {})
                    chunk = sock.recv(65536)
                    if not chunk:
                        raise RuntimeError(f"{method} failed: connection closed before response")
                    buf += chunk
        else:
            # Windows named pipe
            with open(self.socket_path, "r+b", buffering=0) as pipe:
                pipe.write(raw_req)
                line = pipe.readline()
                msg = json.loads(line.decode("utf-8"))
                if "error" in msg:
                    raise RuntimeError(f"{method} error: {msg['error']}")
                return msg.get("result", {})


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    # Hooks can run concurrently, so each writer needs its own temp file.
    temp_file = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp_file, path)


class PluginState:
    """Manages persistent plugin state and configuration."""

    def __init__(self) -> None:
        self.plugin_id = os.environ.get("HERDR_PLUGIN_ID", DEFAULT_PLUGIN_ID)
        self.state_dir = os.environ.get("HERDR_PLUGIN_STATE_DIR")
        self.config_dir = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")

        if not self.state_dir:
            self.state_dir = str(Path.home() / ".config" / "herdr" / "plugins" / "state" / self.plugin_id)
        if not self.config_dir:
            self.config_dir = str(Path.home() / ".config" / "herdr" / "plugins" / "config" / self.plugin_id)

        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.config_dir, exist_ok=True)

        self.state_file = Path(self.state_dir) / "state.json"
        self.tree_file = Path(self.state_dir) / "tree.json"
        self.lock_file = Path(self.state_dir) / "tree.lock"
        self.config_file = Path(self.config_dir) / "config.json"

    def get_source(self) -> str:
        return f"plugin:{self.plugin_id}"

    def load_config(self) -> Dict[str, Any]:
        if self.config_file.is_file():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"default_enabled": True}

    def load_state(self) -> Dict[str, Any]:
        cfg = self.load_config()
        default_enabled = cfg.get("default_enabled", True)
        label = cfg.get("label", DEFAULT_LABEL)

        if self.state_file.is_file():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "enabled" in data:
                        if data.get("label") == "Space" and "label" not in cfg:
                            data["label"] = label
                        return data
            except Exception:
                pass

        return {
            "enabled": default_enabled,
            "label": label,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    def save_state(self, state: Dict[str, Any]) -> None:
        state["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _atomic_write_json(self.state_file, state)

    def tree_enabled(self) -> bool:
        return bool(self.load_config().get("tree_enabled", True))

    def load_tree(self) -> Dict[str, Any]:
        """Lineage, collapsed nodes, and the hide-subagents flag."""
        data: Dict[str, Any] = {}
        if self.tree_file.is_file():
            try:
                with open(self.tree_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data.setdefault("lineage", {})
        data.setdefault("collapsed", [])
        data.setdefault("hide_subagents", False)
        return data

    def save_tree(self, tree: Dict[str, Any]) -> None:
        _atomic_write_json(self.tree_file, tree)

    @contextlib.contextmanager
    def tree_lock(self) -> Iterator[None]:
        """Serialize tree updates; event hooks can fire concurrently."""
        try:
            import fcntl
        except ImportError:
            yield
            return
        with open(self.lock_file, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


class SpaceAwareController:
    """Controls the dynamic agent view projection on the Herdr server."""

    def __init__(self, client: HerdrClient, state_mgr: PluginState) -> None:
        self.client = client
        self.state_mgr = state_mgr

    def selected_workspace_label(self) -> str:
        """Return the current workspace label for the agent view heading."""
        fallback = self.state_mgr.load_config().get("label", DEFAULT_LABEL)
        try:
            workspaces = self.client.call("workspace.list", {}).get("workspaces", [])
        except Exception:
            return fallback

        focused_id = os.environ.get("HERDR_WORKSPACE_ID")
        if not focused_id:
            focused_id = next(
                (workspace.get("workspace_id") for workspace in workspaces if workspace.get("focused")),
                None,
            )
        for workspace in workspaces:
            if workspace.get("workspace_id") == focused_id:
                return workspace.get("label") or focused_id or fallback
        return fallback

    def view_params(self, space_filter: bool, label: str) -> Optional[Dict[str, Any]]:
        """Build the agent view: optional space filter plus the agent tree.

        Returns None when neither feature is on, meaning the view should be cleared.
        """
        filters: List[Dict[str, Any]] = []
        if space_filter:
            # workspace_id == current_workspace_id
            filters.append({
                "op": "eq",
                "field": "workspace_id",
                "value": {
                    "context": "current_workspace_id"
                }
            })
        tree = self.state_mgr.tree_enabled()
        if tree:
            filters.append({"op": "not", "filter": {"op": "exists", "field": {"token": "hidden"}}})
        if not filters:
            return None

        params: Dict[str, Any] = {
            "source": self.state_mgr.get_source(),
            "label": label,
            "filter": filters[0] if len(filters) == 1 else {"op": "all", "filters": filters},
        }
        if tree:
            params["sort"] = [
                {"field": "workspace_order", "order": "asc"},
                {"field": {"token": "tree_key"}, "order": "asc"},
                {"field": "tab_order", "order": "asc"},
                {"field": "pane_order", "order": "asc"},
            ]
        return params

    def apply_view(self, space_filter: bool, label: Optional[str] = None) -> Dict[str, Any]:
        state = self.state_mgr.load_state()
        view_label = label or self.selected_workspace_label()
        params = self.view_params(space_filter, view_label)
        if params is None:
            result = self.client.call("agent.view.clear", {"source": self.state_mgr.get_source()})
        else:
            result = self.client.call("agent.view.set", params)
        state["enabled"] = space_filter
        state["label"] = view_label
        self.state_mgr.save_state(state)
        return result

    def apply_projection(self, label: Optional[str] = None) -> Dict[str, Any]:
        """Apply space-following agent view projection."""
        return self.apply_view(True, label)

    def clear_projection(self) -> Dict[str, Any]:
        """Stop filtering by space; the agent tree view stays if enabled."""
        return self.apply_view(False)

    def reapply_view(self) -> Dict[str, Any]:
        return self.apply_view(bool(self.state_mgr.load_state().get("enabled", True)))

    def _agents(self) -> List[Dict[str, Any]]:
        return self.client.call("agent.list", {}).get("agents", [])

    def refresh_tree(self) -> Dict[str, Any]:
        """Recompute lineage tokens on every live agent pane."""
        if not self.state_mgr.tree_enabled():
            return {"patched": 0}
        with self.state_mgr.tree_lock():
            agents = self._agents()
            tree = self.state_mgr.load_tree()
            lineage = tree["lineage"]
            agent_tree.ingest_tags(agents, lineage)
            agent_tree.rebind_and_prune(agents, lineage)
            live = {a.get("terminal_id") for a in agents}
            collapsed = {term for term in tree["collapsed"] if term in live}
            desired = agent_tree.compute_tokens(agents, lineage, collapsed, tree["hide_subagents"])
            patches = agent_tree.token_patches(agents, desired)
            source = self.state_mgr.get_source()
            for pane_id, tokens in patches.items():
                self.client.call("pane.report_metadata", {"pane_id": pane_id, "source": source, "tokens": tokens})
            tree["collapsed"] = sorted(collapsed)
            self.state_mgr.save_tree(tree)
        return {"patched": len(patches)}

    def toggle_collapse(self, pane_id: Optional[str], workspace_id: Optional[str] = None) -> str:
        """Collapse or expand the tree under the focused agent.

        Without an agent tree at the focused pane, flips every orchestrator in
        the workspace: collapse all if any is expanded, otherwise expand all.
        """
        with self.state_mgr.tree_lock():
            agents = self._agents()
            tree = self.state_mgr.load_tree()
            agent_tree.ingest_tags(agents, tree["lineage"])
            collapsed = set(tree["collapsed"])
            target = agent_tree.toggle_target(agents, tree["lineage"], pane_id)
            if target is not None:
                collapsed ^= {target}
                message = "collapsed" if target in collapsed else "expanded"
            else:
                parent_of = agent_tree.build_forest(agents, tree["lineage"])
                parents = {p for p in parent_of.values() if p is not None}
                scope = {
                    a.get("terminal_id") for a in agents
                    if a.get("terminal_id") in parents
                    and (workspace_id is None or a.get("workspace_id") == workspace_id)
                }
                if scope - collapsed:
                    collapsed |= scope
                    message = "collapsed all"
                else:
                    collapsed -= scope
                    message = "expanded all"
            tree["collapsed"] = sorted(collapsed)
            self.state_mgr.save_tree(tree)
        self.refresh_tree()
        return message

    def toggle_subagents(self) -> bool:
        """Hide or show every subagent; returns True when subagents are now hidden."""
        with self.state_mgr.tree_lock():
            tree = self.state_mgr.load_tree()
            tree["hide_subagents"] = not tree["hide_subagents"]
            self.state_mgr.save_tree(tree)
        self.refresh_tree()
        return tree["hide_subagents"]

    def toggle(self) -> Tuple[bool, Dict[str, Any]]:
        """Toggle between space-aware filtering and unfiltered view."""
        state = self.state_mgr.load_state()
        is_enabled = state.get("enabled", True)
        if is_enabled:
            res = self.clear_projection()
            return False, res
        else:
            res = self.apply_projection()
            return True, res

    def get_status(self) -> Dict[str, Any]:
        """Fetch live status including workspaces, agents, and active projection."""
        state = self.state_mgr.load_state()
        try:
            ws_res = self.client.call("workspace.list", {})
            workspaces = ws_res.get("workspaces", [])
        except Exception:
            workspaces = []

        try:
            ag_res = self.client.call("agent.list", {})
            agents = ag_res.get("agents", [])
        except Exception:
            agents = []

        focused_ws_id = os.environ.get("HERDR_WORKSPACE_ID")
        if not focused_ws_id:
            for w in workspaces:
                if w.get("focused"):
                    focused_ws_id = w.get("workspace_id")
                    break

        return {
            "plugin_id": self.state_mgr.plugin_id,
            "enabled": state.get("enabled", True),
            "label": state.get("label", DEFAULT_LABEL),
            "source": self.state_mgr.get_source(),
            "focused_workspace_id": focused_ws_id,
            "workspaces": workspaces,
            "agents": agents,
            "tree": self.state_mgr.load_tree() if self.state_mgr.tree_enabled() else None,
        }


def handle_startup(controller: SpaceAwareController) -> int:
    """Handle Herdr server startup or session restore hook."""
    try:
        # Tokens do not survive a server restart; lineage is replayed from plugin state.
        controller.refresh_tree()
        controller.reapply_view()
        print(f"[{controller.state_mgr.plugin_id}] Restored agent view")
    except Exception as e:
        print(f"[{controller.state_mgr.plugin_id}] Startup error: {e}", file=sys.stderr)
        return 1
    return 0


def handle_event(controller: SpaceAwareController) -> int:
    """Handle workspace events (label refresh) and pane events (agent tree)."""
    event_name = os.environ.get("HERDR_PLUGIN_EVENT", "event")

    # In Herdr, agent.view.set with {"context": "current_workspace_id"} is evaluated
    # declaratively by the client shell whenever focus moves between spaces.
    # Workspace events refresh the label; pane events only need new tree tokens.
    try:
        if not event_name.startswith("workspace."):
            controller.refresh_tree()
        elif controller.state_mgr.load_state().get("enabled", True):
            controller.apply_projection()
    except Exception as e:
        print(f"[{controller.state_mgr.plugin_id}] Event ({event_name}) handler error: {e}", file=sys.stderr)
        return 1

    return 0


def handle_action(controller: SpaceAwareController, action_name: str) -> int:
    """Handle action execution (toggle, enable, disable, status)."""
    try:
        if action_name == "toggle":
            enabled, _ = controller.toggle()
            status_str = "ENABLED (filtering agents by selected space)" if enabled else "DISABLED (showing all agents)"
            print(f"Space-Aware Agents: {status_str}")
        elif action_name == "enable":
            controller.apply_projection()
            print("Space-Aware Agents: ENABLED (filtering agents by selected space)")
        elif action_name == "disable":
            controller.clear_projection()
            print("Space-Aware Agents: DISABLED (showing all agents)")
        elif action_name == "collapse":
            context = _plugin_context()
            pane_id = context.get("focused_pane_id") or os.environ.get("HERDR_PANE_ID")
            workspace_id = context.get("workspace_id") or os.environ.get("HERDR_WORKSPACE_ID")
            print(f"Agent tree: {controller.toggle_collapse(pane_id, workspace_id)}")
        elif action_name == "subagents":
            hidden = controller.toggle_subagents()
            print(f"Agent tree: subagents {'hidden' if hidden else 'shown'}")
        elif action_name == "refresh":
            controller.refresh_tree()
            controller.reapply_view()
            print("Agent tree: refreshed")
        elif action_name == "status":
            info = controller.get_status()
            print(json.dumps(info, indent=2))
        else:
            print(f"Unknown action: {action_name}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"Action {action_name} failed: {e}", file=sys.stderr)
        return 1
    return 0


def _plugin_context() -> Dict[str, Any]:
    try:
        return json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        return {}


def _read_single_key() -> str:
    """Read a single keypress without waiting for Enter."""
    if sys.platform == "win32":
        import msvcrt
        ch = msvcrt.getch()
        if ch in (b"\x00", b"\xe0"):
            ch = msvcrt.getch()
        return ch.decode("latin1", errors="ignore")
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            ch = sys.stdin.read(1)
            # Handle escape sequences
            if ch == "\x1b":
                # Check if there are more characters immediately available
                import select
                r, _, _ = select.select([sys.stdin], [], [], 0.05)
                if r:
                    seq = sys.stdin.read(2)
                    return f"\x1b{seq}"
                return "\x1b"
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def handle_pane(controller: SpaceAwareController) -> int:
    """Render interactive status and control popup pane."""
    def render() -> None:
        # Clear screen and move cursor to home
        sys.stdout.write("\x1b[2J\x1b[H")
        try:
            status = controller.get_status()
            is_enabled = status["enabled"]
            workspaces = status["workspaces"]
            agents = status["agents"]
            focused_id = status["focused_workspace_id"]

            # Match focused workspace name
            ws_map = {w["workspace_id"]: w.get("label", w["workspace_id"]) for w in workspaces}
            if not focused_id and workspaces:
                # Default to the first focused or first workspace
                for w in workspaces:
                    if w.get("focused"):
                        focused_id = w["workspace_id"]
                        break
                if not focused_id:
                    focused_id = workspaces[0]["workspace_id"]

            curr_space_name = ws_map.get(focused_id, "None (No Space Selected)")
            agents_in_space = [a for a in agents if a.get("workspace_id") == focused_id]
            agents_in_other = [a for a in agents if a.get("workspace_id") != focused_id]

            status_badge = "\x1b[1;32m[ACTIVE - FOLLOWING SPACES]\x1b[0m" if is_enabled else "\x1b[1;33m[DISABLED - SHOWING ALL]\x1b[0m"

            lines = [
                "┌──────────────────────────────────────────────────────────────┐",
                f"│ \x1b[1mSpace-Aware Agents\x1b[0m             {status_badge:>39} │",
                "├──────────────────────────────────────────────────────────────┤",
                f"│ Selected Space : \x1b[1;36m{curr_space_name:<43}\x1b[0m │",
                f"│ Filter Scope   : \x1b[37m{'Only agents in selected space' if is_enabled else 'All spaces (unfiltered)':<43}\x1b[0m │",
                "├──────────────────────────────────────────────────────────────┤",
            ]

            if not focused_id:
                lines.append("│ \x1b[33mNo space selected (Empty selection state)\x1b[0m                   │")
                lines.append("│ Agents list: 0 agents                                        │")
            elif not agents_in_space:
                lines.append(f"│ \x1b[33mNo active agents in {curr_space_name}\x1b[0m (Empty state)                │")
                lines.append("│ Lower-right Agents list cleanly displays empty.              │")
            else:
                lines.append(f"│ Active Agents in Selected Space ({len(agents_in_space)}):                           │")
                for a in agents_in_space[:5]:
                    name = a.get("agent", "agent")
                    st = a.get("agent_status", "idle")
                    pane = a.get("pane_id", "")
                    title = a.get("terminal_title_stripped") or name
                    color = "\x1b[32m" if st == "working" else "\x1b[33m" if st == "blocked" else "\x1b[36m"
                    bullet = "●"
                    row = f"  {color}{bullet}\x1b[0m {title} ({st}) [{pane}]"
                    lines.append(f"│ {row:<68} │")

            lines.append("├──────────────────────────────────────────────────────────────┤")
            other_count = len(agents_in_other)
            other_spaces_count = len([w for w in workspaces if w["workspace_id"] != focused_id])
            lines.append(f"│ Other Spaces   : {other_spaces_count} spaces, {other_count} agents total{' (hidden)' if is_enabled else '' :<21} │")
            lines.append("├──────────────────────────────────────────────────────────────┤")
            lines.append("│ [t] Toggle    [e] Enable    [d] Disable    [q] Close popup   │")
            lines.append("└──────────────────────────────────────────────────────────────┘")

            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stdout.write(f"\nError rendering status: {e}\n[q] Close popup\n")
            sys.stdout.flush()

    # Interactive event loop for popup pane
    if not sys.stdin.isatty():
        render()
        return 0

    while True:
        render()
        key = _read_single_key().lower()
        if key in ("q", "\x1b", "\x03"):  # q, esc, ctrl-c
            break
        elif key == "t":
            controller.toggle()
        elif key == "e":
            controller.apply_projection()
        elif key == "d":
            controller.clear_projection()

    # Clean exit
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Herdr Space-Aware Agents Plugin")
    parser.add_argument("--startup", action="store_true", help="Execute server startup / session restore hook")
    parser.add_argument("--event", action="store_true", help="Execute event hook (workspace events)")
    parser.add_argument("--action", type=str, choices=ACTIONS, help="Invoke plugin action")
    parser.add_argument("--pane", action="store_true", help="Open interactive status popup pane")
    parser.add_argument("--status", action="store_true", help="Print current status JSON to stdout")

    args = parser.parse_args()

    client = HerdrClient()
    state_mgr = PluginState()
    controller = SpaceAwareController(client, state_mgr)

    # If HERDR_PLUGIN_ACTION_ID is set in environment, prioritize it
    env_action = os.environ.get("HERDR_PLUGIN_ACTION_ID")
    if env_action and not args.action:
        args.action = env_action

    if args.startup or os.environ.get("HERDR_PLUGIN_EVENT") == "startup":
        return handle_startup(controller)
    elif args.event:
        return handle_event(controller)
    elif args.pane:
        return handle_pane(controller)
    elif args.action:
        return handle_action(controller, args.action)
    elif args.status:
        try:
            info = controller.get_status()
            print(json.dumps(info, indent=2))
            return 0
        except Exception as e:
            print(f"Error getting status: {e}", file=sys.stderr)
            return 1
    else:
        # Default behavior when run directly: show status or help
        try:
            info = controller.get_status()
            print(json.dumps(info, indent=2))
            return 0
        except Exception:
            parser.print_help()
            return 0


if __name__ == "__main__":
    sys.exit(main())
