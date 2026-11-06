// Rules & policies: the rule catalogue, per-NBFC on/off and outcome (with a reason), rule parameters, and custom
// rules built from a form (preview on the latest validation, save, edit, delete).
const LABELS = { WARN: "Warn only", REJECT_FIELD: "Drop field", REJECT_SEGMENT: "Drop segment",
                 REJECT_RECORD: "Reject record", REJECT_FILE: "Block file" };
const EMPTY_RULE = { editing: "", name: "", field: "", check: "range", values: "", min: "", max: "", pattern: "",
                     operator: "", other_field: "", when_field: "", when_values: "", outcome: "WARN", message: "", reason: "" };

document.addEventListener("alpine:init", () => {
  Alpine.data("rulesPage", () => ({
    data: null,
    type: F.param("type") || "CONSUMER",
    q: F.param("q") || "",
    onlyChanged: false,
    busy: false,
    form: { ...EMPTY_RULE },
    previewResult: null,

    init() { this.load(); },

    // Only administrators change rules (the API refuses others); everyone else sees the settings read-only.
    canEdit() { return !!(this.data?.tenant_id && this.$store.app.user?.is_admin); },

    async load() {
      try { this.data = await Api.get("/rules", { type: this.type }); } catch (e) { App.error(e); }
    },

    tenantName() {
      const id = this.data?.tenant_id;
      return id ? (this.$store.app.meta?.tenants.find((t) => t.tenant_id === id)?.display_name || id) : "";
    },
    enabledTypes() { return (this.$store.app.meta?.submission_types || []).filter((t) => t.enabled); },
    customRules() { return this.data.rules.filter((r) => r.source === "CUSTOM"); },

    shown() {
      const q = this.q.trim().toLowerCase();
      return this.data.rules.filter((r) => (!this.onlyChanged || r.changed) && (!q
        || r.rule_id.toLowerCase().includes(q) || r.message.toLowerCase().includes(q) || (r.field || "").toLowerCase().includes(q)));
    },

    async post(path, body = {}) {
      this.busy = true;
      try {
        const data = await Api.post(path, { type: this.type, ...body });
        App.flash(data.message);
        await this.load();
        return data;
      } catch (e) {
        App.error(e);
        return null;
      } finally {
        this.busy = false;
      }
    },

    savePolicy(r, enabled, outcome, reason) {
      return this.post(`/rules/${encodeURIComponent(r.rule_id)}`, { enabled, outcome, reason });
    },

    saveParameter(p, value, reason) { return this.post(`/rules/parameters/${p.name}`, { value, reason }); },

    edit(ruleId) {
      const d = this.data.custom[ruleId];
      const qualified = (f) => (f ? `${d.entity}.${f}` : "");
      this.form = { ...EMPTY_RULE, ...d, editing: ruleId, field: qualified(d.field), other_field: qualified(d.other_field),
                    when_field: qualified(d.when_field), values: d.values.join("\n"), when_values: d.when_values.join("\n"),
                    min: d.min || "", max: d.max || "", pattern: d.pattern || "", operator: d.operator || "", reason: "" };
      this.previewResult = null;
      this.$nextTick(() => document.getElementById("rule-form").scrollIntoView({ behavior: "smooth" }));
    },

    cancelEdit() { this.form = { ...EMPTY_RULE }; this.previewResult = null; },

    async preview() {
      this.busy = true;
      try { this.previewResult = await Api.post("/rules/custom/preview", { type: this.type, ...this.form }); }
      catch (e) { App.error(e); }
      finally { this.busy = false; }
    },

    async saveCustom() {
      if (await this.post("/rules/custom", this.form)) this.cancelEdit();
    },

    async deleteRule(ruleId) {
      const reason = prompt(`Delete ${ruleId}? Earlier findings keep its id.\n\nReason (kept in the audit trail):`);
      if (reason === null) return;
      if (await this.post(`/rules/custom/${encodeURIComponent(ruleId)}/delete`, { reason })) {
        if (this.form.editing === ruleId) this.cancelEdit();
      }
    },
  }));
});
