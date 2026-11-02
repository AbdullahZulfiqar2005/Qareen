# Review implementation map

This branch implements the reliability foundation and a usable proposal-only workflow. It is not a claim that every possible defect is gone, or that every optional roadmap idea is production-ready.

| Review finding | Change and remaining boundary |
|---|---|
| F01 arbitrary diagnostic shell | Removed the execution agent. Chat and incident assistance produce text only. |
| F02 unauthenticated localhost APIs | Separate admin/ingest tokens, Host/Origin validation, request validation, bounded concurrency. Go dashboard proxies the caller's token. |
| F03 dashboard injection | Rebuilt dynamic rendering with text nodes; CSP; source enum validation. |
| F04 sensitive logs/cloud context | Removed event-body logs; redaction before storage and generation; cloud source opt-in, private modes. Encryption remains a separate project. |
| F05 PID ownership | systemd user lifecycle and private flock locks; no global PID signaling or import effects. |
| F06–F10 deadlock, skipped/duplicate vectors, stale deletion, unload races | Replaced mutable event FAISS state with SQLite-owned chunk vectors and durable enrichment states. No native library unloading. Cascading deletion and replay tombstones. |
| F11 RemoteEmbeddings contract | Actual LangChain Embeddings subclass, shared authenticated client. |
| F12–F14 write-before-embedding, backfill, blocking calls | Persistence precedes enrichment. Retry metadata, bounded batches/timeouts, warm serialized model; embedding work outside DB lock. |
| F15–F17 temporal retrieval and misleading scores | Calendar start/end bounds, timezone/daypart support, filtered hybrid retrieval, day-wide sampling with omission counts, non-probability scores. |
| F18 empty personal history blocks docs | Optional `chat.py --documentation` exposes separately labeled documentation even without personal evidence. No claim that documentation is a past user action. |
| F19 timestamp ambiguity | UTC source timestamps and separate ingestion time; explicit legacy timezone migration. Unknown browser message times labeled as observation times. |
| F20–F21 unsent drafts and cross-contact deduplication | No input/click-to-send capture. ChatGPT rendered IDs; WhatsApp explicit selections; source IDs include conversation/account scope. |
| F22 observation described as action | Explicit action/direction fields; window titles are observations, not proof of chat activity or command success. |
| F23 WhatsApp heuristics | Removed two-minute cutoff and inferred direction/recipient logic. One-to-one message selection requires user-supplied contact; no autonomous WhatsApp transport. |
| F24–F25 streaming/observer incompleteness | Revision upserts; scan all matching message nodes on child/text mutations and visibility changes; later streaming revisions remain eligible. |
| F26 Gmail DOM snippets | Official Gmail API sync, MIME extraction, persistent history cursors; no attachment fetching or unsent-draft import. |
| F27 Meet captions | Opt-in partial observations with bounded observer state and per-node revisions; not advertised as a complete transcript. |
| F28 journal skipped errors | Journal cursors advance only after event persistence or durable queueing; IDs deduplicate replay. |
| F29 shell history parsing/rotation | Persistent offsets and inode checks, partial-line retention, Bash timestamps, plain/extended Zsh, bounded multiline records. Overwritten/unflushed history cannot be recovered. |
| F30 Firefox profile/fallback | Profile INI/config override, direct read-only SQLite, source visit times and persistent cursor; removed unsafe copy fallback. |
| F31 file debounce/overflow | Debounce by path and event mask; bounded map; overflow reporting; rearm deleted/recreated watched roots. Watching remains shallow. |
| F32 monitor stale log/EOF | Live runtime log selection or explicit path; idle flush for short incidents; rotation/truncation handling; no action execution. |
| F33 chat follow-ups | Bounded local session question context; model output is never treated as observed history. |
| F34 packaging/reproducibility | Actual user units, installer/uninstaller, configurable paths, pinned direct dependencies split by optional capability, CPU model install, Go-version-file CI. |
| F35 hidden API/CLI errors | Explicit HTTP/error handling, CLI nonzero status, corrected Rofi status propagation; removed unused legacy API/generation paths. |
| F36 weak tests/import effects | Actual production store/API/workflow tests, Go collector regressions, Node production observer fixture, import-safe service. |
| F37 binary/legacy encoding | Known vector byte length checked before JSON interpretation; migration marks old vectors for enrichment. |
| F38 modifiers/dashboard filters | Left/right modifier state tracked separately; all declared event sources and exact app filters displayed. Physical key names are still a layout-limited approximation. |

## Added workflow and portfolio features

- Durable leased background jobs, retry states and error cooldowns.
- Cloud budget, token/latency counters, source policies, retained preferences and approved edit examples.
- Evidence-linked proposal inbox with revision-bound approval, expiry and stale-context checks.
- Gmail account/thread preflight, draft-only export and ambiguous-result reconciliation.
- Pause, retention, export, selective deletion, cascaded feedback/vector deletion.
- Synthetic demo and reproducible 10k/100k-capable evaluation script, architecture and privacy documentation, CI.

## Deliberate follow-up work

- Live Gmail OAuth/refresh/draft reconciliation against a dedicated test mailbox; Google verification for distribution.
- Real desktop sessions and Firefox release smoke tests, selectors maintained as upstream pages change; other prototype website adapters are not enabled.
- Application encryption and secure backup/restore UI; signed desktop/extension releases and a chosen open-source license.
- Semantic retrieval and grounded-answer quality evaluation on a consented, labeled dataset; realistic LLM cost and latency measurements. The synthetic exact-topic benchmark is not a substitute.
- Larger-history vector indexing after measuring the bounded 5k-candidate design; richer aggregates and full conversation pagination.
- Verified problem → action → outcome annotations and explicit user feedback about success. An approved plan does not establish that it was executed successfully.
- Provider-independent local generation can be added behind ModelProvider. The shipped generator remains Groq with explicit cloud consent; moving it into a background worker does not remove inference cost.
