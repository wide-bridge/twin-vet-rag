"""Restore the prepared knowledge dump into the empty Neon DB, then verify it. No model calls.

    python -m scripts.neon_restore --check      # connect + show target state only (nothing written)
    python -m scripts.neon_restore              # verify dump checksum -> pg_restore -> verify
    python -m scripts.neon_restore --verify     # verification only (after a restore)

Connection strings are read from the git-ignored file .env.neon (NEON_DIRECT_URL, NEON_POOLED_URL, copied
from Neon Console > Connect). They are never printed; output shows only the host. pg_restore runs with the
PostgreSQL 16 client inside the local pgvector container (same version that made the dump), connecting with
the *direct* URL; the app later uses the *pooled* URL.

Safety: refuses to restore into a DB that already has tables in public (no --clean, ever).
Verification: row counts vs the dump manifest, pgvector + HNSW index present, alembic head, embedding
dimensions, and a nearest-neighbour self-match on sample chunks through the HNSW index.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.neon"
DUMP_DIR = ROOT / "data_output" / "neon"
CONTAINER = "twinvet-care-db-1"
HEAD = "9d4e2b7a1c35"  # current alembic head; after restoring an older dump run `alembic upgrade head`


def load_env() -> dict[str, str]:
    if not ENV_FILE.exists():
        sys.exit(f"{ENV_FILE.name} not found: copy the Neon connection strings into it first")
    env = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("NEON_DIRECT_URL", "NEON_POOLED_URL"):
        if not env.get(k, "").startswith(("postgresql://", "postgres://")):
            sys.exit(f"{k} is missing or not a postgresql:// URL in {ENV_FILE.name}")
    if "-pooler" in urlsplit(env["NEON_DIRECT_URL"]).hostname:
        sys.exit("NEON_DIRECT_URL points at the pooler: use the connection string with pooling turned off")
    return env


def host(url: str) -> str:
    return urlsplit(url).hostname or "?"


def latest_dump() -> tuple[Path, dict]:
    manifests = sorted(DUMP_DIR.glob("*.dump.manifest.json"))
    if not manifests:
        sys.exit(f"no dump manifest in {DUMP_DIR}")
    m = json.loads(manifests[-1].read_text(encoding="utf-8"))
    return DUMP_DIR / m["dump"], m


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest().upper()


def public_tables(url: str) -> list[str]:
    with psycopg.connect(url, connect_timeout=30) as c:
        return [r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")]


def server_info(url: str) -> str:
    with psycopg.connect(url, connect_timeout=30) as c:
        v = c.execute("SHOW server_version").fetchone()[0]
        ext = c.execute("SELECT default_version FROM pg_available_extensions WHERE name='vector'").fetchone()
        db = c.execute("SELECT current_database()").fetchone()[0]
    return f"db={db} server={v} pgvector_available={ext[0] if ext else 'no'}"


def restore(url: str, dump: Path) -> None:
    inside = f"/tmp/{dump.name}"
    subprocess.run(["docker", "cp", str(dump), f"{CONTAINER}:{inside}"], check=True)
    try:
        # URL goes in through the environment, not the command line
        script = (f'pg_restore --no-owner --no-acl --exit-on-error --verbose --dbname="$TVC_TARGET" {inside} '
                  "> /tmp/tvc_restore.log 2>&1; rc=$?; "
                  "grep -E 'creating|processing|error' /tmp/tvc_restore.log | sed -E 's#postgres(ql)?://[^ ]*#<url>#g'; "
                  "rm -f /tmp/tvc_restore.log; exit $rc")
        r = subprocess.run(["docker", "exec", "-e", "TVC_TARGET", CONTAINER, "sh", "-c", script],
                           env={**os.environ, "TVC_TARGET": url}, text=True, capture_output=True)
        print(r.stdout[-4000:])
        if r.returncode != 0 or "error" in r.stdout.lower():
            sys.exit("pg_restore failed (see above); fix before starting the app")
    finally:
        subprocess.run(["docker", "exec", CONTAINER, "rm", "-f", inside], check=False)


def verify(url: str, manifest: dict) -> bool:
    ok = True
    want = manifest["source_counts"]
    with psycopg.connect(url, connect_timeout=30) as c:
        q = lambda s: c.execute(s).fetchone()  # noqa: E731
        got = {
            "document": q("SELECT count(*) FROM document")[0],
            "active_documents": q("SELECT count(*) FROM document WHERE status='active' AND active_version_id IS NOT NULL")[0],
            "document_version": q("SELECT count(*) FROM document_version")[0],
            "chunk": q("SELECT count(*) FROM chunk")[0],
            "embedding": q("SELECT count(embedding) FROM chunk")[0],
            "ingestion_run": q("SELECT count(*) FROM ingestion_run")[0],
        }
        for k, v in got.items():
            mark = "ok" if v == want.get(k) else "MISMATCH"
            ok &= mark == "ok"
            print(f"  {k:<18} {v:>7}  (source {want.get(k)})  {mark}")
        head = q("SELECT version_num FROM alembic_version")[0]
        vec = q("SELECT extversion FROM pg_extension WHERE extname='vector'")
        idx = q("SELECT indexdef FROM pg_indexes WHERE indexname='ix_chunk_embedding_hnsw'")
        dims = q("SELECT vector_dims(embedding) FROM chunk WHERE embedding IS NOT NULL LIMIT 1")
        empty = {t: q(f"SELECT count(*) FROM {t}")[0] for t in
                 ("session", "consultation", "message", "answer_run", "citation", "feedback")}
        print(f"  alembic            {head}  {'ok' if head == HEAD else 'MISMATCH (expected ' + HEAD + ')'}")
        print(f"  pgvector           {vec[0] if vec else 'missing'}")
        print(f"  hnsw index         {'present' if idx and 'hnsw' in idx[0] else 'MISSING'}")
        print(f"  embedding dims     {dims[0] if dims else '-'}")
        print(f"  consultation data  {empty}  {'ok (empty)' if not any(empty.values()) else 'NOT EMPTY'}")
        ok &= head == HEAD and bool(vec) and bool(idx and "hnsw" in idx[0]) and bool(dims and dims[0] == 1536)
        ok &= not any(empty.values())
        # nearest neighbour of a stored vector must be that same chunk, through the HNSW index
        c.execute("SET hnsw.ef_search = 40")
        samples = c.execute("SELECT id FROM chunk WHERE embedding IS NOT NULL ORDER BY id LIMIT 5").fetchall()
        hits = 0
        for (cid,) in samples:
            top = c.execute("SELECT id FROM chunk ORDER BY embedding <=> (SELECT embedding FROM chunk WHERE id=%s) LIMIT 1",
                            (cid,)).fetchone()[0]
            hits += top == cid
        plan = " ".join(r[0] for r in c.execute(
            "EXPLAIN SELECT id FROM chunk ORDER BY embedding <=> (SELECT embedding FROM chunk LIMIT 1) LIMIT 6").fetchall())
        print(f"  self-match search  {hits}/{len(samples)}  index used: {'yes' if 'ix_chunk_embedding_hnsw' in plan else 'no'}")
        ok &= hits == len(samples)
        size = q("SELECT pg_size_pretty(pg_database_size(current_database()))")[0]
        print(f"  database size      {size}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true")
    g.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    env = load_env()
    url = env["NEON_DIRECT_URL"]
    print(f"target (direct): {host(url)}   app (pooled): {host(env['NEON_POOLED_URL'])}")
    print("  " + server_info(url))
    dump, manifest = latest_dump()
    tables = public_tables(url)
    print(f"  public tables now: {len(tables)} {tables[:12]}")
    if args.check:
        return 0
    if not args.verify:
        if tables:
            sys.exit("target DB is not empty: refusing to restore (no --clean). Use a fresh database/branch.")
        digest = sha256(dump)
        if digest != manifest["sha256"].upper():
            sys.exit("dump checksum does not match the manifest")
        print(f"dump {dump.name} sha256 ok; restoring (HNSW index build on a small Neon compute can take minutes)...")
        restore(url, dump)
    print("verification:")
    ok = verify(url, manifest)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
