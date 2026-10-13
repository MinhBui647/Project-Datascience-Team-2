"""Stage 6c - Run the whole data preparation with one command.

Usage:  python "Data integration and preprocessing/run_prepare.py"             (download new data, then prepare)
        python "Data integration and preprocessing/run_prepare.py" --offline   (only data already in raw_data)
        python "Data integration and preprocessing/run_prepare.py" --rebuild   (recreate matches.csv from scratch)

Steps: update_current_season (pull raw data, stages 1-3, matches.csv) -> features -> validate -> data_dictionary.
Exit code 1 when the quality checks fail.
"""
import sys

import data_dictionary
import features
import update_current_season
import validate


def main():
    update_current_season.main()   # reads --offline / --rebuild / --now from the command line
    print("\n== Stage 5: features")
    features.main()
    print("\n== Stage 6: quality checks")
    ok = validate.main()
    data_dictionary.main()
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
