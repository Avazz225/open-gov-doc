# Known vulnerabilities

**Example:** "Sind wir von CVE-2024-… betroffen?" / "Welche Schwachstellen haben wir?" / "Was bedeutet
der Alarm ‚Behebbare Schwachstellen'?"
**Role:** domain-admin-monitoring

OG Doc ships no vulnerability data. Records come live from OSV.dev, if the installation allows it, or
from an OSV database an admin imported (air-gapped). Matching is always local, against what actually
runs.

**One CVE**
1. `dms vulnerabilities check CVE-…`:
   - online if `DMS_VULNERABILITY_LOOKUP_ENABLED` is on;
   - otherwise from the imported database;
   - a 409 means neither is available.
2. Air-gapped without a database:
   - `--file record.json`, with the OSV record fetched on a connected machine;
   - or `--package openssl --ecosystem Debian --fixed 3.3.2-1`. Debian/Alpine use the source package
     name.
3. Explain:
   - "betroffen": service, installed version, „Behoben in";
   - "nicht betroffen": the installed versions that were checked;
   - a database answer without records means none of the packages installed at import time is named.
     If the database is older than the advisory, it may not know it yet.

**Everything ("Welche Schwachstellen haben wir?")**
1. `dms vulnerabilities report`: the latest scan.
   - The scheduler scans daily if a source exists. `dms vulnerabilities scan` starts one now and waits.
   - Online it sends every installed package name and version to OSV.dev
     (`DMS_VULNERABILITY_SCAN_ONLINE_ENABLED`). Say so if the user asks what leaves the installation.
2. Summarise:
   - the counts per severity, then the critical/high ones that are fixable: id, package, services,
     fix version;
   - unfixed ones (`kein Fix`, typically Debian packages) can only wait for the distribution. Don't
     present them as a to-do;
   - `--fixable` and `--severity critical,high` narrow the list.
3. "Nicht geprüft": packages the scan could not look at. Examples:
   - the Python interpreter (no OSV ecosystem);
   - an ecosystem without an imported database;
   - packages installed after the import.
   The fix for the latter two is a fresh database import.
4. **Fixing** means a new OG Doc release (rebuilt images), not a change in the running system. Name what
   to update; don't promise more.

**Air-gapped database**
1. On a connected machine, download from `https://osv-vulnerabilities.storage.googleapis.com/`:
   `PyPI/all.zip`, `npm/all.zip`, `Debian/all.zip`, `Alpine/all.zip`. Check the ecosystems with
   `dms vulnerabilities database status` or `dms components list`.
2. `dms vulnerabilities database import PyPI-all.zip npm-all.zip …` (audited). The CLI keeps only records
   for installed packages, so the large npm dump uploads small. The admin UI takes the smaller dumps
   directly.
   Use `Debian/all.zip`, not `Debian:13/all.zip`, which is a stale leftover.
3. **Verify:** `dms vulnerabilities database status`: the „data_as_of" column should be recent. Then
   run `dms vulnerabilities scan --source offline`.

**Alarms** (monitoring default package):
- „Kritische Schwachstellen" (error);
- „Behebbare Schwachstellen" (warning);
- „Schwachstellenprüfung veraltet" (warning after 72 h without a finished scan; check the online
  switch, or import a fresh database);
- „Schwachstellen-Datenbank veraltet" (an imported database's data date is older than 30 days:
  import fresh dumps).

They stay `unknown` until the first scan finishes.
