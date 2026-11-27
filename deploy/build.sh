#!/usr/bin/env bash
# Build one release's artifacts on Linux (the release workflow runs this; it also works on a dev machine):
#   dist/function.zip   the Function App package: function_app.py, host.json and every package as Linux / Python 3.14
#                       wheels installed into .python_packages (no remote build), exactly the locked versions
#   dist/web.zip        the pages as they are: config.json and the CSP addresses are written per install at deploy
#   dist/migrations.zip the SQL migrations as SSMS scripts (the owner runs new ones; each records itself)
# Only the shell's packages go in (CONVERGENCE-PLAN.md section 2, control 1): the workspace packages it depends on, read
# from the lock, never --all-packages. Check the result with deploy/check_package.py <shell> dist.
# The same steps as deploy/deploy.ps1 (the Windows fallback). Usage: deploy/build.sh [version] [shell: azure]
set -euo pipefail
cd "$(dirname "$0")/.."
version="${1:-dev}"
shell="${2:-azure}"
case "$shell" in azure) ;; *) echo "unknown shell $shell (azure only for now)" >&2; exit 2 ;; esac
rm -rf dist && mkdir -p dist/function dist/wheels
cp deploy/function_app.py deploy/host.json dist/function/
site=dist/function/.python_packages/lib/site-packages
uv export --locked --no-dev --package "shell-$shell" --no-emit-workspace --no-hashes --format requirements-txt \
  -o dist/requirements-linux.txt
uv pip install --quiet --target "$site" --python-platform x86_64-manylinux_2_28 --python-version 3.14 \
  --only-binary :all: -r dist/requirements-linux.txt
members=$(uv export --locked --no-dev --package "shell-$shell" --no-hashes --format requirements-txt \
  | sed -n 's#^-e \./packages/##p')
[ -n "$members" ] || { echo "no workspace packages found for shell-$shell" >&2; exit 1; }
for member in $members; do uv build --quiet --package "$(echo "$member" | tr _ -)" --wheel --out-dir dist/wheels; done
uv pip install --quiet --target "$site" --no-deps --python-platform x86_64-manylinux_2_28 --python-version 3.14 \
  dist/wheels/*.whl
for secret in .env.sql .env local.settings.json; do
  if find dist/function -name "$secret" | grep -q .; then echo "$secret must never be packaged" >&2; exit 1; fi
done
echo "$version" > "$site/shell_$shell/VERSION"  # reported by the migrate job: the smoke test checks it
zipdir() {  # zipdir <folder> <zip> [glob] [excluded name]: the folder's files, paths relative to it
  python3 - "$@" <<'PY'
import sys, zipfile
from pathlib import Path
folder, target = Path(sys.argv[1]), Path(sys.argv[2])
pattern = sys.argv[3] if len(sys.argv) > 3 else "**/*"
skip = sys.argv[4] if len(sys.argv) > 4 else None
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(folder.glob(pattern)):
        if f.is_file() and f.name != skip:
            z.write(f, f.relative_to(folder).as_posix())
PY
}
zipdir dist/function dist/function.zip
zipdir frontend dist/web.zip "**/*" config.json
# the migrations as the owner runs them in SSMS: each wrapped to run all or nothing and record itself (no mark_applied)
uv run --no-sync python -m canonical.migrate ssms dist/migrations
zipdir dist/migrations dist/migrations.zip "*.sql"
ls -l dist/*.zip
