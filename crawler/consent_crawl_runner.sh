#!/bin/bash

# Function to kill all chrome processes
kill_chrome_processes() {
    pkill -f chrome

    # Check for zombie chrome processes
    zombie_count=$(ps aux | grep '[c]hrome' | awk '{ if ($8 == "Z") print $0 }' | wc -l)
    echo "Number of zombie chrome processes: $zombie_count"
}

# Print all arguments
echo "Arguments: $@"

# Check if --batch-size argument is provided
batch_size=0
for arg in "$@"; do
    if [[ $arg == --batch-size=* || $arg == --batch_size=* ]]; then
        batch_size=${arg#*=}
        break
    fi
done

# If batch-size is provided, compute number of batches
if [[ $batch_size -gt 0 ]]; then
    url_file=""
    for arg in "$@"; do
        if [[ $arg == -f=* || $arg == --file=* ]]; then
            url_file=${arg#*=}
            break
        fi
    done

    if [[ -z $url_file ]]; then
        echo "Error: URL file not specified. Use -f or --file to specify the file."
        exit 1
    fi
    total_urls=$(wc -l < "$url_file")
    num_batches=$(( (total_urls + batch_size - 1) / batch_size ))

    # And check if -d, --use_db, or --use-db argument is provided
    db_arg=""
    for arg in "$@"; do
        if [[ $arg == -d=* || $arg == --use_db=* || $arg == --use-db=* ]]; then
            db_arg=$arg
            break
        fi
    done

    # If no DB argument is provided, generate a new DB name
    if [[ -z $db_arg ]]; then
        timestamp=$(date +%Y%m%d_%H%M%S)
        db_name="./collected_data/crawl_data_${timestamp}.sqlite"
        db_arg="--use_db=$db_name"
        set -- "$@" "$db_arg"
    fi
else
    num_batches=1
fi

# Print updated arguments
echo "Updated arguments: $@"

# Run the script in batches
for ((i=1; i<=num_batches; i++)); do
    echo "Running batch $i of $num_batches"
    if [[ $i -eq 1 ]]; then
        python ./crawler/run_consent_crawl_uc.py "$@"
    else
        python ./crawler/run_consent_crawl_uc.py "$@" --resume
    fi

    exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        echo "Iteration $i failed with exit code $exit_code. Stopping."
        exit $exit_code
    fi

    # Kill all chrome processes after each iteration
    kill_chrome_processes
done
