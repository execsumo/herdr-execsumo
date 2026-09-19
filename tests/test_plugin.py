#!/usr/bin/env python3
"""Unit and integration tests for Space-Aware Agents Herdr plugin."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import main


class TestManifest(unittest.TestCase):
    """Verify herdr-plugin.toml meets Herdr plugin specifications."""

    def setUp(self) -> None:
        self.manifest_path = Path(__file__).parent.parent / "herdr-plugin.toml"

    def test_manifest_exists(self) -> None:
        self.assertTrue(self.manifest_path.is_file(), "herdr-plugin.toml must exist")

    def test_manifest_required_fields(self) -> None:
        # Simple TOML parser for validation
        content = self.manifest_path.read_text(encoding="utf-8")
        self.assertIn('id = "herdr-space-aware-agents"', content)
        self.assertIn('name = "Space-Aware Agents"', content)
        self.assertIn('version = "0.2.0"', content)
        self.assertIn('min_herdr_version = "0.7.5"', content)
        self.assertIn('platforms = ["linux", "macos", "windows"]', content)

    def test_manifest_hooks_and_panes(self) -> None:
        content = self.manifest_path.read_text(encoding="utf-8")
        # Startup hook
        self.assertIn("[[startup]]", content)
        self.assertIn('command = ["python3", "main.py", "--startup"]', content)

        # Workspace event hooks
        self.assertIn('on = "workspace.focused"', content)
        self.assertIn('on = "workspace.created"', content)
        self.assertIn('on = "workspace.closed"', content)
        self.assertIn('on = "workspace.renamed"', content)

        # Actions
        self.assertIn('id = "toggle"', content)
        self.assertIn('id = "enable"', content)
        self.assertIn('id = "disable"', content)
        self.assertIn('id = "status"', content)
        self.assertIn('id = "collapse"', content)
        self.assertIn('id = "subagents"', content)
        self.assertIn('id = "refresh"', content)
        self.assertIn('on = "pane.closed"', content)
        self.assertIn('on = "pane.agent_detected"', content)

        # Pane
        self.assertIn("[[panes]]", content)
        self.assertIn('placement = "popup"', content)


class TestPluginState(unittest.TestCase):
    """Test state persistence and configuration loading."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp_dir.name) / "state"
        self.config_dir = Path(self.tmp_dir.name) / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.env_patch = patch.dict(os.environ, {
            "HERDR_PLUGIN_ID": "test-plugin",
            "HERDR_PLUGIN_STATE_DIR": str(self.state_dir),
            "HERDR_PLUGIN_CONFIG_DIR": str(self.config_dir),
            "HERDR_WORKSPACE_ID": "",
        })
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.tmp_dir.cleanup()

    def test_default_state(self) -> None:
        state_mgr = main.PluginState()
        self.assertEqual(state_mgr.get_source(), "plugin:test-plugin")
        state = state_mgr.load_state()
        self.assertTrue(state.get("enabled"))
        self.assertEqual(state.get("label"), main.DEFAULT_LABEL)

    def test_save_and_reload_state(self) -> None:
        state_mgr = main.PluginState()
        state = {"enabled": False, "label": "CustomSpace"}
        state_mgr.save_state(state)

        reloaded = state_mgr.load_state()
        self.assertFalse(reloaded["enabled"])
        self.assertEqual(reloaded["label"], "CustomSpace")
        self.assertIn("updated_at", reloaded)

    def test_legacy_default_label_migrates(self) -> None:
        state_mgr = main.PluginState()
        state_mgr.state_file.write_text(
            json.dumps({"enabled": True, "label": "Space"}), encoding="utf-8"
        )

        state = state_mgr.load_state()
        self.assertEqual(state["label"], main.DEFAULT_LABEL)

    def test_custom_config(self) -> None:
        config_file = self.config_dir / "config.json"
        config_file.write_text(json.dumps({"default_enabled": False, "label": "MySpace"}), encoding="utf-8")

        state_mgr = main.PluginState()
        state = state_mgr.load_state()
        self.assertFalse(state["enabled"])
        self.assertEqual(state["label"], "MySpace")


class TestSpaceAwareController(unittest.TestCase):
    """Test dynamic agent view projection management."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.env_patch = patch.dict(os.environ, {
            "HERDR_PLUGIN_ID": "herdr-space-aware-agents",
            "HERDR_PLUGIN_STATE_DIR": self.tmp_dir.name,
            "HERDR_PLUGIN_CONFIG_DIR": self.tmp_dir.name,
            "HERDR_WORKSPACE_ID": "",
        })
        self.env_patch.start()

        self.mock_client = MagicMock()
        self.state_mgr = main.PluginState()
        self.controller = main.SpaceAwareController(self.mock_client, self.state_mgr)

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.tmp_dir.cleanup()

    def test_apply_projection_uses_selected_workspace_label(self) -> None:
        self.mock_client.call.side_effect = [
            {"workspaces": [{"workspace_id": "w1", "label": "herdr-execsumo", "focused": True}]},
            {"type": "agent_view", "active": True},
        ]

        self.controller.apply_projection()

        _, params = self.mock_client.call.call_args_list[1][0]
        self.assertEqual(params["label"], "herdr-execsumo")
        self.assertEqual(self.state_mgr.load_state()["label"], "herdr-execsumo")

    def test_apply_projection_payload(self) -> None:
        self.mock_client.call.return_value = {"type": "agent_view", "active": True}

        res = self.controller.apply_projection()
        self.assertEqual(res["active"], True)

        call_args = self.mock_client.call.call_args_list[1]
        method, params = call_args[0]
        self.assertEqual(method, "agent.view.set")
        self.assertEqual(params["source"], "plugin:herdr-space-aware-agents")
        self.assertEqual(params["label"], "Spaces")

        space_filter, hidden_filter = params["filter"]["filters"]
        self.assertEqual(params["filter"]["op"], "all")
        self.assertEqual(space_filter["op"], "eq")
        self.assertEqual(space_filter["field"], "workspace_id")
        self.assertEqual(space_filter["value"], {"context": "current_workspace_id"})
        self.assertEqual(
            hidden_filter, {"op": "not", "filter": {"op": "exists", "field": {"token": "hidden"}}}
        )
        self.assertEqual(params["sort"][1], {"field": {"token": "tree_key"}, "order": "asc"})

        # Verify state was persisted as enabled
        state = self.state_mgr.load_state()
        self.assertTrue(state["enabled"])

    def test_clear_projection_keeps_tree_view(self) -> None:
        self.mock_client.call.return_value = {"type": "agent_view", "active": True}

        self.controller.clear_projection()

        method, params = self.mock_client.call.call_args_list[-1][0]
        self.assertEqual(method, "agent.view.set")
        self.assertEqual(params["filter"]["op"], "not")

        state = self.state_mgr.load_state()
        self.assertFalse(state["enabled"])

    def test_clear_projection_payload_without_tree(self) -> None:
        Path(self.tmp_dir.name, "config.json").write_text(json.dumps({"tree_enabled": False}))
        self.mock_client.call.return_value = {"type": "agent_view", "active": False}

        res = self.controller.clear_projection()
        self.assertEqual(res["active"], False)

        self.mock_client.call.assert_called_with(
            "agent.view.clear", {"source": "plugin:herdr-space-aware-agents"}
        )

        state = self.state_mgr.load_state()
        self.assertFalse(state["enabled"])

    def test_toggle(self) -> None:
        # Initial state is enabled -> toggle should disable
        self.mock_client.call.return_value = {"type": "agent_view", "active": False}
        enabled, _ = self.controller.toggle()
        self.assertFalse(enabled)

        # Second toggle should enable
        self.mock_client.call.return_value = {"type": "agent_view", "active": True}
        enabled, _ = self.controller.toggle()
        self.assertTrue(enabled)

    def test_get_status(self) -> None:
        self.mock_client.call.side_effect = [
            {"workspaces": [{"workspace_id": "w1", "label": "Main", "focused": True}]},
            {"agents": [{"agent": "pi", "workspace_id": "w1"}]},
        ]

        status = self.controller.get_status()
        self.assertEqual(status["plugin_id"], "herdr-space-aware-agents")
        self.assertTrue(status["enabled"])
        self.assertEqual(status["focused_workspace_id"], "w1")
        self.assertEqual(len(status["workspaces"]), 1)
        self.assertEqual(len(status["agents"]), 1)


class TestActionHandlers(unittest.TestCase):
    """Test action and event dispatchers."""

    def setUp(self) -> None:
        self.mock_controller = MagicMock()

    def test_handle_action_toggle(self) -> None:
        self.mock_controller.toggle.return_value = (False, {})
        ret = main.handle_action(self.mock_controller, "toggle")
        self.assertEqual(ret, 0)
        self.mock_controller.toggle.assert_called_once()

    def test_handle_action_enable(self) -> None:
        ret = main.handle_action(self.mock_controller, "enable")
        self.assertEqual(ret, 0)
        self.mock_controller.apply_projection.assert_called_once()

    def test_handle_action_disable(self) -> None:
        ret = main.handle_action(self.mock_controller, "disable")
        self.assertEqual(ret, 0)
        self.mock_controller.clear_projection.assert_called_once()

    def test_handle_action_status(self) -> None:
        self.mock_controller.get_status.return_value = {"enabled": True}
        ret = main.handle_action(self.mock_controller, "status")
        self.assertEqual(ret, 0)
        self.mock_controller.get_status.assert_called_once()

    @patch.dict(os.environ, {"HERDR_PLUGIN_EVENT": "workspace.focused"})
    def test_handle_event_enabled(self) -> None:
        self.mock_controller.state_mgr.load_state.return_value = {"enabled": True}
        ret = main.handle_event(self.mock_controller)
        self.assertEqual(ret, 0)
        self.mock_controller.apply_projection.assert_called_once()

    @patch.dict(os.environ, {"HERDR_PLUGIN_EVENT": "workspace.focused"})
    def test_handle_event_disabled(self) -> None:
        self.mock_controller.state_mgr.load_state.return_value = {"enabled": False}
        ret = main.handle_event(self.mock_controller)
        self.assertEqual(ret, 0)
        self.mock_controller.apply_projection.assert_not_called()

    @patch.dict(os.environ, {"HERDR_PLUGIN_EVENT": "pane.closed"})
    def test_handle_pane_event_refreshes_tree(self) -> None:
        ret = main.handle_event(self.mock_controller)
        self.assertEqual(ret, 0)
        self.mock_controller.refresh_tree.assert_called_once()
        self.mock_controller.apply_projection.assert_not_called()

    @patch.dict(os.environ, {"HERDR_PLUGIN_CONTEXT_JSON": '{"focused_pane_id": "w1:p2", "workspace_id": "w1"}'})
    def test_handle_action_collapse_uses_focused_pane(self) -> None:
        self.mock_controller.toggle_collapse.return_value = "collapsed"
        ret = main.handle_action(self.mock_controller, "collapse")
        self.assertEqual(ret, 0)
        self.mock_controller.toggle_collapse.assert_called_once_with("w1:p2", "w1")

    def test_handle_action_subagents(self) -> None:
        self.mock_controller.toggle_subagents.return_value = True
        ret = main.handle_action(self.mock_controller, "subagents")
        self.assertEqual(ret, 0)
        self.mock_controller.toggle_subagents.assert_called_once()


if __name__ == "__main__":
    unittest.main()
