#!/bin/bash

import argparse
import os
import sys
import logging

logger = logging.getLogger("cookieblock-consent-crawler")


class CrawlerException(Exception):
    """
    Indicates that the crawler has some unrecoverable error and should stop crawling.
    """


def set_log_formatter(fmt: str, date_format: str) -> None:
    """
    Sets the given format for the root logger and all its handlers.
    The handlers may be to a file or to console. It also ensures that
    at least one console handler exists.
    """
    root_logger = logging.getLogger()
    log_formatter = logging.Formatter(
        fmt=fmt,
        datefmt=date_format,
    )
    # Set the log_formatter from above for all and ensure
    # that at lest one handler is present
    for handler in root_logger.handlers:
        handler.setFormatter(log_formatter)
    if len(root_logger.handlers) == 0:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(log_formatter)
        root_logger.addHandler(console_handler)


def run_crawler() -> None:
    """
    This file contains all the main functionality and the entry point.
    """

    # ver = version("cookieblock-consent-crawler")

    # Parse the input arguments
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-f", "--file", help="Path to file containing one URL per line", required=True
    )
    parser.add_argument(
        "-n",
        "--num_browsers",
        help="Number of browsers to use in parallel",
        dest="num_browsers",
    )

    args = parser.parse_args()

    file = args.file

    if not os.path.exists(file):
        raise CrawlerException(f"File at {file} does not exist")

    if args.num_browsers:
        raise CrawlerException("--num_browsers Not yet implemented")

    logger.info("Finished")


def main() -> None:
    """
    Call run_crawler and if any exception occurs we'll stop with code 1.
    Exit code 1 means that there is something fundamentally wrong and
    cb-cc should not be called again.
    """

    set_log_formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d:%H:%M:%S"
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(
        logging.INFO
    )  # Only until we've loaded the config and set the desired LOG_LEVEL
    logger.setLevel(logging.INFO)

    try:
        run_crawler()

    # pylint: disable=broad-exception-caught
    except CrawlerException as e:
        logger.error(e)
        sys.exit(1)
    except Exception as e:
        logger.error("%s", str(e))
        logger.exception(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
