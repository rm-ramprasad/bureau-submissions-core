"""Client configuration versions (step 4): list, export, import. Routes on the API blueprint (/api/...).

Every saved change on the Bureaus and Rules pages calls `versioned` after it is written, which records a new version
(app.configstore). Import is admin-only and audited; reading versions and exporting are open to every signed-in
user, like the settings themselves.
"""

from __future__ import annotations

import json

from flask import Response, jsonify, request

from app import audit
from app.api.auth import admin_only, current_user
from app.api.common import _tenant_required, body, bp, fail, message, svc
from app.configstore import store
from clientconfig.document import schema


def versioned(tenant_id: str, action: str, target: str | None = None, reason: str | None = None) -> int | None:
    """Record the settings after a change. Never breaks the change itself (it is saved and audited already)."""
    try:
        return store.record(svc().engine, tenant_id, current_user(), action, target, reason)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("configuration version not recorded for %s (%s)", tenant_id, action)
        return None


@bp.get("/config/versions")
def config_versions():
    tenant_id = _tenant_required("configuration versions")
    return jsonify(tenant_id=tenant_id, versions=store.versions(svc().engine, tenant_id))


@bp.get("/config/schema")
def config_schema():
    return jsonify(schema())


@bp.get("/config/export")
def config_export():
    """The current settings (or `?version=N`) as a JSON file download."""
    s = svc()
    tenant_id = _tenant_required("configuration export")
    raw = request.args.get("version")
    if raw:
        if not raw.isdigit():
            fail("version: a number")
        doc = store.get(s.engine, tenant_id, int(raw))
        if doc is None:
            fail(f"No configuration version {raw} for {tenant_id}", 404)
        doc = doc | {"version_no": int(raw)}
    else:
        doc = store.current(s.engine, tenant_id)
    name = f"config-{tenant_id}-v{doc.get('version_no') or 0}.json"
    return Response(
        json.dumps(doc, indent=2, ensure_ascii=False),
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@bp.post("/config/import")
@admin_only
def config_import():
    """Replace the NBFC's settings with an exported document: {document, reason}. Checked fully before applying."""
    s, b = svc(), body()
    tenant_id = _tenant_required("configuration import")
    reason = (b.get("reason") or "").strip()
    if not reason:
        fail("Give a reason for the import (it is kept in the audit trail).")
    before = store.latest(s.engine, tenant_id)
    try:
        number = store.import_document(s.engine, tenant_id, b.get("document"), current_user(), reason[:500])
    except store.ConfigDocumentError as exc:
        fail(str(exc))
    audit.record(
        s.engine,
        current_user(),
        "CONFIG_IMPORTED",
        tenant_id=tenant_id,
        target=f"version {number}",
        reason=reason[:500],
        before=before,
        after=number,
    )
    changed = "nothing changed" if number == before else f"now version {number}"
    return message(f"Configuration imported for {tenant_id}: {changed}.", version_no=number)
