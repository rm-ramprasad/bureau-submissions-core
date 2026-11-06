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

    async download(f, kind) {
      try { await Api.download(`/requests/${f.request_id}/${kind}`); } catch (e) { App.error(e); }
    },
  }));
});
