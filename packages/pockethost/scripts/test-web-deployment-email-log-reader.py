#!/usr/bin/env python3
"""Exercise the exact embedded Python log reader without a controller or any network access."""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

source = pathlib.Path(__file__).parents[1] / 'src/mothership-app/src/lib/handlers/webApps/deploymentFailureEmail.ts'
reader = re.search(r'DEPLOYMENT_EMAIL_LOG_READER = String.raw`(.*?)`', source.read_text(), re.S).group(1)
with tempfile.TemporaryDirectory(prefix='ph-email-reader-') as temporary:
    root = pathlib.Path(temporary).resolve()
    logs = root / 'logs'
    logs.mkdir()
    ident = 'job123456789012'

    def read(job=ident, base=root):
        result = subprocess.run([sys.executable, '-c', reader, str(base), job], capture_output=True, timeout=7, check=True)
        return json.loads(result.stdout)

    assert read() == {'unavailable': True}
    path = logs / (ident + '.log')
    path.write_text('Échec 😀\n')
    assert read() == {'text': 'Échec 😀\n', 'truncated': False}
    assert read('../outside') == {'unavailable': True}
    assert read(base=root / '..' / root.name) == {'unavailable': True}
    path.unlink()
    outside = root / 'outside.log'
    outside.write_text('forbidden\n')
    path.symlink_to(outside)
    assert read() == {'unavailable': True}
    path.unlink()
    os.mkfifo(path)
    assert read() == {'unavailable': True}
    path.unlink()
    path.mkdir()
    assert read() == {'unavailable': True}
    path.rmdir()
    path.write_text('partial-line-' + 'x' * 1048576 + '\nlast-line\n')
    assert read() == {'text': 'last-line\n', 'truncated': True}
    path.unlink()
    logs.rmdir()
    logs.symlink_to(root)
    assert read() == {'unavailable': True}
print(json.dumps({'ok': True, 'checks': ['utf8', 'missing', 'id_traversal', 'root_traversal', 'symlink', 'fifo', 'directory', 'bounded_tail_complete_lines', 'parent_symlink']}))
