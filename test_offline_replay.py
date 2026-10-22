import importlib.util
import json
import unittest
import sys
from unittest.mock import Mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class ReplayTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('replay_builder', ROOT / 'tools/build_offline_replay.py')
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        self.audit = json.loads((ROOT / 'evidence/local-demo-2026-10-22/e2e-audit.json').read_text())

    def test_complete_recorded_chain_has_human_save_and_monitor(self):
        steps = self.builder.make_steps(self.audit)
        self.assertEqual(len(steps), 12)
        self.assertEqual(steps[6]['decision'], 'plan_saved')
        self.assertEqual(steps[6]['plan']['version'], 1)
        self.assertEqual(len(steps[4]['plan']['days']), 7)
        self.assertEqual(steps[10]['review']['alert'], True)
        self.assertEqual(len(steps[-1]['audit']), 11)

    def test_replies_are_verbatim_and_raw_model_execution_is_excluded(self):
        steps = self.builder.make_steps(self.audit)
        for index, row in enumerate(self.audit):
            self.assertEqual(steps[index]['reply'], row['output'])
        self.assertNotIn('system_prompt', json.dumps(steps))
        self.assertNotIn('tool_executions', json.dumps(steps))

    def test_render_is_self_contained_and_script_safe(self):
        html = self.builder.render([{'reply': '</script><script>alert(1)</script>'}])
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertNotIn('<script src=', html)
        self.assertNotIn('<link ', html)
        self.assertNotIn('fetch(', html)
        self.assertIn('Recorded replay', html)

    def test_live_app_serves_full_replay_without_model(self):
        sys.path.insert(0, str(ROOT / 'prototype'))
        import server
        handler = object.__new__(server.Handler)
        handler._send = Mock()
        handler._get('/full-replay', {})
        args = handler._send.call_args.args
        self.assertEqual(args[0], 200)
        self.assertIn(b'Recorded replay', args[1])


if __name__ == '__main__':
    unittest.main()
