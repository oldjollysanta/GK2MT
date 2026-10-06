# GK2 modding research — 29 September 2026

Graveyard Keeper 2 uses Steam app **4358690**. Current BepInEx mods target **BepInEx 5.4.23.5, Windows x64, Unity Mono**. Merge the BepInEx package beside `GraveyardKeeper2.exe`; code plugins normally belong under `BepInEx/plugins`. BepInEx scans plugins recursively, so a disabled plugin must leave that tree or lose its `.dll` extension. Other mod systems and custom game-folder installers exist; a random DLL is not automatically a BepInEx plugin.

- [Mod author's GK2 requirements and layout](https://www.nexusmods.com/graveyardkeeper2/mods/72)
- [BepInEx installation guide](https://docs.bepinex.dev/articles/user_guide/installation/index.html)
- [Example of a separate mod loader and Steam Deck launch options](https://www.nexusmods.com/graveyardkeeper2/mods/58?tab=description)

## Current foundation and Workshop loader

[BepInEx for Graveyard Keeper 2, Nexus #48](https://www.nexusmods.com/graveyardkeeper2/mods/48) is the selected foundation. Bundle version **1.1** includes BepInEx **5.4.23.5-1**, Unity Doorstop **4.5.0**, and Configuration Manager **19.0**. Its ZIP root belongs beside `GraveyardKeeper2.exe`, including `winhttp.dll`, `doorstop_config.ini`, `.doorstop_version`, and `BepInEx`. The Workshop auto-loader is installed separately.

GK2MT downloads this bundle through Nexus with a Premium API key, or accepts the downloaded ZIP. It validates the foundation layout, preserves existing configs and separate Workshop patchers, backs up existing files, and uses atomic replacements. It no longer runs the old GitHub installer. Foundation archives use the setup flow rather than generic mod import. Nexus update checks compare the bundle's package version separately from a component's version.

Local files and the latest game log confirm the active patcher is `BepInEx/patchers/GK2_WorkshopLoader.dll`. It supports loose and nested BepInEx plugins and stages approved items under `BepInEx/plugins/_Workshop`. The trust file `BepInEx/config/GK2_WorkshopLoader.trust.txt` uses `ItemID = SHA256` or `ItemID = BLOCKED`. GK2MT disables an item with `BLOCKED`; enabling removes the block so the loader can request consent again. It never generates approval hashes. The loader owns the staged copies; do not install another copy of the same plugin manually.

The previous `GK2.WorkshopAutoLoader.dll` uses the incompatible `id|sha256|yes/no/ask|title|note` format and narrower layouts. Compatibility remains for existing installations; GK2MT identifies the active patcher before reading or changing trust and prevents trust edits when both loaders are active. A downloaded Workshop item is not proof that its code is active. Steam owns subscription downloads and updates. GK2Notepad is unnecessary for this user's Shopping List setup and is not installed or enabled by GK2MT.

Workshop display names come from Steam's public [GetPublishedFileDetails endpoint](https://partner.steamgames.com/doc/webapi/ISteamRemoteStorage#GetPublishedFileDetails), using a keyless HTTPS POST with the discovered item IDs. Only successful responses for app 4358690 are accepted. Names are cached locally; startup refreshes expired/missing metadata and Rescan forces a refresh. Local plugin names remain the fallback when Steam is unavailable. On 2 October 2026, item 3807777627 returned **Talent & Tech Refund**, while its plugin identified itself as **GK2 Respec**.

- [Legacy Workshop scanner](https://github.com/otkosss-png/GK2ModInstaller/blob/v1.2.3/src/GK2ModInstaller.Core/WorkshopItems.cs)
- [Legacy consent format](https://github.com/otkosss-png/GK2ModInstaller/blob/v1.2.3/src/GK2ModInstaller.Core/TrustStore.cs)
- [Framework limits](https://www.nexusmods.com/graveyardkeeper2/mods/42): its menu only lists mods that integrate with the framework; it does not update or hot-reload mods.

## Nexus Mods API

**Yes, API integration is possible.** The game domain is `graveyardkeeper2`. Personal API keys are suitable for this personal/testing app; a public production release must register with Nexus Mods. GK2MT identifies itself in `Application-Name` and `Application-Version` headers. It never borrows Vortex's API key.

The current official Node client still implements these REST v1 calls, also available with `.json` suffixes:

- `GET https://api.nexusmods.com/v1/users/validate.json` — API key and Premium status.
- `GET /v1/games/graveyardkeeper2.json` — category IDs and names, matched to each mod's `category_id`.
- `GET /v1/games/graveyardkeeper2/mods/{mod_id}.json` — mod metadata.
- `GET /v1/games/graveyardkeeper2/mods/{mod_id}/files.json` — files and author-declared `file_updates`.
- `GET /v1/games/graveyardkeeper2/mods/{mod_id}/files/{file_id}.json` — file details.
- `GET /v1/games/graveyardkeeper2/mods/{mod_id}/files/{file_id}/download_link.json` — download mirrors.
- `GET /v1/games/graveyardkeeper2/mods/md5_search/{hash}.json` — identify a manually downloaded archive before updating to a particular release.

Use the `apikey` request header. Unattended download links require **Nexus Premium**. Free accounts can use website-generated, expiring `nxm` download keys after choosing a file on the website; GK2MT keeps Vortex's existing `nxm` association and offers a browser/manual ZIP path instead. Metadata/version checks do not require Premium. API keys must stay on the user's computer, requests must be user-initiated, and HTTP 429 must stop a batch. File IDs matter: never choose an arbitrary newest MAIN file when a mod provides variants. Follow an installed file's declared update chain, or select the only MAIN file if the installed version is known; otherwise require choosing the matching file on Nexus.

GK2MT now caches Nexus categories during connection/update checks. Individual and bulk update actions download, validate, and install eligible releases into the existing package. Free users download the release on the Nexus website and use **Update from ZIP**; Nexus's MD5 lookup confirms the selected mod/file identity, while local SHA256 hashes guard staged-file integrity. Both installed and target versions must be known, even when a file ID is linked. Non-numeric version labels require an author-declared file successor. Steam Workshop remains under Steam. Existing configs and enabled state are preserved, retired files are backed up, and changed files or ambiguous layouts block the update.

GK2MT also provides a visible Windows WebView2 download panel for Free-account updates. Users click Nexus's Manual Download/Slow Download themselves. Native download events route the completed ZIP into an app-owned job folder before the existing exact-release verification and installer run. Partial files and unrequested downloads never trigger installation. The panel exposes no Python API to Nexus pages, keeps its website login in an isolated profile, and does not register `nxm` or automate site interactions. See [pywebview's native window API](https://pywebview.flowrl.com/api/#window-native) and [WebView2 DownloadStarting](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2.downloadstarting). A real local WebView2 download passed; authenticated Nexus website compatibility remains an interactive check.

- [Nexus API acceptable use policy](https://help.nexusmods.com/article/114-api-acceptable-use-policy)
- [Official API client, authentication and download rules](https://github.com/Nexus-Mods/node-nexus-api/blob/master/src/Nexus.ts)
- [Official file update schema](https://github.com/Nexus-Mods/node-nexus-api/blob/master/src/types.ts)
- [API key settings](https://www.nexusmods.com/settings/api-keys)
- [Current v3 documentation](https://api-docs.nexusmods.com/) and [OpenAPI schema](https://api.nexusmods.com/openapi.yaml); the implemented read/download flow uses the official client's supported v1 endpoints.

Update on **2 October 2026**: Nexus mod metadata for **ESC to Leave (#187)** reported `status: hidden` and `available: false`; the files endpoint returned HTTP 403, `Mod not available: 187`, while the same key validated successfully. This is a mod availability restriction, not a Free-account download restriction. GK2MT skips known unavailable mods and continues checking the others. The official file schema's `changelog_html` contains notes matched to that file's version; GK2MT converts these to plain text for the selected update card without extra API requests.

The same day's API check for **Weekly Overview (#131)** showed installed file **825 / 0.1.5** archived and file **1144 / 0.1.6** as the only active MAIN file, with an empty `file_updates` list. GK2MT now handles this missing-history case when the archived/old file and sole active MAIN have matching names and the new numeric version exceeds both the archived and installed versions. Explicit update chains retain priority; ambiguous variants, missing file records, unknown versions, and inferred downgrades remain blocked.

## Vortex rules and Steam Deck

Profile Workshop buttons use `steam://url/CommunityFilePage/<item-id>` to open the item's page in the Steam client. This URI is documented in Valve's [Steam Workshop implementation guide](https://partner.steamgames.com/doc/features/workshop/implementation#Workshop_Legal_Agreement). Subscriptions remain a user action; GK2MT rescans downloaded Workshop files when the profile's **Refresh status** button is used.

On **3 October 2026**, the installed Bag Tweaks DLL/log reported **1.1.0**, but its Vortex deployment source still named **1.0.0** and had a different recorded file modification time. Other externally updated mods had the same stale records. Vortex's [IDeployedFile definition](https://github.com/Nexus-Mods/Vortex/blob/master/src/renderer/src/extensions/mod_management/types/IDeploymentMethod.ts) documents `time` as a file's last-modified time, usable for detecting changes after deployment. GK2MT now checks this timestamp before trusting the archive version. The same local check showed Queue Count's unchanged DLL matching its **0.3.1** archive even though its plugin reports **0.3.0**, so genuine package/component differences are preserved. This is timestamp provenance rather than a content hash. Cached update entries are bound to their checked inventory identities and pruned when those identities change; an old unmanaged row cannot survive adoption into a managed package.

GK2MT uses these records only as optional initial discovery hints. It retains Nexus IDs and trustworthy archive versions in its own `nexus-discovery.json`, bound to current DLL hashes (the full foundation file set for #48). Removing legacy manifests does not remove those links. Later rescans cannot rebind changed files to an old hint. Installed or updated GK2MT packages have their own release identity and deployment hashes; verified foundation tracking takes precedence. Both mod pages display the resolved installed release, with a differing runtime plugin version retained in details. Tests remove temporary legacy manifests and verify continued tracking without Vortex.

The screenshot shows rules for **which package wins a shared file**. GK2MT can provide before/after deployment rules and detect cycles. That does not override BepInEx dependency metadata or guarantee arbitrary runtime plugin ordering. Avoid having Vortex and GK2MT deploy changes simultaneously; Vortex can redeploy its own version over a GK2MT change later.

For Steam Deck, sync the Windows mod files and use the Windows game under Proton. BepInEx needs the native `winhttp` override. GK2MT can save `winhttp=native,builtin` in the game's existing Proton `user.reg` under `Software\\Wine\\AppDefaults\\GraveyardKeeper2.exe\\DllOverrides`; Steam launch options are then unnecessary for this override. The manual alternative is `WINEDLLOVERRIDES="winhttp=n,b" %command%`. Environment overrides take precedence over the registry. If a separate `version.dll` loader is used, its override is also required. Match plugin bytes, loader files, configs, enabled state, relevant Workshop payloads, and game build; check hashes after transfer. Mods and saves matching does not prove that a mod is Proton-compatible, and the game must be closed at both ends while syncing. Saves themselves should remain with Steam Cloud.

Automatic setup finds a unique app-4358690 prefix across known Steam libraries, locks Proton's `pfx.lock`, refuses running Wine/Proton applications, backs up `user.reg`, then atomically replaces and verifies it. Proton's lock covers prefix preparation, not the whole runtime; checking running processes is required because Wine caches and rewrites registry files. This supports normal Steam-managed Proton prefixes; ambiguous or linked custom environments require the manual fallback.

- [Proton prefix preparation and lock](https://github.com/ValveSoftware/Proton/blob/proton_10.0/proton#L838)
- [Proton's flock implementation](https://github.com/ValveSoftware/Proton/blob/proton_10.0/filelock.py#L347)
- [Wine DLL override precedence](https://github.com/wine-mirror/wine/blob/master/dlls/ntdll/unix/loadorder.c#L358)
- [Wine registry persistence](https://github.com/wine-mirror/wine/blob/master/server/registry.c#L1931)

- [BepInEx Proton/Wine requirements](https://docs.bepinex.dev/articles/advanced/proton_wine.html)
- [GK2 mod author's combined Proton overrides](https://www.nexusmods.com/graveyardkeeper2/mods/58?tab=description)
