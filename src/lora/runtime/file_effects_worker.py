from __future__ import annotations

import argparse
from typing import Sequence

from .file_effects import process_file_effect_batch_file


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process one persisted Lora file-effect batch.")
    parser.add_argument("--batch-file", required=True)
    args = parser.parse_args(argv)
    process_file_effect_batch_file(args.batch_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
