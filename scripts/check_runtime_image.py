"""CI-only assertions, passed to Python on stdin in the built runtime image."""

import importlib.util
from pathlib import Path


root = Path("/app")
expected = {
    "LICENSE", "THIRD_PARTY_NOTICES.md", "backend", "scripts",
    "frontend-dist", "data",
}
assert {p.name for p in root.iterdir()} == expected
assert not list((root / "data").iterdir()), "Dataset must not be baked into image"
for path in root.rglob("*"):
    assert path.name not in {"__pycache__", "node_modules", ".git", ".env"}, path
    assert not path.name.endswith((".pyc", ".egg-info", ".map")), path
    assert not path.name.startswith("test_"), path
for name in ("build_smoke_release.py", "smoke_container.sh", "test_dsny_lookup.py",
             "check_runtime_image.py"):
    assert not (root / "scripts" / name).exists(), name
for module in ("pytest", "pip", "setuptools", "wheel", "app"):
    assert importlib.util.find_spec(module) is None, module

# Check the full refresh import chain and all fingerprinted processing sources.
from backend.app.main import app
from scripts.run_refresh import PROCESSING_CODE_PATHS
from scripts.build_tiles import build_tiles

assert callable(app)
for relative in PROCESSING_CODE_PATHS:
    assert (root / relative).is_file(), relative
assert callable(build_tiles)
print("Runtime packaging and refresh imports verified")
