// Admin settings (step 6): bureaus per submission type, reporting cycles for new requests, data source per type, configuration versions with
// export and import. Everyone can read; administrators change (with a reason, audited, versioned).
document.addEventListener("alpine:init", () => {
  Alpine.data("adminPage", () => ({
    data: null,
    versions: [],
    cycles: [],
    cyclesReason: "",
    importDoc: null,
    importReason: "",
    busy: false,

    init() { this.load(); },

    async load() {
      try {
        this.data = await Api.get("/admin/settings");
        this.cycles = this.data.cycles.filter((c) => c.enabled).map((c) => c.value);
        this.versions = (await Api.get("/config/versions")).versions;
      } catch (e) { App.error(e); }
    },

    canEdit() { return !!this.$store.app.user?.is_admin; },
    cell(type, bureau) { return this.data.switches.find((s) => s.submission_type === type && s.bureau_code === bureau); },
    cyclesChanged() {
      const was = this.data.cycles.filter((c) => c.enabled).map((c) => c.value);
      return was.length !== this.cycles.length || was.some((c) => !this.cycles.includes(c));
    },

    async switchBureau(s, event) {
      const active = event.target.checked;
      const reason = prompt(`Reason for switching ${s.display_name} ${active ? "on" : "off"} (kept in the audit trail):`);
      if (!reason) { event.target.checked = !active; return; }
      await this.send(`/admin/bureaus/${s.submission_type}/${s.bureau_code}/active`, { active, reason });
    },

    async switchSource(d, event) {
      const source = event.target.value;
      const reason = prompt(`Reason for switching ${d.label} to ${source === "master" ? "the customer tables" : "the source views"} (kept in the audit trail):`);
      if (!reason) { event.target.value = d.source; return; }
      await this.send(`/admin/data-source/${d.submission_type}`, { source, reason });
    },

    async saveCycles() {
      await this.send("/admin/cycles", { cycles: this.cycles, reason: this.cyclesReason });
      this.cyclesReason = "";
    },

    async send(path, body) {
      this.busy = true;
      try {
        const data = await Api.post(path, body);
        App.flash(data.message);
      } catch (err) { App.error(err); } finally { this.busy = false; await this.load(); }
    },

    async exportVersion(version) {
      try {
        const doc = await Api.get("/config/export", version ? { version } : {});
        const file = new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" });
        const link = Object.assign(document.createElement("a"), {
          href: URL.createObjectURL(file), download: `config-${doc.tenant_id}-v${doc.version_no || 0}.json` });
        link.click();
        URL.revokeObjectURL(link.href);
      } catch (e) { App.error(e); }
    },

    async pick(event) {
      const file = event.target.files[0];
      this.importDoc = null;
      if (!file) return;
      try { this.importDoc = JSON.parse(await file.text()); } catch (e) { App.error(new Error("Not a JSON file.")); }
    },

    async importFile() {
      if (!confirm("Replace the current settings with the file's? A new version is recorded; earlier ones are kept.")) return;
      await this.send("/config/import", { document: this.importDoc, reason: this.importReason });
      this.importDoc = null;
      this.importReason = "";
    },
  }));
});
