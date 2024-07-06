#!/bin/bash

import argparse
import os
import sys
import logging
from datetime import datetime
from pathlib import Path
from importlib.metadata import version
import time
import traceback
import tarfile
import shutil
from pqdm.threads import pqdm
import threading
import random

from hyperlink import URL


from crawler.browser import Chrome
from crawler.database import initialize_base_db, SiteVisit, SessionLocal, start_task, Crawl, start_crawl
from crawler.utils import set_log_formatter, is_on_same_domain



class CrawlerException(Exception):
    """
    Indicates that the crawler has some unrecoverable error and should stop crawling.
    """


def run_crawler() -> None:
    """
    This file contains all the main functionality and the entry point.
    """
    logger = logging.getLogger("cookieblock-consent-crawler")

    set_log_formatter(
        logger,
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d:%H:%M:%S"
    )
    logger.setLevel(logging.INFO)

    ver = version("cookieblock-consent-crawler")
    logger.info(f"Starting cookieblock-consent-crawler version {ver}")

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
        "--num-subpages",
        help="Amount of links to follow when visiting a domain",
        default=10
    )

    args = parser.parse_args()

    file_crawllist = args.file

    if file_crawllist and (not os.path.exists(file_crawllist)):
        raise CrawlerException(f"File at {file_crawllist} does not exist")

    if args.num_browsers:
        # TODO implemet this
        num_browsers = int(args.num_browsers)
        # raise CrawlerException("--num_browsers Not yet implemented")
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
        logger = logging.getLogger()

        with Chrome(
            seconds_before_processing_page=1,
            headless=False, # Definitely start headfull
            use_temp=False,
            chrome_profile_path=chrome_profile_path,
            chromedriver_path=chromedriver_path,
            chrome_path=chrome_path,
            crawl=None,
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
    num_subpages = args.num_subpages

    task = start_task(browser_version="Chrome 122")
    task_id = task.task_id

    logger.info("Task: %s", task)
    
    for i in range(num_browsers):
        browser_logger = logging.getLogger(f"browser-{i}")

        file_handler = logging.FileHandler(log_dir / f"browser_{i}.log")
        log_formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d:%H:%M:%S"
        )
        file_handler.setFormatter(log_formatter)

        browser_logger.addHandler(file_handler)
        browser_logger.addHandler(logger.handlers[0])
        browser_logger.setLevel(logging.INFO)


    def run_domain(url: str) -> bool:
        tid = threading.get_native_id() % num_browsers
        browser_logger = logging.getLogger(f"browser-{tid}")

        logger.info("Working on %s [thread: %s]", url, tid)

        crawl, visit = start_crawl(task_id=task_id, browser_params="TODO", url=url)

        with Chrome(
            seconds_before_processing_page=1,
            headless=headless,
            use_temp=True,
            chrome_profile_path=chrome_profile_path,
            chromedriver_path=chromedriver_path,
            chrome_path=chrome_path,
            crawl=crawl,
            logger=browser_logger,
        ) as browser:
            u = URL.from_text(url)

            browser.load_page(u)
            browser_logger.info("Loaded url %s", u)
            
            # bot mitigation
            browser_logger.info("Calling bot mitigation")
            browser.bot_mitigation(max_sleep_seconds=1)

            browser.crawl_cmps(visit=visit)

            browser.load_page(u)

            # visit subpages
            links = list(filter(lambda x: is_on_same_domain(x.url.to_text(), url), browser.get_links()))
            logger.info("Found %s links", len(links))
            
            chosen = random.choices(links, k=min(num_subpages, len(links)))
            for i, l in enumerate(chosen):
                browser_logger.info("Subvisiting [%i]: %s", i, l.url.to_text())
                browser.load_page(l.url)
                browser.bot_mitigation(max_sleep_seconds=0.5, num_mouse_moves=1)

            browser.collect_cookies(visit=visit)

            return True

    res = pqdm(urls, run_domain, n_jobs=num_browsers, total=len(urls), exception_behaviour="immediate")

    logger.info("Result is %s", all(res))
    logger.info("CB-CCrawler has finished.")


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
