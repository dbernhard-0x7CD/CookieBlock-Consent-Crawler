#!/bin/bash

import argparse
import os
import sys
import re
from logging import Logger
from typing import List, Optional, Tuple
import logging
from datetime import datetime, timezone
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
from multiprocessing import Queue, Process
from queue import Empty
from psutil import TimeoutExpired, NoSuchProcess
from multiprocessing.managers import ListProxy
from pathos import multiprocessing
import multiprocessing as pmultiprocessing

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
        "%(asctime)s %(levelname)s %(name)s %(processName)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)
    logger.setLevel(logging.INFO)

    ver = version("cookieblock-consent-crawler")
    logger.info("Starting cookieblock-consent-crawler version %s", ver)

    # Has some WARNING messages that the pool is full
    logging.getLogger("urllib3").setLevel(logging.ERROR)

    log_dir = Path("./logs")
    log_dir.mkdir(exist_ok=True)

    chrome_profile_path = Path("./chrome_profile/")
    chromedriver_path = Path("./chromedriver/chromedriver")
    chrome_path = Path("./chrome/")

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
    run_group.add_argument("--url", help="Url to crawl once")

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
        type=int
    )
    parser.add_argument(
        "--timeout",
        help="Amount of seconds to spend on one website",
        default=600,
        type=int
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
            urls = list(filter(lambda x: not x.strip() == "", urls))
    else:
        assert args.url
        urls = [args.url]

    # Start
    num_subpages = int(args.num_subpages)

    # sort for having the same database as the original. TODO: remove?
    urls = list(sorted(urls))

    parameters = {
        "data_directory": str(data_path),
        "log_directory": str(log_dir),
        "database_name": str(database_file),
        "num_browsers": str(num_browsers)
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
    crawl = register_browser_config(task_id=task_id, browser_params=json.dumps(browser_params))
    assert crawl.browser_id
    browser_id = crawl.browser_id

    # Debugging
    proc = psutil.Process()

    def run_domain(visit: SiteVisit, process_result: Queue) -> None:
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
            **browser_params, # type: ignore
        ) as browser:
            u = URL.from_text(url)

            browser.load_page(u)
            crawl_logger.info("Loaded url %s", u)

            # bot mitigation
            crawl_logger.info("Calling bot mitigation")
            browser.bot_mitigation(max_sleep_seconds=1)

            crawler_type, crawler_state, consent_data, result = browser.crawl_cmps(visit=visit)

            if crawler_type == CrawlerType.FAILED or crawler_state != CrawlState.SUCCESS:
                browser.collect_cookies(visit=visit)
                crawl_logger.info("Aborted crawl crawl to %s [crawl_type: %s; crawler_state: %s]", u, crawler_type.name, crawler_state.name)

                # Close file handler
                for handler in crawl_logger.handlers:
                    if isinstance(handler, logging.FileHandler):
                        handler.close()
                process_result.put((result, consent_data, []))
                return

            crawl_logger.info(
                "Ran %s CMP: %s (Found %s consents)", crawler_type.name, crawler_state.name, len(consent_data)
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

            # To detect resource leakage
            crawl_logger.info("Number of open files: %s", len(proc.open_files()))
            for f in proc.open_files():
                crawl_logger.info("\tOpen file: %s", f)
            crawl_logger.info("Number of connections: %s", len(proc.net_connections()))
            crawl_logger.info("fds: %s", proc.num_fds())

            process_result.put((result, consent_data, cookies))
            
            crawl_logger.info("End of crawl to %s", u)

            # Close file handler
            for handler in crawl_logger.handlers:
                if isinstance(handler, logging.FileHandler):
                    handler.close()
                    crawl_logger.removeHandler(handler)

    def run_domain_with_timeout(visit: SiteVisit, timeout: int, slist) -> bool:
        q: Queue[Tuple[ConsentCrawlResult, ]] = Queue(maxsize=1)

        p = Process(target=run_domain, args=(visit, q))

        try:
            url = visit.site_url

            p.start()
            p.join(timeout=timeout)

            try:
                ps_p = psutil.Process(p.pid)
                logger.info("PID: %s", p.pid)

                ps_p = psutil.Process(p.pid)
                if ps_p.is_running():
                    logger.info("Terminating process %s", p)
                    logger.info("Terminating process %s", ps_p)
                    
                    for cp in ps_p.children(True):
                        cp.terminate()
                    ps_p.terminate()
                    logger.info("Sent signals")
            except NoSuchProcess:
                pass

            slist.append(q.get(timeout=1))
            return True
        except (Empty, TimeoutError, urllib3.exceptions.TimeoutError, urllib3.exceptions.MaxRetryError, TimeoutExpired) as e:
            logger.warning("Website %s had a timeout (%s)", visit.site_url, type(e))
            # This except block should store the websites for later to retry them

            try:
                ps_p = psutil.Process(p.pid)
                if ps_p.is_alive():
                    ps_p.terminate()
            except NoSuchProcess:
                pass

            with open("./retry_list.txt", "a", encoding="utf-8") as file:
                file.write(url)

            slist.append((ConsentCrawlResult(report=f"TimeoutError: {visit.site_url}", browser=visit.browser, visit=visit, cmp_type=CrawlerType.FAILED.value, crawl_state=CrawlState.LIBRARY_ERROR.value), [], []))
        except Exception as e:
            logger.error("visit_id: %s Failure when crawling %s: %s", visit.visit_id, visit.site_url, str(e))
            logger.exception(e)
            slist.append((ConsentCrawlResult(report=f"Failure: {str(e)}", browser=visit.browser, visit=visit, cmp_type=CrawlerType.FAILED.value, crawl_state=CrawlState.LIBRARY_ERROR.value), [], []))
        return False

    pqdm_args: List[SiteVisit] = []

    # Prepare all visits in one thread
    with SessionLocal.begin() as session:
        for url in urls:
            visit = start_crawl(browser_id=browser_id, url=url)
            session.add(visit)
            session.refresh(visit)
            session.refresh(visit.browser)
            
            pqdm_args.append(visit)

    timeout: int = args.timeout
    manager = pmultiprocessing.Manager()
    slist: ListProxy[Tuple[ConsentCrawlResult, List[ConsentData], List[Cookie]]] = manager.list()

    if num_browsers == 1:
        res = []
        for i, arg in enumerate(pqdm_args):
            run_domain_with_timeout(arg, timeout, slist)
            logger.info("Finished %s/%s", i+1, len(pqdm_args))
    else:
        # Start one instance to patch the chromedriver executable and
        # later start multiple which all do _not_ need to patch the
        # chromedriver executable because it is already patched.
        with Chrome(
            seconds_before_processing_page=1,
            chrome_profile_path=chrome_profile_path,
            chromedriver_path=chromedriver_path,
            browser_id=browser_id,
            crawl=crawl,
            logger=logger,
            **browser_params, # type: ignore
        ) as browser:
            state, content = browser.get_content("chrome://version")
            
            ver_pat = r"(Chromium[\s]*\|[\s]*[\d\.]+)"
            match = re.search(ver_pat, content)
            
            if not match:
                raise Exception(f"Unable to detect chrome version in {content}")

            groups = match.groups()
            
            if groups and len(groups) > 0:
                logger.info("Chrome version: %s", groups[0])
            else:
                logger.error("Unable to detect chrome version in %s", content)
                raise Exception(f"Unable to detect chrome version in {content}")

        n_jobs = min(num_browsers, len(urls))

        pool = multiprocessing.ProcessPool(nodes=n_jobs)
 
        res = pool.map(
            lambda x: run_domain_with_timeout(x, timeout, slist),
            pqdm_args
        )

        all(res)
    logger.info("All %s crawls have finished", len(pqdm_args))

    logger.info("Number of open files: %s", len(proc.open_files()))
    for f in proc.open_files():
        logger.info("\tOpen file: %s", f)
    logger.info("Number of connections: %s", len(proc.net_connections()))
    logger.info("fds: %s", proc.num_fds())
    
    # Store data in database
    with SessionLocal.begin() as session:
        for result, cds, cookies in tqdm(slist):
            session.merge(result)

            for cd in cds:
                session.merge(cd)
            for c in cookies:
                session.merge(c)

    logger.info("CB-CCrawler has finished.")
    logging.shutdown()


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
