from pathlib import Path
import hashlib
import subprocess

paths = subprocess.check_output(['git', 'ls-files', '-z']).decode().split('\0')
for name in sorted(set(paths) - {'', 'SHA256SUMS'}):
    path = Path(name)
    if not path.is_file():
        raise FileNotFoundError(name)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f'{digest}  {path.as_posix()}')
