"""Agent lineage tree: who spawned whom, projected into Herdr pane tokens.

Spawners tag a child pane with a `parent_term` (preferred) or `parent_pane`
token. This module turns those tags plus persisted lineage into display
tokens that the Agents sidebar can sort, filter, and render:

  tree_key   sort key that keeps each child directly under its parent
  tree       connector glyph (▾/▸ on orchestrators, ├─/└─ on children)
  role       "orchestrator" or "subagent"
  collapsed  "+N" on a collapsed node, N = hidden descendants
  hidden     "1" when an ancestor is collapsed or subagents are hidden

Everything here is pure so it can be tested without a Herdr server.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set

TREE_TOKENS = ("tree_key", "tree", "role", "collapsed", "hidden")
PARENT_TOKENS = ("parent_term", "parent_pane")
LINEAGE_TTL_SECONDS = 24 * 60 * 60

ORCHESTRATOR = "orchestrator"
SUBAGENT = "subagent"
# Leading whitespace is trimmed from token values, so pad nested connectors
# with a braille blank, which renders empty but is not Unicode whitespace.
BLANK = "⠀"


def session_key(agent: Dict[str, Any]) -> Optional[str]:
    """Stable identity for an agent session that survives terminal restarts."""
    session = agent.get("agent_session") or {}
    value = session.get("value")
    return f"{session.get('agent', '')}:{value}" if value else None


def ingest_tags(
    agents: List[Dict[str, Any]], lineage: Dict[str, Dict[str, Any]], now: Optional[float] = None
) -> None:
    """Record parent tags reported on live panes into the persisted lineage map."""
    now = time.time() if now is None else now
    by_pane = {a.get("pane_id"): a for a in agents if a.get("pane_id")}
    by_term = {a.get("terminal_id"): a for a in agents if a.get("terminal_id")}

    for agent in agents:
        child = agent.get("terminal_id")
        tokens = agent.get("tokens") or {}
        if not child:
            continue
        existing = lineage.get(child)
        parent = tokens.get("parent_term")
        # Pane ids compact when panes close, so a parent_pane tag is only
        # trusted the first time; after that the lineage entry is authoritative.
        if not parent and not existing and tokens.get("parent_pane") in by_pane:
            parent = by_pane[tokens["parent_pane"]].get("terminal_id")
        if not parent or parent == child:
            continue
        if existing and existing.get("parent") == parent:
            continue
        parent_agent = by_term.get(parent, {})
        lineage[child] = {
            "parent": parent,
            "child_session": session_key(agent),
            "parent_session": session_key(parent_agent) if parent_agent else None,
            "seen": now,
        }


def rebind_and_prune(
    agents: List[Dict[str, Any]], lineage: Dict[str, Dict[str, Any]], now: Optional[float] = None
) -> None:
    """Follow agents whose terminal changed (session restore) and drop stale entries."""
    now = time.time() if now is None else now
    live = {a.get("terminal_id") for a in agents if a.get("terminal_id")}
    by_session = {session_key(a): a.get("terminal_id") for a in agents if session_key(a)}

    for child in list(lineage):
        entry = lineage[child]
        if entry.get("parent") not in live and by_session.get(entry.get("parent_session")):
            entry["parent"] = by_session[entry["parent_session"]]
        if child not in live and by_session.get(entry.get("child_session")):
            new_child = by_session[entry["child_session"]]
            lineage.pop(child)
            lineage[new_child] = entry
            child = new_child
        if child in live and entry.get("parent") in live:
            entry["seen"] = now
        elif now - entry.get("seen", 0) > LINEAGE_TTL_SECONDS:
            lineage.pop(child, None)


def build_forest(
    agents: List[Dict[str, Any]], lineage: Dict[str, Dict[str, Any]]
) -> Dict[str, Optional[str]]:
    """Map each live terminal to its live parent (None for roots), breaking cycles."""
    live = [a.get("terminal_id") for a in agents if a.get("terminal_id")]
    live_set = set(live)
    parent_of: Dict[str, Optional[str]] = {}
    for term in live:
        parent = (lineage.get(term) or {}).get("parent")
        parent_of[term] = parent if parent in live_set and parent != term else None

    for term in live:
        seen: Set[str] = {term}
        node = parent_of[term]
        while node is not None:
            if node in seen:
                parent_of[term] = None
                break
            seen.add(node)
            node = parent_of[node]
    return parent_of


def compute_tokens(
    agents: List[Dict[str, Any]],
    lineage: Dict[str, Dict[str, Any]],
    collapsed: Set[str],
    hide_subagents: bool = False,
) -> Dict[str, Dict[str, Optional[str]]]:
    """Return the desired tree tokens for every live agent, keyed by terminal id.

    A value of None means the token should be cleared.
    """
    parent_of = build_forest(agents, lineage)
    order = [a.get("terminal_id") for a in agents if a.get("terminal_id")]
    workspace_of = {a.get("terminal_id"): a.get("workspace_id") for a in agents}
    children: Dict[str, List[str]] = {term: [] for term in order}
    for term in order:
        if parent_of[term] is not None:
            children[parent_of[term]].append(term)

    def descendants(term: str) -> int:
        return sum(1 + descendants(child) for child in children[term])

    result: Dict[str, Dict[str, Optional[str]]] = {}

    def visit(term: str, key: str, depth: int, pad: str, last: bool, hidden: bool) -> None:
        kids = children[term]
        is_collapsed = term in collapsed and bool(kids)
        if depth == 0:
            glyph = ("▸" if is_collapsed else "▾") if kids else None
            role = ORCHESTRATOR if kids else None
        else:
            glyph = pad + ("└─" if last else "├─")
            role = SUBAGENT
        result[term] = {
            "tree_key": key,
            "tree": glyph,
            "role": role,
            "collapsed": f"+{descendants(term)}" if is_collapsed else None,
            "hidden": "1" if hidden else None,
        }
        child_pad = "" if depth == 0 else pad + (BLANK + " " if last else "│ ")
        for index, child in enumerate(kids):
            visit(
                child,
                f"{key}.{index:04d}",
                depth + 1,
                child_pad,
                index == len(kids) - 1,
                hidden or is_collapsed or hide_subagents,
            )

    # Keys are only compared within a workspace; the view sorts by workspace first.
    root_index: Dict[Any, int] = {}
    for term in order:
        if parent_of[term] is None:
            workspace = workspace_of.get(term)
            index = root_index.get(workspace, 0)
            root_index[workspace] = index + 1
            visit(term, f"{index:04d}", 0, "", True, False)
    return result


def token_patches(
    agents: List[Dict[str, Any]], desired: Dict[str, Dict[str, Optional[str]]]
) -> Dict[str, Dict[str, Optional[str]]]:
    """Diff desired tokens against live pane tokens; return per-pane patches."""
    patches: Dict[str, Dict[str, Optional[str]]] = {}
    for agent in agents:
        term, pane = agent.get("terminal_id"), agent.get("pane_id")
        if not term or not pane or term not in desired:
            continue
        current = agent.get("tokens") or {}
        patch = {
            name: value
            for name, value in desired[term].items()
            if current.get(name) != value
        }
        if patch:
            patches[pane] = patch
    return patches


def toggle_target(
    agents: List[Dict[str, Any]], lineage: Dict[str, Dict[str, Any]], pane_id: Optional[str]
) -> Optional[str]:
    """Pick the node a collapse key press acts on: the pane if it has children,
    otherwise its parent. Returns a terminal id or None."""
    parent_of = build_forest(agents, lineage)
    term = next((a.get("terminal_id") for a in agents if a.get("pane_id") == pane_id), None)
    if term is None:
        return None
    if any(parent == term for parent in parent_of.values()):
        return term
    return parent_of.get(term)
