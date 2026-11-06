// TUDF reader for the viewer. Splits the file by its own markers only: the fixed-width header, then segments
// (<tag><seq> or <tag><len><seq>) of fields <tag 2><length 2><value>, each record ending at the record end (ES02**),
// and the trailer. It returns positions, never values: the page shows text.slice(start, end), so what is on screen
// is the file itself. Anything that does not follow the structure becomes a problem; nothing is guessed or fixed.
// Must agree with bureau_core/formats/tudf/parser.py (tests/test_tudf_viewer.py runs both on the golden files).
(function (root) {
  const isDigits = (s) => /^\d+$/.test(s);

  function readTudf(text, labels) {
    const enc = labels.encoding, known = labels.segments, endTag = enc.record_end.slice(0, 2);
    const problems = [], records = [];
    const problem = (at, message) => problems.push({ at, message });

    // ---- header: fixed width
    const hl = labels.header.length;
    let p = 0;
    const header = {
      start: 0, end: Math.min(hl, text.length),
      fields: labels.header.fields.map((f) => {
        const s = p; p += f.width;
        return { name: f.name, start: Math.min(s, text.length), end: Math.min(p, text.length) };
      }),
    };
    if (text.length < hl) problem(text.length, `The file is shorter than the ${hl}-character header`);

    // ---- trailer: the last characters (line breaks after it are allowed and shown as such)
    let end = text.length;
    while (end > hl && (text[end - 1] === "\n" || text[end - 1] === "\r")) end--;
    let trailer = null;
    if (end - enc.trailer.length >= hl && text.slice(end - enc.trailer.length, end) === enc.trailer) {
      trailer = { start: end - enc.trailer.length, end };
      end = trailer.start;
    } else {
      problem(end, `No trailer: the file does not end with ${enc.trailer}`);
    }

    // ---- segment tag style: "plain" (PNN01) or "tlv" (PN03N01), read from the first segment
    let i = hl;
    const skipBreaks = (k) => {
      for (;;) {
        if (text[k] === "\r" || text[k] === "\n") k++;
        else if (enc.record_separator && text.startsWith(enc.record_separator, k)) k += enc.record_separator.length;
        else return k;
      }
    };
    i = skipBreaks(i);
    let style = enc.segment_tag_style;
    if (i < end && text.slice(i, i + 2) in known && isDigits(text.slice(i + 2, i + 4)) && /[A-Z]/.test(text[i + 4] || "")) {
      style = "tlv";
    } else if (i < end) {
      style = "plain";
    }

    // ---- records
    let current = null;
    const open = (at) => (current = current || { start: at, end: at, segments: [] });
    const close = (at) => { current.end = at; records.push(current); current = null; };

    while (i < end) {
      i = skipBreaks(i);
      if (i >= end) break;
      if (text.startsWith(enc.record_end, i)) {
        open(i).segments.push({ tag: endTag, start: i, seqStart: i + 2, end: i + enc.record_end.length, fields: [] });
        i += enc.record_end.length;
        close(i);
        continue;
      }
      const tag = text.slice(i, i + 2);
      open(i);
      if (!(tag in known) || tag === endTag) {
        const next = text.indexOf(enc.record_end, i);
        const stop = next === -1 || next > end ? end : next;
        problem(i, `Unknown segment tag "${tag}": cannot read from here to ${next === -1 ? "the end" : "the next record end"}`);
        current.segments.push({ tag: null, bad: true, start: i, end: stop, fields: [] });
        i = stop;
        continue;
      }
      const seg = { tag, start: i, fields: [] };
      let k = i + 2, seqLength = known[tag].seq_length;
      if (style === "tlv") {
        const n = text.slice(k, k + 2);
        if (!isDigits(n)) { problem(k, `${tag} segment: "${n}" is not a length`); seqLength = 0; } else seqLength = Number(n);
        k += 2;
      }
      seg.seqStart = k;
      seg.seqEnd = k + seqLength;
      k += seqLength;
      while (k < end && isDigits(text.slice(k, k + 2))) {
        const lengthText = text.slice(k + 2, k + 4);
        if (!isDigits(lengthText)) {
          problem(k, `${tag} field ${text.slice(k, k + 2)}: "${lengthText}" is not a length`);
          seg.fields.push({ tag: text.slice(k, k + 2), start: k, valueStart: k + 2, end: k + 2, bad: true });
          k = end; break;
        }
        const valueEnd = k + 4 + Number(lengthText);
        if (valueEnd > end) {
          problem(k, `${tag} field ${text.slice(k, k + 2)}: length ${lengthText} runs past the end of the data`);
          seg.fields.push({ tag: text.slice(k, k + 2), start: k, valueStart: k + 4, end, bad: true });
          k = end; break;
        }
        seg.fields.push({ tag: text.slice(k, k + 2), start: k, valueStart: k + 4, end: valueEnd });
        k = valueEnd;
      }
      seg.end = k;
      current.segments.push(seg);
      i = k;
    }
    if (current) {
      problem(i, `The file ends inside record ${records.length + 1} (no ${enc.record_end})`);
      current.unfinished = true;
      close(i);
    }
    return { length: text.length, style, header, records, trailer, problems };
  }

  // Pipe-delimited files (commercial): one segment per line, fields split at the delimiter. Grouped per loan: a
  // record is the borrower block (BS + its AS / RS lines) followed by one CR line and its GS / SS / CD lines.
  // Field n (1 = the tag) is text.slice(start, end), as for TUDF.
  function readDelimited(text, labels) {
    const enc = labels.encoding, st = labels.structure, known = labels.segments, d = enc.delimiter;
    const problems = [], records = [];
    const problem = (at, message) => problems.push({ at, message });

    // ---- lines, with their positions (line ends are not part of a line)
    const lines = [];
    let i = 0, bareLf = 0;
    while (i < text.length) {
      let j = text.indexOf("\n", i);
      const next = j === -1 ? text.length : j + 1;
      if (j === -1) j = text.length;
      let end = j;
      if (end > i && text[end - 1] === "\r") end--;
      else if (j < text.length && enc.line_end === "\r\n") bareLf++;
      if (end > i) lines.push({ start: i, end });
      i = next;
    }
    if (bareLf) problem(0, `${bareLf} line(s) end with a line feed only; the layout expects CRLF`);

    const parse = (ln) => {
      const fields = [];
      let s = ln.start;
      for (;;) {
        const k = text.indexOf(d, s);
        const e = k === -1 || k > ln.end ? ln.end : k;
        fields.push({ start: s, end: e });
        if (e === ln.end) break;
        s = e + d.length;
      }
      if (enc.trailing_delimiter) {
        if (text.slice(ln.end - d.length, ln.end) === d) fields.pop();       // the empty piece after the last "|"
        else problem(ln.start, `Line does not end with "${d}"`);
      }
      const tag = text.slice(fields[0].start, fields[0].end);
      const seg = { tag, start: ln.start, end: ln.end, fields: fields.slice(1).map((f, n) =>
        ({ tag: String(n + 2), start: f.start, valueStart: f.start, end: f.end })) };
      const spec = known[tag];
      if (!spec) { seg.bad = true; problem(ln.start, `Unknown segment "${tag}"`); }
      else if (fields.length !== spec.fields_count) {
        seg.fields.forEach((f) => { f.bad = true; });
        problem(ln.start, `${tag} line has ${fields.length} fields; the layout has ${spec.fields_count}`);
      }
      return seg;
    };
    const segs = lines.map(parse);

    // ---- header and trailer
    const toHeader = (seg) => ({ start: seg.start, end: seg.end, fields: seg.fields.map((f) => ({
      name: known[seg.tag].fields[f.tag]?.name || `Field ${f.tag}`, start: f.start, end: f.end })) });
    let header = { start: 0, end: 0, fields: [] }, trailer = null;
    if (segs.length && segs[0].tag === st.header) header = toHeader(segs.shift());
    else problem(0, `The file does not start with ${st.header}`);
    if (segs.length && segs[segs.length - 1].tag === st.trailer) trailer = segs.pop();
    else problem(text.length, `No trailer: the file does not end with a ${st.trailer} line`);

    // ---- loans
    let group = null, loan = null, groups = 0;
    const close = () => { if (loan) { loan.end = loan.segments[loan.segments.length - 1].end; records.push(loan); loan = null; } };
    for (const seg of segs) {
      if (seg.tag === st.group) { close(); group = [seg]; groups++; }
      else if (st.group_children.includes(seg.tag)) {
        if (group && !loan) group.push(seg);
        else problem(seg.start, `${seg.tag} line is not inside a borrower block (it must follow ${st.group} before any ${st.record})`);
      } else if (seg.tag === st.record) {
        close();
        if (!group) problem(seg.start, `${st.record} line has no ${st.group} borrower line before it`);
        loan = { start: seg.start, segments: [...(group || []), seg] };
      } else if (st.record_children.includes(seg.tag) && loan) loan.segments.push(seg);
      else {
        problem(seg.start, seg.bad ? `Unknown segment "${seg.tag}"` : `${seg.tag} line is out of place`);
        (loan || { segments: [] }).segments.push({ ...seg, bad: true });
      }
    }
    close();

    // ---- the trailer's own counts (compared, never corrected)
    if (trailer) {
      const val = (n) => { const f = trailer.fields.find((x) => x.tag === String(n)); return f ? text.slice(f.start, f.end) : ""; };
      if (val(2) !== String(groups)) problem(trailer.start, `${st.trailer} says ${val(2)} ${st.group} line(s); the file has ${groups}`);
      if (val(3) !== String(records.length)) problem(trailer.start, `${st.trailer} says ${val(3)} ${st.record} line(s); the file has ${records.length}`);
    }
    return { length: text.length, style: "delimited", header, records, trailer, problems };
  }

  const api = { readTudf, readDelimited };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.TudfReader = api;
})(typeof window !== "undefined" ? window : globalThis);
