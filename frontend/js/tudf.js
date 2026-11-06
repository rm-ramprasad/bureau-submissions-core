// Bureau file viewer: open a file (or ?request=<id>&name=<file> for a stored one) and show it as it is: consumer TUDF
// or the commercial pipe-delimited file (told apart by its first line). The readers (tudf-reader.js) return
// positions; the page shows text.slice(start, end) and labels from tudf-labels.json.
// Where the list's name / account / ID numbers come from, per format: [segment, fields joined with a space].
const SUMMARY = {
  tudf: { name: ["PN", ["01", "02", "03", "04", "05"]], account: ["TL", ["03"]], ids: ["ID", ["02"]] },
  delimited: { name: ["BS", ["4"]], account: ["CR", ["2"]], ids: ["BS", ["8"]] },
};
document.addEventListener("alpine:init", () => {
  Alpine.data("tudfPage", () => ({
    all: null,               // tudf-labels.json: labels per submission type
    labels: null,            // ... of the open file's type
    file: null,              // { name, size, sha256 }
    text: "",
    result: null,
    summaries: [],
    query: "",
    selected: -1,
    raw: false,
    loading: false,
    dragging: false,

    async init() {
      try {
        this.all = await (await fetch("tudf-labels.json", { cache: "no-store" })).json();
        const q = new URLSearchParams(location.search);
        if (q.get("request")) {
          this.loading = true;
          const resp = await Api.request("GET", `/requests/${encodeURIComponent(q.get("request"))}/file`,
                                         { tenant: App.store().tenant, view: 1 });
          if (!resp.ok) await Api.json(resp);                              // throws the API's message
          this.load(q.get("name") || `request ${q.get("request")}`, new Uint8Array(await resp.arrayBuffer()));
        }
      } catch (e) { App.error(e); } finally { this.loading = false; }
    },

    async openFile(f) {
      if (!f) return;
      this.loading = true;
      try { this.load(f.name, new Uint8Array(await f.arrayBuffer())); } catch (e) { App.error(e); }
      finally { this.loading = false; }
    },

    load(name, bytes) {
      // One character per byte (TUDF is ASCII): positions are byte positions, and nothing is re-encoded.
      let text = "";
      for (let i = 0; i < bytes.length; i += 8192) text += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
      this.text = text;
      this.file = { name, size: bytes.length, sha256: null };
      this.labels = this.all[text.startsWith(`${this.all.COMMERCIAL.structure.header}|`) ? "COMMERCIAL" : "CONSUMER"];
      this.result = (this.labels.kind === "delimited" ? TudfReader.readDelimited : TudfReader.readTudf)(text, this.labels);
      this.summaries = this.result.records.map((rec, n) => this.summary(rec, n));
      this.query = "";
      this.select(this.result.records.length ? 0 : -1);
      crypto.subtle.digest("SHA-256", bytes).then((h) => {
        this.file.sha256 = Array.from(new Uint8Array(h), (b) => b.toString(16).padStart(2, "0")).join("");
      });
    },

    // List entry: name, account and ID numbers, all as written in the record.
    summary(rec, n) {
      const pick = ([tag, fields], every = false) => {
        const segs = rec.segments.filter((x) => x.tag === tag && !x.bad);
        return (every ? segs : segs.slice(0, 1)).flatMap((seg) => fields
          .map((t) => seg.fields.find((f) => f.tag === t)).filter(Boolean)
          .map((f) => this.show(f.valueStart, f.end).trim())).filter(Boolean);
      };
      const how = SUMMARY[this.labels.kind];
      const name = pick(how.name).join(" "), account = pick(how.account).join(" ");
      return { n, name, account, bad: rec.unfinished || rec.segments.some((s) => s.bad || s.fields.some((f) => f.bad)),
               search: [name, account, ...pick(how.ids, true)].join(" ").toUpperCase() };
    },

    get matches() {
      const q = this.query.trim().toUpperCase();
      return q ? this.summaries.filter((s) => s.search.includes(q)) : this.summaries;
    },

    get record() { return this.result && this.selected >= 0 ? this.result.records[this.selected] : null; },

    select(n) {
      if (!this.result || n < 0 || n >= this.result.records.length) return;
      this.selected = n;
    },

    jumpTo(at) {
      const n = this.result.records.findIndex((r) => at >= r.start && at <= r.end);
      if (n >= 0) { this.select(n); this.raw = false; }
    },

    show(a, b) { return this.text.slice(a, b); },
    get delimited() { return this.labels?.kind === "delimited"; },
    // Raw record: TUDF segments tile the record (so this is its text); commercial lines are joined by line breaks.
    rawOf(rec) { return rec.segments.map((s) => this.show(s.start, s.end)).join(this.delimited ? "\n" : ""); },
    markOf(f) { return this.delimited ? `#${f.tag}` : this.show(f.start, f.valueStart); },
    segName(tag) { return this.labels.segments[tag]?.name || ""; },
    fieldName(segTag, tag) { return this.labels.segments[segTag]?.fields[tag]?.name || "not in the layout"; },

    // Tooltip only: names and code meanings never replace what the file says.
    hover(seg, f) {
      const spec = this.labels.segments[seg.tag]?.fields[f.tag];
      const value = this.show(f.valueStart, f.end);
      const lines = [`${seg.tag} ${f.tag} · ${spec ? spec.name : "tag not in the layout"}`,
                     this.delimited ? `field ${f.tag} · ${f.end - f.start} characters · characters ${f.start + 1}–${f.end}`
                                    : `length ${this.show(f.start + 2, f.valueStart)} · characters ${f.valueStart + 1}–${f.end}`];
      if (spec?.excel?.length) lines.push(`Bureau Excel: ${spec.excel.join("; ")}`);
      if (spec && Object.keys(spec.codes).length) lines.push(spec.codes[value] ? `Code ${value} = ${spec.codes[value]}` : `Code ${value} is not in the code list`);
      if (f.bad) lines.push("Not readable: see the problems above");
      return lines.join("\n");
    },
  }));
});
