# Odoo 19 — Snowflake Connector — Complete Build Plan
### (Full feature parity with `wk_snowflake_connector` — every table, every field, every view, every button)

This plan is exhaustive on purpose: every feature listed on the module page and every screen implied by the module's screenshots is mapped to a concrete model, field, view, and button. Nothing from the source feature list is dropped, and nothing extra/unnecessary is added beyond what's needed to support those exact features.

---

## 0. Master Feature Checklist (from module page — all must be covered)

| # | Feature (as advertised) | Covered by (section) |
|---|---|---|
| 1 | Secure connection setup from Odoo UI | Section 3.1 `snowflake.connection` |
| 2 | Auth via Username/Password **or** PAT (Programmatic Access Token) | Section 3.1 (`auth_type`, conditional fields) |
| 3 | Test Connection button | Section 3.1 (`action_test_connection`) |
| 4 | Snowpark-based communication | Section 3.1 / Section 6 (backend) |
| 5 | Database creation from Odoo | Section 3.2 `snowflake.database` |
| 6 | Direct connection Odoo↔Snowflake | Section 3.1 |
| 7 | Prepare DB for structured sync | Section 3.2 |
| 8 | Publish Odoo model as Snowflake table | Section 3.3 `snowflake.table.config` |
| 9 | Auto-generate columns from model fields | Section 3.4 `snowflake.field.mapping` |
| 10 | Sync table data, structure, relationships | Section 3.3 / Section 3.4 |
| 11 | Remove/delete table from Snowflake | Section 3.3 (`action_delete_table`) |
| 12 | Schema migration: add columns on new fields | Section 3.3 (`action_alter_table`) |
| 13 | Schema migration: drop columns on removed fields | Section 3.3 |
| 14 | Update column definitions/constraints | Section 3.3 / Section 3.4 |
| 15 | Export data — insert new records | Section 3.3 (`action_export_data`) |
| 16 | Export data — update existing via MERGE | Section 3.3 |
| 17 | Export only records updated since last sync (incremental) | Section 3.3 (`last_sync_date`) |
| 18 | Domain-based filtering of records | Section 3.3 (`domain_filter` tab) |
| 19 | Scheduled sync via cron | Section 3.6 `ir.cron` + wizard |
| 20 | Column preview (Snowflake-end) | Section 3.7 `snowflake.column.preview` wizard |
| 21 | Data preview | Section 3.8 `snowflake.data.preview` wizard |
| 22 | Dashboard overview (connections/DBs/tables/status) | Section 3.9 Dashboard (kanban/home action) |
| 23 | Migration status: pending / migrated | Section 3.3 (`migration_state`) |
| 24 | Sync log / history (new & updated record counts) | Section 3.5 `snowflake.sync.log` |
| 25 | "Discuss (mail)" dependency → chatter/activity on key records | Section 3.1/Section 3.3 (`mail.thread`) |

---

## 1. Module Skeleton

```
snowflake_connector/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── snowflake_connection.py        # Model 1
│   ├── snowflake_database.py          # Model 2
│   ├── snowflake_table_config.py      # Model 3
│   ├── snowflake_field_mapping.py     # Model 4
│   ├── snowflake_sync_log.py          # Model 5
│   ├── snowflake_migration_log.py     # Model 6
│   └── ir_cron.py                     # cron helper methods
├── wizard/
│   ├── __init__.py
│   ├── snowflake_test_connection_wizard.py
│   ├── snowflake_column_preview_wizard.py     # column preview
│   ├── snowflake_data_preview_wizard.py        # data preview
│   ├── snowflake_export_data_wizard.py         # export/sync now
│   └── snowflake_cron_config_wizard.py         # cron configuration screen
├── views/
│   ├── snowflake_connection_views.xml
│   ├── snowflake_database_views.xml
│   ├── snowflake_table_config_views.xml
│   ├── snowflake_field_mapping_views.xml
│   ├── snowflake_sync_log_views.xml
│   ├── snowflake_migration_log_views.xml
│   ├── snowflake_dashboard_views.xml
│   ├── wizard_views.xml
│   └── menu_views.xml
├── security/
│   ├── ir.model.access.csv
│   └── snowflake_security.xml
├── data/
│   └── ir_cron_data.xml
├── static/description/
│   ├── icon.png
│   └── index.html
└── requirements.txt
```

`__manifest__.py`
```python
{
    "name": "Snowflake Connector",
    "version": "19.0.1.0.0",
    "category": "Extra Tools",
    "summary": "Sync Odoo data to Snowflake — connection, table sync, schema migration, scheduled sync",
    "depends": ["base", "mail"],
    "external_dependencies": {
        "python": ["snowflake-connector-python",]
    },
    "data": [
        "security/snowflake_security.xml",
        "security/ir.model.access.csv",
        "views/snowflake_connection_views.xml",
        "views/snowflake_database_views.xml",
        "views/snowflake_table_config_views.xml",
        "views/snowflake_field_mapping_views.xml",
        "views/snowflake_sync_log_views.xml",
        "views/snowflake_migration_log_views.xml",
        "views/wizard_views.xml",
        "views/snowflake_dashboard_views.xml",
        "views/menu_views.xml",
        "data/ir_cron_data.xml",
    ],
    "installable": True,
    "application": True,
    "license": "LGPL-3",
}
```

Python libraries required (`requirements.txt`):
```
snowflake-connector-python
```

---

## 2. Full Menu Tree

```
Snowflake Connector                              (root menu, icon)
├── Dashboard                                     → snowflake.dashboard (kanban/home)
├── Connections
│   └── Connections                               → snowflake.connection (tree/form)
├── Databases
│   └── Databases                                 → snowflake.database (tree/form)
├── Tables
│   ├── Table Configurations                      → snowflake.table.config (tree/form)
│   └── Field Mappings                            → snowflake.field.mapping (tree/form, usually inline)
├── Synchronization
│   ├── Sync Logs                                 → snowflake.sync.log (tree/form, read-only)
│   ├── Migration Logs                            → snowflake.migration.log (tree/form, read-only)
│   └── Scheduled Actions (Cron)                  → ir.cron (filtered list, technical)
```

---

## 3. Models — Every Table, Every Field, Every View, Every Button

### 3.1 `snowflake.connection`
Represents one Odoo↔Snowflake secure connection (covers Features #1–4, #6).

**Fields (all):**
| Field | Type | Description |
|---|---|---|
| `name` | Char (required) | Connection label |
| `account_identifier` | Char (required) | Snowflake account identifier (e.g. `xy12345.us-east-1`) |
| `auth_type` | Selection `[('password','Username/Password'),('pat','Programmatic Access Token')]` (required, default `password`) | Authentication mode toggle |
| `login` | Char | Snowflake username (visible when `auth_type = password`) |
| `password` | Char (password widget) | Snowflake password (visible when `auth_type = password`) |
| `pat_token` | Char (password widget) | Programmatic Access Token (visible when `auth_type = pat`) |
| `warehouse` | Char (required) | Snowflake virtual warehouse name |
| `role` | Char | Snowflake role to assume |
| `default_database_id` | Many2one → `snowflake.database` | Default DB for new table configs |
| `default_schema` | Char (default `PUBLIC`) | Default schema name |
| `state` | Selection `[('draft','Draft'),('tested','Connected'),('failed','Failed')]` (readonly) | Connection health |
| `last_test_date` | Datetime (readonly) | Timestamp of last successful/failed test |
| `last_test_message` | Text (readonly) | Raw response/error from last test |
| `active` | Boolean (default True) | Archive toggle |
| `company_id` | Many2one → `res.company` | Multi-company support |
| `database_ids` | One2many → `snowflake.database` (`connection_id`) | Databases created under this connection |
| `table_config_ids` | One2many → `snowflake.table.config` (`connection_id`) | Table configs using this connection |
| `message_ids` / `activity_ids` | (via `mail.thread`, `mail.activity.mixin`) | Chatter/log/activity (Discuss dependency) |

**Buttons (form view):**
- **Test Connection** → `action_test_connection()` — opens Snowpark session, runs `SELECT CURRENT_VERSION()`; sets `state`, `last_test_date`, `last_test_message`; shows a "Successfully Connected" notification/banner on success, error dialog on failure.
- **Create Database** → opens `snowflake.database` creation form pre-filled with this connection (Feature #5).
- Smart buttons (top of form): **Databases** (count → `database_ids`), **Table Configs** (count → `table_config_ids`), **Sync Logs** (count, aggregated across table configs).

**Views:**
- `tree`: name, account_identifier, auth_type, state, last_test_date
- `form`: header with status bar (`state`) + buttons above; two tabs — "Password Auth" fields shown only if `auth_type='password'`, "PAT Auth" fields shown only if `auth_type='pat'` (matches "password-authentication.png" / "pat-configuration.png" screenshots)
- `search`: filters by `state` (Connected/Failed/Draft), group by `auth_type`

---

### 3.2 `snowflake.database`
Represents a Snowflake database created/managed from Odoo (Feature #5, #7).

**Fields (all):**
| Field | Type | Description |
|---|---|---|
| `name` | Char (required) | Database name in Snowflake |
| `connection_id` | Many2one → `snowflake.connection` (required) | Owning connection |
| `comment` | Char | Optional description stored as Snowflake `COMMENT` |
| `state` | Selection `[('draft','Not Created'),('created','Created'),('error','Error')]` | Creation status |
| `table_count` | Integer (computed) | Count of tables under this DB (Feature: "Tables in DB" screen) |
| `table_config_ids` | One2many → `snowflake.table.config` (`database_id`) | Table configs targeting this DB |
| `create_date` | Datetime (readonly) | |
| `active` | Boolean (default True) | |

**Buttons:**
- **Create Database in Snowflake** → `action_create_database()`, runs `CREATE DATABASE IF NOT EXISTS <name>`.
- **Refresh Table List** → `action_fetch_tables()`, queries `SHOW TABLES IN DATABASE <name>` and syncs into a display list (drives the "Tables in DB" screen).
- **View Tables** (smart button) → opens `table_config_ids` list filtered to this database.

**Views:**
- `tree`: name, connection_id, state, table_count
- `form`: name, connection_id, comment, state, button box (Table Configs)

---

### 3.3 `snowflake.table.config`
The central model: maps one Odoo model to one Snowflake table and drives sync/migration (Features #8, #10, #11, #12, #13, #14, #15, #16, #17, #18, #23).

**Fields (all):**
| Field | Type | Description |
|---|---|---|
| `name` | Char (computed/editable) | Display name, e.g. "Sale Order → SALE_ORDER" |
| `connection_id` | Many2one → `snowflake.connection` (required) | |
| `database_id` | Many2one → `snowflake.database` (required) | |
| `schema_name` | Char (default `PUBLIC`) | |
| `model_id` | Many2one → `ir.model` (required) | Source Odoo model |
| `model_name` | Char (related `model_id.model`, stored) | Technical model name |
| `name` | Char (required) | Target Snowflake table name (auto-suggested, editable) |
| `field_mapping_ids` | One2many → `snowflake.field.mapping` (`table_config_id`) | All column mappings |
| `domain_filter` | Char (default `[]`) | Odoo domain applied before every sync (Feature #18) |
| `include_archived` | Boolean (default False) | Whether to include inactive records |
| `sync_mode` | Selection `[('manual','Manual'),('scheduled','Scheduled')]` | |
| `cron_id` | Many2one → `ir.cron` | Linked scheduled job (only if `sync_mode='scheduled'`) |
| `sync_interval_number` | Integer | Shown/used only to configure `cron_id.interval_number` |
| `sync_interval_type` | Selection `[('minutes','Minutes'),('hours','Hours'),('days','Days'),('weeks','Weeks')]` | Maps to `cron_id.interval_type` |
| `table_state` | Selection `[('not_created','Not Created'),('created','Created')]` (readonly) | Whether `CREATE TABLE` has run |
| `migration_state` | Selection `[('up_to_date','Up To Date'),('pending_migration','Pending Migration'),('migrated','Migrated Successfully')]` (readonly) | Drives "pending-migration-schema" / "schema-migrated-successfully" screens |
| `last_sync_date` | Datetime (readonly) | Used for incremental filter `write_date >= last_sync_date` |
| `last_sync_new_count` | Integer (readonly) | New records inserted in last run |
| `last_sync_updated_count` | Integer (readonly) | Existing records updated (merged) in last run |
| `last_sync_status` | Selection `[('success','Success'),('failed','Failed')]` (readonly) | |
| `sync_log_ids` | One2many → `snowflake.sync.log` (`table_config_id`) | Full history |
| `migration_log_ids` | One2many → `snowflake.migration.log` (`table_config_id`) | Full schema-change history |
| `active` | Boolean (default True) | |
| `company_id` | Many2one → `res.company` | |

**Buttons (form view):**
- **Create Table** → `action_create_table_structure()` — builds and executes `CREATE TABLE IF NOT EXISTS` from `field_mapping_ids`; sets `table_state='created'`.
- **Check Migration** → `action_check_migration()` — diffs current `ir.model.fields` vs stored mapping/Snowflake `INFORMATION_SCHEMA.COLUMNS`; sets `migration_state='pending_migration'` if a diff is found.
- **Alter Table / Migrate Schema** → `action_migrate_schema()` — runs `ALTER TABLE ADD COLUMN` / `DROP COLUMN` / `ALTER COLUMN` statements; sets `migration_state='migrated'` then `up_to_date`; writes a `snowflake.migration.log` entry (matches "alter-table.png").
- **Export Data / Sync Now** → `action_export_data()` — opens the Export Data wizard (or runs directly): inserts new + merges updated records; updates `last_sync_*` fields; writes `snowflake.sync.log` entry (Features #15–17, matches "export-data.png", "updated-and-new-record.png").
- **Preview Columns** → opens `snowflake.column.preview` wizard (Feature #20, "snowflake-end-column-preview.png").
- **Preview Data** → opens `snowflake.data.preview` wizard (Feature #21, "data-preview.png").
- **Delete Table** → `action_delete_table()` — confirmation popup, then runs `DROP TABLE`; sets `table_state='not_created'` (Feature #11, "delete-table.png").
- **Configure Schedule** → opens Cron Configuration wizard to create/edit `cron_id` (Feature #19, "cron-configuration.png").
- Smart buttons: **Sync Logs** (count → `sync_log_ids`), **Migration Logs** (count → `migration_log_ids`), **Field Mappings** (count → `field_mapping_ids`).

**Views:**
- `tree`: name, model_id, name, table_state, migration_state, last_sync_date, last_sync_status
- `form`: status bar (`table_state` / `migration_state`), button box, notebook tabs:
  - **Field Mapping** tab → inline editable `field_mapping_ids` tree
  - **Filters** tab → `domain_filter` (domain widget), `include_archived` (matches "filters-in-table-configuration.png")
  - **Sync Settings** tab → `sync_mode`, `cron_id`, `sync_interval_number`, `sync_interval_type`, `last_sync_date`
  - **Logs** tab → embedded `sync_log_ids` and `migration_log_ids` trees
- `search`: filter by `migration_state`, `table_state`, `sync_mode`; group by `model_id`, `database_id`

---

### 3.4 `snowflake.field.mapping`
Column-level mapping, one row per Odoo field ↔ Snowflake column (Features #9, #14).

**Fields (all):**
| Field | Type | Description |
|---|---|---|
| `table_config_id` | Many2one → `snowflake.table.config` (required) | |
| `odoo_field_id` | Many2one → `ir.model.fields` (required) | Source Odoo field |
| `field_name` | Char (related `odoo_field_id.name`, stored) | Technical field name |
| `field_label` | Char (related `odoo_field_id.field_description`) | Human label |
| `odoo_field_type` | Char (related `odoo_field_id.ttype`) | Original Odoo type |
| `column_name` | Char (required, editable) | Target Snowflake column name |
| `column_type` | Selection `[('VARCHAR','VARCHAR'),('NUMBER','NUMBER'),('FLOAT','FLOAT'),('BOOLEAN','BOOLEAN'),('DATE','DATE'),('TIMESTAMP_NTZ','TIMESTAMP'),('VARIANT','VARIANT (JSON)')]` (editable, auto-defaulted from `odoo_field_type`) | |
| `column_length` | Integer | For VARCHAR sizing (default 255, or larger for text fields) |
| `is_primary_key` | Boolean | Marks the merge key (default True only on `id` row) |
| `is_included` | Boolean (default True) | Toggle to exclude a field from sync without deleting the mapping |
| `sequence` | Integer | Column display/creation order |

**Buttons:**
- **Auto-Generate Mapping** (on parent `snowflake.table.config`, but acts on this model) → `action_generate_field_mapping()` — populates one row per stored field on `model_id`, skipping binary fields by default.
- **Reset to Default Type** (row button/action) → recomputes `column_type` from `odoo_field_type` mapping table (see Section 4).

**Views:**
- Primarily an inline editable `tree` inside the parent form's "Field Mapping" tab (columns: sequence, field_label, field_name, odoo_field_type, column_name, column_type, column_length, is_primary_key, is_included)
- Standalone `tree`/`form` also provided under "Field Mappings" menu for bulk editing across configs

---

### 3.5 `snowflake.sync.log`
History of every data export/sync run (Features #16, #17, #24).

**Fields (all):**
| Field | Type | Description |
|---|---|---|
| `table_config_id` | Many2one → `snowflake.table.config` (required) | |
| `date` | Datetime (default now) | |
| `sync_type` | Selection `[('manual','Manual'),('scheduled','Scheduled')]` | |
| `new_record_count` | Integer | Records inserted |
| `updated_record_count` | Integer | Records merged/updated |
| `total_record_count` | Integer (computed) | new + updated |
| `status` | Selection `[('success','Success'),('failed','Failed')]` | |
| `error_message` | Text | Full traceback if failed |
| `duration_seconds` | Float | Execution time |

**Buttons:**
- **View Table Config** (smart button/link) → opens parent record.

**Views:**
- `tree` (read-only, default sort by `date desc`): date, table_config_id, sync_type, new_record_count, updated_record_count, status
- `form` (read-only): all fields, error_message shown in a `Text` box only when `status='failed'`
- `search`: filter by status/date range, group by `table_config_id`

---

### 3.6 `snowflake.migration.log`
History of schema/ALTER TABLE operations (Features #12, #13, #14, #23).

**Fields (all):**
| Field | Type | Description |
|---|---|---|
| `table_config_id` | Many2one → `snowflake.table.config` (required) | |
| `date` | Datetime (default now) | |
| `added_columns` | Text | Comma/line list of columns added |
| `removed_columns` | Text | Comma/line list of columns dropped |
| `altered_columns` | Text | Columns whose type/constraint changed |
| `status` | Selection `[('success','Success'),('failed','Failed')]` | |
| `error_message` | Text | |

**Views:**
- `tree` (read-only): date, table_config_id, added_columns, removed_columns, status
- `form` (read-only): full detail

---

### 3.7 Wizard — `snowflake.column.preview` (transient)
Drives the "Snowflake end column preview" screen (Feature #20).

**Fields:** `table_config_id` (Many2one, required), `preview_line_ids` (One2many, computed on open: column_name, column_type, sample_value/nullable).
**Button:** **Close**. Opened via **Preview Columns** button on `snowflake.table.config`.

### 3.8 Wizard — `snowflake.data.preview` (transient)
Drives the "Data preview" screen (Feature #21).

**Fields:** `table_config_id`, `preview_row_ids` (One2many, computed: first N rows as they'll appear in Snowflake, respecting `domain_filter` and `field_mapping_ids`).
**Buttons:** **Refresh Preview**, **Close**.

### 3.9 Wizard — `snowflake.export.data` (transient, optional confirmation step)
Backs the **Export Data** button when a confirmation/summary step is desired before running `action_export_data()`.

**Fields:** `table_config_id`, `record_count_to_sync` (computed preview count), `incremental` (Boolean, default True).
**Button:** **Confirm & Export**, **Cancel**.

### 3.10 Cron Configuration (Feature #19)
No new model required — configuration happens directly on `snowflake.table.config` (`sync_mode`, `sync_interval_number`, `sync_interval_type`) which creates/updates a linked standard `ir.cron` record (`cron_id`). The **Configure Schedule** button opens a small wizard (`snowflake.cron.config` transient, fields: `interval_number`, `interval_type`, `nextcall`) purely as a friendlier UI over `ir.cron`.

### 3.11 Dashboard (Feature #22)
A simple non-model "home" view (kanban dashboard using `ir.actions.client` or a kanban on a dummy/aggregating transient model) showing:
- Total Connections (and how many are `state='tested'`)
- Total Databases created
- Total Table Configs (and split by `table_state`, `migration_state`)
- Last 5 Sync Logs (status + date)
- Quick-action buttons: **New Connection**, **New Table Config**, **View Sync Logs**

### 3.12 Configuration flow
The standalone `res.config.settings` flow is intentionally omitted in the Odoo 19 build.
Any runtime defaults used by sync logic are handled directly in the connector code.

---

## 4. Odoo → Snowflake Type Mapping (used by Field Mapping defaults)

| Odoo Field Type | Default Snowflake `column_type` |
|---|---|
| `char`, `text`, `html`, `selection` | `VARCHAR` |
| `integer` | `NUMBER` |
| `float`, `monetary` | `FLOAT` |
| `boolean` | `BOOLEAN` |
| `date` | `DATE` |
| `datetime` | `TIMESTAMP_NTZ` |
| `many2one` | `NUMBER` (stores `.id`) |
| `one2many`, `many2many` | `VARIANT` (JSON array of ids) |
| `binary` | Excluded by default (`is_included=False`); can be manually re-enabled as `VARCHAR` (base64) |

---

## 5. Security

- Group **Snowflake / Manager**: full CRUD on all models, can run all buttons (test, create table, alter, export, delete).
- Group **Snowflake / User**: read-only on connections/databases/table configs/logs; can trigger **Export Data / Sync Now** and view previews, but cannot edit connection credentials or delete tables.
- `ir.model.access.csv` — one row per model per group (8 models × 2 groups = 16 access rules minimum).
- Record rule: table configs/connections filtered by `company_id` if multi-company is enabled.
- Password/PAT fields: widget `password="True"`, additionally restricted via `groups="snowflake_connector.group_snowflake_manager"` on the field definition so Users can't read raw credentials even via export.

---

## 6. Backend Sync Logic (summary — ties buttons to Snowpark/connector calls)

1. **Connect** — build a Snowpark `Session` (or `snowflake.connector.connect(...)`) from `snowflake.connection` fields, chosen by `auth_type` (`password`+`login` vs `pat_token`).
2. **Create Database** — `CREATE DATABASE IF NOT EXISTS "<name>" COMMENT='<comment>'`.
3. **Create Table** — build `CREATE TABLE IF NOT EXISTS "<db>"."<schema>"."<table>" (<col> <type>, ...)` from `field_mapping_ids` ordered by `sequence`.
4. **Check Migration** — query `INFORMATION_SCHEMA.COLUMNS` for the table; diff against current `field_mapping_ids` (which itself should be refreshed from `ir.model.fields` first); flag `pending_migration`.
5. **Alter Table** — issue one `ALTER TABLE ... ADD COLUMN` per new field, one `DROP COLUMN` per removed field, one `ALTER COLUMN ... SET DATA TYPE` per type change; log to `snowflake.migration.log`.
6. **Export Data** — `env[model_name].search(domain_filter + ([('write_date','>=',last_sync_date)] if incremental))`; batch rows (e.g. 5,000/batch); `MERGE INTO target USING (VALUES ...) AS source ON target.id = source.id WHEN MATCHED THEN UPDATE ... WHEN NOT MATCHED THEN INSERT ...`; count inserts vs updates; log to `snowflake.sync.log`; update `last_sync_date`.
7. **Delete Table** — `DROP TABLE IF EXISTS "<db>"."<schema>"."<table>"`; reset `table_state`.
8. **Scheduled Sync** — `ir.cron` calls `table_config.action_export_data(incremental=True)` on its own interval.

---

## 7. Testing Plan (unchanged scope, confirming full coverage)

1. Unit tests mocking Snowpark `Session` for: connection test (success/fail), create database, create table, alter table (add/drop/alter column), export data (insert-only, update-only, mixed).
2. Integration test against a real Snowflake trial account for at least one full cycle: connect → create DB → create table → export → add a field → check migration → alter table → export again (verify incremental).
3. Wizard tests: column preview, data preview, export confirmation wizard.
4. Cron test: manually trigger and confirm `last_sync_date`, `last_sync_new_count`, `last_sync_updated_count`.
5. Security test: confirm "User" group cannot read `password`/`pat_token`, cannot delete tables, cannot edit connections.

---

## 8. Deployment Checklist (unchanged, confirmed complete)

- [ ] Install `snowflake-connector-python` on the Odoo server
- [ ] Whitelist Odoo's outbound IP in Snowflake network policy if restricted
- [ ] Create a dedicated Snowflake service user/role with least-privilege grants (CREATE DATABASE/TABLE, INSERT, ALTER on target scope only)
- [ ] Install module, set up security groups, assign users
- [ ] Create connection → Test Connection → confirm "Successfully Connected"
- [ ] Create Database → Refresh Table List
- [ ] Create Table Config → Auto-Generate Mapping → adjust columns/types → Create Table
- [ ] Set Filters (domain) as needed
- [ ] Export Data (manual) → verify in Snowsight
- [ ] Add/remove a field on the Odoo model → Check Migration → Alter Table → verify schema
- [ ] Configure Schedule → confirm cron runs and logs populate
- [ ] Review Sync Logs and Migration Logs for the first few automated runs

---

## 9. Confirmation — Nothing Removed, Nothing Missing

- All 6 "Essential Feature" blocks from the module page → mapped 1:1 to models/buttons above (Section 3.1–Section 3.6).
- All 6 "Highlighted Features" → same underlying features, just re-emphasized on the page (secure integration, automatic tables, flexible mapping, incremental sync, domain filtering, automated sync) — all present.
- All 17 screenshots on the page → each corresponds to a specific view/button called out explicitly in Section 3 (cross-referenced inline).
- The only dependency (`mail`/Discuss) → used for chatter/activity tracking on `snowflake.connection` and `snowflake.table.config` (audit trail of who changed config/credentials).
- Nothing beyond what's needed for these features was added (no speculative extra models); the only "extra" items (Dashboard, config wizards, migration/sync logs) exist solely because the screenshots/feature list explicitly reference a dashboard, cron configuration screen, column preview, and data preview.
