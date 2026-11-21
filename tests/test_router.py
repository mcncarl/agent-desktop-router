#!/usr/bin/env python3
"""Comprehensive automated test suite for agent-desktop-router."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_PY = REPO_ROOT / "src" / "guard.py"
PROBE_SWIFT = REPO_ROOT / "src" / "probe.swift"
INSTALL_PY = REPO_ROOT / "scripts" / "install.py"
DOCTOR_PY = REPO_ROOT / "scripts" / "doctor.py"
UNINSTALL_PY = REPO_ROOT / "scripts" / "uninstall.py"
DEFAULT_CFG = REPO_ROOT / "config" / "blocked-apps.default.json"


class GuardAndProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp_path = Path(cls._tmp.name)

        # Create a fast deterministic mock probe for unit tests of guard.py
        cls.mock_probe = cls.tmp_path / "mock_probe"
        cls.mock_probe.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "mode = sys.argv[1]\n"
            "arg = sys.argv[2]\n"
            "if mode == 'pid':\n"
            "    if arg == '101':\n"
            "        print('101\\tObsidian\\tmd.obsidian\\tObsidian')\n"
            "    elif arg == '102':\n"
            "        print('102\\t企业微信\\tcom.tencent.WeWorkMac\\t企业微信')\n"
            "    elif arg == '666':\n"
            "        print('666\\tWeChat\\tcom.tencent.xinWeChat\\tWeChat')\n"
            "    elif arg == '667':\n"
            "        print('667\\t1Password\\tcom.1password.1password\\t1Password')\n"
            "    else:\n"
            "        print(f'{arg}\\tFinder\\tcom.apple.finder\\tFinder')\n"
            "elif mode == 'window':\n"
            "    if arg == '200':\n"
            "        print('2000\\tGoogle Chrome\\tcom.google.Chrome\\tGoogle Chrome\\tGoogle Chrome')\n"
            "    elif arg == '600':\n"
            "        print('666\\tWeChat\\tcom.tencent.xinWeChat\\tWeChat\\t微信')\n"
            "    else:\n"
            "        sys.exit(3)\n"
            "elif mode == 'at':\n"
            "    x = float(sys.argv[2])\n"
            "    if int(x) == 999:\n"
            "        print('667\\t1Password\\tcom.1password.1password\\t1Password\\t1Password')\n"
            "    else:\n"
            "        print('101\\tObsidian\\tmd.obsidian\\tObsidian\\tObsidian')\n",
            encoding="utf-8",
        )
        cls.mock_probe.chmod(0o755)

        # Create mock snapshot state directory
        cls.state_dir = cls.tmp_path / "ad_state"
        snap_dir = cls.state_dir / "snapshots" / "snap1"
        snap_dir.mkdir(parents=True)
        refmap = {
            "inner": {
                "@e1": {"pid": 101, "name": "Note Title", "role": "AXTextField", "source_app": "Obsidian"},
                "@e2": {"pid": 102, "name": "Work Thread", "role": "AXRow", "source_app": "企业微信"},
                "@e3": {"pid": 666, "name": "Chat", "role": "AXWindow", "source_app": "WeChat"},
                "@e4": {"pid": 667, "name": "Vault", "role": "AXButton", "source_app": "1Password"},
            }
        }
        (snap_dir / "refmap.json").write_text(json.dumps(refmap), encoding="utf-8")

        cls.base_env = {
            **os.environ,
            "ADR_CONFIG": str(DEFAULT_CFG),
            "ADR_PROBE": str(cls.mock_probe),
            "ADR_STATE": str(cls.state_dir),
            "ADR_LOG": str(cls.tmp_path / "guard.log"),
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def run_claude(self, tool_name: str, tool_input: dict, env: dict | None = None) -> dict | None:
        payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input, "cwd": str( self.tmp_path )})
        res = subprocess.run(
            [sys.executable, "-I", str(GUARD_PY), "--protocol", "claude"],
            input=payload,
            capture_output=True,
            text=True,
            env=env or self.base_env,
            timeout=10,
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        return json.loads(res.stdout) if res.stdout.strip() else None

    def assert_claude_allow(self, tool_name: str, tool_input: dict) -> None:
        out = self.run_claude(tool_name, tool_input)
        self.assertIsNone(out, msg=f"Expected allow, got: {out}")

    def assert_claude_deny(self, tool_name: str, tool_input: dict, env: dict | None = None) -> str:
        out = self.run_claude(tool_name, tool_input, env=env)
        self.assertIsNotNone(out, msg=f"Expected deny for {tool_name}: {tool_input}")
        hook_out = out["hookSpecificOutput"]
        self.assertEqual(hook_out["permissionDecision"], "deny")
        return hook_out["permissionDecisionReason"]

    def run_agy(self, tool_name: str, args: dict) -> dict:
        payload = json.dumps({"toolCall": {"name": tool_name, "args": args}, "cwd": str(self.tmp_path)})
        res = subprocess.run(
            [sys.executable, "-I", str(GUARD_PY), "--protocol", "antigravity"],
            input=payload,
            capture_output=True,
            text=True,
            env=self.base_env,
            timeout=10,
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        return json.loads(res.stdout)

    def test_claude_allows_safe_and_exempt_commands(self) -> None:
        self.assert_claude_allow("Bash", {"command": "git status && ls -la"})
        self.assert_claude_allow("Bash", {"command": "npm install -g agent-desktop"})
        self.assert_claude_allow("Bash", {"command": "agent-desktop status"})
        self.assert_claude_allow("Bash", {"command": 'agent-desktop snapshot --app "Obsidian" -i'})
        self.assert_claude_allow("Bash", {"command": "agent-desktop snapshot --window-id w-200 -i --compact"})
        self.assert_claude_allow("Bash", {"command": "agent-desktop click @snap1:e1"})
        self.assert_claude_allow("Bash", {"command": "agent-desktop mouse-click --xy 200,300"})

        # Work / public exemptions must NOT be false-positived by "微信" / "weixin"
        self.assert_claude_allow("Bash", {"command": 'agent-desktop snapshot --app "企业微信" -i'})
        self.assert_claude_allow("Bash", {"command": 'agent-desktop snapshot --app "com.tencent.WeWorkMac" -i'})
        self.assert_claude_allow("Bash", {"command": "agent-desktop click @snap1:e2"})
        self.assert_claude_allow("Bash", {"command": 'agent-desktop type @snap1:e1 "阅读微信公众号文章与微信读书笔记"'})
        self.assert_claude_allow("mcp__lcu__js", {"code": 'let app = await cua.getApp("企业微信");'})

    def test_claude_blocks_protected_apps_and_surfaces(self) -> None:
        blocked_commands = [
            'agent-desktop snapshot --app "WeChat" -i',
            'agent-desktop snapshot --app "微信" -i',
            'agent-desktop snapshot --app "微信2" -i',
            'agent-desktop snapshot --app "WeChatAppEx" -i',
            'agent-desktop launch "com.tencent.xinWeChat"',
            'agent-desktop snapshot --app "Messages" --expected-app "com.apple.MobileSMS"',
            'agent-desktop snapshot --app "iMessage" -i',
            'agent-desktop snapshot --app "WhatsApp" -i',
            'agent-desktop snapshot --app "Telegram" -i',
            'agent-desktop snapshot --app "Signal" -i',
            'agent-desktop snapshot --app "1Password" -i',
            'agent-desktop snapshot --app "Bitwarden" -i',
            'agent-desktop snapshot --app "Keychain Access" -i',
            'agent-desktop snapshot --app "钥匙串访问" -i',
            "agent-desktop snapshot --surface notification-center",
            "agent-desktop list-notifications",
            # Mixed command: allowed WeCom + blocked personal WeChat must still be denied!
            'agent-desktop snapshot --app "企业微信" --title "转发给微信好友"',
        ]
        for cmd in blocked_commands:
            with self.subTest(cmd=cmd):
                self.assert_claude_deny("Bash", {"command": cmd})

    def test_claude_blocks_unscoped_refs_windows_coords_and_bypasses(self) -> None:
        # Unscoped commands that would hit frontmost window blindly
        self.assert_claude_deny("Bash", {"command": "agent-desktop snapshot -i"})
        self.assert_claude_deny("Bash", {"command": "agent-desktop screenshot /tmp/out.png"})
        self.assert_claude_deny("Bash", {"command": "agent-desktop press Enter"})
        self.assert_claude_deny("Bash", {"command": 'agent-desktop focus-window --title "Notes"'})

        # Probe-backed PID / window / coordinate blocks
        self.assert_claude_deny("Bash", {"command": "agent-desktop click @snap1:e3"})
        self.assert_claude_deny("Bash", {"command": "agent-desktop click @snap1:e4"})
        self.assert_claude_deny("Bash", {"command": "agent-desktop click @missing:e1"})
        self.assert_claude_deny("Bash", {"command": "agent-desktop snapshot --window-id w-600 -i"})
        self.assert_claude_deny("Bash", {"command": "agent-desktop snapshot --window-id w-999 -i"})
        self.assert_claude_deny("Bash", {"command": "agent-desktop mouse-click --xy 999,200"})

        # Shell variable & wrapper bypasses
        self.assert_claude_deny("Bash", {"command": 'agent-desktop snapshot --app "$TARGET_APP"'})
        self.assert_claude_deny("Bash", {"command": "agent-desktop batch '[]'"})
        self.assert_claude_deny("Bash", {"command": "echo snapshot | xargs agent-desktop"})
        self.assert_claude_deny("Bash", {"command": 'python3 -c "import os; os.system(\'agent-desktop status\')"'})

        ext_script = self.tmp_path / "sneaky.sh"
        ext_script.write_text("#!/bin/sh\nagent-desktop snapshot -i\n", encoding="utf-8")
        ext_script.chmod(0o755)
        self.assert_claude_deny("Bash", {"command": f"sh {ext_script}"})

        # MCP blocks
        self.assert_claude_deny("mcp__agent-desktop__snapshot", {"interactive": True})
        self.assert_claude_deny("mcp__agent-desktop__snapshot", {"app": "1Password"})
        self.assert_claude_deny("mcp__lcu__js", {"code": 'await cua.getApp("WeChat")'})
        self.assert_claude_deny("mcp__lcu__js", {"code": 'await cua.getApp("Bitwarden")'})

    def test_fail_closed_on_corrupt_config(self) -> None:
        bad_cfg = self.tmp_path / "corrupt.json"
        bad_cfg.write_text("{not-valid-json", encoding="utf-8")
        bad_env = {**self.base_env, "ADR_CONFIG": str(bad_cfg)}
        reason = self.assert_claude_deny("Bash", {"command": "agent-desktop status"}, env=bad_env)
        self.assertIn("Failed to load blocked-apps config", reason)

    def test_antigravity_protocol(self) -> None:
        ok = self.run_agy("run_command", {"CommandLine": 'agent-desktop snapshot --app "企业微信" -i'})
        self.assertEqual(ok["decision"], "allow")

        deny_cmd = self.run_agy("run_command", {"CommandLine": 'agent-desktop snapshot --app "Telegram" -i'})
        self.assertEqual(deny_cmd["decision"], "deny")

        deny_lcu = self.run_agy("mcp_router_lcu_js", {"code": 'await cua.getApp("WhatsApp")'})
        self.assertEqual(deny_lcu["decision"], "deny")

    def test_cli_check_protocol(self) -> None:
        allow_res = subprocess.run(
            [sys.executable, "-I", str(GUARD_PY), "--cli-check", "snapshot", "--app", "Obsidian", "-i"],
            capture_output=True,
            text=True,
            env=self.base_env,
            timeout=10,
        )
        self.assertEqual(allow_res.returncode, 0)

        deny_res = subprocess.run(
            [sys.executable, "-I", str(GUARD_PY), "--cli-check", "snapshot", "-i"],
            capture_output=True,
            text=True,
            env=self.base_env,
            timeout=10,
        )
        self.assertEqual(deny_res.returncode, 2)
        payload = json.loads(deny_res.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "POLICY_DENIED")

    @unittest.skipUnless(sys.platform == "darwin", "Swift CoreGraphics probe requires macOS")
    def test_native_swift_probe_compiles_and_runs(self) -> None:
        compiled = self.tmp_path / "probe_real"
        subprocess.run(["swiftc", "-O", "-o", str(compiled), str(PROBE_SWIFT)], check=True, timeout=60)
        res_pid = subprocess.run([str(compiled), "pid", str(os.getpid())], capture_output=True, text=True, timeout=5)
        self.assertEqual(res_pid.returncode, 0)
        self.assertTrue(res_pid.stdout.startswith(f"{os.getpid()}\t"))

        res_missing = subprocess.run([str(compiled), "window", "999999999"], capture_output=True, text=True, timeout=5)
        self.assertEqual(res_missing.returncode, 3)


class InstallerLifecycleTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "End-to-end installer compiles probe.swift on macOS")
    def test_install_doctor_idempotency_and_uninstall_in_isolated_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "home"
            fake_home.mkdir()

            # Pre-seed an unrelated Claude hook to ensure install & uninstall preserve user hooks
            claude_dir = fake_home / ".claude"
            claude_dir.mkdir(parents=True)
            existing_settings = {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Write", "hooks": [{"type": "command", "command": "/usr/bin/true"}]}
                    ]
                }
            }
            (claude_dir / "settings.json").write_text(json.dumps(existing_settings), encoding="utf-8")

            # Create mock real `agent-desktop` binary and mock `lcu` tree
            ext_bin = Path(tmp) / "ext_bin"
            ext_bin.mkdir()
            real_ad = ext_bin / "real-ad"
            real_ad.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "status" ]; then\n'
                '  echo \'{"ok":true,"data":{"permissions":{"accessibility":{"state":"granted"}}}}\'\n'
                "  exit 0\n"
                "fi\n"
                'echo "EXECUTED:$*"\n',
                encoding="utf-8",
            )
            real_ad.chmod(stat.S_IRWXU)

            lcu_root = fake_home / ".local" / "share" / "lcu" / "current"
            (lcu_root / "bin").mkdir(parents=True)
            (lcu_root / "adapters").mkdir(parents=True)
            (lcu_root / "agent-tools" / "node" / "bin").mkdir(parents=True)
            for p in (
                lcu_root / "bin" / "lcu",
                lcu_root / "adapters" / "antigravity.mjs",
                lcu_root / "agent-tools" / "node" / "bin" / "node",
            ):
                p.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                p.chmod(stat.S_IRWXU)

            env = {
                **os.environ,
                "HOME": str(fake_home),
                "ADR_REAL_AGENT_DESKTOP": str(real_ad),
                "ADR_LCU_BIN": str(lcu_root / "bin" / "lcu"),
            }

            # 1. Install for all three agents
            ins1 = subprocess.run(
                [sys.executable, str(INSTALL_PY), "--agents", "all", "--home", str(fake_home), "--json"],
                capture_output=True,
                text=True,
                env=env,
                timeout=90,
            )
            self.assertEqual(ins1.returncode, 0, msg=ins1.stderr)
            ins1_data = json.loads(ins1.stdout)
            self.assertTrue(ins1_data["ok"])
            self.assertTrue(ins1_data["agents"]["antigravity"]["lcu_wired"])

            # 2. Test generated CLI wrapper blocks protected app & passes safe app through
            wrapper = Path(ins1_data["cli_wrapper"])
            self.assertTrue(wrapper.is_file())
            ok_wrap = subprocess.run(
                [str(wrapper), "snapshot", "--app", "Obsidian", "-i"],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertEqual(ok_wrap.returncode, 0)
            self.assertIn("EXECUTED:snapshot --app Obsidian -i", ok_wrap.stdout)

            deny_wrap = subprocess.run(
                [str(wrapper), "snapshot", "--app", "WeChat", "-i"],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertEqual(deny_wrap.returncode, 2)
            self.assertIn("POLICY_DENIED", deny_wrap.stdout)

            # 3. Run doctor.py --json
            doc = subprocess.run(
                [sys.executable, str(DOCTOR_PY), "--home", str(fake_home), "--json"],
                capture_output=True,
                text=True,
                env=env,
                timeout=20,
            )
            doc_data = json.loads(doc.stdout)
            self.assertTrue(doc_data["checks"]["config"]["ok"])
            self.assertTrue(doc_data["checks"]["probe"]["ok"])
            self.assertTrue(doc_data["checks"]["cli_wrapper"]["ok"])
            self.assertEqual(doc_data["checks"]["agent_desktop"]["accessibility"], "granted")
            self.assertTrue(doc_data["checks"]["hosts"]["claude_pretool_hook"])
            self.assertTrue(doc_data["checks"]["hosts"]["antigravity_plugin"])
            self.assertTrue(doc_data["checks"]["hosts"]["codex_rules"])

            # 4. Re-run install.py (--lang zh) to verify idempotency (no duplicate hooks or rule blocks)
            ins2 = subprocess.run(
                [sys.executable, str(INSTALL_PY), "--agents", "all", "--lang", "zh", "--home", str(fake_home), "--skip-compile-probe", "--json"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            self.assertEqual(ins2.returncode, 0, msg=ins2.stderr)
            claude_settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(len(claude_settings["hooks"]["PreToolUse"]), 2)
            codex_md = (fake_home / ".codex" / "AGENTS.md").read_text(encoding="utf-8")
            self.assertEqual(codex_md.count("<!-- agent-desktop-router:start -->"), 1)
            self.assertIn("能调接口不点界面", codex_md)

            # 5. Uninstall cleanly and verify unrelated Claude hook remains intact & files moved to ~/.Trash
            unins = subprocess.run(
                [sys.executable, str(UNINSTALL_PY), "--home", str(fake_home), "--purge-config"],
                capture_output=True,
                text=True,
                env=env,
                timeout=20,
            )
            self.assertEqual(unins.returncode, 0, msg=unins.stderr)
            after_settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(len(after_settings["hooks"]["PreToolUse"]), 1)
            self.assertEqual(after_settings["hooks"]["PreToolUse"][0]["matcher"], "Write")
            self.assertFalse(wrapper.exists())
            self.assertTrue(any((fake_home / ".Trash").iterdir()))


if __name__ == "__main__":
    unittest.main()
