"""Build the AWS Lambda package: build/rooi-nooi-shop-lambda.zip

    .venv\\Scripts\\python deploy\\build_lambda.py

Downloads Linux (x86_64, Python 3.13) versions of the libraries, so it works from Windows.
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
PKG = BUILD / "lambda"
ZIP = BUILD / "rooi-nooi-shop-lambda.zip"
SKIP = {"__pycache__", ".pytest_cache"}


def main():
    if PKG.exists():
        shutil.rmtree(PKG)
    PKG.mkdir(parents=True)
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
        "--platform", "manylinux2014_x86_64", "--implementation", "cp", "--python-version", "3.13",
        "--only-binary=:all:", "--target", str(PKG), "-r", str(ROOT / "deploy" / "requirements-lambda.txt"),
    ])
    for name in ("shop", "seed"):
        shutil.copytree(ROOT / name, PKG / name, ignore=shutil.ignore_patterns(*SKIP, "*.pyc"))
    shutil.copy2(ROOT / "lambda_handler.py", PKG / "lambda_handler.py")

    if ZIP.exists():
        ZIP.unlink()
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(PKG.rglob("*")):
            if path.is_file() and not (set(path.parts) & SKIP) and path.suffix != ".pyc":
                z.write(path, path.relative_to(PKG).as_posix())
    print(f"built {ZIP} ({ZIP.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
