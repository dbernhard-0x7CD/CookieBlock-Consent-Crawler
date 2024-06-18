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

from hyperlink import URL


from crawler.browser import Chrome
from crawler.database import initialize_base_db
from crawler.utils import logger


class CrawlerException(Exception):
    """
    Indicates that the crawler has some unrecoverable error and should stop crawling.
    """


def run_crawler() -> None:
    """
    This file contains all the main functionality and the entry point.
    """

    ver = version("cookieblock-consent-crawler")
    logger.info(f"Starting cookieblock-consent-crawler version {ver}")

    chrome_profile_path = "./chrome_profile/"
    chromedriver_path = Path("./chromedriver/chromedriver")
    chrome_path = Path("./chrome/")

    os.makedirs(chrome_profile_path, exist_ok=True)

    # Parse the input arguments
    parser = argparse.ArgumentParser()

    parser.add_argument("-f", "--file", help="Path to file containing one URL per line")
    parser.add_argument(
        "-n",
        "--num_browsers",
        help="Number of browsers to use in parallel",
        dest="num_browsers",
    )
    parser.add_argument(
        "-d",
        "--use_db",
        help="Use specified database file to add rows to. Format: DATA_PATH/FILENAME.sqlite",
        dest="use_db",
    )
    parser.add_argument(
        "--launch-browser",
        help="Only launches the browser which allows modification of the current profile",
        action="store_true",
    )

    args = parser.parse_args()

    file_crawllist = args.file

    if file_crawllist and (not os.path.exists(file_crawllist)):
        raise CrawlerException(f"File at {file_crawllist} does not exist")

    if args.num_browsers:
        raise CrawlerException("--num_browsers Not yet implemented")

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

    headless = False

    os.makedirs(data_path, exist_ok=True)

    if args.launch_browser:
        # Definitely start headfull
        headless = False
        with Chrome(
            seconds_before_processing_page=1,
            headless=headless,
            use_temp=False,
            chrome_profile_path=chrome_profile_path,
            chromedriver_path=chromedriver_path,
            chrome_path=chrome_path,
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

    logger.info("finished database setup")

    chrome_profile_path = "./chrome_profile/"
    chromedriver_path = Path("./chromedriver/chromedriver")
    chrome_path = Path("./chrome/")
    with open(file_crawllist, "r", encoding="utf-8") as fo:
        lines = fo.readlines()

        for l in [x.strip() for x in lines]:
            logging.info("working on %s", l)

            with Chrome(
                seconds_before_processing_page=1,
                headless=False,
                use_temp=False,
                chrome_profile_path=chrome_profile_path,
                chromedriver_path=chromedriver_path,
                chrome_path=chrome_path,
            ) as browser:
                u = URL.from_text(l)

                browser.load_page(u)

                time.sleep(1)

                browser.collect_cookies()

                time.sleep(60)

                logging.info("Loaded url %s", u)

    logger.info("Finished")


def main() -> None:
    """
    Call run_crawler and if any exception occurs we'll stop with code 1.
    Exit code 1 means that there is something fundamentally wrong and
    cb-cc should not be called again.
    """

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
