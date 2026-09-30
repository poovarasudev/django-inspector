"""Fail if a built wheel or sdist is missing files the package needs at runtime.

Run from the repository root after building:

    python -m build            # or: uv build
    python scripts/check_dist.py dist/
"""

import pathlib
import sys
import tarfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "django_inspector"

# Every non-Python file the package needs at runtime, plus the migrations.
REQUIRED = sorted(
    str(p.relative_to(ROOT))
    for p in list(PACKAGE.glob("templates/**/*.html")) + list(PACKAGE.glob("migrations/*.py"))
)


def check_wheel(path):
    names = set(zipfile.ZipFile(path).namelist())
    missing = [f for f in REQUIRED if f not in names]
    if not any(n.endswith(".dist-info/METADATA") for n in names):
        missing.append("*.dist-info/METADATA")
    if not any(".dist-info/" in n and n.rsplit("/", 1)[-1].startswith("LICENSE") for n in names):
        missing.append("*.dist-info/**/LICENSE")
    return missing


def check_sdist(path):
    with tarfile.open(path) as tar:
        names = {n.split("/", 1)[1] for n in tar.getnames() if "/" in n}
    return [f for f in REQUIRED + ["README.md", "LICENSE", "CHANGELOG.md"] if f not in names]


def main(dist_dir):
    dist = pathlib.Path(dist_dir)
    wheels, sdists = sorted(dist.glob("*.whl")), sorted(dist.glob("*.tar.gz"))
    if not wheels or not sdists:
        print("expected a wheel and an sdist in %s" % dist)
        return 1
    failed = False
    for path, check in [(wheels[-1], check_wheel), (sdists[-1], check_sdist)]:
        missing = check(path)
        status = "OK" if not missing else "MISSING %d" % len(missing)
        print("%s: %s (%d required files)" % (path.name, status, len(REQUIRED)))
        for name in missing:
            print("  - " + name)
        failed = failed or bool(missing)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "dist"))
