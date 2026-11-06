// Bureau profiles: per bureau and submission type, the member ID, member short name, header tag and file-name
// pattern used in the bureau files. Everyone can read them; administrators change them (with a reason, audited).
// The example file name updates as the pattern is typed.
document.addEventListener("alpine:init", () => {
  Alpine.data("bureausPage", () => ({
    data: null,
    edit: {},            // "<type>/<bureau>" -> the values being edited
    busy: false,

    init() { this.load(); },

    async load() {
      try {
        this.data = await Api.get("/bureaus");
        this.edit = Object.fromEntries(this.data.profiles.map((p) => [this.key(p), { ...p, reason: "" }]));
      } catch (e) { App.error(e); }
    },

    key(p) { return `${p.submission_type}/${p.bureau_code}`; },
    canEdit() { return !!this.$store.app.user?.is_admin; },
    types() {                                                       // in the registry's order (consumer first)
      const order = (this.$store.app.meta?.submission_types || []).map((t) => t.value);
      return [...new Set((this.data?.profiles || []).map((p) => p.submission_type))]
        .sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99));
    },
    profiles(type) { return this.data.profiles.filter((p) => p.submission_type === type); },
    changed(p) {
      const e = this.edit[this.key(p)];
      return ["member_id", "member_short_name", "header_segment_tag", "file_name_pattern", "is_active"]
        .some((f) => (e[f] ?? "") !== (p[f] ?? ""));
    },

    // The file name the pattern gives for the last month end (the same fields the server fills in).
    example(e) {
      const now = new Date(), two = (n) => String(n).padStart(2, "0");
      const asOf = new Date(now.getFullYear(), now.getMonth(), 0);
      const dmy = (d) => two(d.getDate()) + two(d.getMonth() + 1) + d.getFullYear();
      return (e.file_name_pattern || "")
        .replaceAll("{member_id}", e.member_id || "").replaceAll("{bureau}", e.bureau_code)
        .replaceAll("{cycle}", "ME").replaceAll("{as_of:%d%m%Y}", dmy(asOf))
        .replaceAll("{created:%d%m%Y}", dmy(now))
        .replaceAll("{created:%H%M%S}", two(now.getHours()) + two(now.getMinutes()) + two(now.getSeconds()));
    },

    async save(p) {
      this.busy = true;
      try {
        const data = await Api.post(`/bureaus/${p.submission_type}/${p.bureau_code}`, this.edit[this.key(p)]);
        App.flash(data.message);
        await this.load();
      } catch (err) { App.error(err); } finally { this.busy = false; }
    },
  }));
});
