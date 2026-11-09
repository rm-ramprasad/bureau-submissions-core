// Files: generated bureau files with bureau / period filters, paging and downloads (short-lived links).
document.addEventListener("alpine:init", () => {
  Alpine.data("filesPage", () => ({
    data: null,
    bureau: "",
    period: "all",
    page: 1,

    init() { this.load(); },

    async load() {
      try {
        this.data = await Api.get("/files", { bureau: this.bureau, period: this.period, page: this.page });
      } catch (e) { App.error(e); }
    },

    async markSubmitted(f) {
      const ack = prompt(`Mark ${f.output_file_name} as submitted to ${F.title(f.bureau_code)}?\n\n`
                         + "The bureau's acknowledgement reference (optional; kept in the audit trail):");
      if (ack === null) return;
      try {
        const data = await Api.post(`/requests/${f.request_id}/submitted`, { ack_reference: ack });
        App.flash(data.message);
        await this.load();
      } catch (e) { App.error(e); }
    },

    async download(f, kind) {
      try { await Api.download(`/requests/${f.request_id}/${kind}`); } catch (e) { App.error(e); }
    },
  }));
});
