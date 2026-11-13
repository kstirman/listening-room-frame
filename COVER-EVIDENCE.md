# Private cover evidence

Optional preservation keeps accepted Qobuz covers after they leave the recent-album collage. It adds no network requests and does not identify tracks or invent metadata. Spotify artwork is excluded.

Set both `cover_evidence_mount` to an existing canonical mountpoint and `cover_evidence_root` to a canonical directory beneath it in your private bridge configuration. The default is disabled. The bridge preserves surviving history covers on startup and newly accepted covers thereafter. It must be the sole archive writer.

Images are normalized cached JPEGs, stored by SHA256 with separate exact-album manifests and retained image versions. This is private listening evidence; do not commit the archive or configuration.

If the mount is unavailable, existing unarchived covers are retained. Above a 16 MiB cache budget, new images use one transient display file and state reports `unavailable_cache_budget_reached`; those new images are not guaranteed permanent. Pending retained images retry when another cover is processed. No mount or audio operation is attempted.

## Backup and restore

The ordinary metadata-only catalog backup does not include covers. Create a separate companion ZIP with `cover_evidence.Archive(root, mount).backup(output_zip)`, choosing an output directory on the data disk. This reads immutable objects and atomic manifests, verifies hashes and stages beside the output. Protect it with the same private backup destination as the catalog.

Restore into a separate private directory. Verify each entry against `SHA256.json`, then call `Archive.verify_manifest(restored_root, manifest)` for every album manifest. Merge immutable objects and retained versions carefully; never overwrite newer manifests blindly. The archive cannot recover covers deleted before this feature was enabled.
