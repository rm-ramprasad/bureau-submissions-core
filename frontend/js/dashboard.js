// Dashboard: one submission type at a time (?type=, record counts of types do not add up), request-stage KPIs, data
// KPIs (with records per bureau), NBFC status, recent requests (period filter). What needs attention is in the
// top-bar bell (core.js); the type switch shows how many items need attention in each type.
document.addEventListener("alpine:init", () => {
  Alpine.data("dashboard", () => ({
    board: null,
    type: F.param("type") || "",
    period: F.param("period") || "month",
    periods: [{ value: "month", label: "This month" }, { value: "30d", label: "Last 30 days" },
              { value: "90d", label: "Last 90 days" }, { value: "all", label: "All time" }],

    init() { this.load(); },

    async load() {
      try {
        this.board = await Api.get("/dashboard", { period: this.period, type: this.type });
        this.period = this.board.period;
        this.type = this.board.type;
        history.replaceState(null, "", F.url("index.html", { type: this.type, period: this.period }));
      } catch (e) { App.error(e); }
    },

    periodLabel() { return (this.periods.find((p) => p.value === this.period) || this.periods[0]).label; },
    typeLabel() { return F.title(this.type); },
    tracker(stage) { return F.url("tracker.html", { stage, period: this.period, type: this.type }); },
    queueSlow() { return !!this.$store.app.health?.slow; },
    commercial() { return this.type === "COMMERCIAL"; }
  }));
});
