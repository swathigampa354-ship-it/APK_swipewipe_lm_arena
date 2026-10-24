# Swipewipe static-recovery investigation — report

**Subject:** `com.afternoonproducts.swipewipe` ("Swipewipe") on Android/Termux
**Report date:** 2026-10-24 (Asia/Calcutta)
**Method:** static analysis only. Nothing from the target APK was executed; no reinstall, no
modification, no runtime instrumentation.
**Status:** *partial by evidence.* One class of 52 was ever decompiled by the previous run, and
this report documents both what was recovered and exactly where that run stopped.

---

## 1. Why this report exists

An earlier reverse-engineering session on this phone produced a work directory
(`~/rea/swipewipe/`) plus an REA evidence store (`~/.local/share/rea/`). Both were deleted from
the device, so a device-wide search (whole Termux home, `find`, package paths, OpenCode's session
database) found **nothing**. A snapshot of that lost work was later supplied by the user as a
Filebin package, `swipewipe-recon-main.zip`. This report is the assessment of that snapshot,
cross-checked against the live device.

An important correction of record: the initial conclusion *"there was no Swipewipe investigation on
this device"* was wrong in substance. It was correct only about the device's *current* filesystem
state. The work had happened; only its traces were removed.

## 2. Integrity and provenance verification

| Check | Recovered record | Live device | Result |
|---|---|---|---|
| SHA-256 of the analyzed `base.apk` | `cfe3d01eed1351737b0894ea2cba85bea8888dd8038b5cfe71cbc4fc36380e22` | `sha256sum` of the installed `base.apk` — identical | **same build** |
| ZIP central-directory entries | `occurrence_count: 1763`, `node_count: 1601`, `edge_count: 1762` | `unzip -l` → 1762 entries | consistent |
| Package identity | `com.afternoonproducts.swipewipe`, `~/rea/swipewipe/base.apk` | v`2.19.00` / code `21900`, label `Swipewipe` | match |
| `network_security_rules.xml` | cleartext permitted for `10.0.2.2` and `0.0.0.0`, `includeSubdomains` | `aapt dump xmltree` on the live APK → `cleartextTrafficPermitted=0xffffffff`, 2 `domain` nodes | **real, reproducible** |
| `strings.xml` provenance | `app_name = Swipewipe`; `streak`, `cooldown`, `similar_images` | present in live `resources.arsc` | match |
| Package version | minSdk 32 / targetSdk 36 (from `aapt`) | `platformBuildVersionCode=36`, `compileSdkVersion=36` | match |
| `com.mwm.swipewipe` (in the driver's `--include-packages`) | assumed second app | `pm list packages` → not installed | **it is the upstream/vendor SDK namespace, not a second package** |

Package-level integrity of the snapshot itself: 517,921 bytes, sha256 `6e47bfbc…` (full value in
`sources/package-manifest.json`), `unzip -t` clean, 19 entries under one top directory, no `..`
or absolute paths.

## 3. Toolchain recovered from the snapshot

* **`jadx-headless-mcp 0.7.1`** (`jadx-headless-mcp-0.7.1-all.jar`, normally provisioned under
  `~/.local/share/rea/android/`) as an MCP stdio server.
* Driven by `tools/swipewipe_mcp.sh` through a FIFO (`$PREFIX/tmp/opencode/jadxmcp.in`) with a
  one-hour holder process — i.e. the *first real execution* of the REA jadx toolchain found in
  this whole line of work.
* Launch flags: `--apk ~/rea/swipewipe/base.apk --resources none
  --include-packages com.afternoonproducts.swipewipe,com.mwm.swipewipe --threads 2
  --decompile-timeout-ms 90000`, JVM `-Xmx2048m`.
* Separately, the **`rea` CLI** produced `static/swipewipe_artifact.json`
  (`provider: rea-artifact-graph`, `operation: inspect_artifact`).
* 26 MCP tools were available; the catalogue is preserved in `sources/mcp-tools-catalogue.txt`.

## 4. What the previous run actually did (reconstructed from its own logs)

1. Whole-APK `jadx` on-device → killed at **16,854 / 46,970 classes (35 %)** — see
   `sources/jadx-run-log.txt`. Full-APK decompilation is not viable on this phone.
2. Driver script first launch → **`NO_INIT_RESPONSE`** (`sources/relaunch-log.txt`).
   **Quantified root cause:** the script polled for the init response at most `18 × 5 s = 90 s`,
   while the measured `load_duration_ms` was **94,040 ms**. The server was healthy; the driver gave
   up four seconds early.
3. Relaunch with a longer wait → `load_apk` succeeded: `state LOADED`, `class_count 53` filtered
   from `raw_class_count 63,031`, `resource_count 1`.
4. `list_classes` → **all 52 in-scope class names** (`sources/class-inventory.json`).
5. One malformed call → **`isError: "class_name is required"`** (`sources/mcp-load-probe-error.json`).
6. Corrected call → **`SwipeApp.java` decompiled, 432 lines / 21,946 chars.**
7. **Stop.** No further calls survive. The saved transcript holds ids 1, 13, 14, 15, 16 only; ids 2
   and 3–12 are absent because the script truncates `server.out` on every launch — so more calls
   were made than were preserved.

**Coverage achieved: 1 of 52 classes (≈2 %).** Never called: `get_app_info`,
`get_android_manifest`, `get_smali_of_class`, every xref tool, every `search_*` tool,
`get_strings` / `get_resource_file` / `list_resource_files` (the last three are also blocked by
`--resources none`, which is why `resource_count: 1` and why jadx logged
`App 'R' class not found … put all resources ids into com.afternoonproducts.swipewipe.R`).

## 5. jadx defects recorded in the snapshot (must be routed around, not retried)

From `sources/jadx-server-error-digest.txt` (47,910-byte `server.err` digest):

* `JadxOverflowException in pass: RegionMakerVisitor` ×3, including
  **`com.afternoonproducts.swipewipe.one_drive_data_source.OneDriveDataSourceManagerImpl.deleteItemsBatch(List<String>, …)`**
  — the most interesting method in the app is one of the three that jadx could not restructure.
* **38 ×** `Code restructure failed: missing block: B:…, code lost` concentrated in `ss2.b/e`,
  `wv2.b/c/e/f/l`, `zd0.a` (Compose-heavy code).
* `Found duplicated class: kotlin.Metadata, count: 2, sources: assets/audience_network/classes2.dex,
  classes6.dex` — Facebook Audience Network ships its own dex inside `assets/`.

Consequence: for those classes use `get_smali_of_class` / `get_method_smali` instead of Java output.

## 6. Findings about the app

**Build:** v2.19.00 (code 21900), minSdk 32, targetSdk 36, `base.apk` 92,066,971 bytes + 3 split
APKs, 1,762 entries, `classes.dex`–`classes12.dex`. No `index.android.bundle`, no
`flutter_assets` → native Kotlin/Java (Jetpack Compose), **not** React Native or Flutter.
R8-obfuscated support code (`xk1`, `wv2`, `zd0`, `ss2`) but **application package names are not
obfuscated**, and **no packer** was detected (unlike the GoTall sample analysed in the same
session, which used PairIP) — which is why class-name-scoped decompilation is cheap here.

**Architecture (from the 52 class names + the recovered `SwipeApp` structure):** a local-first
media triage app.

| area | evidence |
|---|---|
| storage | `data.AppDatabase` + `AppDatabase_Impl` (Room); models: `BookmarkModel`, `CloudMediaItemModel`, `CloudMonthAggregateModel`, `CompletedPeriodModel`, `LocalDecisionReconstructionModel`, `MediaDecisionModel`, `SimilarImageModel`, `StatsModel` |
| cloud | **Microsoft OneDrive only**: `one_drive_data_source.*` (`OneDriveApiService`, `BatchRequest/Response(Item)`, `OneDriveDataSourceManager(Impl)`, `OneDriveMappersKt`, photo/video/location/thumbnail facets) + `cloud_provider_sync.CloudProviderSyncCoroutineWorker` |
| media pipeline | `image_grouping.ImageGroupingWorker` (on-device similar-image grouping), `photo_compress_batch.PhotoCompressBatchWorker` + `…CancelReceiver`, `glide_config.SwipewipeAppGlideModule` |
| interaction | `swipeable_card.SwipeableCardState`, `main_activity.MainActivity` (+ Hilt), `splash_activity.SplashActivity`, `LocalNotificationTrampolineActivity` |
| reminders | `push_local.OtdReminderWorker`, `push_local.SimilarImagesReminderWorker`, `push_local_schedule.AlarmReceiver`, `boot.BootReceiver`; **5 notification channels**: `Swipewipe`, `streak`, `cooldown`, `similar_images`, `media_tools_progress` |
| monetization / growth | `com.mwm.sdk.*` white-label kit: `adskit.AdsKit` (AppLovin **MAX** + ironSource ILRD), `google_ump_consent`, `interstitial`, `accountkit2.AccountManager`, `appkits.helper.{account,billing,dynamicscreen,event,firebase,installreferrer,ledger}`, `abtest`, `eventkit.EventLogger`, `sessionskit`, `userfeedback.{DislikeSurvey,ReviewPopup}` |
| telemetry | `FirebaseAnalytics`, `FirebaseCrashlytics` |
| hardening/obfuscation | `safedk_SwipeApp_onCreate_<hash>` method name in `SwipeApp` — string/method protection from a commercial hardening vendor |

**Security-relevant observations (verified on the live APK):**

1. **Cleartext HTTP is permitted for `10.0.2.2` and for `0.0.0.0` with `includeSubdomains`** in a
   *release* build (`res/xml/network_security_rules.xml`). This is an emulator/dev-backend leftover
   that disables TLS enforcement for an address range covering the device-local network. This is the
   strongest single finding in the recovered set.
2. `allowBackup=false`, `fullBackupContent=false`, `dataExtractionRules` set → the app closes the
   adb-backup extraction path (good for user privacy; it also means *our* data-side analysis is
   blocked without root — see §8).
3. Advertising, analytics and crash telemetry SDKs are present in a "privacy-first" media app;
   consent is mediated by Google UMP (`google_ump_consent`) rather than by the app alone.
4. App-shipped identifiers (`google_api_key`, `facebook_app_id`, `gcm_defaultSenderId`) are present
   in `resources.arsc`. They are public by construction (they ship in the APK), but they are
   **deliberately not reproduced here or in this repository**: unrestricted Firebase/Google API
   keys are an abuse surface for the vendor, and a public repository is not the place for them.

## 7. What is *not* concluded here

* **`raw_result: null`, `hypotheses: []`, `contradictions: []`, `unexplored_branches: []`,
  `next_probes: []`** in the REA evidence bundle, with `coverage: complete-within-substeps` and
  `substeps_completed: 1`. In plain terms: REA recorded an **inventory**, not an analysis. Nothing
  was promoted to a finding, so no previous conclusion exists to inherit or to contradict.
* The "white-label template" origin of the app is an **inference** (two package ids plus the
  `appkits`/`appkits_helper_*` naming), not a proven fact.
* Whether the lost run inspected anything beyond what the snapshot preserves is unknowable: its
  `~/rea/` working directory and REA's evidence store are deleted, and no OpenCode session
  references Swipewipe (the work was driven by the shell script + MCP, not through OpenCode).

## 8. Next steps, in cost order

1. **Mirror the package before it disappears** — the Filebin bin is unauthenticated and expires
   (6 days from 2026-10-24). This repository now holds the sanitized copy; the original stays
   off-repo with its hash recorded so integrity is still provable.
2. **Rebuild the toolchain without a download if possible**: `~/gotall-work/skills/rea/third_party/jadx-headless-mcp`
   exists on the phone but is an **empty submodule**; so either `git submodule update --init` there
   or fetch the `0.7.1` release jar into `~/.local/share/rea/android/`. `rea` itself is installed
   and responsive (`rea capabilities`).
3. **One-line fix before reuse:** raise the driver's init wait from `18 × 5 s` to `60 × 5 s`
   (load measured at 94 s). That converts the recorded failure into a success.
4. **Resume, don't restart:** load the APK once, then loop `get_class_source` over the remaining
   **51 classes in the same server process** — every restart costs ~94 s. Order by value:
   `one_drive_data_source.OneDriveDataSourceManagerImpl` → `data.AppDatabase_Impl` (the real
   schema) → `cloud_provider_sync.CloudProviderSyncCoroutineWorker` →
   `image_grouping.ImageGroupingWorker` → `push_local.*`/`boot.BootReceiver`/`AlarmReceiver` →
   `MainActivity`/`SplashActivity`/`swipeable_card.SwipeableCardState`.
5. For `OneDriveDataSourceManagerImpl`, `wv2.*`, `zd0.a`, `ss2.*`: **smali first**, because that is
   precisely where jadx loses code.
6. Then answer the two questions worth asking: `get_xrefs_to_*` for the cleartext path (which client
   actually honours it) and for `MediaDecisionModel`/`CloudMediaItemModel` (what is uploaded, and
   whether "delete" reaches OneDrive — `deleteItemsBatch` is the lost method).
7. Runtime analysis stays blocked rootless (`dataDir` permission denied, no other-app `logcat`,
   backup disabled) → budget for Shizuku/`amctl` UI driving, not data extraction.
8. Retro-fit the missing case structure (`reverse-skill` → `case-init.sh`), then write
   `evidence/`, `timeline.md` and the report properly — the same gap that stopped the GoTall run
   from producing a deliverable.

## 9. Reproducibility

Every number in §2, §4, §5 and §6 is reproducible from `sources/` in this folder plus the
commands recorded in `../execution-log.md`; the two device-side verifications (`sha256sum`,
`aapt dump xmltree`) are quoted verbatim in those execution records. Nothing here depends on
evidence that is not published alongside it.
