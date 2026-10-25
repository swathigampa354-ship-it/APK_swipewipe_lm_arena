# Swipewipe (`com.afternoonproducts.swipewipe`) — static analysis report

Target: Swipewipe **2.19.00** (versionCode 21900), minSdk 32, targetSdk 36.
Artifact analysed: `/data/data/com.termux/files/home/rea/swipewipe/base.apk`,
92,066,971 bytes, sha256 `cfe3d01eed1351737b0894ea2cba85bea8888dd8038b5cfe71cbc4fc36380e22`.
Mode: **static only**. The installed app was not modified, reinstalled, instrumented or launched;
its private data was not read. Date: 2026-10-26.

Confidence vocabulary: **VERIFIED** = read out of the APK on the device in this session;
**PRIOR** = taken from the operator's recon snapshot, cross-checked against the same digest;
**NOT ESTABLISHED** = asked about, no evidence obtained.

## 0. Same build, so the prior work is reusable

The snapshot's own record (`static/swipewipe_artifact.json`) declares
`normalized_result.subject.root_sha256 = cfe3d01eed1351737b08…` — **identical** to the APK staged
here. Reuse of its findings is therefore valid rather than assumed.

What the prior run actually accomplished, from that file: `operation: inventory_artifact`,
`substeps: 1`, `observations: 1`, `hypotheses: 0`, `contradictions: 0`, `next_probes: 0`,
`raw_result: null`, graph `node_count 1601 / occurrence_count 1763 / edge_count 1762`,
`graph_sha256 d21ee64419d173ac23801f5c9dbc3f9e3709b04cf2bbcafba84ca357e3bc1717`.
Its own limitation is recorded verbatim: *"ZIP inventory lists central-directory entries only.
Non-entry bytes, including an APK Signing Block before the central directory or a ZIP comment after
it, are not occurrences."*

The prior MCP transcript (`mcp_out/server.out`) contains responses to ids **1, 13, 14, 15, 16**
only — `initialize`, `load_apk`, `list_classes` (52 FQNs), one errored probe, and **one** class
source (`application.SwipeApp`, 21,946 chars / 431 lines). So 51 classes were never decompiled, and
no manifest/xref/smali pull ever ran. **So the prior state was an inventory — this report goes past it.**

## 1. Headline: the OneDrive empty-list delete hypothesis is REFUTED

**VERIFIED from smali.** `one_drive_data_source/OneDriveDataSourceManagerImpl.smali`
(in `apktool_out/smali_classes8/`, 4,532 lines), method:

```
.method public deleteItemsBatch(Ljava/util/List;Lqb0;)Ljava/lang/Object;
    # signature: (List<String>, Continuation<Map<String,Boolean>>) -> Object
```

Body, in order (line numbers within the method as printed by `awk '/^.method.*deleteItemsBatch/,/^.end method/'`):

| line | instruction | meaning |
| --- | --- | --- |
| 224 | `invoke-interface/range {p1 .. p1}, Ljava/util/List;->isEmpty()Z` | explicit empty-list test |
| 228 | `if-eqz v0, :cond_4` | non-empty → real work |
| 230–236 | `sget-object v0, Lux0;->a:Lux0;` … `return-object v0` | **empty → return the shared empty map, immediately** |
| 240 | `new-instance v0, Ljava/util/LinkedHashMap;` | result map keyed per item id |
| 246–258 | `Iterable` → `Iterator` → `next()` loop over `p1` | one entry per requested id |
| 392 / 428 | `new-instance … BatchRequestItem;` / `new-instance … BatchRequest;` | per-id Graph sub-requests |
| 537 | `OneDriveApiService;->batchRequest(Ljava/lang/String;…BatchRequest;Lqb0;)` | the only network call |
| 730 | `Ljava/util/List;->size()I` | used for the response/batch bookkeeping |

Strings inside the same class: `"/me/drive/items/"` and `"DELETE"`.

**Conclusion.** `deleteItemsBatch` cannot be turned into "delete everything" by passing an empty
(or null-derived-empty) list: the `isEmpty()` branch returns Kotlin's empty-map singleton before any
batch request is built, and every delete is addressed by an explicit item id under
`/me/drive/items/`. The concern that a caller passing the *pre-sync* list could wipe cloud items is
therefore **not supported** by this build. What *is* true: if the pre-sync list is non-empty and the
remote state changed meanwhile, the call will attempt one Graph `DELETE` per id — failures are
reported per id in the returned map, not as an all-or-nothing transaction.

Callers: inside `com.afternoonproducts.swipewipe.**` the only references are the interface
(`OneDriveDataSourceManager.smali:42` in `smali_classes12`, name array + `public abstract` at :126)
and the coroutine continuation class (`…Impl$deleteItemsBatch$1.smali`). **No in-package caller
site exists**, so the call is made from an out-of-package obfuscated class
(the `qb0`/`jc0`/`ux0`/`mh1` short-name classes), which the include-package filter deliberately
excluded → **NOT ESTABLISHED** which list the caller passes.

## 2. Manifest-level findings (VERIFIED, decoded by apktool 3.0.3 from the release APK)

`apktool d -f base.apk` finished `rc=0` in ~2 min; 18 top-level entries, 13 `smali*` dex
containers, 92 in-package `.smali` files (more classes than the 52 FQNs because inner classes are
separate files).

| item | value | note |
| --- | --- | --- |
| `android:allowBackup` | **`false`** | backup is closed |
| `android:fullBackupContent` | **`false`** | ditto |
| `android:dataExtractionRules` | `@xml/appsflyer_data_extraction_rules` | SDK ships its own rules |
| `android:networkSecurityConfig` | `@xml/network_security_rules` | the real issue, below |
| `android:usesCleartextTraffic` | *absent* | so default `false` for targetSdk 36, overridden per-domain |
| `debuggable` / `testOnly` | *absent* | release build |
| `DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION` | declared by the app itself | targetSdk 34+ registerReceiver behaviour |

`res/xml/network_security_rules.xml`, verbatim and complete:

```xml
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="true">10.0.2.2</domain>
        <domain includeSubdomains="true">0.0.0.0</domain>
    </domain-config>
</network-security-config>
```

**Assessment (VERIFIED as shipped, judgement flagged):** cleartext HTTP is permitted — with
`includeSubdomains` — to `10.0.2.2` (the Android emulator's host alias) and to `0.0.0.0`. The first
is a developer-loopback convenience left in a **release** APK; `0.0.0.0` as a *target* domain is not
a routable address, so in practice this is dead configuration whose main cost is signal: the release
build carries a debug-network exception. No production host is exposed by this file, and no
cleartext override is global. This is a hygiene finding, **not** an exploitable MITM surface on its
own, and I am not reporting it as one.

`res/xml/appsflyer_data_extraction_rules.xml` excludes, from both `cloud-backup` and
`device-transfer`: `sharedpref/appsflyer-data`, `sharedpref/appsflyer-purchase-data`,
`database/afpurchases.db`. Two facts follow — AppsFlyer is integrated, and AppsFlyer keeps
**purchase** data in a Room DB named `afpurchases.db` that the SDK deliberately keeps off backup.

### Correction to an earlier claim

The delegation brief I wrote earlier (`~/rea/swipewipe/inputs/task2.md`) listed "`allowBackup`" among
Swipewipe's headline findings. **That was wrong**: `allowBackup="true"` belongs to the *GoTall*
write-up (`reports/prior-task-gotall-recovery.md:39`), a different app. Swipewipe's release manifest
says `allowBackup="false"`. The Swipewipe investigation report (`:121`) already had it right.

## 3. Permissions (VERIFIED, from the decoded manifest — 29 uses-permission entries)

* Media: `READ_MEDIA_IMAGES`, `READ_MEDIA_VIDEO`, `READ_MEDIA_VISUAL_USER_SELECTED`,
  `ACCESS_MEDIA_LOCATION`, plus legacy `READ_EXTERNAL_STORAGE` **and** `WRITE_EXTERNAL_STORAGE`.
  The three scoped-media permissions are the modern set; the two legacy ones are only honoured on
  API ≤ 32/28 and sit oddly against minSdk 32 — worth flagging as over-broad, not as a vulnerability.
* Notifications / scheduling: `POST_NOTIFICATIONS`, `RECEIVE_BOOT_COMPLETED`, `WAKE_LOCK`,
  `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_DATA_SYNC` (the type matches a sync worker).
* App lock: `USE_BIOMETRIC`, `USE_FINGERPRINT`.
* Ads / attribution / billing: `AD_ID`, four `ACCESS_ADSERVICES_*` (AD_ID, ATTRIBUTION,
  CUSTOM_AUDIENCE, TOPICS), `com.android.vending.BILLING`, `com.android.vending.CHECK_LICENSE`,
  `BIND_GET_INSTALL_REFERRER_SERVICE`, and `com.amazon.privacypass.ATTEST`.
* Also: `INTERNET`, `ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE`, `NFC`, `VIBRATE`.

`WRITE_EXTERNAL_STORAGE` + `ACCESS_MEDIA_LOCATION` together are the broadest pair here: location
metadata of every image/video the app reads is available to it, which matters given the media
grouping and cloud-sync features below.

## 4. Credential material shipped in resources (VERIFIED present, values withheld)

`res/values/strings.xml` (184,365 B in the snapshot; same build) contains:

| resource | value length | sha256[:12] of the value | class |
| --- | --- | --- | --- |
| `google_api_key` | 39 | `1d69ec76740b` | matches `AIza…` Google API key shape |
| `facebook_client_token` | 32 | `3b85303130b1` | Facebook SDK client token |
| `com_facebook_device_auth_instructions` | 70 | `71570942f961` | template text, not a secret |

19 further `password`/`token`-named entries are **UI copy** (e.g. `http_auth_dialog_password` =
"Password"), not credentials — listed so the absence of more keys is itself evidence.

A Google API key and a Facebook client token in a shipped APK are **public by construction** (any
client can be unpacked); the finding that matters is whether they are restricted. That cannot be
established statically — it needs the Cloud console — so it is recorded as **NOT ESTABLISHED**
rather than as a risk score. Values are deliberately absent from this report and are redacted by
`bin/redact.py` in the archive.

**Tooling defect found while checking this:** `facebook_client_token`'s 32-hex value passed the
archive's 15-pattern gate untouched. `redact.py` now has an `android-resource-secret` pattern keyed
on the *resource name* (a blanket "32 hex chars" rule would have redacted the git/sha256 ids this
archive legitimately publishes — verified no false positive on a `commit <40-hex> sha256 <32-hex>`
probe), and it reports a finding so the pre-commit gate blocks. 13 findings raised on the strings
file; 23/23 archive tests still pass.

## 5. SDK integrations (VERIFIED from manifest/resources; Java-level wiring pending)

AppsFlyer (its own `dataExtractionRules`, `afpurchases.db`), Facebook (client token + device-auth
strings), Google Play billing + license check + install referrer, Google Mobile Services ad
services, Glide (`glide_config.SwipewipeAppGlideModule` is in the 52), Hilt
(`main_activity.Hilt_MainActivity`, `splash_activity.Hilt_SplashActivity`), WorkManager
(`CloudProviderSyncCoroutineWorker`, `ImageGroupingWorker`, `PhotoCompressBatchWorker`,
`OtdReminderWorker`, `SimilarImagesReminderWorker`), Room (`data.AppDatabase_Impl`), OneDrive via a
hand-written Graph API service (`OneDriveApiService`, `/me/drive/items/`, batch endpoint).

## 6. What is NOT established — stated plainly

* **The other 50 classes have no decompiled Java source.** jadx-headless-mcp loaded the APK
  (`Loaded classes: 91473, methods: 496680, instructions: 13536913`, ~31 s with
  `--resources none`) but the driver never received its `initialize` response; see §7 for the
  narrowed diagnosis. Their *smali* is on disk and greppable; their Java is not written.
* Whether `wv2.l` / `zd0.a` behave as suspected — out of package scope, never reached.
* Reminders/`push_local`, `image_grouping`, `photo_compress_batch`, `AppDatabase_Impl.onOpen`
  pruning SQL: **not yet read** in this session.
* Local media storage layout and image-grouping algorithm: not yet read.
* Whether `google_api_key` is restricted: needs console access.

## 7. Reproducible commands

On the device (Termux), all paths absolute because `/tmp` is not writable:

```sh
W=$HOME/rea/swipewipe; mkdir -p $W/logs

# 1. apktool: resources + smali for every dex (this one SUCCEEDED, rc=0, ~2 min)
JAVA_OPTS=-Xmx1024m apktool d -f -o $W/apktool_out $W/base.apk

# 2. the empty-list question, end to end
I=$W/apktool_out/smali_classes8/com/afternoonproducts/swipewipe/one_drive_data_source/OneDriveDataSourceManagerImpl.smali
awk '/^.method.*deleteItemsBatch/,/^.end method/' $I | grep -nE 'isEmpty|LinkedHashMap|BatchRequest|batchRequest|size\(\)'
grep -oE '"/me/drive/items/"|"DELETE"' $I | sort -u

# 3. manifest facts
grep -oE '(allowBackup|fullBackupContent|networkSecurityConfig|dataExtractionRules)="[^"]*"' $W/apktool_out/AndroidManifest.xml
cat $W/apktool_out/res/xml/network_security_rules.xml

# 4. jadx headless MCP (the part that did NOT complete): server side, proven flags
java -Xmx1200m -jar $HOME/.local/share/rea/android/jadx-headless-mcp-0.7.1-all.jar \
  --transport stdio --apk $W/base.apk --resources none \
  --include-packages com.afternoonproducts.swipewipe,com.mwm.swipewipe \
  --threads 2 --decompile-timeout-ms 90000
# The driver I wrote pipes stdin/stdout directly; the historical driver that DID get responses
# (ids 1,13,14,15,16) used a FIFO + polling an output FILE. That difference is the next thing to
# test: read the server's stdout through a file, not through a Python pipe.

# 5. single-instance + memory-safe relaunch (flock guard; refuses under 1.35x heap of MemAvailable)
bash $W/launch.sh
```

Toolchain on the device: `apktool 3.0.3-dirty`, `jadx 1.5.5`, `openjdk 21.0.12`,
`rea 6.7.0` (`rea doctor` → `unsupported_host` on android/arm64),
`reverse-skill` **v1.0.1** at `~/gotall-work/skills/reverse-skill` (PRIMARY skill used:
`skills/apk-reverse/SKILL.md`; its `scope.md` gate is satisfied at
`$W/analysis/work/swipewipe/scope.md`, `auth.status: granted`, `network_profile: offline`).

**Resource 4 unavailable:** `fishforks/apk-reverse2` is **not installed anywhere I could find**
(checked `~/gotall-work/skills/`, `~/.claude/skills`, `~/skills`, `~/.config/opencode/skill`,
`~/tools/`, `~/.cache/`). `reverse-skill` already provides `skills/apk-reverse`, which covers the
same ground. I did not install it: the standing instruction from this session's earlier tasks is
no installs, and the recovery task forbids modifying the device environment. So three of four
resources were used; that is a scope gap, not a silent substitution.

**Deliberately not reused:** `~/gotall-work/{apktool_out,jadx_out,jadx.zip}` look like prior
Swipewipe output but decode to `package="app.gotall.play"` (base.apk 86,262,802 B) — a **different
app**. Treating them as Swipewipe evidence would have been a cross-app contamination error.

## 8. Honest account of the device instability in this session

Three avoidable mistakes of mine, all recorded because they explain missing artifacts:
(a) I first ran jadx with `--resources full`, which made it build resource IDs for all 91,473
classes; RSS reached 1.68 GB with `MemFree: 28 MB`;
(b) I used `pkill -f run_mcp.py`, and the bridge executes my command inside
`bash -c '<the command text>'` — the pattern matched its own wrapper;
(c) my client retried a non-idempotent launch on a 502, starting **three** concurrent
`run_all.sh` pipelines (each with its own 1 GB+ heap) before I noticed.
Both (a) and (c) coincide with the serveo upstream going 502 — the Android low-memory killer taking
the Termux process group is the likely mechanism, stated as a hypothesis, unprovable from here.
Mitigations now in the tooling: `flock` single-instance guard in `run_all.sh` **and** the driver,
pidfile-based stop instead of pattern kills, a `MemAvailable ≥ 1.35 × heap` preflight refusal,
`RECON_XMX` override, and `attempts=1` for launch calls since a 502 does not mean "did not run".
