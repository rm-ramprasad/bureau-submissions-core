"""Generic delimited file engine (e.g. Commercial UCRF): a layout (YAML) describes the format; the engine renders a
validated snapshot with it.

  layout.py      the layout model (header, borrower group, records, trailer; positional fields), checked on load
  formatters.py  value formatters: the TUDF ones plus delimited-specific ones (leading minus, DPD 1nnn ...)
  engine.py      rendering lines, joined by the delimiter and ended by the line end
"""
