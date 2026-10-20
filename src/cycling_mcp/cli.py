import argparse
import json
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
    args = parser.parse_args()
    settings = Settings.from_env()
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
