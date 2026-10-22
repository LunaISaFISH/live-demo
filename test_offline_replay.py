import importlib.util,json,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('builder',ROOT/'tools/build_offline_replay.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
class ReplayTests(unittest.TestCase):
 def setUp(self):self.r=json.loads((ROOT/'evidence/local-demo-2026-10-22/ui-recording.json').read_text())
 def test_same_frontend(self):
  html=b.render(self.r)
  self.assertIn((ROOT/'prototype/web/app.js').read_text(),html)
  self.assertIn((ROOT/'prototype/web/style.css').read_text(),html)
  self.assertNotIn('<script src=',html);self.assertNotIn('<link ',html)
 def test_complete_chain(self):
  b.validate(self.r)
  self.assertEqual(len(self.r['steps']),9)
  self.assertEqual(self.r['steps'][6]['response']['version']['version'],1)
  self.assertTrue(self.r['steps'][7]['next_signal']['signals'])
 def test_reject_incomplete(self):
  self.r['complete']=False
  with self.assertRaises(ValueError):b.render(self.r)
 def test_script_safe(self):
  self.r['steps'][0]['message']='</script><script>alert(1)</script>'
  self.assertNotIn('<script>alert(1)</script>',b.render(self.r))
if __name__=='__main__':unittest.main()
