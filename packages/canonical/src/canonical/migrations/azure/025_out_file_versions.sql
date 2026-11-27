-- Pack versions (CONVERGENCE-PLAN step 8, owner 2026-11-27): every generated file records the release that built it
-- and the version and checksum of its type's rule and format files (bureau_core/types/<type>/CHANGES.md), so an
-- audit can tell which logic produced any file. NULL for files generated before this migration.
-- Run in SSMS from the release's migrations.zip (it records itself). No BEGIN/COMMIT (the runner wraps the file).
ALTER TABLE automn.out_file ADD
	release_version NVARCHAR(40) NULL,
	pack_version NVARCHAR(20) NULL,
	pack_checksum CHAR(12) NULL;
