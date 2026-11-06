// One request from start to finish (?id=<batch>[&type=&account=&field=]; no id: create a new one). A stepper shows
// where it stands: 1 Create · 2 Upload & check · 3 Exceptions · 4 Validate · 5 Generate · 6 Files. The open section is
// the current step; finished steps fold up and can be reopened. Files are generated once per validation.
// Refreshes every 5 s while something is pending or running.
const KINDS = { EXCLUDE_ACCOUNT: "Exclude loan", EXCLUDE_CUSTOMER: "Exclude customer", OVERRIDE: "Override" };
const ADJ_STATUS = {
  WILL_APPLY: ["Will apply", "b-DRAFT"], WAITING: ["Waiting for validation", "b-PENDING"],
  APPLIED: ["Applied", "b-SUCCEEDED"], NOT_FOUND: ["Not found", "b-WARN"], REMOVED: ["Removed", "b-CANCELLED"],
};
const STEPS = [["create", "Create"], ["upload", "Upload & check"], ["exceptions", "Exceptions"],
               ["validate", "Validate"], ["generate", "Generate"], ["files", "Files"]];
const ACTIVE = ["PENDING", "RUNNING"];

// Reporting dates (business rule): the 9th W1, 16th W2, 23rd W3, the last day of the month ME; other days have none.
function cycleOf(y, m, d) {                                  // m: 0-11
  if (d === new Date(y, m + 1, 0).getDate()) return "ME";
  return { 9: "W1", 16: "W2", 23: "W3" }[d] || null;
}
const iso = (y, m, d) => `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;

// Each step's state from the request: done | current | running | failed | todo. Pure, so it can be tested.
function requestSteps(b, inputs) {
  const state = Object.fromEntries(STEPS.map(([k]) => [k, "todo"]));
  if (!b) return { ...state, create: "current" };
  state.create = "done";
  const rows = b.rows, gens = b.generations || [];
  const draft = rows.some((r) => r.status === "DRAFT");
  if (rows.every((r) => r.status === "CANCELLED")) return { ...state, upload: "failed" };     // discarded
  const loaded = (inputs || []).every((i) => !i.needs_workbook || i.latest?.status === "LOADED");
  if (draft) {
    state.upload = loaded ? "done" : "current";
    state.exceptions = loaded ? "current" : "todo";
    return state;
  }
  state.upload = state.exceptions = "done";
  const validated = rows.every((r) => r.status === "SUCCEEDED");
  state.validate = rows.some((r) => ACTIVE.includes(r.status)) ? "running" : validated ? "done" : "failed";
  if (!validated) return state;
  const live = gens.filter((g) => [...ACTIVE, "SUCCEEDED"].includes(g.status));
  // current while a validated type still waits for its files (a consumer + commercial request generates per type)
  state.generate = live.length && !(b.generate_from || []).length ? "done" : "current";
  if (gens.some((g) => ACTIVE.includes(g.status))) state.files = "running";
  else if (live.length) state.files = "done";
  else if (gens.length) state.files = "failed";
  return state;
}

if (typeof document !== "undefined") document.addEventListener("alpine:init", () => {
  Alpine.data("batchPage", () => ({
    id: F.param("id"),
    type: F.param("type"),
    b: null,
    page: null,              // the selected type's exceptions: { row, rows, is_draft, adjustments, ... }
    inputs: null,            // every type's input workbook: [{ submission_type, needs_workbook, latest }]
    open: {},                // step -> section open (user's choice; the current step opens by default)
    busy: false,
    file: null,
    history: false,
    form: { as_of_date: "", reporting_cycle: "", submission_types: ["CONSUMER"] },   // NBFC: the deployment's;
                                                                                      // cycle "" = from the date
    ex: { kind: "account", keys: "", reason: "" },
    ov: { account_number: F.param("account") || "", field: F.param("field") || "days_past_due", value: "", reason: "" },
    cal: { open: false, month: new Date() },   // the as-of date calendar (shows the first of `month`)
    chosen: {},              // request_id -> bureaus ticked for generation
    confirm: {},             // request_id -> adjustments confirmed
    timer: null,

    init() {
      const defaults = (meta) => { if (meta && !this.form.as_of_date) this.form.as_of_date = meta.default_as_of; };
      defaults(this.$store.app.meta);
      this.$watch("$store.app.meta", defaults);
      if (this.id) this.load().then(() => { if (this.ov.account_number) this.$nextTick(() => this.$refs.value?.focus()); });
    },

    // ------------------------------------------------------------ data
    async load() {
      clearTimeout(this.timer);
      const q = encodeURIComponent(this.id);
      try {
        const b = await Api.get(`/batches/${q}`);
        if (b.parent_batch_id) { location.replace(F.url("batch.html", { id: b.parent_batch_id })); return; }
        const validation = b.rows.filter((r) => r.run_mode === "VALIDATE");
        if (validation.length) {
          [this.page, this.inputs] = await Promise.all([
            Api.get(`/batches/${q}/exceptions`, { type: this.type, history: this.history ? 1 : "" }),
            Api.get(`/batches/${q}/inputs`).then((d) => d.inputs),
          ]);
          this.type = this.page.row.submission_type;
        }
        this.b = b;
        for (const g of b.generate_from) if (!(g.request_id in this.chosen)) this.chosen[g.request_id] = [...g.bureaus];
        if (b.active) this.timer = setTimeout(() => this.load(), 5000);
      } catch (e) { App.error(e); }
    },

    async run(fn) {
      this.busy = true;
      try { return await fn(); } catch (e) { App.error(e); return null; } finally { this.busy = false; }
    },

    // ------------------------------------------------------------ stepper
    steps() { return requestSteps(this.b, this.inputs); },
    current() {
      const s = this.steps();
      return (STEPS.find(([k]) => ["current", "running", "failed"].includes(s[k])) || STEPS[STEPS.length - 1])[0];
    },
    isOpen(step) { return this.open[step] ?? step === this.current(); },
    toggle(step) { this.open[step] = !this.isOpen(step); },
    show(step) { this.open[step] = true; this.$nextTick(() => document.getElementById("step-" + step)?.scrollIntoView({ behavior: "smooth" })); },
    validationRows() { return (this.b?.rows || []).filter((r) => r.run_mode === "VALIDATE"); },
    input() { return (this.inputs || []).find((i) => i.submission_type === this.type) || null; },
    draft() { return !!this.page?.is_draft; },
    uploadsMissing() {
      return (this.inputs || []).filter((i) => i.needs_workbook && i.latest?.status !== "LOADED").map((i) => F.title(i.submission_type));
    },
    liveGenerations() { return (this.b?.generations || []).filter((g) => [...ACTIVE, "SUCCEEDED"].includes(g.status)); },
    generatedSummary() {
      const live = this.liveGenerations();
      if (!live.length) return "";
      return `${live.map((g) => F.title(g.bureau_code)).join(", ")} · requested ${F.dt(live[0].requested_at)} by ${live[0].requested_by || "—"}`;
    },
    selectType(t) { this.type = t; this.load(); },

    // ------------------------------------------------------------ step 1: create
    // As-of calendar: with the cycle from the date, only reporting dates can be picked; with DL / DC / AH / RR any
    // past day. Future days never.
    dateCycle(value) { const [y, m, d] = value.split("-").map(Number); return cycleOf(y, m - 1, d); },
    openCalendar() {
      const base = this.form.as_of_date ? new Date(this.form.as_of_date + "T00:00") : new Date();
      this.cal.month = new Date(base.getFullYear(), base.getMonth(), 1);
      this.cal.open = !this.cal.open;
    },
    shiftMonth(n) { this.cal.month = new Date(this.cal.month.getFullYear(), this.cal.month.getMonth() + n, 1); },
    canNext() {
      const now = new Date();
      return this.cal.month < new Date(now.getFullYear(), now.getMonth(), 1);
    },
    calendarCells() {
      const y = this.cal.month.getFullYear(), m = this.cal.month.getMonth();
      const today = iso(new Date().getFullYear(), new Date().getMonth(), new Date().getDate());
      const lead = (new Date(y, m, 1).getDay() + 6) % 7;         // Monday first
      const cells = Array.from({ length: lead }, (_, i) => ({ key: "b" + i, day: 0, ok: false }));
      for (let d = 1; d <= new Date(y, m + 1, 0).getDate(); d++) {
        const value = iso(y, m, d), cycle = cycleOf(y, m, d);
        cells.push({ key: value, day: d, iso: value, cycle,
                     ok: value <= today && (!!this.form.reporting_cycle || !!cycle) });
      }
      return cells;
    },
    pickDate(value) { this.form.as_of_date = value; this.cal.open = false; },

    async create() {
      const data = await this.run(() => Api.post("/requests", this.form));
      if (!data) return;
      this.id = data.batch_id;
      history.replaceState(null, "", F.url("batch.html", { id: this.id }));
      document.body.dataset.nav = "tracker";
      App.flash(data.message);
      await this.load();
    },

    // ------------------------------------------------------------ step 2: upload & check
    async uploadWorkbook() {
      if (!this.file) return;
      const data = await this.run(() => Api.upload(`/batches/${encodeURIComponent(this.id)}/inputs/${this.type}`, this.file));
      this.file = null;
      this.$refs.workbook.value = "";                             // so choosing the same file again uploads it again
      if (!data) return;
      if (data.rejected) App.error(new ApiError(data.message, 422)); else App.flash(data.message);
      this.open = {};                                             // let the stepper move on
      await this.load();
    },

    // ------------------------------------------------------------ step 3: exceptions, submit
    async act(path, body = {}) {
      const data = await this.run(() => Api.post(`/batches/${encodeURIComponent(this.id)}${path}`, { type: this.type, ...body }));
      if (data) { App.flash(data.message); await this.load(); }
      return data;
    },
    async exclude() { if (await this.act("/exceptions/exclude", this.ex)) this.ex = { ...this.ex, keys: "", reason: "" }; },
    async override() {
      if (await this.act("/exceptions/override", this.ov)) this.ov = { ...this.ov, account_number: "", value: "", reason: "" };
    },
    remove(a) { return this.act(`/exceptions/${a.adjustment_id}/remove`); },
    async submit() { this.open = {}; await this.act("/submit"); },
    async discard() {
      if (!confirm("Discard this draft request? Its exceptions stay in the history.")) return;
      const data = await this.run(() => Api.post(`/batches/${encodeURIComponent(this.id)}/discard`));
      if (data) App.go("tracker.html", data.message);
    },

    // ------------------------------------------------------------ step 4: validation
    async rowAction(r, action) {
      if (this.busy) return;
      await this.run(async () => {                                 // buttons stay disabled until the page is reloaded
        const data = await Api.post(`/requests/${r.request_id}/${action}`);
        App.flash(data.message);
        await this.load();
      });
    },
    csv(snapshotId) { return this.run(() => Api.download(`/snapshots/${snapshotId}/findings.csv`)); },

    // ------------------------------------------------------------ step 5: generate (once)
    adjusted(g) { return this.b.snapshots.find((s) => s.snapshot.snapshot_id === g.snapshot_id).adjusted; },
    async generate(g) {
      const bureaus = this.chosen[g.request_id] || [];
      if (!confirm(`Generate the files for ${bureaus.map(F.title).join(", ")}? Files are generated once per `
                   + "validation; to generate again you will need a new request.")) return;
      const data = await this.run(() => Api.post(`/batches/${encodeURIComponent(this.id)}/generate`, {
        request_id: g.request_id, bureau_codes: bureaus, confirm_adjustments: !!this.confirm[g.request_id],
      }));
      if (data) { App.flash(data.message); this.open = {}; await this.load(); }
    },

    // ------------------------------------------------------------ step 6: files
    download(r, kind) { return this.run(() => Api.download(`/requests/${r.request_id}/${kind}`)); },
  }));
});

if (typeof module !== "undefined") module.exports = { requestSteps, STEPS };
