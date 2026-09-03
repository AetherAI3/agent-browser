"""The two clients are one release in two ecosystems, so their metadata must agree.

npm and PyPI spell a prerelease differently (`0.1.0-rc.1` vs `0.1.0rc1`), so the comparison
normalises rather than demanding an identical string. It costs nothing on a final version and
keeps working on the next candidate.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import aether_browser
from aether_browser import cli

HERE = Path(__file__).resolve().parent
PYTHON_CLIENT = HERE.parent
NODE_CLIENT = PYTHON_CLIENT.parent / "node"


def _pyproject_field(name: str) -> str:
    text = (PYTHON_CLIENT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(rf'^{name} = "([^"]+)"', text, re.MULTILINE)
    assert match is not None, f"{name} missing from pyproject.toml"
    return match.group(1)


def _normalize(version: str) -> str:
    """Fold npm's `0.1.0-rc.1` and PEP 440's `0.1.0rc1` onto the same value."""
    return version.replace("-", "").replace(".rc", "rc").replace("rc.", "rc")


class TestPackaging(unittest.TestCase):
    def setUp(self) -> None:
        self.node = json.loads((NODE_CLIENT / "package.json").read_text(encoding="utf-8"))

    def test_the_two_clients_publish_under_the_same_name(self) -> None:
        self.assertEqual(_pyproject_field("name"), self.node["name"])

    def test_the_two_clients_publish_the_same_version(self) -> None:
        self.assertEqual(
            _normalize(_pyproject_field("version")),
            _normalize(self.node["version"]),
            "bump both clients together or they stop being the same release",
        )

    def test_the_module_version_matches_the_distribution_version(self) -> None:
        self.assertEqual(aether_browser.__version__, _pyproject_field("version"))

    def test_the_cli_builds_the_source_tag_this_client_was_cut_from(self) -> None:
        self.assertEqual(cli.SOURCE_TAG, f"v{self.node['version']}")

    def test_the_two_clients_expose_the_same_commands(self) -> None:
        node_cli = (NODE_CLIENT / "src" / "cli.js").read_text(encoding="utf-8")
        for command in ("doctor", "up", "down", "status", "open", "mcp", "help"):
            self.assertIn(command, cli.COMMANDS)
            self.assertIn(f"  {command}", node_cli)

    def test_the_two_mcp_servers_expose_the_same_tools(self) -> None:
        """A client must be able to swap `aether-browser mcp` for `npx aether-browser mcp`."""
        from aether_browser.mcp import TOOLS

        node_mcp = (NODE_CLIENT / "src" / "mcp.js").read_text(encoding="utf-8")
        for tool in TOOLS:
            self.assertIn(f"name: '{tool['name']}'", node_mcp)
        self.assertEqual(len(TOOLS), node_mcp.count("    name: 'browser_"))

    def test_the_package_declares_no_runtime_dependencies(self) -> None:
        text = (PYTHON_CLIENT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("dependencies = []", text)


if __name__ == "__main__":
    unittest.main()
