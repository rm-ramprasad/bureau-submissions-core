"""Package-contents check for a release (CONVERGENCE-PLAN.md section 2, control 2). Standard library only.

    python3 deploy/check_package.py <shell> <dist folder>

<shell> is the cloud the package is for (azure today; aws when shell_aws exists). The check fails the release when
function.zip holds another cloud's parts, a repository package the shell does not depend on (an unlisted module),
a dropped database dialect or a secret, or misses a part the shell needs; and when migrations.zip holds anything but
the shell's top-level SQL files.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

SITE = ".python_packages/lib/site-packages/"
REPO_PACKAGES = Path(__file__).resolve().parent.parent / "packages"

SHELLS: dict[str, dict[str, set[str]]] = {
    "azure": {
        # top-level folders in site-packages that must be there / must not be there
        "required": {"shell_azure", "app", "bureau_core", "canonical", "clientconfig", "sources"},
        "forbidden": {"shell_aws", "boto3", "botocore", "s3transfer", "aioboto3", "awswrangler", "sqlglot", "psycopg"},
        "files": {"function_app.py", "host.json", SITE + "shell_azure/VERSION", SITE + "app/VERSION"},
    },
}
SECRETS = {".env", ".env.sql", "local.settings.json"}
AWS_TEMPLATES = {"template.yaml", "template.yml", "samconfig.toml", "cdk.json"}
DROPPED_DIALECTS = ("canonical/migrations/postgres/", "canonical/migrations/mysql/", "canonical/build_views.py")


def repo_package_names(root: Path = REPO_PACKAGES) -> set[str]:
    """Import names of the repository's own packages (packages/<dist>/src/<name> or packages/<name>/<name>)."""
    names = set()
    for dist in root.iterdir() if root.is_dir() else []:
        src = dist / "src"
        for candidate in src.iterdir() if src.is_dir() else [dist / dist.name]:
            if (candidate / "__init__.py").is_file():
                names.add(candidate.name)
    return names


def check_function_zip(names: list[str], shell: str, repo_packages: set[str]) -> list[str]:
    rules = SHELLS[shell]
    top = {n[len(SITE) :].split("/", 1)[0] for n in names if n.startswith(SITE) and "/" in n[len(SITE) :]}
    problems = [f"missing {p}" for p in sorted(rules["required"] - top)]
    problems += [f"missing {f}" for f in sorted(rules["files"] - set(names))]
    problems += [f"another cloud's or a dropped library: {p}" for p in sorted(rules["forbidden"] & top)]
    unlisted = (repo_packages & top) - rules["required"]
    problems += [f"repository package the {shell} shell does not depend on: {p}" for p in sorted(unlisted)]
    for n in names:
        base = n.rsplit("/", 1)[-1]
        if base in SECRETS:
            problems.append(f"secret: {n}")
        elif base in AWS_TEMPLATES and shell != "aws":
            problems.append(f"AWS template: {n}")
        elif any(d in n for d in DROPPED_DIALECTS):
            problems.append(f"dropped database dialect: {n}")
    return problems


def check_migrations_zip(names: list[str]) -> list[str]:
    return [f"not a top-level .sql file: {n}" for n in names if "/" in n or not n.endswith(".sql")]


def check(shell: str, dist: Path) -> list[str]:
    if shell not in SHELLS:
        return [f"unknown shell {shell!r}: one of {', '.join(SHELLS)}"]
    problems = []
    for zip_name, checker in (
        ("function.zip", lambda n: check_function_zip(n, shell, repo_package_names())),
        ("migrations.zip", check_migrations_zip),
    ):
        path = dist / zip_name
        if not path.is_file():
            problems.append(f"{zip_name} not built")
            continue
        with zipfile.ZipFile(path) as z:
            problems += [f"{zip_name}: {p}" for p in checker(z.namelist())]
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    problems = check(argv[1], Path(argv[2]))
    for p in problems:
        print(f"FAIL {p}")
    print(f"{argv[1]} package: {'OK' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
