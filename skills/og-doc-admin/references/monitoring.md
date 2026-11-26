# Monitoring and alerts

- **Sensors** are named measurements the services report (`dms monitoring sensors`), for example:
  - `storage.target.used_percent` (fill level of a storage target);
  - `archival.transfers.failed`;
  - `auth.logins`;
  - `document.locks.oldest_age_hours`.
  
  Some are off by default (`sensor_config`).
- **Alert rules** watch a sensor:
  - `aggregation` (`max`/`min`/`sum`/`avg`/`rate_per_minute`) over the matching series, optionally
    narrowed by `label_filter`;
  - thresholds `max_warning`/`max_error` (or `min_*`);
  - `warning_repeat`/`error_repeat`: how many consecutive evaluations before the level changes.
  
  Alarm mails go to the operations mailbox.
- **Commands:**
  - `dms monitoring alert-rule list|preview <file>|create <file>|update <name|id> <file>|delete <name|id>`;
  - `dms monitoring alerts [--all]`: the current level of every rule;
  - `dms monitoring downtime list|create <file>|delete <id>`.
- **Role needed:** `domain-admin-monitoring`.
- **Preview:** `alert-rule preview` evaluates a draft against the current values without saving. Use it
  every time: it shows whether the sensor exists, which series match, and the current value.
- **The standard rules** are in `packages/monitoring-defaults/config.json` (storage 80/90 %, failed
  transfers, old locks, ...). Applying the package is often better than writing a rule by hand.
- **Downtime windows** suppress alerts for some or all rules: once (`starts_at`/`ends_at`) or weekly
  (`weekdays` 0=Monday, `daily_start`/`daily_end` "HH:MM"). `rule_ids` `null` or empty means every rule.
- **Log levels** (P97, ADR 0267): set centrally per scope, from broad to specific:
  - global (root level of every service);
  - one service (`--service`);
  - one logger, with its child loggers (`--logger`, with `--service` or for all services).

  Every replica of a service applies a change within 15 s. Without a setting a service runs on its own
  `DMS_LOG_LEVEL` (`root_source` `env`).
  - **DEBUG always expires:** default 60 min, at most 24 h. It can write personal data and multiplies log
    volume, so prefer one logger over a whole service and keep the duration short.
  - **Logger names** are module paths (`workflow_service.repository`) or libraries (`sqlalchemy.engine`
    for SQL, `httpx` for outgoing HTTP). `dms logging loggers --service S` lists the ones a service knows.
  - **Commands:** `dms logging show [--service S]`,
    `dms logging set <LEVEL> [--service S] [--logger L] [--for 30m|--until ISO]`,
    `dms logging reset [--service S] [--logger L]`, `dms logging loggers --service S [--filter T]`.
  - Admin UI: „Monitoring-Sensoren" → „Log-Level".
