import logging

import alembic.config
import alembic.command
import psycopg2

from sqlalchemy import (
    String,
    Boolean,
    Engine,
    Integer,
    func,
    ForeignKey,
    DateTime,
    JSON,
    select,
    desc,
    text,
    create_engine,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
    DeclarativeBase,
    sessionmaker,
)

logger = logging.getLogger("enfbots_crawler")

"""
We do not want to use expire after commit to still have access to attributes from objects.
If 'expire_on_commit' is set to true all attributes of instances are marked out of date and 
the next time they are accessed a query will be issued. This is not desired to not overload
the database.
"""
SessionLocal = sessionmaker(expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Crawl(Base):
    """
    Basically one invokation of the crawler.
    """

    __tablename__ = "crawl"

    browser_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int]
    task: Mapped["Task"] = relationship(
        back_populates="crawl", uselist=False, lazy="select"
    )

    browser_params = mapped_column(JSON)

    start_time = mapped_column(DateTime(timezone=True), server_default=func.now())


class Cookie(Base):
    """ """

    __tablename__ = "javascript_cookies"

    extension_session_uuid: Mapped[str]

    time_stamp = mapped_column(DateTime(timezone=True))


class SiteVisit(Base):
    """ """

    __tablename__ = "site_visits"

    visit_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    browser_id: Mapped[int]
    browser: Mapped["Crawl"] = relationship(back_populates="site_visits", lazy="select")
    # TODO: browser_id

    site_url: Mapped[str]
    site_rank: Mapped[int]


class Task(Base):
    """ """

    __tablename__ = "task"

    task_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    start_time = mapped_column(DateTime(timezone=True), server_default=func.now())

    # manager_params: Mapped[str]
    # openwpm_version: Mapped[str]
    # browser_version: Mapped[str]
