import argparse
import json
from dataclasses import replace
from .config import Settings
from .storage import ActivityStore
from .sync import sync_activities

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("list")
    sync = sub.add_parser("sync")
    sync.add_argument("--max-pages", type=int, default=0)
    sync.add_argument("--download-fit", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "sync" and args.download_fit is not None:
        settings = replace(settings, download_fit=args.download_fit)
    store = ActivityStore(settings.data_dir)
    if args.command == "status":
        result = store.status()
    elif args.command == "list":
        result = store.list_activities()
    else:
        result = sync_activities(settings, store, args.max_pages or None)
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
