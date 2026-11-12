// Upload details page (owner 2026-11-12): how one upload was processed: loading, data-quality issues, what the save
// into the customer tables did and left out. Every list downloads as CSV (made here, from what the page shows).
const UPLOAD_STAGES = { RECEIVED: "Waiting to start", LOADING: "Loading the sheets", CHECKING: "Checking the data",
                        SAVING: "Saving in the customer tables" };
const ITEM_TITLES = { NO_BORROWER: "Account not saved: no borrower", LISTED_TWICE: "Listed twice, first used",
                      MANY_CLEARED: "Many values cleared", SAVE_FAILED: "Save failed" };

document.addEventListener("alpine:init", () => {
  Alpine.data("uploadPage", () => ({
    u: null,
    severity: "",
    timer: null,

    init() { this.load(); },

    id() { return new URLSearchParams(location.search).get("id"); },
    running() { return this.u && this.u.stage && this.u.stage !== "DONE"; },
    stageText() { return UPLOAD_STAGES[this.u.stage] || this.u.stage; },
    itemTitle(code) { return ITEM_TITLES[code] || code; },
    issues() { return (this.u.all_issues || []).filter((i) => !this.severity || i.severity === this.severity); },
    rowCounts() {
      const c = this.u.row_counts || {};
      return Object.entries(c).map(([sheet, n]) => `${sheet} ${F.num(n)}`).join(", ");
    },

    async load() {
      try {
        const out = await Api.get(`/uploads/${this.id()}`, { details: 1, tenant_id: this.$store.app.tenant || "" });
        this.u = out.upload;
        clearTimeout(this.timer);
        if (this.running()) this.timer = setTimeout(() => this.load(), 3000);
      } catch (e) { App.error(e); }
    },

    csv(which) {
      const rows = which === "save"
        ? [["what", "sanction_reference_number", "account_number", "details"],
           ...this.u.save_items.map((i) => [this.itemTitle(i.code), i.sanction_reference_number, i.account_number,
                                            i.message])]
        : [["severity", "check", "sheet", "row", "column", "message"],
           ...this.issues().map((i) => [i.severity, i.code, i.sheet, i.source_row, i.column_name, i.message])];
      const text = rows.map((r) => r.map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")).join("\r\n");
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob(["﻿" + text], { type: "text/csv" }));
      link.download = `upload-${this.u.load_id}-${which === "save" ? "not-saved" : "issues"}.csv`;
      link.click();
      URL.revokeObjectURL(link.href);
    },
  }));
});
