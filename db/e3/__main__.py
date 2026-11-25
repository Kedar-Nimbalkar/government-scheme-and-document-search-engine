"""E3 PostgreSQL migration, development seed, and curated PDF import."""

import argparse
import json
import logging
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from .corpus import Rejected, import_verified, load_catalog, scan

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@contextmanager
def connect(schema=None):
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url, autocommit=True, connect_timeout=5) as db:
        if schema is not None:
            db.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        yield db


def local_dev(schema):
    if not re.fullmatch(r"team06_dev_[a-z0-9_]{1,40}", schema):
        raise ValueError("Choose a new schema named team06_dev_<name>")
    config = conninfo_to_dict(os.environ["DATABASE_URL"])
    if config.get("host") not in {"127.0.0.1", "::1", "localhost"} or config.get("hostaddr") not in {
        None, "127.0.0.1", "::1",
    }:
        raise ValueError("Development commands require an explicit loopback DATABASE_URL")


def migrate_dev(schema):
    local_dev(schema)
    with connect() as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        db.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        for migration in sorted(MIGRATIONS.glob("*.sql")):
            db.execute(migration.read_text(encoding="utf-8"), prepare=False)
        db.execute("CREATE TABLE team06_dev_marker (created_at timestamptz DEFAULT now())")


def seed(db, env):
    import bcrypt

    roles = (("ADMIN", "admin"), ("CITIZEN", "citizen"), ("DEPARTMENT", "department"))
    accounts = []
    for prefix, role in roles:
        username = env.get(f"E3_TEST_{prefix}_USERNAME")
        password = env.get(f"E3_TEST_{prefix}_PASSWORD")
        if username is None and password is None:
            continue
        if not username or not password or not username.strip() or len(password.encode("utf-8")) > 72:
            raise ValueError(f"Supply a username and a nonempty bcrypt password (up to 72 bytes) for {prefix}")
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
        accounts.append((username.strip(), password_hash, role))
    if not accounts:
        raise ValueError("No test credential pairs supplied")
    if len({account[0] for account in accounts}) != len(accounts):
        raise ValueError("Use a different test username for each role")
    inserted = 0
    with db.transaction():
        for username, password_hash, role in accounts:
            dept_id = None
            if role == "department":
                db.execute("""
                    INSERT INTO department (dept_name, level) VALUES ('Synthetic Test Department','State')
                    ON CONFLICT DO NOTHING
                """)
                dept_id = db.execute("""
                    SELECT dept_id FROM department WHERE dept_name='Synthetic Test Department' AND level='State'
                """).fetchone()[0]
            inserted += db.execute("""
                INSERT INTO app_user (username,password_hash,role,dept_id) VALUES (%s,%s,%s,%s)
                ON CONFLICT (username) DO NOTHING
            """, (username, password_hash, role, dept_id)).rowcount
    return {"created": inserted, "existing": len(accounts) - inserted}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("migrate-dev", "seed"):
        sub.add_parser(name).add_argument("--schema", required=True)
    for name in ("inspect", "import"):
        action = sub.add_parser(name)
        action.add_argument("--source-root", type=Path, required=True)
        action.add_argument("--catalog", type=Path, required=True)
        action.add_argument("--report", type=Path, required=True)
        if name == "import":
            action.add_argument("--pdf-root", type=Path, default=os.getenv("PDF_ROOT"))
            action.add_argument("--schema")
            action.add_argument("--limit", type=int)
        else:
            action.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    logging.getLogger("pypdf").setLevel(logging.CRITICAL + 1)
    try:
        if args.command == "migrate-dev":
            migrate_dev(args.schema)
            print(json.dumps({"dev_schema": args.schema, "migrations": "applied"}))
            return 0
        if args.command == "seed":
            local_dev(args.schema)
            with connect(args.schema) as db:
                if db.execute("SELECT to_regclass('team06_dev_marker')").fetchone()[0] is None:
                    raise ValueError("Seed requires a schema created by migrate-dev")
                print(json.dumps(seed(db, os.environ)))
            return 0
        if not args.source_root.is_dir():
            raise ValueError("Source root must be an existing directory")
        dataset, entries = load_catalog(args.catalog)
        if args.command == "inspect":
            if not 1 <= args.workers <= 8:
                raise ValueError("Inspection requires between 1 and 8 workers")
            result = scan(args.source_root, dataset, entries, args.workers)
        else:
            if args.pdf_root is None or (args.limit is not None and args.limit < 1):
                raise ValueError("Set PDF_ROOT or --pdf-root and use a positive optional limit")
            with connect(args.schema) as db:
                result = import_verified(db, args.source_root, args.pdf_root, dataset, entries, args.limit)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in result.items() if key != "records"}))
        return 0
    except KeyError:
        print("E3 requires DATABASE_URL", file=sys.stderr)
    except (Rejected, ValueError) as error:
        print(f"E3: {error}", file=sys.stderr)
    except (OSError, psycopg.Error, ImportError) as error:
        print(f"E3 operation failed ({type(error).__name__})", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
