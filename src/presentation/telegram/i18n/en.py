STRINGS: dict[str, str] = {
    "backups": "💾 Backups",
    "admins": "👥 Admins",
    "remote_servers": "🖥 Remote Servers",
    "add_server": "➕ Add Server",
    "edit_server": "✏️ Edit Server",
    "delete_server": "🗑 Delete Server",
    "refresh_servers": "🔄 Refresh",
    "test_connection": "🔌 Test Connection",
    "audit": "🧾 Audit Log",
    "health": "🩺 System Health",
    "settings": "⚙️ Settings",
    "language": "🌐 Language",
    "help": "ℹ️ Help",
    "my_backups": "📦 My Backups",
    "back": "⬅️ Back",
    "home": "🏠 Home",
    "cancel": "❌ Cancel",
    "english": "🇬🇧 English",
    "persian": "🇮🇷 فارسی",
    "add_admin": "➕ Add Admin",
    "edit_admin": "✏️ Edit Admin",
    "delete_admin": "🗑 Delete Admin",
    "refresh_admins": "📋 Refresh Admins",
    "add_backup": "➕ Add Backup",
    "edit_backup": "✏️ Edit Backup",
    "delete_backup": "🗑 Delete Backup",
    "refresh_backups": "📋 Refresh Backups",
    "active_runs": "⚙️ Active Runs",
    "run_history": "📜 Run History",
    "run_details": "🔎 Details",
    "cancel_run": "⛔ Cancel Run",
    "retry_run": "🔁 Retry",
    "purge_artifact": "🗑 Purge Artifact",
    "edit": "✏️ Edit",
    "delete": "🗑 Delete",
    "backup_access": "📦 Backup Access",
    "admin_access": "👥 Admin Access",
    "change_name": "✏️ Change Name",
    "change_telegram_id": "🔢 Change Telegram ID",
    "change_language": "🌐 Change Language",
    "toggle_enabled": "🔘 Enable / Disable",
    "change_description": "📝 Change Description",
    "run_backup_now": "▶️ Run Backup Now",
    "reconfigure_backup": "🛠 Edit Full Configuration",
    "save_access": "✅ Save Access",
    "remove_all": "🚫 Remove All",
    "confirm_delete": "✅ Confirm Delete",
    "confirm": "✅ Confirm",
    "previous_page": "◀️ Previous",
    "next_page": "Next ▶️",
    "welcome": "Welcome.",
    "cancelled": "Operation cancelled.",
    "returned": "Returned home.",
    "choose_menu": "Please choose an option from the menu.",
    "invalid": "Invalid input; try again.",
    "too_many_invalid": "Too many invalid inputs; operation cancelled.",
    "expired": "The operation expired.",
    "no_choices": "Nothing is available to select.",
    "language_prompt": "Choose your language:",
    "language_saved": "Language updated.",
    "admins_title": "Admin management",
    "no_admins": "No admins found.",
    "admin_card": (
        "👤 Admin Details\n\n━━━━━━━━━━━━━━\n📌 Name\n<code>{name}</code>\n\n"
        "🆔 Telegram ID\n<code>{telegram_id}</code>\n\n✅ Status\n{status}\n\n"
        "🌐 Language\n{admin_language}\n\n━━━━━━━━━━━━━━\n📦 Allowed Backups\n{backups}\n\n"
        "━━━━━━━━━━━━━━"
    ),
    "enabled": "Enabled",
    "disabled": "Disabled",
    "none": "None",
    "language_en": "English",
    "language_fa": "Persian",
    "ask_admin_name": "Enter display name:",
    "ask_telegram_id": "Enter the numeric Telegram ID:",
    "ask_admin_language": "Choose the admin's preferred language:",
    "admin_confirmation": (
        "Name: {name}\nTelegram ID: {telegram_id}\nLanguage: {admin_language}\n\nCreate this admin?"
    ),
    "admin_created_access": "Admin created. Assign backup access:",
    "admin_saved": "Admin and access saved.",
    "select_admin_edit": "Select an admin to edit:",
    "select_admin_delete": "Select an admin to delete:",
    "edit_admin_prompt": "Choose what to edit:",
    "ask_new_name": "Enter the new name:",
    "ask_new_telegram_id": "Enter the new Telegram ID:",
    "admin_updated": "Admin updated.",
    "admin_status_changed": "Admin status changed.",
    "admin_delete_summary": (
        "Delete and disable this admin?\n\nAdmin: {name}\nTelegram ID: {telegram_id}\n"
        "Status: {status}\nLanguage: {admin_language}"
    ),
    "admin_deleted": "Admin deleted, disabled, and access revoked.",
    "select_backup_access": "Select allowed backups:",
    "access_saved": "Access updated.",
    "no_active_operations": "No backup operations are currently running.",
    "active_runs_overview": "{count} backup run(s) are currently active.",
    "active_runs_title": "Active backup runs: {count}",
    "no_run_history": "No backup run history is available.",
    "run_history_header": "Run history — page {page}/{pages} ({total} total)",
    "run_history_footer": "Run history navigation",
    "run_list_card": (
        "🔎 Run #<code>{run_id}</code> · <code>{profile}</code>\n\n━━━━━━━━━━━━━━\n"
        "✅ Status\n{status}\n\n⚙️ Stage\n{stage}\n\n🕒 Created\n{created}\n\n━━━━━━━━━━━━━━"
    ),
    "run_detail_card": (
        "🔎 <b>Backup Run Details</b>\n━━━━━━━━━━━━━━\n🆔 <b>Run ID:</b> <code>{run_id}</code>\n"
        "📌 <b>Profile:</b> <code>{profile}</code>\n✅ <b>Status:</b> {status}\n"
        "⚙️ <b>Stage:</b> {stage}\n▶️ <b>Trigger:</b> {trigger}\n{source_details}\n"
        "━━━━━━━━━━━━━━\n"
        "🕒 <b>Created:</b> {created}\n📅 <b>Scheduled:</b> {scheduled}\n"
        "🚀 <b>Started:</b> {started}\n🏁 <b>Finished:</b> {finished}\n"
        "🌍 <b>Timezone:</b> {timezone}\n━━━━━━━━━━━━━━\n"
        "⚠️ <b>Error Code:</b> <code>{error_code}</code>\n"
        "📝 <b>Safe Error:</b> {error_message}\n━━━━━━━━━━━━━━\n"
        "📦 <b>Artifact:</b> {artifact}\n📨 <b>Deliveries:</b> "
        "Pending: <code>{pending}</code> · Succeeded: <code>{succeeded}</code>\n"
        "Failed: <code>{failed}</code> · Skipped: <code>{skipped}</code>\n━━━━━━━━━━━━━━"
    ),
    "run_cancel_requested": "Cancellation requested for run <code>#{run_id}</code>.",
    "run_delivery_retry_requested": (
        "Failed deliveries for run <code>#{run_id}</code> were queued safely."
    ),
    "run_retry_queued": "A new retry run was queued as run <code>#{run_id}</code>.",
    "run_artifact_purge_complete": (
        "The verified retained artifact for run <code>#{run_id}</code> was purged."
    ),
    "run_artifact_purge_confirm": (
        "Permanently purge the verified retained artifact for run <code>#{run_id}</code>? "
        "Database history will be kept."
    ),
    "run_action_unavailable": "That run action is no longer available or is unsafe.",
    "run_artifact_retained": "Retained ({size:,} bytes)",
    "run_artifact_purged": "Purged; database history retained",
    "run_artifact_delivered": "Delivery completed ({size:,} bytes)",
    "run_error_cancelled": "The run was cancelled by an operator.",
    "run_error_timeout": "The configured backup deadline was exceeded.",
    "run_error_delivery_action_required": (
        "Automatic delivery stopped and requires operator action."
    ),
    "run_error_upload_limit": "The artifact exceeds the configured upload limit.",
    "run_error_artifact_invalid": "The retained artifact is unavailable or failed verification.",
    "run_error_generic": (
        "The run failed; detailed diagnostics are available only in protected logs."
    ),
    "run_trigger_manual": "Manual",
    "run_trigger_scheduled": "Scheduled",
    "run_status_queued": "Queued",
    "run_status_running": "Running",
    "run_status_delivery_pending": "Delivery pending",
    "run_status_succeeded": "Succeeded",
    "run_status_failed": "Failed",
    "run_status_cancelled": "Cancelled",
    "run_status_skipped": "Skipped",
    "run_stage_queued": "Queued",
    "run_stage_backup": "Backup",
    "run_stage_verify_source": "Verifying source",
    "run_stage_compress": "Compressing",
    "run_stage_encrypt": "Encrypting",
    "run_stage_checksum": "Checksumming",
    "run_stage_finalize": "Finalizing",
    "run_stage_pending_delivery": "Pending delivery",
    "run_stage_telegram_delivery": "Telegram delivery",
    "run_stage_cleanup": "Cleanup",
    "run_stage_complete": "Complete",
    "no_backups": "No backups found.",
    "backup_card": (
        "📦 <b>Backup Profile</b>\n━━━━━━━━━━━━━━\n📌 <b>Name:</b> <code>{name}</code>\n"
        "📁 <b>Source:</b> {source}\n🖥 <b>Remote Server:</b> <code>{server}</code>\n"
        "⏰ <b>Schedule:</b> {schedule}{next_run}\n🌍 <b>Timezone:</b> {timezone}\n"
        "{source_details}\n\n{archive_compression}\n\n━━━━━━━━━━━━━━\n"
        "🔐 <b>Encryption:</b> {encryption}\n📋 <b>Profile Status:</b> {status}\n"
        "✅ <b>Enabled:</b> {enabled}\n━━━━━━━━━━━━━━\n👥 <b>Access:</b> {admins}\n━━━━━━━━━━━━━━"
    ),
    "ask_backup_name": "📦 <b>Backup Name</b>\nEnter a name for this backup profile.",
    "ask_edit_backup_name": (
        "📦 <b>Backup Name</b>\nEnter a new name, or send <code>-</code> to keep “{current}”."
    ),
    "ask_edit_description": (
        "📝 <b>Description</b>\nEnter a new description, send <code>-</code> to keep it, "
        "or <code>!</code> to clear it."
    ),
    "ask_edit_database_" + "password": (
        "Enter a replacement database password, or send “-” to preserve the current password:"
    ),
    "ask_edit_age_" + "passphrase": (
        "Enter a replacement Age passphrase, or send “-” to preserve the current passphrase:"
    ),
    "edit_keep_hint": "During this edit, send “-” to preserve an existing non-secret value.",
    "ask_description": (
        "📝 <b>Description</b>\nEnter a description, or send <code>-</code> to leave it empty."
    ),
    "choose_source": "🧩 <b>Source Type</b>\nChoose what you want to back up.",
    "source_database": "🗄 Database",
    "source_files_directories": "📁 Files & Directories",
    "source_gitlab": "🦊 GitLab",
    "database_postgresql": "PostgreSQL",
    "database_mysql": "MySQL / MariaDB",
    "database_mssql": "Microsoft SQL Server",
    "tls_disabled": "TLS Disabled",
    "tls_preferred": "TLS Preferred",
    "tls_required": "TLS Required",
    "tls_verify_ca": "TLS Verify CA",
    "tls_verify_full": "TLS Verify Full",
    "location_local": "Local Host",
    "location_remote": "Remote Server",
    "schedule_manual": "Manual Only",
    "schedule_once": "Once",
    "schedule_minutes": "Every N Minutes",
    "schedule_hours": "Every N Hours",
    "schedule_days": "Every N Days",
    "schedule_minutes_value": "Every {value} minutes",
    "schedule_hours_value": "Every {value} hours",
    "schedule_days_value": "Every {value} days",
    "schedule_daily_value": "Daily at {value}",
    "schedule_weekly_value": "Weekly on day {day} at {time}",
    "schedule_monthly_value": "Monthly on day {day} at {time}",
    "schedule_daily": "Daily",
    "schedule_weekly": "Weekly",
    "schedule_monthly": "Monthly",
    "compression_none": "◻️ None",
    "archive_tar": "📦 TAR ✓",
    "archive_zip": "📦 ZIP",
    "archive_7z": "📦 7-Zip",
    "archive_tar_name": "TAR",
    "archive_zip_name": "ZIP",
    "archive_7z_name": "7-Zip",
    "compression_zstandard": "🚀 Zstandard ✓",
    "compression_gzip": "🌐 Gzip",
    "compression_xz": "💾 XZ",
    "compression_lz4": "⚡ LZ4",
    "compression_bzip2": "📚 Bzip2",
    "compression_zip": "ZIP",
    "compression_7z": "7z",
    "compression_zstandard_name": "Zstandard",
    "compression_gzip_name": "Gzip",
    "compression_xz_name": "XZ (LZMA2)",
    "compression_bzip2_name": "Bzip2 (legacy)",
    "compression_lz4_name": "LZ4",
    "compression_deflate_name": "Deflate (built into ZIP)",
    "compression_lzma2_name": "LZMA2 (built into 7-Zip)",
    "preset_fast": "⚡ Fast",
    "preset_balanced": "⚖️ Balanced ✓",
    "preset_high": "High",
    "preset_maximum": "📦 Maximum Compression",
    "preset_fast_display": "Fast",
    "preset_balanced_display": "Balanced",
    "preset_high_display": "Maximum Compression",
    "preset_maximum_display": "Maximum Compression",
    "preset_not_applicable": "Not applicable",
    "encryption_none": "No Encryption",
    "encryption_age_public": "Age Public-Key — recommended",
    "encryption_age_" + "passphrase": "Age Passphrase",
    "ask_server_name": "Enter a unique server name:",
    "ask_server_host": "Enter the host name or IP address:",
    "ask_server_port": "Enter the SSH port:",
    "ask_server_username": "Enter the SSH username:",
    "ask_server_private_key": (
        "Send the SSH private key. It will be encrypted and never displayed again:"
    ),
    "ask_server_" + "passphrase": "Enter the private-key passphrase, or send “-”:",
    "ask_server_name_edit": "Enter the replacement server name:",
    "server_fingerprint_confirm": (
        "Connection succeeded. Trust this exact SSH host-key fingerprint?\n\n{fingerprint}"
    ),
    "server_saved": "Remote server saved with pinned host-key verification.",
    "server_updated": "Remote server updated after trusting its new host-key fingerprint.",
    "edit_server_prompt": "Choose how to edit this remote server:",
    "reconfigure_server": "🔐 Replace Connection Details",
    "server_status_changed": "Remote server status changed.",
    "server_deleted": "Remote server deleted.",
    "server_test_ok": "Connection succeeded and the host key matches.",
    "servers_title": "Remote server management",
    "no_servers": "No remote servers found.",
    "server_card": (
        "🖥 <b>Remote Server Details</b>\n━━━━━━━━━━━━━━\n"
        "📌 <b>Server Name:</b> <code>{name}</code>\n"
        "🌐 <b>Address:</b> <code>{host}:{port}</code>\n👤 <b>User:</b> <code>{username}</code>\n"
        "🔐 <b>Authentication:</b> {authentication}\n"
        "🛡 <b>Fingerprint:</b> <code>{fingerprint}</code>\n"
        "✅ <b>Status:</b> {status}\n━━━━━━━━━━━━━━"
    ),
    "ssh_private_key": "SSH private key",
    "choose_database_engine": "Choose one database engine:",
    "choose_tls": "Choose the database TLS/SSL mode:",
    "ask_tls_ca": "Send the trusted CA certificate in PEM format:",
    "ask_database_host": "Enter the database host:",
    "ask_database_port": "Enter the database port:",
    "ask_database_name": "Enter exactly one database name:",
    "ask_database_username": "Enter the database username:",
    "ask_database_" + "password": "Enter the database password. It will never be displayed again:",
    "ask_mssql_docker": "Is SQL Server running inside Docker?",
    "ask_mssql_backup_path": "Enter the SQL Server/container backup destination path:",
    "ask_mssql_host_path": "Enter the corresponding remote-host mounted path:",
    "choose_location": "Choose the file/directory source location:",
    "ask_paths": "Enter one or more absolute file / directory paths, one per line:",
    "choose_gitlab_location": "📍 <b>GitLab Location</b>\nWhere is this GitLab instance running?",
    "ask_gitlab_container": "🐳 <b>GitLab Container</b>\nEnter the Docker container name.",
    "ask_gitlab_worker_path": (
        "📂 <b>GitLab Backup Directory</b>\nEnter the path visible inside the worker container."
    ),
    "ask_gitlab_remote_path": (
        "📂 <b>GitLab Backup Directory</b>\nEnter the directory on the remote server "
        "(for example, <code>/opt/gitlab/data/backups</code>)."
    ),
    "no_remote_servers_available": (
        "⚠️ <b>No Remote Servers Available</b>\n"
        "You do not have any enabled remote servers yet.\n"
        "Add a remote server first, or choose Local Host."
    ),
    "gitlab_exclusive_warning": (
        "⚠️ GitLab backup directory is managed exclusively by this profile. Existing backup "
        "files in this directory will be removed before each new backup.\n\n"
        "GitLab application backups do not include all configuration/secrets. Back up "
        "gitlab.rb and gitlab-secrets.json separately for complete disaster recovery."
    ),
    "gitlab_source_details": (
        "\n🦊 <b>GitLab source</b>\n📍 <b>Location:</b> {location}"
        "\n<b>Container:</b> {container}\n<b>Backup Path:</b> {backup_path}"
    ),
    "choose_schedule": "Choose a schedule. Times use Backup Timezone: {timezone}",
    "ask_schedule_interval": "Enter N as a positive whole number:",
    "ask_schedule_daily": "Enter time as HH:MM:",
    "ask_schedule_weekly": "Enter weekday (0=Monday..6=Sunday) and time as: 0 HH:MM",
    "ask_schedule_monthly": "Enter day (1..28) and time as: 1 HH:MM",
    "choose_remote_server": "🌐 <b>Remote Server</b>\nChoose the server hosting this source.",
    "choose_assigned_admins": "Choose assigned admins, then Save Access:",
    "archive_explanation": (
        "📦 Archive Format\n\nAn archive groups files into one package. It does not "
        "necessarily reduce their size; compression is selected separately.\n\n"
        "━━━━━━━━━━━━━━\n🗃 TAR (Recommended)\n• Extension: .tar\n"
        "• Container only; preserves Unix metadata\n"
        "• Compatibility: excellent on Linux/macOS; widely supported elsewhere\n"
        "• Use for: server backups and file trees\n\n"
        "━━━━━━━━━━━━━━\n🗃 ZIP\n• Extension: .zip\n• Cross-platform container\n"
        "• Compatibility: built into Windows, macOS, and Linux desktops\n"
        "• Use for: convenient sharing\n\n"
        "━━━━━━━━━━━━━━\n🗃 7Z\n• Extension: .7z\n• Modern flexible container\n"
        "• Compatibility: requires 7-Zip or a compatible extractor on many systems\n"
        "• Use for: tooling-rich environments"
    ),
    "compression_explanation": (
        "🗜 Compression\n\nCompression reduces the archive size. Higher ratios usually cost "
        "more CPU and time. The algorithm is independent of the archive container.\n\n"
        "━━━━━━━━━━━━━━\n◻️ None · no extension · ratio none · fastest · CPU minimal\n"
        "🚀 Zstandard (Recommended) · .zst · ratio high · fast · CPU medium\n"
        "🌐 Gzip · .gz · ratio medium · medium speed · CPU medium\n"
        "📚 bzip2 · .bz2 · ratio high · slow · CPU high\n"
        "💾 XZ (LZMA2) · .xz · ratio very high · very slow · CPU high\n"
        "⚡ LZ4 · .lz4 · ratio low · extremely fast · CPU low"
    ),
    "compression_lz4_unavailable": (
        "⚡ LZ4 is not available in the current backup worker. Choose another algorithm; "
        "your backup configuration has not been changed."
    ),
    "choose_compression_preset": (
        "🗜 Compression Preset\n\nChoose the speed and size balance. Balanced is recommended."
    ),
    "choose_encryption": "Choose encryption:",
    "ask_age_recipients": "Enter Age recipient public keys, one per line:",
    "ask_age_" + "passphrase": (
        "Enter the Age passphrase. It will be encrypted and never displayed again:"
    ),
    "profile_confirmation": (
        "📋 <b>Confirm Backup Profile</b>\n━━━━━━━━━━━━━━\n"
        "📌 <b>Name:</b> <code>{name}</code>\n📁 <b>Source:</b> {source}\n"
        "🖥 <b>Remote Server:</b> <code>{server}</code>\n"
        "⏰ <b>Schedule:</b> {schedule}\n🌍 <b>Timezone:</b> {timezone}\n"
        "{source_details}\n\n{archive_compression}\n\n━━━━━━━━━━━━━━\n"
        "🔐 <b>Encryption:</b> {encryption}\n📋 <b>Profile Status:</b> {status}\n"
        "✅ <b>Enabled:</b> {enabled}\n━━━━━━━━━━━━━━\n"
        "👥 <b>Access:</b> {admins}\n━━━━━━━━━━━━━━\nNo secret values are shown."
    ),
    "compression_summary": (
        "<b>Algorithm:</b> {algorithm}\n<b>Preset:</b> {preset}\n"
        "<b>Extension:</b> <code>{extension}</code>"
    ),
    "archive_summary": (
        "<b>Format:</b> {archive}\n<b>Extension:</b> <code>{extension}</code>\n"
        "<b>Output:</b> <code>{output}</code>"
    ),
    "archive_compression_sections": (
        "━━━━━━━━━━━━━━\n🗜 <b>Compression</b>\n{compression}\n\n"
        "━━━━━━━━━━━━━━\n📦 <b>Archive</b>\n{archive}"
    ),
    "file_directory_path": "📂 <b>Path (File / Directory):</b> <code>{path}</code>",
    "file_source_details": "📍 <b>Location:</b> {location}\n{paths}",
    "database_source_details": "⚙️ <b>Engine:</b> {engine}",
    "run_source_details": "📁 <b>Source:</b> {source}\n{details}",
    "source_location_local": "Local Host",
    "source_location_remote": "Remote Server",
    "backup_timezone": "🕓 Backup Timezone",
    "ask_backup_timezone": "Enter an IANA backup timezone (for example, Asia/Tehran):",
    "status_draft": "Draft",
    "status_archived": "Archived",
    "backup_created": "Draft “{name}” saved.",
    "backup_setup_expired": (
        "⚠️ This setup session expired. Please restart backup profile creation."
    ),
    "backup_confirm_missing": "⚠️ Missing field: {field}. Return to that step and complete it.",
    "backup_confirm_invalid_state": (
        "⚠️ This confirmation does not match the current setup step. Review the profile again."
    ),
    "backup_confirm_stale": (
        "⚠️ This confirmation button is outdated. Use the newest confirmation message."
    ),
    "backup_confirm_unavailable": (
        "⚠️ Profile saving is temporarily unavailable because secure storage is not configured."
    ),
    "backup_confirm_validation_failed": "⚠️ Profile validation failed: {reason}",
    "backup_confirm_save_failed": (
        "⚠️ The profile could not be saved because of an internal error. "
        "Your setup was not accepted."
    ),
    "select_backup_edit": "Select a backup to edit:",
    "select_backup_delete": "Select a backup to delete:",
    "edit_backup_prompt": "Choose what to edit:",
    "backup_updated": "Backup updated.",
    "backup_configuration_updated": "Backup “{name}” configuration updated atomically.",
    "current_value": "Current value:",
    "existing_credential": ("Existing secret is set — enter a new value or keep the existing one."),
    "backup_status_changed": "Backup status changed.",
    "backup_run_queued": "Backup queued. Run <code>#{run_id}</code>.",
    "backup_run_active": "A run is already active: {stage}, started {started}.",
    "backup_backpressure": "New backups are paused: delivery storage is at its safety limit.",
    "backup_delete_summary": (
        "Archive this backup?\n\nName: {name}\nSource type: {source}\nStatus: {status}"
    ),
    "backup_deleted": "Backup archived and access revoked.",
    "select_admin_access": "Select allowed admins:",
    "my_backups_title": "Your assigned backups:",
    "audit_empty": "No audit events found.",
    "help_text": "Use the menu buttons to work with the bot.",
    "permission_invalid": "That option is not available.",
    "settings_text": (
        "Connection: MTProto Bot\nSOCKS5: {proxy}\nUpload limit: {limit:,} bytes\n"
        "Timezone: {timezone}\nSQLite: enabled"
    ),
    "health_text": (
        "System: {status}\n{live} Live  {ready} Ready\n"
        "{database} Control DB  {schema} Schema {revision}\n"
        "{worker} Worker  {scheduler} Scheduler  {telegram} Telegram\n"
        "Disk free: {disk:,} MB | Pending: {pending:,} MB\n"
        "Runs Q/R/F: {queued}/{running}/{failed} | Stale leases: {stale}\n"
        "Retained failed artifacts: {retained} | Tools: {tools}"
    ),
    "active": "Active",
    "inactive": "Inactive",
    "yes": "Yes",
    "no": "No",
}
