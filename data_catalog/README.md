# Source catalogue

Every production input must have one row in `sources.csv`. Record its provider, exact dataset
version, licence, source URL, acquisition date, native CRS/resolution, local path and SHA-256
checksum. `UNKNOWN_REQUIRES_CONFIRMATION` is deliberate: replace it only from authoritative
metadata. The pipeline warns about gaps but does not invent provenance or reject otherwise valid
data solely for incomplete descriptive metadata.

