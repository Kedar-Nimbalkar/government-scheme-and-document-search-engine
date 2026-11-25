# Sprint 2 E1 process and recovery verification

## Functional status

| Requirement | Result |
| --- | --- |
| FR-01 | The sequential PDF and CSV CLI and positional index fixture still pass. |
| FR-02 | Spawn drives real parser processes through bounded queues. Worker exit and timeout tests show replacement, bounded retries, and continued indexing of good jobs. |
| FR-03 | One writer publishes complete documents with monotonic passage IDs, immutable checksummed segments, and atomic manifest replacement. PostgreSQL tests cover lease fencing and reconciliation before and after publication. Explicit heap compaction preserves postings and statistics. |
| NFR-03 | Tested process crashes, restart searchability, duplicate replay, checksum validation, and concurrent reads during append. Power-loss behavior remains untested. |
| NFR-04 | Measured sequential, two-worker and four-worker runs on the same synthetic corpus. The full 1/2/4/8-worker scaling target remains unverified. |
| NFR-06 | A worker exited after emitting part of a document. Healthy jobs remained searchable, and the failed document exhausted its bounded retry budget without publishing partial postings. |

## Commands and checks

- `python -m pytest -q` with `E1_TEST_DATABASE_URL` set to a temporary standalone
  PostgreSQL instance passed 23 tests. These included filesystem and
  database writer locks, worker kill and timeout, corrupt input, spool
  size rejection, both manifest crash points, bounded durable retries,
  duplicate replay, concurrent reads, and compaction.
- `python -m engines.e1 --workers 2 --queue-items 1 --chunk-bytes 64` with the
  synthetic PDF and CSV inputs passed. The snapshot matched the sequential
  baseline at three passages and 13 tokens.
- `python -m pip check` reported no broken requirements.
- `npm.cmd test` passed all five contract and migration tests.

The PostgreSQL test schemas were isolated. The Windows sandbox requires
running outside it for spawn and loopback PostgreSQL. See the
[E1 runbook](../engines/e1/README.md) for inputs and environment variables.

## Benchmark

`python -m engines.e1.benchmark` used 48 synthetic documents with 24 digital
pages each. Each run started from an empty index. Wall time includes worker
startup, indexing, publication, and shutdown. The queue columns are exact
peak item counts for a worker's task queue, the shared result queue, the
writer inbox, and the writer acknowledgement queue.

| Mode | Wall time | Docs/sec | Indexed / failed | Task / result / writer / ack peak | Writer PID | Worker PIDs |
| --- | ---: | ---: | ---: | --- | ---: | --- |
| Sequential | 1.0292 s | 46.639 | 48 / 0 | 0 / 0 / 0 / 0 | 25860 | — |
| 2 workers | 2.3463 s | 20.458 | 48 / 0 | 1 / 1 / 1 / 1 | 18620 | 30624, 31508 |
| 4 workers | 2.3284 s | 20.615 | 48 / 0 | 1 / 3 / 4 / 2 | 1696 | 26828, 32300, 27676, 21940 |

The supervisor PID was 25860. All three snapshots had the same logical
fingerprint over document/page text, token spans, positional postings,
and corpus statistics. The sequential run was fastest on this corpus.
It publishes one batch segment, and the concurrent path commits one
complete document at a time. This measurement covers both parsing and
publication overhead. Full compaction optimization and a 1/2/4/8 scaling
study remain pending.
