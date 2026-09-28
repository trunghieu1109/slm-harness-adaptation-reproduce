"""Remove duplicated Codex plugin caches from benchmark rollout results."""

import argparse
import shutil
from pathlib import Path


CACHE_GLOB = "*/*/rollouts/*/*_codex_home_*/.tmp/plugins"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove per-rollout Codex plugin caches under results/."
    )
    parser.add_argument("--results-root", type=Path, default=Path("results"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    plugin_caches = sorted(
        path
        for path in args.results_root.resolve().glob(CACHE_GLOB)
        if path.is_dir()
    )
    for plugin_cache in plugin_caches:
        print(plugin_cache)
        if args.execute:
            shutil.rmtree(plugin_cache)

    action = "Removed" if args.execute else "Found"
    print(f"{action} {len(plugin_caches)} Codex plugin cache(s).")


if __name__ == "__main__":
    main()
