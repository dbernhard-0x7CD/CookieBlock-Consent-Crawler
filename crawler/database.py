import logging
from typing import Optional, Tuple
from pathlib import Path

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

from crawler.enums import CrawlState, CrawlerType
from crawler.utils import logger

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
    Basically one browser instance crawling.
    """

    __tablename__ = "crawl"

    browser_id: Mapped[Optional[int]] = mapped_column(
        primary_key=True, autoincrement=True
    )
    task_id: Mapped[int] = mapped_column(ForeignKey("task.task_id"))
    task: Mapped["Task"] = relationship(uselist=False, lazy="select")

    browser_params: Mapped[str]

    start_time = mapped_column(DateTime(timezone=True), server_default=func.now())


class Cookie(Base):
    """ """

    __tablename__ = "javascript_cookies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    browser_id: Mapped[int] = mapped_column(ForeignKey("crawl.browser_id"))
    browser: Mapped["Crawl"] = relationship(lazy="select")

    visit_id: Mapped[int] = mapped_column(ForeignKey("site_visits.visit_id"))
    visit: Mapped["SiteVisit"] = relationship(lazy="select")

    extension_session_uuid: Mapped[Optional[str]]

    event_ordinal: Mapped[Optional[int]]
    record_type: Mapped[Optional[str]]
    change_cause: Mapped[Optional[str]]

    expiry = mapped_column(DateTime(timezone=True))
    is_http_only: Mapped[Optional[int]]
    is_host_only: Mapped[Optional[int]]
    is_session: Mapped[Optional[int]]

    host: Mapped[Optional[str]]
    is_secure: Mapped[Optional[int]]

    name: Mapped[Optional[str]]
    path: Mapped[Optional[str]]
    value: Mapped[Optional[str]]
    same_site: Mapped[Optional[str]]
    first_party_domain: Mapped[Optional[str]]
    store_id: Mapped[Optional[str]]

    time_stamp = mapped_column(DateTime(timezone=True))


class SiteVisit(Base):
    """ """

    __tablename__ = "site_visits"

    visit_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    browser_id: Mapped[int] = mapped_column(ForeignKey("crawl.browser_id"))
    browser: Mapped["Crawl"] = relationship(lazy="select")

    site_url: Mapped[str]
    site_rank: Mapped[int]

    def __repr__(self) -> str:
        return f"[SiteVisit visit_id={self.task_id} browser_id={self.browser_id} site_url={self.site_url}]"


class IncompleteVisits(Base):
    """ """

    __tablename__ = "incomplete_visits"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    visit_id: Mapped[int] = mapped_column(ForeignKey("site_visits.visit_id"))
    visit: Mapped["SiteVisit"] = relationship(lazy="select")

    def __repr__(self) -> str:
        return f"[IncompleteVisit visit_id={self.visit_id}]"


class Task(Base):
    """ """

    __tablename__ = "task"

    task_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    start_time = mapped_column(DateTime(timezone=True), server_default=func.now())

    manager_params: Mapped[str]
    openwpm_version: Mapped[str]
    browser_version: Mapped[str]

    def __repr__(self) -> str:
        return f"[Task task_id={self.task_id}, start_time={self.start_time}]"


class ConsentData(Base):
    """ """

    __tablename__ = "consent_data"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    browser_id: Mapped[int] = mapped_column(ForeignKey("crawl.browser_id"))
    browser: Mapped["Crawl"] = relationship(lazy="select")

    visit_id: Mapped[int] = mapped_column(ForeignKey("site_visits.visit_id"))
    visit: Mapped["SiteVisit"] = relationship(lazy="select")

    name: Mapped[str]
    domain: Mapped[str]

    cat_id: Mapped[int]
    cat_name: Mapped[str]

    purpose: Mapped[Optional[str]]
    expiry: Mapped[Optional[str]]

    type_name: Mapped[Optional[str]]
    type_id: Mapped[Optional[int]]


class ConsentCrawlResult(Base):
    """ """

    __tablename__ = "consent_crawl_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    browser_id: Mapped[int] = mapped_column(ForeignKey("crawl.browser_id"))
    browser: Mapped["Crawl"] = relationship(lazy="select")

    visit_id: Mapped[int] = mapped_column(ForeignKey("site_visits.visit_id"))
    visit: Mapped["SiteVisit"] = relationship(lazy="select")

    cmp_type: Mapped[int]
    crawl_state: Mapped[int]

    report: Mapped[Optional[str]]


def initialize_base_db(
    db_url: str,
    alembic_root_dir: Path,
    create: bool = False,
    pool_size: int = 8,
) -> None:

    logger.info("Creating database connection to %s", db_url)
    engine = create_engine(db_url, pool_size=pool_size)

    SessionLocal.configure(bind=engine)

    if create:
        logger.info("Creating initial database structure")
        Base.metadata.create_all(bind=engine, checkfirst=True)
        config = alembic.config.Config(file_=str(alembic_root_dir / "alembic.ini"))

        config.set_main_option("sqlalchemy.url", db_url)
        config.set_main_option("script_location", str(alembic_root_dir / "alembic"))
        config.attributes["configure_logger"] = False
        alembic.command.stamp(config, "head")

        logger.info("Created database.")


def start_task(browser_version: str) -> Task:
    with SessionLocal.begin() as session:
        t = Task(
            manager_params="TODO", openwpm_version="-1", browser_version=browser_version
        )
        session.add(t)
    return t


def start_crawl(task: Task, browser_params: str, url: str) -> Tuple[Crawl, SiteVisit]:
    with SessionLocal.begin() as session:
        c = Crawl(task=task, browser_params=browser_params)
        session.add(c)

        visit = SiteVisit(browser=c, site_url=url, site_rank=-1)
        session.add(visit)

    return (c, visit)


def store_result(
    browser: Crawl,
    visit: SiteVisit,
    cmp_type: CrawlerType,
    report: str,
    crawlState: CrawlState,
) -> None:
    with SessionLocal.begin() as session:
        result = ConsentCrawlResult(
            browser=browser,
            visit=visit,
            crawl_state=crawlState.value,
            cmp_type=cmp_type.value,
            report=report,
        )
        session.add(result)


def store_consent_data(
    name: str,
    domain: str,
    cat_id: int,
    cat_name: str,
    browser: Crawl,
    visit: SiteVisit,
    purpose: Optional[str],
    expiry: Optional[str],
    type_name: Optional[str],
    type_id: Optional[int],
) -> None:
    with SessionLocal.begin() as session:
        data = ConsentData(browser=browser, name=name, visit=visit, domain=domain, cat_id=cat_id, cat_name=cat_name, purpose=purpose, expiry=expiry, type_name=type_name, type_id=type_id)
        session.add(data)

def store_cookie(visit: SiteVisit, browser: Crawl, extension_session_uuid: Optional[str], event_ordinal: Optional[str], record_type: Optional[str], change_cause: Optional[str], expiry: DateTime, is_http_only: Optional[int], name: Optional[str]):
    with SessionLocal.begin() as session:
        js_cookie = Cookie(visit=visit, browser=browser, extension_session_uuid=extension_session_uuid, event_ordinal=event_ordinal, record_type=record_type, change_cause=change_cause, expiry=expiry, is_http_only=is_http_only, name=name)
        session.add(js_cookie)
