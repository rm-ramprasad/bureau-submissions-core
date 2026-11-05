"""App users: who may use the app once signed in through the company identity provider (Entra ID SSO).

Passwords, MFA and leavers' accounts are managed by the company identity provider. This table only says who may use
the app (active flag) and who is an administrator, matched on the e-mail address of the signed-in identity.
Managed through the admin commands (step 1.3e) and the API.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Engine, Index, String, Table, select, update
from sqlalchemy.exc import IntegrityError

from canonical.db import metadata, utcnow

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,50}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")

app_user = Table(  # migrations 010, 011
    "app_user",
    metadata,
    Column("username", String(50), primary_key=True),
    Column("display_name", String(100), nullable=False),
    Column("email", String(254)),  # unique while set: uq_app_user_email (migration 003)
    Column("is_active", Boolean, nullable=False, default=True),
    Column("is_admin", Boolean, nullable=False, default=False),
    Column("last_login_at", DateTime),
    Column("created_at", DateTime, nullable=False),
)
Index(
    "uq_app_user_email",
    app_user.c.email,
    unique=True,  # filtered: SQL Server allows only one NULL otherwise
    mssql_where=app_user.c.email.isnot(None),
    sqlite_where=app_user.c.email.isnot(None),
)


class UserError(ValueError):
    """Shown to the user."""


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    email: str | None
    is_active: bool
    is_admin: bool
    last_login_at: datetime | None
    created_at: datetime

    @classmethod
    def from_row(cls, row) -> User:
        m = dict(row._mapping)
        m["is_active"], m["is_admin"] = bool(m["is_active"]), bool(m["is_admin"])
        return cls(**m)


def normalise_email(email: str | None) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise UserError(f"Not a valid e-mail address: {email!r}")
    return email


class UserRepository:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] = utcnow):
        self.engine = engine
        self.clock = clock

    def add(self, username: str, display_name: str, email: str, is_admin: bool = False) -> User:
        username = (username or "").strip().lower()
        if not USERNAME_RE.match(username):
            raise UserError("Username: 3-50 characters, a-z, 0-9, dot, dash or underscore")
        if not (display_name or "").strip():
            raise UserError("Enter the user's name")
        email = normalise_email(email)
        if self.find(username):
            raise UserError(f"User {username} already exists")
        if self.find_by_email(email):
            raise UserError(f"{email} already belongs to another user")
        with self.engine.begin() as conn:
            conn.execute(
                app_user.insert().values(
                    username=username,
                    display_name=display_name.strip()[:100],
                    email=email,
                    is_active=True,
                    is_admin=is_admin,
                    created_at=self.clock(),
                )
            )
        return self.find(username)

    def find(self, username: str) -> User | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(app_user).where(app_user.c.username == (username or "").strip().lower())).first()
        return User.from_row(row) if row else None

    def find_by_email(self, email: str | None) -> User | None:
        if not email:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(select(app_user).where(app_user.c.email == email.strip().lower())).first()
        return User.from_row(row) if row else None

    def list(self) -> list[User]:
        with self.engine.connect() as conn:
            return [User.from_row(r) for r in conn.execute(select(app_user).order_by(app_user.c.username))]

    def set_email(self, username: str, email: str) -> None:
        email = normalise_email(email)
        try:
            with self.engine.begin() as conn:
                if (
                    conn.execute(
                        update(app_user).where(app_user.c.username == (username or "").lower()).values(email=email)
                    ).rowcount
                    == 0
                ):
                    raise UserError(f"No user {username}")
        except IntegrityError:
            raise UserError(f"{email} already belongs to another user") from None

    def set_active(self, username: str, active: bool) -> None:
        with self.engine.begin() as conn:
            if (
                conn.execute(
                    update(app_user).where(app_user.c.username == (username or "").lower()).values(is_active=active)
                ).rowcount
                == 0
            ):
                raise UserError(f"No user {username}")

    def record_login(self, username: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(app_user).where(app_user.c.username == username).values(last_login_at=self.clock()))
