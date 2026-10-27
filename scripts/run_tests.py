"""Run all tests with every temporary file and log inside the project."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
temporary = ROOT / 'logs' / 'test-tmp'
temporary.mkdir(parents=True, exist_ok=True)
for key in ('TMP', 'TEMP', 'TMPDIR'):
    os.environ[key] = str(temporary)
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
tempfile.tempdir = str(temporary)
with (ROOT / 'logs' / 'all-tests.log').open('w', encoding='utf-8') as stream:
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.discover(str(ROOT / 'tests')))
summary = {'tests': result.testsRun, 'failures': len(result.failures),
           'errors': len(result.errors), 'skipped': len(result.skipped), 'success': result.wasSuccessful()}
(ROOT / 'logs' / 'all-tests.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
print(json.dumps(summary))
sys.exit(0 if result.wasSuccessful() else 1)
