#!/bin/bash

# Installs chrome to the current directory.
# The crawler has to be run from the same directory.

echo "Installing to $(pwd)"

cdir=$(pwd)
CRAWLER_CHROME_DRIVER_PATH="${cdir}/chromedriver"
CRAWLER_CHROME_PATH="${cdir}/chrome/"
CRAWLER_CHROME_PROFILE_PATH="${cdir}/chrome_profile"
CRAWLER_BROWSER="chrome"

mkdir -p ${CRAWLER_CHROME_DRIVER_PATH}
mkdir -p ${CRAWLER_CHROME_PATH}
mkdir -p ${CRAWLER_CHROME_PROFILE_PATH}

# Chrome version 122; Also change version in run_consent_crawl.py if upgrading
cd /tmp/
wget -q -O chrome.zip 'https://www.googleapis.com/download/storage/v1/b/chromium-browser-snapshots/o/Linux_x64%2F1250580%2Fchrome-linux.zip?generation=1705972829597946&alt=media'
unzip chrome.zip
mv chrome-linux/* "${CRAWLER_CHROME_PATH}"

wget -q -O chromedriver.zip 'https://www.googleapis.com/download/storage/v1/b/chromium-browser-snapshots/o/Linux_x64%2F1250580%2Fchromedriver_linux64.zip?generation=1705972833274207&alt=media'

unzip chromedriver.zip
mv chromedriver_linux64/chromedriver "${CRAWLER_CHROME_DRIVER_PATH}"

rm /tmp/chrome.zip
rm /tmp/chromedriver.zip