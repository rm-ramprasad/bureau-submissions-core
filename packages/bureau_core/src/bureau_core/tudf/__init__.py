"""Standalone Excel -> TUDF converter: a filled-in Data Submission Form in, the bureau TUDF file out (as the bureaus'
upload portals do). No database or Azure. See convert.py; command line: python -m bureau_core.tudf."""

from bureau_core.tudf.convert import ConversionError, ConversionResult, convert

__all__ = ["ConversionError", "ConversionResult", "convert"]
