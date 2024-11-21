#!/bin/bash

# Script used to merge all json files given via arguments.
# Remove the -c to get pretty json

jq -cs add $@


