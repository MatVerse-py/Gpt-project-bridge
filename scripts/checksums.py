from pathlib import Path
import hashlib

root=Path('.')
ignored={'.git','.venv','data','backups','dist','__pycache__'}
for path in sorted(p for p in root.rglob('*') if p.is_file() and not any(part in ignored for part in p.parts) and p.name not in {'SHA256SUMS'}):
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    print(f'{digest}  {path.as_posix()}')
