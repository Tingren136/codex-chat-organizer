"""Audit the public tree; runtime data must stay outside it."""
from pathlib import Path
import re
import sys

root = Path(__file__).resolve().parents[1]
issues = []
for path in root.rglob('*'):
    if not path.is_file() or any(p in ('.git', '__pycache__') for p in path.relative_to(root).parts):
        continue
    rel = path.relative_to(root)
    if 'work' in rel.parts or path.suffix in ('.sqlite', '.db', '.log') or path.name.endswith('.local.json'):
        issues.append(str(rel) + ': runtime data')
    try:
        body = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        issues.append(str(rel) + ': unexpected binary'); continue
    if re.search(r'[A-Z]:[\\/]+Users[\\/]+(?!Public|Example)[^\\/\s]+', body):
        issues.append(str(rel) + ': private user path')
    if re.search(r'(?:sk-|sk_proj_)[A-Za-z0-9_-]{24,}', body):
        issues.append(str(rel) + ': possible secret')
    if re.search(r'01a1[0-9a-f]{4}-[0-9a-f-]{27,}', body):
        issues.append(str(rel) + ': real chat identifier')
print('\n'.join(issues) if issues else 'PASS: no runtime data, private user paths or known chat identifiers')
sys.exit(bool(issues))

