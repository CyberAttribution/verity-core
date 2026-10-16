"""Verify a telemetry export against an independently retained checkpoint."""
import argparse
from pathlib import Path
from verity_core.json_safety import bounded_json_loads
from . import telemetry


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('record');parser.add_argument('--checkpoint',required=True)
    args=parser.parse_args()
    try:
        record=bounded_json_loads(Path(args.record).read_text())
        anchor=bounded_json_loads(Path(args.checkpoint).read_text())
        if not telemetry.verify(record,anchor):raise ValueError('Checkpoint mismatch')
    except (ValueError,TypeError,KeyError,OSError,AttributeError):
        print('FAILED: telemetry integrity or retained checkpoint mismatch.');return 1
    print('VERIFIED: telemetry matches the retained checkpoint. This does not establish identity, authorization, evidence truth or complete capture.')
    return 0


if __name__=='__main__':raise SystemExit(main())
