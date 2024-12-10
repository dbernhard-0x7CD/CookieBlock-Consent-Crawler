#!/bin/bash

# Installs chrome to the current directory or the first argument.

cdir=${1:-"$(pwd)"}
echo "Installing to ${cdir}"

CRAWLER_CHROME_DRIVER_PATH="${cdir}/chromedriver"
CRAWLER_CHROME_PATH="${cdir}/chrome/"
CRAWLER_CHROME_PROFILE_PATH="${cdir}/chrome_profile"

mkdir -p ${CRAWLER_CHROME_DRIVER_PATH}
mkdir -p ${CRAWLER_CHROME_PATH}
mkdir -p ${CRAWLER_CHROME_PROFILE_PATH}

# Chrome version 130; Also change version in run_consent_crawl.py if upgrading
cd /tmp/ || exit
wget -q -O chrome-linux.zip 'https://www.googleapis.com/download/storage/v1/b/chromium-browser-snapshots/o/Linux_x64%2F1356007%2Fchrome-linux.zip?generation=1726513315891763&alt=media'
unzip -o chrome-linux.zip
mv -f chrome-linux/* "${CRAWLER_CHROME_PATH}"

wget -q -O chromedriver.zip 'https://www.googleapis.com/download/storage/v1/b/chromium-browser-snapshots/o/Linux_x64%2F1356007%2Fchromedriver_linux64.zip?generation=1726513319019044&alt=media'

unzip -o chromedriver.zip
mv -f chromedriver_linux64/chromedriver "${CRAWLER_CHROME_DRIVER_PATH}"

rm /tmp/chrome-linux.zip
rm /tmp/chromedriver.zip
