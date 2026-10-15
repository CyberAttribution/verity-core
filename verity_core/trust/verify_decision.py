"""Verify decision exports without running Verity or its database.

python -m verity_core.trust.verify_decision run.decision.json [--checkpoint retained.json]
"""
import argparse
import json
from pathlib import Path
from verity_core.json_safety import bounded_json_loads
from . import decision


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def load(path):
    if Path(path).stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Decision export exceeds 4 MiB')
    return bounded_json_loads(Path(path).read_bytes(), object_pairs_hook=_pairs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('export')
    parser.add_argument('--checkpoint')
    args = parser.parse_args()
    try:
        result = decision.verify(load(args.export), load(args.checkpoint) if args.checkpoint else None)
    except (ValueError, OSError, TypeError) as e:
        result = {'verified': False, 'issues': [str(e)], 'limits': decision.LIMITS}
    print(json.dumps(result, indent=2))
    return 0 if result['verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
