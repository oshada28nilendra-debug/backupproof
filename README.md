# BackupProof

BackupProof is a small, read-only restore verifier for S3-compatible object storage. It is for cloud engineers who need evidence that a test backup can be downloaded into a fresh isolated directory and matches a checksum manifest—not merely that objects exist in a bucket.

It downloads one documented manifest, restores listed objects into temporary staging, reports missing/corrupted/unexpected objects, and promotes the staged directory only when every expected file passes size and SHA-256 verification. It never uploads, changes or deletes bucket objects.

## Setup

Python 3.11+; no paid infrastructure is required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
python evaluate.py
```

Real S3-compatible read-only restore:

```bash
export AWS_ACCESS_KEY_ID='read-only-key'
export AWS_SECRET_ACCESS_KEY='read-only-secret'
python backupproof.py \
  --endpoint-url http://127.0.0.1:9000 \
  --bucket test-backups \
  --prefix nightly/2026-10-14 \
  --destination outputs/restored-2026-10-14 \
  --report outputs/restore-report.json
```

Omit `--endpoint-url` for AWS S3. Standard boto3 environment/config credential resolution applies. Use credentials restricted to `s3:GetObject` and `s3:ListBucket` for the chosen test prefix. The destination and report must not exist; this prevents accidental overwrite. The report must be outside the destination.

`sample_backup.py` writes a **synthetic local fixture only**; it does not contact S3. The automated integration tests and `evaluate.py` use Moto's mocked S3 API and are clearly not evidence of compatibility with a particular deployed S3 provider. Point the CLI at a disposable MinIO/LocalStack bucket for a real local integration check.

## Manifest contract

Object `<prefix>/manifest.json` must be UTF-8 JSON:

```json
{
  "schema_version": 1,
  "files": [
    {
      "path": "data/users.csv",
      "size": 28,
      "sha256": "64-lowercase-hex-characters"
    }
  ]
}
```

Paths are relative POSIX paths. Absolute paths, backslashes, NULs, `.`/`..`, duplicates, invalid digests and invalid sizes are rejected before restoration. Limits are 1,000 files, 64 MiB per file, 256 MiB total, and 2 MiB for the manifest. These deliberately narrow bounds reduce denial-of-service and disk-exhaustion risk in this MVP.

## Architecture and safety

1. Create a unique temporary directory under the destination's parent.
2. Fetch and validate the versioned manifest.
3. List the prefix to report unexpected objects.
4. Stream each expected object with a hard size bound, then calculate SHA-256 and size.
5. Write only verified bytes into the safe relative target inside staging.
6. Write the JSON report outside staging.
7. Atomically rename staging to the requested destination only if all files verify; otherwise remove staging.

Manifest parsing or download errors also clean staging. The tool never follows paths supplied by object keys: destinations come only from validated manifest paths. Existing destination/report paths are refused. It does not provision buckets, exercise production backups, or run an application-level recovery.

### Publication hardening

The publication build additionally rejects Windows drive/alternate-stream syntax, reserved device names, trailing spaces/dots, case-insensitive duplicate names and file/directory conflicts. Reads are bounded by each expected file size and response streams are always closed. Report creation is exclusive; dangling output symlinks are rejected. Run under a trusted local parent directory without concurrent writers: portable directory rename is not a security boundary against a malicious process racing promotion.

## Evaluation

[The committed evaluation](reports/mock-evaluation.json) runs three deterministic mocked scenarios against the same synthetic three-file backup:

- intact: all three files verify and staging is promoted;
- corrupted: one checksum/size failure is reported and nothing is promoted;
- missing: one missing object is reported and nothing is promoted.

The `expected_behavior_passed` field is calculated, not handwritten. Timings are machine-specific. Tests additionally cover traversal, malformed/empty/duplicate manifests, invalid hashes, cleanup after manifest failure, unexpected objects and overwrite refusal.

SHA-256 equality establishes byte integrity relative to the supplied manifest. It does **not** establish freshness, authenticity, semantic correctness, database consistency, malware safety, retention-policy compliance, or successful application recovery. A maliciously replaced object and manifest can still agree; production use should authenticate/version the manifest and pin object versions. Unexpected objects are informational and do not make a listed restore fail.

## Interview explanation

“I treated backup success as a restore-and-verify problem. BackupProof stages objects in an isolated directory, validates a strict path-safe manifest, checks size and SHA-256, reports missing/corrupt/unexpected data, and only promotes a complete verified restore. I tested S3 behavior with a clearly labelled Moto integration and documented why checksum success is not the same as application recovery.”

## License

MIT. Synthetic fixtures are original and dedicated under CC0-1.0. Dependencies retain their upstream licenses.
