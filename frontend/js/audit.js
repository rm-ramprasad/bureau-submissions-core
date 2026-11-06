// Audit trail: latest 200 events, filtered by action, user and target (request id, rule, user...).
document.addEventListener("alpine:init", () => {
  Alpine.data("auditPage", () => ({
    data: null,
    flt: { action: "", actor: "", target: F.param("target") || "" },

    init() { this.load(); },

    async load() {
      try { this.data = await Api.get("/audit", this.flt); } catch (e) { App.error(e); }
    },
  }));
});
