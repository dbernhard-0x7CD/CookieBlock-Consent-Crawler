#!/bin/bash
from __future__ import annotations

import argparse
import os
import sys
import re
from itertools import repeat
from logging import Logger
from typing import List, Optional, Tuple, Dict, cast
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from importlib.metadata import version
import time
import traceback
import tarfile
import shutil
import threading
import random
import json
import psutil

from tqdm import tqdm
from threading import Thread
from multiprocessing import Queue, Process
from psutil import TimeoutExpired, NoSuchProcess
from multiprocessing.managers import ListProxy
from pebble import ProcessPool, ThreadPool
from selenium.common.exceptions import TimeoutException, WebDriverException

import multiprocessing

from hyperlink import URL
import urllib3

from crawler.browser import Chrome
from crawler.database import (
    initialize_base_db,
    SiteVisit,
    SessionLocal,
    start_task,
    Crawl,
    start_crawl,
    register_browser_config,
    ConsentData,
    ConsentCrawlResult,
    Cookie,
)
from crawler.utils import set_log_formatter, is_on_same_domain
from crawler.enums import CrawlerType, CrawlState


class CrawlerException(Exception):
    """
    Indicates that the crawler has some unrecoverable error and should stop crawling.
    """


class BrowserProcess:
    """Represents a process needed to crawl a given website"""

    def __init__(self, pid: int, name: str, start_time: datetime) -> None:
        self.pid = pid
        self.name = name
        self.start_time = start_time

    def __str__(self) -> str:
        return f"PID {self.pid} to {self.name} and started at {self.start_time}"

    def __eq__(self, o: object) -> bool:
        return "pid" in o.__dict__ and isinstance(o, BrowserProcess) and o.pid == self.pid

    def __repr__(self) -> str:
        return f"PID {self.pid}, {self.name}"

log_dir = Path("./logs")
log_dir.mkdir(exist_ok=True)

chrome_profile_path = Path("./chrome_profile/")
chromedriver_path = Path("./chromedriver/chromedriver")
chrome_path = Path("./chrome/")

ver = version("cookieblock-consent-crawler")


def run_domain(
    visit: SiteVisit,
    process_result: ListProxy[BrowserProcess],
    browser_id: int,
    no_stdout: bool,
    crawl: Crawl,
    browser_params: Dict,
    num_subpages: int = 10,
) -> Tuple[ConsentCrawlResult, List[ConsentData], List[Cookie]]:
    """ """
    url = visit.site_url

    id = visit.visit_id
    crawl_logger = logging.getLogger(f"visit-{visit.visit_id}")
    crawl_logger.propagate = False

    file_handler = logging.FileHandler(log_dir / f"visit_{id}.log", delay=False)
    log_formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(filename)s %(name)s %(processName)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(log_formatter)
    crawl_logger.addHandler(file_handler)

    # Only add stdout as handler if desired
    if not no_stdout:
        stdout_handler = logging.StreamHandler()
        stdout_handler.setFormatter(log_formatter)
        crawl_logger.addHandler(stdout_handler)

    crawl_logger.setLevel(logging.INFO)
    crawl_logger.info("CookieBlock-ConsentCrawler version %s", ver)
    crawl_logger.info("Working on %s", url)
    file_handler.flush()

    with Chrome(
        seconds_before_processing_page=1,
        chrome_profile_path=chrome_profile_path,
        chromedriver_path=chromedriver_path,
        browser_id=browser_id,
        crawl=crawl,
        logger=crawl_logger,
        **browser_params,  # type: ignore
    ) as browser:
        u = URL.from_text(url)

        browser.load_page(u)
        crawl_logger.info(
            "Loaded website %s (chrome pid: %s, chromedriver: %s)",
            u,
            browser.driver.browser_pid,
            browser.driver.service.process.pid,
        )

        # Add all PIDs of the browser and chromedriver to the queue
        now = datetime.now()

        process_result.append(
            BrowserProcess(
                pid=browser.driver.browser_pid, name=f"[chrome] crawl to {url}", start_time=now
            )
        )
        process_result.append(
            BrowserProcess(
                pid=browser.driver.service.process.pid, name=f"[chromedriver]: crawl to {url}", start_time=now
            )
        )

        # bot mitigation
        crawl_logger.info("Calling bot mitigation")
        browser.bot_mitigation(max_sleep_seconds=1)

        crawler_type, crawler_state, consent_data, result = browser.crawl_cmps(
            visit=visit
        )

        if crawler_type == CrawlerType.FAILED or crawler_state != CrawlState.SUCCESS:
            browser.collect_cookies(visit=visit)
            crawl_logger.info(
                "Aborted crawl crawl to %s [crawl_type: %s; crawler_state: %s]",
                u,
                crawler_type.name,
                crawler_state.name,
            )

            # Close file handler
            for handler in crawl_logger.handlers:
                if isinstance(handler, logging.FileHandler):
                    handler.close()
            return (result, consent_data, [])

        crawl_logger.info(
            "Ran %s CMP: %s (Found %s consents)",
            crawler_type.name,
            crawler_state.name,
            len(consent_data),
        )
        browser.load_page(u)

        # visit subpages
        links = list(
            filter(
                lambda x: is_on_same_domain(x.url.to_text(), url),
                browser.get_links(),
            )
        )
        crawl_logger.info("Found %s links", len(links))

        chosen = random.choices(links, k=min(num_subpages, len(links)))
        for i, l in enumerate(chosen):
            crawl_logger.info("Subvisiting [%i]: %s", i, l.url.to_text())
            browser.load_page(l.url)
            browser.bot_mitigation(max_sleep_seconds=1, num_mouse_moves=2)

        cookies = browser.collect_cookies(visit=visit)

        crawl_logger.info("Sucessfully finished crawl to %s", u)

        proc = psutil.Process()
        # To detect resource leakage
        crawl_logger.info("Number of open files: %s", len(proc.open_files()))
        for f in proc.open_files():
            crawl_logger.info("\tOpen file: %s", f)
        crawl_logger.info("Number of connections: %s", len(proc.net_connections()))
        crawl_logger.info("fds: %s", proc.num_fds())

        crawl_logger.info("End of crawl to %s", u)

        # Close file handler
        for handler in crawl_logger.handlers:
            if isinstance(handler, logging.FileHandler):
                handler.close()
                crawl_logger.removeHandler(handler)
        crawl_logger.info("End(2) of crawl to %s", u)

        return (result, consent_data, cookies)


def run_domain_with_timeout(
    visit: SiteVisit,
    proc_list: ListProxy[BrowserProcess],
    browser_id: int,
    no_stdout: bool,
    crawl: Crawl,
    browser_params: Dict,
) -> Tuple[ConsentCrawlResult, List[ConsentData], List[Cookie]]:
    logger = logging.getLogger("cookieblock-consent-crawler")

    try:
        url = visit.site_url

        return run_domain(visit, proc_list, browser_id, no_stdout, crawl, browser_params)
    except (
        TimeoutError,
        WebDriverException,  # selenium
        urllib3.exceptions.TimeoutError,
        urllib3.exceptions.MaxRetryError,
        TimeoutExpired,
    ) as e:
        logger.warning(
            "Website %s had an exception (%s)", visit.site_url, type(e)
        )
        # This except block should store the websites for later to retry them

        with open("./retry_list.txt", "a", encoding="utf-8") as file:
            file.write(url)
            file.write("\n")

        return (
            ConsentCrawlResult(
                report=f"Failure: TimeoutError: {visit.site_url}",
                browser=visit.browser,
                visit=visit,
                cmp_type=CrawlerType.FAILED.value,
                crawl_state=CrawlState.LIBRARY_ERROR.value,
            ),
            [],
            [],
        )
    except Exception as e: # Currently unknown exceptions
        logger.error(
            "visit_id: %s Failure when crawling %s: %s",
            visit.visit_id,
            visit.site_url,
            str(e),
        )
        logger.exception(e)
        return (
            ConsentCrawlResult(
                report=f"Failure: {str(e)}",
                browser=visit.browser,
                visit=visit,
                cmp_type=CrawlerType.FAILED.value,
                crawl_state=CrawlState.LIBRARY_ERROR.value,
            ),
            [],
            [],
        )


def run_crawler() -> None:
    """
    This file contains all the main functionality and the entry point.
    """
    logger = logging.getLogger("cookieblock-consent-crawler")
    logger.propagate = False

    set_log_formatter(
        logger, "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    log_formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(processName)s: %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)
    logger.setLevel(logging.INFO)

    logger.info("Starting cookieblock-consent-crawler version %s", ver)

    # Has some WARNING messages that the pool is full
    logging.getLogger("urllib3").setLevel(logging.ERROR)

    if os.path.exists(chrome_profile_path):
        shutil.rmtree(chrome_profile_path)

    os.makedirs(chrome_profile_path, exist_ok=True)

    # Parse the input arguments
    parser = argparse.ArgumentParser()

    run_group = parser.add_mutually_exclusive_group(required=True)

    run_group.add_argument(
        "-f", "--file", help="Path to file containing one URL per line"
    )
    run_group.add_argument(
        "--launch-browser",
        help="Only launches the browser which allows modification of the current profile",
        action="store_true",
    )
    run_group.add_argument("-u", "--url", help="Url to crawl once")

    parser.add_argument(
        "-n",
        "--num_browsers",
        "--num-browsers",
        help="Number of browsers to use in parallel",
        dest="num_browsers",
    )
    parser.add_argument(
        "-d",
        "--use_db",
        "--use-db",
        help="Use specified database file to add rows to. Format: DATA_PATH/FILENAME.sqlite",
        dest="use_db",
    )
    parser.add_argument(
        "--profile_tar", help="Location of a tar file containing the browser profile"
    )
    parser.add_argument(
        "--no-headless",
        help="Start the browser with GUI (headless disabled)",
        action="store_true",
    )
    parser.add_argument(
        "--no-stdout",
        help="Do not print crawl results to stdout",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--num-subpages",
        help="Amount of links to follow when visiting a domain",
        default=10,
        type=int,
    )
    parser.add_argument(
        "--timeout",
        help="Amount of seconds to spend on one website",
        default=600,
        type=int,
    )

    args = parser.parse_args()

    file_crawllist = args.file

    no_stdout: bool = args.no_stdout

    if file_crawllist and (not os.path.exists(file_crawllist)):
        raise CrawlerException(f"File at {file_crawllist} does not exist")

    if args.num_browsers:
        num_browsers = int(args.num_browsers)
    else:
        num_browsers = 1

    if args.use_db:
        splitted = os.path.split(args.use_db)
        if len(splitted) != 2:
            raise CrawlerException("--use_db is wrongly formatted")
        data_path = splitted[0]
        database_file = splitted[1]
    else:
        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        data_path = "./collected_data"
        database_file = f"crawl_data_{now}.sqlite"

    headless = True
    if args.no_headless:
        headless = False

    if args.profile_tar:
        if not os.path.exists(args.profile_tar):
            raise CrawlerException(f"File at {args.profile_tar} does not exist")
        with tarfile.open(args.profile_tar, errorlevel=0) as tfile:
            tfile.extractall(".", filter="data")
    else:
        # Simply use existing
        pass

    os.makedirs(data_path, exist_ok=True)

    if args.launch_browser:
        with Chrome(
            seconds_before_processing_page=1,
            headless=False,  # Definitely start headfull
            use_temp=False,
            chrome_profile_path=chrome_profile_path,
            chromedriver_path=chromedriver_path,
            chrome_path=str(chrome_path),
            browser_id=-1,
            logger=logger,
        ) as browser:
            browser.load_page(URL.from_text("about:blank"))
            time.sleep(60 * 60)  # wait one hour
        return

    logger.info("Using data_path %s and file %s", data_path, database_file)

    # Connect to sqlite
    db_file = Path(data_path) / database_file
    create = not db_file.exists()
    initialize_base_db(
        db_url="sqlite:///" + str(db_file),
        create=create,
        alembic_root_dir=Path(__file__).parent / "crawler",
        pool_size=int(4 + num_browsers * 1.5),
    )
    logger.info("Finished database setup")

    if file_crawllist:
        with open(file_crawllist, "r", encoding="utf-8") as fo:
            urls = [x.strip() for x in fo.readlines()]
            urls = list(filter(lambda x: x.strip() != "", urls))
    else:
        assert args.url
        urls = [args.url]

    # Start
    num_subpages = int(args.num_subpages)
    timeout: int = args.timeout

    manager = multiprocessing.Manager()
    slist: ListProxy[BrowserProcess] = manager.list()

    # sort for having the same database as the original. TODO: remove?
    urls = list(sorted(urls))

    parameters = {
        "data_directory": str(data_path),
        "log_directory": str(log_dir),
        "database_name": str(database_file),
        "num_browsers": str(num_browsers),
    }
    task = start_task(
        browser_version="Chrome 126", manager_params=json.dumps(parameters)
    )
    task_id = task.task_id

    logger.info("Task: %s", task)

    browser_params = {
        "use_temp": True,
        "chrome_path": str(chrome_path.absolute()),
        "headless": headless,
    }

    # Add browser config to the database
    crawl = register_browser_config(
        task_id=task_id, browser_params=json.dumps(browser_params)
    )
    assert crawl.browser_id
    browser_id = crawl.browser_id

    # Debugging
    proc = psutil.Process()

    # Checks all running chrome processes.
    # Some processes that are spawned by this process launch the
    # chromedriver and chrome processes.
    # If these are killed the children of them (chrome/chromedriver)
    # are adopted by the container.
    # We iterate over all processes and kill those that are too old.
    def check() -> None:
        file = open("watcher.log", "a+")
        proc = psutil.Process()

        logger.info("Starting watcher for process: %s", proc)

        while True:
            current_time = datetime.now()

            print("Checking at ", current_time, file=file)

            # Kill browser processes that are older than X minutes
            # Iterate over all processes as each pebble worker
            # is its own process, and if he dies the chrome/chromedriver child
            # process is adopted by the docker container init process.
            x: psutil.Process

            for x in psutil.process_iter():
                if "chrome" not in x.name():
                    continue

                if x._create_time:
                    # Kill if older than four times the timeout
                    if (current_time.timestamp() - x.create_time()) >= timeout * 4:
                        print("Found too old process: ", x, file=file)
                        x.kill()
                else:
                    print("Process without starttime found: ", x, file=file)

            file.flush()
            children = proc.children(recursive=True)

            print("Number of all children: %s" % len(children), file=file)
            print("Number of direct children: %s" % len(proc.children()), file=file)
            time.sleep(10)

            print(file=file)
            file.flush()

    watcher = Process(target=check, daemon=True)
    watcher.start()

    visits: List[SiteVisit] = []

    # Prepare all visits in one thread
    with SessionLocal.begin() as session:
        for url in urls:
            visit = start_crawl(browser_id=browser_id, url=url)
            session.add(visit)
            session.refresh(visit)
            session.refresh(visit.browser)

            visits.append(visit)

    # Run a warmup browser to check if the current profile works
    # Also patches the executable for patching the chromedriver executable
    # to run concurrently and not need patching in each thread.
    # Also prints the browser version.

    logger.info("Starting warmup browser")
    null_logger = logging.getLogger("empty")
    null_logger.setLevel(logging.ERROR)

    with Chrome(
        seconds_before_processing_page=1,
        chrome_profile_path=chrome_profile_path,
        chromedriver_path=chromedriver_path,
        browser_id=browser_id,
        crawl=crawl,
        logger=null_logger,
        **browser_params,  # type: ignore
    ) as browser:
        _, content = browser.get_content("chrome://version")

        ver_pat = r"Chromium[\s]*\|[\s]*([\d\.]+)"
        match = re.search(ver_pat, content)

        if not match or not match.groups() or len(match.groups()) == 0:
            raise RuntimeError(f"Unable to detect chrome version in {content}")

        groups = match.groups()
        logger.info("Chrome version: %s", groups[0])

    if num_browsers == 1:
        with SessionLocal.begin() as session:
            for i, arg in enumerate(visits):
                crawl_result, cds, cookies = run_domain_with_timeout(
                    arg, slist, browser_id, no_stdout, crawl, browser_params
                )

                logger.info("Finished %s/%s", i + 1, len(visits))

                session.merge(crawl_result)

                for cd in cds:
                    session.merge(cd)
                for c in cookies:
                    session.merge(c)
        logger.info("%s crawls have finished.", len(visits))
    else:
        n_jobs = min(num_browsers, len(urls))

        with ProcessPool(max_workers=n_jobs, max_tasks=1) as pool:
            fut = pool.map(
                run_domain_with_timeout,
                visits,
                repeat(slist),
                repeat(browser_id),
                repeat(no_stdout),
                repeat(crawl),
                repeat(browser_params),
                timeout=timeout,
            )

            all_true = True
            it = fut.result()
            try:
                print("starting")
                with SessionLocal.begin() as session:
                    for i in tqdm(range(len(visits)), total=len(visits), desc="Crawling"):
                        try:
                            crawl_result, cds, cookies  = cast(Tuple[ConsentCrawlResult, List[ConsentData], List[Cookie]], next(it))

                            session.merge(crawl_result)

                            for cd in cds:
                                session.merge(cd)
                            for c in cookies:
                                session.merge(c)

                            # TODO: warn of unseccessfull crawls; if next_result[0].report
                            # logger.warning("Crawl to %s finished", visits[i])
                        except TimeoutError as e:
                            logger.warning("Crawl to %s froze", visits[i])

                            with open("./retry_list.txt", "a", encoding="utf-8") as file:
                                file.write(visits[i].site_url)
                                file.write("\n")
                            logger.error(e)
            except StopIteration:
                pass

            all_succeeded = all_true
        logger.info("All %s crawls have finished. Success: %s", len(visits), all_succeeded)

    logger.info("Number of open files: %s", len(proc.open_files()))
    for f in proc.open_files():
        logger.info("\tOpen file: %s", f)
    logger.info("Number of connections: %s", len(proc.net_connections()))
    logger.info("fds: %s", proc.num_fds())

    logger.info("CB-CCrawler has finished.")
    logging.shutdown()

    # Make sure all browsers are down
    for b in list(slist):
        try:
            browser_proc = psutil.Process(b.pid)

            if browser_proc.is_running():
                browser_proc.terminate()
        except NoSuchProcess:
            pass

    watcher.kill()
    input("Press ENTER to quit this python process")


def main() -> None:
    """
    Call run_crawler and if any exception occurs we'll stop with code 1.
    Exit code 1 means that there is something fundamentally wrong and
    cb-cc should not be called again.
    """

    logger = logging.getLogger("cookieblock-consent-crawler")
    try:
        run_crawler()

    # pylint: disable=broad-exception-caught
    except CrawlerException as e:
        logger.error(e)
        sys.exit(1)
    except Exception as e:
        print(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
