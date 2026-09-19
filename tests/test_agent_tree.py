#!/usr/bin/env python3
"""Unit tests for agent lineage tree computation."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import agent_tree
import main


def agent(term, pane, workspace="w1", tokens=None, session=None):
    record = {"terminal_id": term, "pane_id": pane, "workspace_id": workspace, "tokens": tokens}
    if session:
        record["agent_session"] = {"agent": "claude", "kind": "id", "value": session}
    return record


class TestLineage(unittest.TestCase):

    def test_ingest_parent_term_and_parent_pane(self) -> None:
        agents = [
            agent("t1", "w1:p1"),
            agent("t2", "w1:p2", tokens={"parent_term": "t1"}),
            agent("t3", "w1:p3", tokens={"parent_pane": "w1:p1"}),
        ]
        lineage = {}
        agent_tree.ingest_tags(agents, lineage, now=100)
        self.assertEqual(lineage["t2"]["parent"], "t1")
        self.assertEqual(lineage["t3"]["parent"], "t1")

    def test_stale_parent_pane_does_not_override_lineage(self) -> None:
        # After panes compact, w1:p1 may point at a different terminal.
        agents = [agent("t9", "w1:p1"), agent("t3", "w1:p3", tokens={"parent_pane": "w1:p1"})]
        lineage = {"t3": {"parent": "t1", "seen": 100}}
        agent_tree.ingest_tags(agents, lineage, now=200)
        self.assertEqual(lineage["t3"]["parent"], "t1")

    def test_self_parent_ignored(self) -> None:
        lineage = {}
        agent_tree.ingest_tags([agent("t1", "w1:p1", tokens={"parent_term": "t1"})], lineage)
        self.assertEqual(lineage, {})

    def test_rebind_follows_session_after_restart(self) -> None:
        lineage = {"old2": {"parent": "old1", "child_session": "claude:s2", "parent_session": "claude:s1", "seen": 100}}
        agents = [agent("new1", "w1:p1", session="s1"), agent("new2", "w1:p2", session="s2")]
        agent_tree.rebind_and_prune(agents, lineage, now=200)
        self.assertEqual(lineage, {"new2": {**lineage["new2"], "parent": "new1"}})
        self.assertEqual(lineage["new2"]["seen"], 200)

    def test_prune_expired_entries(self) -> None:
        lineage = {"gone": {"parent": "t1", "seen": 0}}
        agent_tree.rebind_and_prune([agent("t1", "w1:p1")], lineage, now=agent_tree.LINEAGE_TTL_SECONDS + 1)
        self.assertEqual(lineage, {})

    def test_cycle_is_broken(self) -> None:
        agents = [agent("a", "w1:p1"), agent("b", "w1:p2")]
        lineage = {"a": {"parent": "b"}, "b": {"parent": "a"}}
        parent_of = agent_tree.build_forest(agents, lineage)
        self.assertIn(None, parent_of.values())


class TestComputeTokens(unittest.TestCase):

    def setUp(self) -> None:
        self.agents = [
            agent("lead", "w1:p1"),
            agent("solo", "w1:p2"),
            agent("kid1", "w1:p3"),
            agent("kid2", "w1:p4"),
            agent("grandkid", "w1:p5"),
            agent("other", "w2:p1", workspace="w2"),
        ]
        self.lineage = {
            "kid1": {"parent": "lead"},
            "kid2": {"parent": "lead"},
            "grandkid": {"parent": "kid1"},
        }

    def test_tree_shape(self) -> None:
        tokens = agent_tree.compute_tokens(self.agents, self.lineage, set())
        self.assertEqual(tokens["lead"], {"tree_key": "0000", "tree": "▾", "role": "orchestrator", "collapsed": None, "hidden": None})
        self.assertEqual(tokens["solo"]["tree_key"], "0001")
        self.assertIsNone(tokens["solo"]["role"])
        self.assertIsNone(tokens["solo"]["tree"])
        self.assertEqual(tokens["kid1"]["tree_key"], "0000.0000")
        self.assertEqual(tokens["kid1"]["tree"], "├─")
        self.assertEqual(tokens["kid2"]["tree"], "└─")
        self.assertEqual(tokens["grandkid"]["tree_key"], "0000.0000.0000")
        self.assertEqual(tokens["grandkid"]["tree"], "│ └─")
        self.assertEqual(tokens["grandkid"]["role"], "subagent")
        # Root keys restart per workspace; the view sorts by workspace first.
        self.assertEqual(tokens["other"]["tree_key"], "0000")

    def test_children_sort_after_parent(self) -> None:
        tokens = agent_tree.compute_tokens(self.agents, self.lineage, set())
        in_w1 = [a["terminal_id"] for a in self.agents if a["workspace_id"] == "w1"]
        ordered = sorted(in_w1, key=lambda t: tokens[t]["tree_key"])
        self.assertEqual(ordered, ["lead", "kid1", "grandkid", "kid2", "solo"])

    def test_collapse_hides_descendants(self) -> None:
        tokens = agent_tree.compute_tokens(self.agents, self.lineage, {"lead"})
        self.assertEqual(tokens["lead"]["tree"], "▸")
        self.assertEqual(tokens["lead"]["collapsed"], "+3")
        self.assertIsNone(tokens["lead"]["hidden"])
        for term in ("kid1", "kid2", "grandkid"):
            self.assertEqual(tokens[term]["hidden"], "1")
        self.assertIsNone(tokens["solo"]["hidden"])

    def test_hide_subagents(self) -> None:
        tokens = agent_tree.compute_tokens(self.agents, self.lineage, set(), hide_subagents=True)
        self.assertEqual({t for t, v in tokens.items() if v["hidden"]}, {"kid1", "kid2", "grandkid"})

    def test_last_child_pad_is_not_whitespace(self) -> None:
        lineage = {"kid1": {"parent": "lead"}, "grandkid": {"parent": "kid1"}}
        tokens = agent_tree.compute_tokens(self.agents, lineage, set())
        self.assertEqual(tokens["grandkid"]["tree"], agent_tree.BLANK + " └─")
        self.assertEqual(tokens["grandkid"]["tree"], tokens["grandkid"]["tree"].strip())

    def test_token_patches_only_send_changes(self) -> None:
        agents = [
            agent("lead", "w1:p1", tokens={"tree_key": "0000", "tree": "▾", "role": "orchestrator"}),
            agent("kid", "w1:p2", tokens={"parent_term": "lead", "hidden": "1"}),
        ]
        desired = agent_tree.compute_tokens(agents, {"kid": {"parent": "lead"}}, set())
        patches = agent_tree.token_patches(agents, desired)
        self.assertNotIn("w1:p1", patches)
        self.assertEqual(patches["w1:p2"], {"tree_key": "0000.0000", "tree": "└─", "role": "subagent", "hidden": None})

    def test_toggle_target(self) -> None:
        self.assertEqual(agent_tree.toggle_target(self.agents, self.lineage, "w1:p1"), "lead")
        self.assertEqual(agent_tree.toggle_target(self.agents, self.lineage, "w1:p4"), "lead")
        self.assertEqual(agent_tree.toggle_target(self.agents, self.lineage, "w1:p3"), "kid1")
        self.assertIsNone(agent_tree.toggle_target(self.agents, self.lineage, "w1:p2"))
        self.assertIsNone(agent_tree.toggle_target(self.agents, self.lineage, "w9:p9"))


class TestControllerTree(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.env_patch = patch.dict(os.environ, {
            "HERDR_PLUGIN_ID": "herdr-space-aware-agents",
            "HERDR_PLUGIN_STATE_DIR": self.tmp_dir.name,
            "HERDR_PLUGIN_CONFIG_DIR": self.tmp_dir.name,
            "HERDR_WORKSPACE_ID": "",
        })
        self.env_patch.start()
        self.agents = [
            agent("lead", "w1:p1"),
            agent("kid", "w1:p2", tokens={"parent_term": "lead"}),
            agent("lead2", "w1:p3"),
            agent("kid2", "w1:p4", tokens={"parent_term": "lead2"}),
        ]
        self.client = MagicMock()
        self.client.call.side_effect = self._call
        self.state_mgr = main.PluginState()
        self.controller = main.SpaceAwareController(self.client, self.state_mgr)

    def _call(self, method, params=None):
        if method == "agent.list":
            return {"agents": self.agents}
        return {}

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.tmp_dir.cleanup()

    def _reports(self):
        return {
            params["pane_id"]: params["tokens"]
            for (method, params), _ in self.client.call.call_args_list
            if method == "pane.report_metadata"
        }

    def test_refresh_reports_tokens_and_persists_lineage(self) -> None:
        self.controller.refresh_tree()
        reports = self._reports()
        self.assertEqual(reports["w1:p1"]["role"], "orchestrator")
        self.assertEqual(reports["w1:p2"]["role"], "subagent")
        self.assertEqual(self.state_mgr.load_tree()["lineage"]["kid"]["parent"], "lead")
        for params in (c[0][1] for c in self.client.call.call_args_list if c[0][0] == "pane.report_metadata"):
            self.assertEqual(params["source"], "plugin:herdr-space-aware-agents")

    def test_toggle_collapse_focused_orchestrator(self) -> None:
        self.assertEqual(self.controller.toggle_collapse("w1:p1"), "collapsed")
        self.assertEqual(self._reports()["w1:p2"]["hidden"], "1")
        self.assertEqual(self.state_mgr.load_tree()["collapsed"], ["lead"])
        self.assertEqual(self.controller.toggle_collapse("w1:p1"), "expanded")
        self.assertEqual(self.state_mgr.load_tree()["collapsed"], [])

    def test_toggle_collapse_without_tree_flips_workspace(self) -> None:
        self.agents.append(agent("solo", "w1:p5"))
        self.assertEqual(self.controller.toggle_collapse("w1:p5", "w1"), "collapsed all")
        self.assertEqual(self.state_mgr.load_tree()["collapsed"], ["lead", "lead2"])
        self.assertEqual(self.controller.toggle_collapse("w1:p5", "w1"), "expanded all")

    def test_toggle_subagents(self) -> None:
        self.assertTrue(self.controller.toggle_subagents())
        self.assertEqual(self._reports()["w1:p4"]["hidden"], "1")
        self.assertFalse(self.controller.toggle_subagents())

    def test_tree_disabled_skips_refresh(self) -> None:
        Path(self.tmp_dir.name, "config.json").write_text('{"tree_enabled": false}')
        self.controller.refresh_tree()
        self.client.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
