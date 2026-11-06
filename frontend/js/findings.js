// Findings of one snapshot (?snapshot=<id>[&rule_id=|&category=]): KPIs, issues by category, filters, paging, CSV,
// and Exclude / Override into a new draft request for the same NBFC, date and type.
document.addEventListener("alpine:init", () => {
  Alpine.data("findingsPage", () => ({
    snapshot: F.param("snapshot"),
    data: null,
    flt: { category: F.param("category") || "", rule_id: F.param("rule_id") || "", outcome: "", account_key: "",
           party_key: "" },
    page: 1,
    busy: false,

    init() { this.load(); },

    async load() {
      try {
        this.data = await Api.get(`/snapshots/${encodeURIComponent(this.snapshot)}/findings`, { ...this.flt, page: this.page });
      } catch (e) { App.error(e); }
    },

    // One entry per rule (a rule can appear with two outcomes after a policy change), in the chosen category
    issues() {
      const cats = this.data.report.categories.filter((c) => !this.flt.category || c.key === this.flt.category);
      const seen = new Map();
      cats.flatMap((c) => c.rules).forEach((r) => seen.has(r.rule_id) || seen.set(r.rule_id, r));
      return [...seen.values()];
    },
    expected(f) { return this.data.report.expected_rules.includes(f.rule_id) && f.outcome !== "REJECT_RECORD"; },
    pick(f) {
      Object.assign(this.flt, { category: "", rule_id: "" }, f);
      this.page = 1;
      this.load();
    },
    canAdjust() { return this.data.snapshot.status === "VALIDATED"; },

    async run(fn) {
      this.busy = true;
      try { return await fn(); } catch (e) { App.error(e); return null; } finally { this.busy = false; }
    },

    async exclude(f) {
      const data = await this.run(() => Api.post(`/snapshots/${this.snapshot}/exclude`, { account_key: f.account_key }));
      if (data) App.flash(data.message);
    },

    async override(f) {
      const field = this.data.overridable.includes(f.field) ? f.field : null;
      const data = await this.run(() => Api.post(`/snapshots/${this.snapshot}/override`, { account_key: f.account_key, field }));
      if (data) location.href = F.url("batch.html", { id: data.batch_id, type: data.type, account: data.account,
                                                      field: data.field });
    },

    csv() { return this.run(() => Api.download(`/snapshots/${this.snapshot}/findings.csv`, this.flt)); },
  }));
});
