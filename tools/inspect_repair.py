"""Print a read-only supervised-repair diagnosis; never restores files."""
import argparse
import json
from verity_core.release_update import inspect_repair


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--expected-previous-digest")
    args = parser.parse_args()
    print(json.dumps(inspect_repair(args.history, args.target,
                                   expected_previous_digest=args.expected_previous_digest), indent=2))


if __name__ == "__main__":
    main()
