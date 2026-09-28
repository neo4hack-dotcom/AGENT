"""The protections README → "Locking it down" describes, checked.

    cd backend && python -m unittest discover tests

Standard library only, and runnable on Windows as on macOS or Linux: the Windows-only rules
are exercised through their `windows=True` form, so they are tested wherever this runs.
The app is imported from a temporary directory — its store, memory and workspace are created
there, never in the repository.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND.parent / "mcp_servers"))

_TMP = tempfile.TemporaryDirectory()
os.chdir(_TMP.name)                     # before anything builds the container
os.environ.setdefault("AGENT_MCP_AUTOCONNECT", "false")


def _settings(**overrides) -> SimpleNamespace:
    base = {"enable_pandas": True, "allow_custom_commands": True}
    return SimpleNamespace(**{**base, **overrides})


class RemotePaths(unittest.TestCase):
    def test_windows_rules(self):
        from app.tools.files import remote_path
        refused = [r"\\evil\share\x.csv", "//evil/share", r"\\?\C:\x", r"\\.\PhysicalDrive0",
                   r"\\evil@SSL\dav", "report.csv:hidden", "CON:", "C:relative"]
        allowed = [r"C:\ws\a.csv", "C:/ws/a.csv", "reports/q1.csv", "q1.csv"]
        for path in refused:
            self.assertTrue(remote_path(path, windows=True), path)
        for path in allowed:
            self.assertEqual(remote_path(path, windows=True), "", path)

    def test_posix_leaves_paths_alone_but_not_nul(self):
        from app.tools.files import remote_path
        self.assertEqual(remote_path("//tmp/x", windows=False), "")
        self.assertTrue(remote_path("a\x00b", windows=False))

    def test_bundled_servers_share_the_rule(self):
        from _mcp_stdio import remote_path
        self.assertTrue(remote_path(r"\\evil\x", windows=True))
        self.assertEqual(remote_path("data.csv", windows=True), "")

    def test_run_python_guard_refuses_network_paths(self):
        from app.tools.code import UNC_GUARD
        probe = UNC_GUARD + (
            "import os, pathlib\n"
            "hits = 0\n"
            "for f in (lambda: open(r'\\\\evil\\s\\x'), lambda: os.listdir('//evil/s'),\n"
            "          lambda: pathlib.Path(r'\\\\evil\\s\\y').read_text()):\n"
            "    try:\n"
            "        f()\n"
            "    except PermissionError:\n"
            "        hits += 1\n"
            "open('local.txt', 'w').write('ok')\n"
            "print(hits, open('local.txt').read())\n")
        out = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True,
                             cwd=_TMP.name, timeout=60)
        self.assertEqual(out.stdout.strip(), "3 ok", out.stderr)


class ChildEnvironment(unittest.TestCase):
    def test_secrets_are_not_inherited(self):
        from app.mcp.protocol import inherited_env
        planted = {"AGENT_ADMIN_PASSWORD": "x", "AGENT_OPENAI_API_KEY": "x", "DB_PASSWORD": "x",
                   "GITHUB_TOKEN": "x", "MY_SECRET": "x", "ORACLE_HOME": "/opt/oracle"}
        saved = {k: os.environ.get(k) for k in planted}
        os.environ.update(planted)
        try:
            env = inherited_env()
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual({k for k in planted if k in env}, {"ORACLE_HOME"})


class AirGap(unittest.TestCase):
    def test_embedded_ipv4_is_judged_as_ipv4(self):
        from app.network import _private
        self.assertFalse(_private("::ffff:8.8.8.8"))
        self.assertFalse(_private("2002:0808:0808::1"))
        self.assertTrue(_private("::ffff:10.0.0.1"))
        self.assertTrue(_private("10.2.3.4"))
        self.assertTrue(_private("::1"))


class DeploymentSwitches(unittest.TestCase):
    def setUp(self):
        from app.mcp.catalog import CATALOG_BY_ID, instantiate
        self.pandas = instantiate("pandas-frames", {}, {"workspace": _TMP.name})
        self.files = instantiate("filesystem", {"root_path": _TMP.name})
        self.catalog = CATALOG_BY_ID

    def test_pandas_switch(self):
        from app.mcp.catalog import deployment_refusal, visible_catalog
        self.assertIsNone(deployment_refusal(self.pandas, _settings()))
        self.assertIn("AGENT_ENABLE_PANDAS", deployment_refusal(self.pandas, _settings(enable_pandas=False)))
        self.assertNotIn("pandas-frames", [e["id"] for e in visible_catalog(_settings(enable_pandas=False))])
        # Pointed at the bundled script by hand, it is still Pandas Frames.
        by_hand = {"name": "df", "transport": "stdio", "command": "python", "args": self.pandas["args"]}
        self.assertTrue(deployment_refusal(by_hand, _settings(enable_pandas=False)))

    def test_custom_commands_switch(self):
        from app.mcp.catalog import PYTHON, deployment_refusal, launch_command
        locked = _settings(allow_custom_commands=False)
        custom = {"name": "shell", "transport": "stdio", "command": "cmd.exe", "args": ["/c", "calc"]}
        inline = {"name": "py", "transport": "stdio", "command": PYTHON, "args": ["-c", "print(1)"]}
        self.assertTrue(deployment_refusal(custom, locked))
        self.assertTrue(deployment_refusal(inline, locked))
        self.assertIsNone(deployment_refusal(custom, _settings()))
        self.assertIsNone(deployment_refusal(self.files, locked))
        loader = {**self.files, "env": {"PYTHONPATH": _TMP.name}}
        self.assertIn("PYTHONPATH", deployment_refusal(loader, locked))
        self.assertIsNone(deployment_refusal({"name": "gw", "transport": "http", "url": "http://10.0.0.5/mcp"}, locked))
        swapped = {**self.files, "command": "evil.exe"}
        self.assertEqual(launch_command(swapped, locked), PYTHON)


class Access(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.deps import container
        from app.main import app
        cls.c = container
        cls.client = TestClient(app, base_url="http://localhost")

    def tearDown(self):
        self.c.env.require_signin = False

    def test_backslash_urls_are_refused(self):
        self.assertEqual(self.client.get("/assets/%5C%5Cevil%5Cshare%5Cx.js").status_code, 400)
        self.assertEqual(self.client.get("/api/artifacts/%5C%5Cevil%5Cx").status_code, 400)
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_require_signin_ends_loopback_trust(self):
        self.assertEqual(self.client.get("/api/conversations").status_code, 200)
        self.c.env.require_signin = True
        response = self.client.get("/api/conversations")
        self.assertEqual(response.status_code, 403)
        self.assertIn("AGENT_REQUIRE_SIGNIN", response.json()["detail"])

    def test_signin_attempts_are_keyed_on_the_peer(self):
        from app import security
        request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.7"),
                                  headers=SimpleNamespace(getlist=lambda _: ["1.2.3.4"]))
        self.assertEqual(security._client_key(request), "10.0.0.7")

    def test_python_tool_is_capped_by_the_deployment(self):
        self.c.store.set_prefs({"enable_python_tool": True})
        self.c.env.enable_python_tool = False
        try:
            self.assertFalse(self.c.get("enable_python_tool"))
        finally:
            self.c.env.enable_python_tool = True
        self.assertTrue(self.c.get("enable_python_tool"))
        self.c.store.set_prefs({"enable_python_tool": False})
        self.assertFalse(self.c.get("enable_python_tool"))

    def test_csp_allows_no_eval(self):
        from app.main import _csp
        self.assertNotIn("unsafe-eval", _csp([]))


class WindowsNames(unittest.TestCase):
    def test_reserved_device_names(self):
        from app.data.exports import safe_name
        self.assertEqual(safe_name("nul", "csv"), "nul-export.csv")
        self.assertEqual(safe_name("Q1: P&L", "xlsx"), "Q1-PL.xlsx")


if __name__ == "__main__":
    unittest.main()
