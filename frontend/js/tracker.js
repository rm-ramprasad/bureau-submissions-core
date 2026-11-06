// Submission tracker: every request (a validation and the files generated from it) in the shared request table,
// with filters (?stage=&type=&bureau=&period=&from=&to=, also the dashboard's click-through) and paging.
const TRACKER_FILTERS = ["stage", "type", "bureau", "period", "from", "to"];

document.addEventListener("alpine:init", () => {
  Alpine.data("tracker", () => ({
    flt: Object.fromEntries(TRACKER_FILTERS.map((k) => [k, F.param(k) || ""])),
    page: 1,
    data: null,

    init() { this.load(); },

    filtered() { return Object.values(this.flt).some(Boolean); },
    clear() { this.flt = Object.fromEntries(TRACKER_FILTERS.map((k) => [k, ""])); this.page = 1; this.load(); },

    async load() {
      try {
        history.replaceState(null, "", F.url("tracker.html", this.flt));
        this.data = await Api.get("/batches", { ...this.flt, page: this.page });
      } catch (e) { App.error(e); }
    },
  }));
});
