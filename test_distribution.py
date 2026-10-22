"""Regression checks for a freshly extracted distribution, without model calls."""
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import download_pmdata as data

ROOT = pathlib.Path(__file__).resolve().parent


class DistributionTests(unittest.TestCase):
    def test_openrouter_uses_its_key_without_openai_login(self):
        code = f"""
import sys
sys.path.insert(0, {str(ROOT / 'prototype')!r})
from unittest.mock import patch
import agent
assert agent.PROVIDER == 'openrouter'
assert agent.MODEL == 'openai/gpt-5.6-terra'
with patch.object(agent, 'CodexClient') as client:
    conversation = agent.CodexConversation(agent.MODEL, 'test')
    try:
        config = client.call_args.args[0]
        assert any('env_key="OPENROUTER_API_KEY"' in x for x in config.config_overrides)
        assert 'distribution-test-key' not in repr(config)
        conversation.start()
        assert not client.return_value.account_login_start.called
        assert client.return_value.thread_start.call_args.args[0]['modelProvider'] == 'openrouter'
    finally:
        conversation.close()
"""
        env = {k: v for k, v in os.environ.items() if k not in
               ('FITCOACH_PROVIDER', 'FITCOACH_MODEL', 'OPENAI_API_KEY')}
        env['OPENROUTER_API_KEY'] = 'distribution-test-key'
        result = subprocess.run([sys.executable, '-c', code], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_server_imports_outside_project_directory(self):
        with tempfile.TemporaryDirectory() as cwd:
            code = f"import sys; sys.path.insert(0, {str(ROOT / 'prototype')!r}); import server"
            result = subprocess.run([sys.executable, "-c", code], cwd=cwd,
                                    capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sandbox_paths_are_valid_toml(self):
        code = (f"import sys, tomllib; sys.path.insert(0, {str(ROOT / 'prototype')!r}); "
                "import agent; tomllib.loads(agent.sandbox_overrides("
                "r'C:\\Users\\Student\\FitCoach', r'C:\\Users\\Student\\Work')[0])")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_participant_directories_are_not_ready(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(data, "DATA", pathlib.Path(folder)):
            (data.DATA / "participant-overview.xlsx").touch()
            for i in range(1, 17):
                (data.DATA / f"p{i:02d}").mkdir()
            self.assertFalse(data.ready())

    def test_download_resumes_partial_archive(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("record.txt", "a recorded observation")
        content = payload.getvalue()
        ranges = []

        class Download(BaseHTTPRequestHandler):
            def do_GET(self):
                value = self.headers.get("Range")
                ranges.append(value)
                start = int(value.removeprefix("bytes=").removesuffix("-")) if value else 0
                self.send_response(206 if value else 200)
                if value:
                    self.send_header("Content-Range", f"bytes {start}-{len(content)-1}/{len(content)}")
                self.send_header("Content-Length", str(len(content) - start))
                self.end_headers()
                self.wfile.write(content[start:])

            def log_message(self, *args):
                pass

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Download)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as folder, patch.object(
                    data, "ARCHIVE", pathlib.Path(folder) / "pmdata.zip"), patch.object(
                    data, "URL", f"http://127.0.0.1:{httpd.server_port}/pmdata.zip"):
                data.ARCHIVE.with_suffix(".zip.part").write_bytes(content[:25])
                data.download()
                self.assertEqual(data.ARCHIVE.read_bytes(), content)
                self.assertEqual(ranges, ["bytes=25-"])
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
