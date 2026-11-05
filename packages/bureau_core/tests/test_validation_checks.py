"""Name, email and PIN checks used by the validation rules."""

import pytest
from sqlalchemy import update

from bureau_core.validation.checks.addresses import pin_format_problem, pin_ranges, pin_state_problem
from bureau_core.validation.checks.emails import email_problem
from bureau_core.validation.checks.names import DEFAULT_SALUTATION_PATTERN, check_name
from bureau_core.validation.reference import ref_state_pin_rows
from bureau_core.validation.tables import metadata, ref_state_pin, sqlite_engine


@pytest.fixture
def engine():
    eng = sqlite_engine()
    metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(ref_state_pin.insert(), ref_state_pin_rows())
    yield eng
    eng.dispose()


@pytest.mark.parametrize(
    "name,digits,bad,descriptor_first,valid_token,words",
    [
        ("Haren Patel", False, "", False, True, 2),
        ("Anil 2nd Verma", True, "", False, True, 3),
        ("Ramesh Kumar S/O Suresh Kumar", False, "", False, True, 4),
        ("Ramesh S\\O Suresh", False, "", False, True, 2),
        ("S/O Suresh", False, "", True, True, 1),
        ("Mr. Rakesh", False, "", False, True, 1),  # leading salutation ignored
        ("Rakesh Mr.", False, "", False, True, 2),  # trailing salutation is part of the name
        ("Amit & Sons", False, "&", False, True, 3),
        ("C/O Amit", False, "/", False, True, 2),
        ("A. B.", False, "", False, False, 2),  # initials only
        ("O'Brien", False, "", False, True, 1),
    ],
)
def test_check_name(name, digits, bad, descriptor_first, valid_token, words):
    c = check_name(name)
    assert (c.has_digits, c.disallowed_chars, c.starts_with_descriptor, c.has_valid_token, c.word_count) == (
        digits,
        bad,
        descriptor_first,
        valid_token,
        words,
    )


def test_salutation_pattern_is_a_setting():
    assert check_name("Sri Rakesh Kumar").word_count == 3  # not a default salutation
    assert check_name("Sri Rakesh Kumar", DEFAULT_SALUTATION_PATTERN + "|^SRI$").word_count == 2
    assert check_name("shri Rakesh").word_count == 1 and check_name("Shri. Rakesh").word_count == 2


@pytest.mark.parametrize(
    "email,ok",
    [
        ("abc@gmail.com", True),
        ("kumar@yahoo.co.in", True),
        ("A.B-c_d@Mail.COM", True),
        ("ramesh@@gmail", False),
        ("ab cd@yahoo.com", False),
        ("a@gmail.com", False),
        ("abc@g.com", False),
        ("abc@gmail.c", False),
        ("abc@gmail.", False),
        ("abc@gmail..com", False),
        ("abc@gmail", False),
        ("abc@gmail.c0m", False),
        ("ab#c@gmail.com", False),
    ],
)
def test_email_problem(email, ok):
    assert (email_problem(email) is None) == ok


def test_email_double_dot_configurable():
    assert email_problem("abc@gmail..com", strict_dots=False) is None
    assert email_problem("abc@gmail...com", strict_dots=False) is not None


@pytest.mark.parametrize(
    "pin,state,format_ok,state_ok",
    [
        ("604002", "TAMIL_NADU", True, True),
        ("604-002", "TAMIL_NADU", True, True),
        ("900 000", "APO", False, True),
        ("400001", "MAHARASHTRA", True, True),
        ("560001", "MAHARASHTRA", True, False),
        ("604", "TAMIL_NADU", False, True),
        ("6040020000", "TAMIL_NADU", False, True),
        ("M5S2G4", "FOREIGN", True, True),
        ("956789", "APO", True, True),
    ],
)
def test_pin_rules(engine, pin, state, format_ok, state_ok):
    with engine.connect() as conn:  # ranges from ref_state_pin (seeded by conftest)
        ranges = pin_ranges(conn)
    assert (pin_format_problem(pin, state) is None) == format_ok
    assert (pin_state_problem(pin, state, ranges) is None) == state_ok


def test_pin_ranges_come_from_the_table(engine):

    with engine.begin() as conn:  # an admin widens Maharashtra's range in the database
        conn.execute(update(ref_state_pin).where(ref_state_pin.c.state_code == "MAHARASHTRA").values(pin_prefix_max=56))
        assert pin_state_problem("560001", "MAHARASHTRA", pin_ranges(conn)) is None
