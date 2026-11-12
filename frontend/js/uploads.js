// Uploads page (ADR 0002 PR 7; progress on screen, owner 2026-11-12): the upload is sent with a progress bar, then
// processed in the background; the list follows each upload's stage until it is done.
const STAGE_TEXT = { RECEIVED: "Waiting to start", LOADING: "Loading the sheets", CHECKING: "Checking the data",
                     SAVING: "Saving in the customer tables" };

document.addEventListener("alpine:init", () => {
  Alpine.data("uploadsPage", () => ({
    data: null,
    type: "",
    asOf: "",
    file: null,
    busy: false,
    sending: null,          // {file_name, pct} while the file is being sent
    timer: null,

    init() { this.load(); },

    current() { return (this.data?.types || []).find((t) => t.submission_type === this.type); },
    stageText(u) { return STAGE_TEXT[u.stage] || u.stage; },
    running(u) { return u.stage && u.stage !== "DONE"; },

    async load() {
      try {
        const before = new Map((this.data?.uploads || []).map((u) => [u.load_id, u.stage]));
        this.data = await Api.get("/uploads", { tenant_id: this.$store.app.tenant || "" });
        if (!this.type && this.data.types.length) this.type = this.data.types[0].submission_type;
        for (const u of this.data.uploads) {   // an upload that has just finished: say how it ended
          if (before.get(u.load_id) && before.get(u.load_id) !== "DONE" && u.stage === "DONE") this.announce(u);
        }
        this.follow();
      } catch (e) { App.error(e); }
    },

    announce(u) {
      if (u.applied_at) App.flash(`${u.file_name}: saved in the customer tables. ${u.apply_summary || ""}`);
      else if (u.status !== "LOADED") App.flash(`${u.file_name}: not loaded. ${u.error_message || ""}`, "error");
      else if (u.apply_summary) App.flash(`${u.file_name}: ${u.apply_summary}`, "error");
      else if ((u.summary || []).some((g) => g.severity === "ERROR"))
        App.flash(`${u.file_name}: blocking data-quality checks failed; fix the workbook and upload it again.`, "error");
      else App.flash(`${u.file_name}: loaded and checked.`);
    },

    follow() {   // poll while anything is still running
      clearTimeout(this.timer);
      if ((this.data?.uploads || []).some((u) => this.running(u))) this.timer = setTimeout(() => this.load(), 3000);
    },

    async send() {
      this.busy = true;
      this.sending = { file_name: this.file.name, pct: 0 };
      try {
        const out = await Api.uploadWithProgress(`/uploads/${this.type}`, this.file,
                                                 { as_of_date: this.asOf, tenant_id: this.$store.app.tenant || "" },
                                                 (pct) => { this.sending.pct = pct; });
        this.sending = null;
        const u = out.upload;
        if (u && u.stage === "DONE") this.announce(u); else App.flash(out.message);
        await this.load();
      } catch (e) {
        this.sending = null;
        if (!e.status || [502, 503, 504].includes(e.status)) {
          App.flash("No answer from the server. The list below shows whether the upload arrived; if it is not "
                    + "there, upload the workbook again.", "error");
          await this.load();
        } else App.error(e);
      } finally { this.busy = false; }
    },
  }));
});
