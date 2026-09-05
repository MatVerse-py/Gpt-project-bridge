# Forensic Evidence Policy v1

Status: implemented candidate on PR #23. Merge remains manual/HOLD.

## 1. Scope

This layer adds forensic preservation and analysis semantics to the Bridge without claiming courtroom admissibility, authorship, publication, or scientific truth.

Forensic evidence is modeled by predicate. The additional authority domains are:

- `integrity`: whether the bytes/representation can be shown to remain identical to an anchored reference;
- `custody`: whether the sequence of handling events is internally continuous and tamper-evident.

The full forensic vector is:

`content | version | authorship | publication | timestamp | execution | integrity | custody`

Weights are policy weights, not probabilities.

## 2. Core rules

### 2.1 Read-only access is not a write blocker

A software read stream does not prove that the original medium was protected from writes. The Bridge may record `write_blocker_claimed`, but high acquisition-method confidence requires separate evidence such as a verified hardware/software write-block record.

`READ_ONLY_SOFTWARE_ACCESS != HARDWARE_WRITE_BLOCKING`

### 2.2 Hash-chain commitment is not a digital signature

Custody events are chained by SHA-256 over canonical event payloads. This makes mutation detectable inside the chain but does not authenticate the human collector.

`HASH_CHAIN != SIGNATURE != IDENTITY`

Collector identity or non-repudiation requires a separate verified signature/identity layer.

### 2.3 Hash equality proves byte equality, not origin

A matching SHA-256 between an observed source and a logical copy supports byte-for-byte equality at the time of observation. It does not by itself prove who created the source, when it was originally created, or whether the acquisition device was write-blocked.

### 2.4 Filesystem timestamps are contextual observations

`mtime`, `ctime`, `atime`, birth time, MFT or inode timestamps are mutable, filesystem-specific, and affected by acquisition/mount behavior. They never receive maximum timestamp authority solely because they exist.

Higher timestamp authority requires verified filesystem/acquisition context.

### 2.5 Entropy is not a steganography detector

Shannon entropy is stored only as a computed statistic. High entropy may be consistent with compression, encryption, random data, media encoding, or hidden data; it cannot decide among them without additional tests.

`HIGH_ENTROPY != ENCRYPTED != COMPRESSED != STEGANOGRAPHY`

### 2.6 Carved files have weaker origin semantics

Magic-byte or structure-based recovery can establish recovered bytes and their location/offset in a parent image. It does not automatically establish the original filename, directory, owner, timestamps, or filesystem context.

A carved artifact therefore receives lower `content`/`timestamp` authority unless those relations are independently reconstructed.

## 3. Forensic artifact kinds

- `ORIGINAL_FILE`
- `LOGICAL_COPY`
- `FORENSIC_IMAGE`
- `MEMORY_SNAPSHOT`
- `CARVED_ARTIFACT`
- `FILE_SYSTEM_METADATA`
- `HASH_VERIFICATION`
- `TIMELINE_OBSERVATION`
- `ENTROPY_OBSERVATION`

## 4. States

- `FORENSIC_VERIFIED`: expected hash matches and the custody hash chain verifies;
- `INTEGRITY_VERIFIED`: expected hash matches, custody not established;
- `CUSTODY_VERIFIED`: custody chain verifies but no independent expected hash was supplied;
- `PARTIAL`: evidence exists but stronger predicates remain unresolved;
- `HOLD_ACQUISITION_METHOD`: a strong acquisition method is claimed but not verified;
- `BLOCK_TAMPERED`: expected hash mismatch or custody-chain mutation.

`FORENSIC_VERIFIED` is a Bridge policy state. It is not a legal conclusion of admissibility.

## 5. Evidence receipt

Every built forensic object produces the existing `matverse.evidence-receipt.v1` commitment with:

- artifact hash;
- state;
- integrity-verification result;
- custody-verification result;
- custody chain head;
- authority vector;
- forensic object hash.

The receipt commits to the decision but does not duplicate the full evidence payload.

## 6. Relationship to SourceEvidence

Forensic evidence is intentionally not promoted to DOI/publication/authorship authority. It complements the source-resolution layer:

`SourceEvidence -> semantic/source identity`

`ForensicEvidence -> byte integrity + custody + acquisition observations`

A future canonical mapping may reference a forensic receipt from a SourceEvidence root. Until that mapping is explicitly added and tested, forensic objects remain a parallel evidence plane rather than silently masquerading as `API_METADATA`, `GIT_COMMIT`, or another source representation.

## 7. Explicit non-claims

This v1 does not:

- image raw disks or memory devices;
- claim hardware write blocking from ordinary filesystem reads;
- claim digital signatures from SHA-256 event hashes;
- infer steganography from entropy alone;
- treat MD5 as the primary integrity primitive;
- treat filesystem timestamps as trusted chronology without context;
- claim legal admissibility;
- convert forensic integrity into publication or scientific-validity authority.
