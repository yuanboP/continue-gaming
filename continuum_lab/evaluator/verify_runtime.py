#!/usr/bin/env python3
"""Check that the exact bundled Harbor integration is selected, without a run."""
from pathlib import Path
import hashlib,importlib.metadata,json,sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'runtime'))
import harbor
from harbor.agents.installed import codex
from harbor.environments.docker import docker
from harbor_suite.docker_copy import CopyDocker
expected=json.loads((ROOT/'runtime/harbor-source.json').read_text())
package=Path(harbor.__file__).resolve().parent
assert package==(ROOT/'runtime/harbor').resolve(),'Bundled Harbor package was not selected'
assert Path(codex.__file__).resolve()==package/'agents/installed/codex.py'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
actual={p.relative_to(package).as_posix():sha(p) for p in sorted(package.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.name!='.DS_Store'}
assert actual==expected['package_files_sha256'],'Harbor code or template closure changed'
assert importlib.metadata.version('harbor')==expected['distribution_version']
assert sha(Path(codex.__file__))==expected['codex_integration_sha256']
assert 'Local patch (Continuum)' in Path(codex.__file__).read_text()
assert issubclass(CopyDocker,docker.DockerEnvironment)
print(json.dumps({'passed':True,'harbor_version':harbor.__version__,'vendored_runtime_selected':True,'package_files':len(actual),'codex_integration_sha256':expected['codex_integration_sha256'],'models_started':0,'episodes_started':0}))
