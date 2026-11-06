"""Parity check on rarfincare, run by the owner from their machine before go-live (see app.parity):

    set SUBMISSIONS_SQL_ENV_FILE=<path to .env.sql>      (the SQL login file, kept outside the repository)
    set SUBMISSIONS_TENANT=rarfincare
    uv run python -m shell_azure.parity <old request id, e.g. 20261102083455-327be1> [--old-files <folder>] [--keep]

Old files are read from Blob storage with your `az login` (needs read access to the files container), or from a
folder of files downloaded from the Files page (--old-files). New files go to ./parity-output/<request>/.
Exit code 0 = identical, 1 = differences, 2 = could not run.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.parity import local_reader, run_parity


def _blob_reader(settings: Any) -> Callable[[str], bytes]:
    from shell_azure.storage import BlobFileStore, SasSigner, blob_service

    service = blob_service(settings)
    store = BlobFileStore(
        service.get_container_client(settings.files_container), settings.files_prefix, SasSigner(service)
    )

    def read(uri: str) -> bytes:
        return b"".join(store.read_chunks(uri))

    return read


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m shell_azure.parity", description=__doc__.splitlines()[0])
    ap.add_argument("request", help="the old app's request id (the batch id shown on the request page)")
    ap.add_argument("--old-files", type=Path, help="folder with the old files (else read from Blob storage)")
    ap.add_argument("--out", type=Path, default=Path("parity-output"))
    ap.add_argument("--keep", action="store_true", help="keep the temporary snapshot (for investigation)")
    args = ap.parse_args(argv)
    from shell_azure.settings import Settings
    from shell_azure.sql import make_azure_engine

    try:
        settings = Settings.from_env()
        engine = make_azure_engine(settings.database_url)
        reader = local_reader(args.old_files) if args.old_files else _blob_reader(settings)
        report = run_parity(engine, args.request, args.out / args.request, reader, keep=args.keep)
    except Exception as exc:  # a plain message for the owner, not a stack trace
        print(f"Could not run the parity check: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(report.text())
    return 0 if report.same else 1


if __name__ == "__main__":
    raise SystemExit(main())
