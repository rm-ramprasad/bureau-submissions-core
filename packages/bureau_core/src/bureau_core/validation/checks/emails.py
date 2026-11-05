"""E-mail quality checks (spec Appendix E)."""

from __future__ import annotations

import re

LOCAL_CHARS_RE = re.compile(r"^[a-z0-9._-]+$")
DOMAIN_CHARS_RE = re.compile(r"^[a-z0-9.-]+$")


def email_problem(email: str | None, strict_dots: bool = True) -> str | None:
    """Return why the e-mail fails Appendix E, or None. strict_dots forbids any '..' after '@' (rule 7)."""
    e = (email or "").strip().lower()
    if not e:
        return "empty"
    if " " in e:
        return "contains a space"
    if e.count("@") != 1:
        return "must contain exactly one @"
    if e.endswith(("@", ".")):
        return "ends with @ or ."
    local, domain = e.split("@")
    if not LOCAL_CHARS_RE.match(local) or not DOMAIN_CHARS_RE.match(domain):
        return "invalid characters"
    if sum(ch.isalnum() for ch in local) < 2:
        return "fewer than 2 alphanumeric characters before @"
    if "." not in domain or sum(ch.isalnum() for ch in domain) < 2:
        return "domain needs a '.' and 2 alphanumeric characters"
    if len(domain.split(".")[0]) < 2:
        return "fewer than 2 characters between @ and the next ."
    tld = domain.rsplit(".", 1)[1]
    if len(tld) < 2 or not tld.isalpha():
        return "fewer than 2 letters after the last ."
    if ("..." if not strict_dots else "..") in domain:
        return "consecutive dots after @"
    return None
