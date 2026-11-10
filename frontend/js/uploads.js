// Uploads page (ADR 0002 PR 7): upload a workbook once; checked, and saved in the customer tables for types on them.
document.addEventListener("alpine:init", () => {
  Alpine.data("uploadsPage", () => ({
    data: null,
    type: "",
    asOf: "",
    file: null,
    busy: false,

    init() { this.load(); },

    current() { return (this.data?.types || []).find((t) => t.submission_type === this.type); },

    async load() {
      try {
        this.data = await Api.get("/uploads", { tenant_id: this.$store.app.tenant || "" });
        if (!this.type && this.data.types.length) this.type = this.data.types[0].submission_type;
      } catch (e) { App.error(e); }
    },

    async send() {
      this.busy = true;
      try {
        const out = await Api.upload(`/uploads/${this.type}`, this.file,
                                     { as_of_date: this.asOf, tenant_id: this.$store.app.tenant || "" });
        App.flash(out.message, out.rejected ? "error" : "ok");
        await this.load();
      } catch (e) { App.error(e); } finally { this.busy = false; }
    },
  }));
});
