#!/bin/bash

# Installs chrome to the current directory or the first argument.
# Requires wget and rsync to be installed

cdir=${1:-"$(pwd)"}
echo "Installing to ${cdir}"

CRAWLER_CHROME_DRIVER_PATH="${cdir}/chromedriver"
CRAWLER_CHROME_PATH="${cdir}/chrome/"
CRAWLER_CHROME_PROFILE_PATH="${cdir}/chrome_profile"

mkdir -p ${CRAWLER_CHROME_DRIVER_PATH}
mkdir -p ${CRAWLER_CHROME_PATH}
mkdir -p ${CRAWLER_CHROME_PROFILE_PATH}

# Chrome version 131; Also change version in run_consent_crawl.py if upgrading
cd /tmp/ || exit
wget -q -O chrome-linux.zip 'https://www.googleapis.com/download/storage/v1/b/chromium-browser-snapshots/o/Linux_x64%2F1368524%2Fchrome-linux.zip?generation=1728952252940053&alt=media'
unzip -o chrome-linux.zip
rsync -arh --delete chrome-linux/* "${CRAWLER_CHROME_PATH}"

wget -q -O chromedriver.zip 'https://www.googleapis.com/download/storage/v1/b/chromium-browser-snapshots/o/Linux_x64%2F1368524%2Fchromedriver_linux64.zip?generation=1728952255946631&alt=media'

unzip -o chromedriver.zip
rsync -arh --delete chromedriver_linux64/chromedriver "${CRAWLER_CHROME_DRIVER_PATH}"

rm /tmp/chrome-linux.zip
rm /tmp/chromedriver.zip
