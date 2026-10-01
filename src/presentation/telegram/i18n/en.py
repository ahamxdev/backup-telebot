from presentation.telegram.i18n.message import section

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
    "active_runs_footer": section(
        "🧭", "Active Backups", "Use the controls above or return to the backups menu."
    ),
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
    "welcome": section(
        "👋", "Welcome", "Manage your backups, runs, servers, and access from the menu below."
    ),
    "cancelled": section("❎", "Operation Cancelled", "No changes were saved."),
    "returned": section("🏠", "Home", "Choose an option from the menu below."),
    "choose_menu": section("🧭", "Main Menu", "Choose an option below."),
    "invalid": section("⚠️", "Invalid Input", "Please check the value and try again."),
    "too_many_invalid": section(
        "⛔", "Operation Cancelled", "Too many invalid attempts were received. Please start again."
    ),
    "expired": section("⌛", "Session Expired", "Please start the operation again."),
    "no_choices": section("ℹ️", "Nothing to Select", "No eligible items are currently available."),
    "language_prompt": section("🌐", "Language", "Choose your preferred language."),
    "language_saved": section("✅", "Language Updated", "Your preference has been saved."),
    "admins_title": section("👥", "Admin Access", "Manage administrators and their backup access."),
    "no_admins": section("👥", "No Admins", "No administrator accounts have been created yet."),
    "admin_card": (
        "👤 <b>Admin Details</b>\n━━━━━━━━━━━━━━\n📌 <b>Name:</b> <code>{name}</code>\n"
        "🆔 <b>Telegram ID:</b> <code>{telegram_id}</code>\n📋 <b>Status:</b> {status}\n"
        "🌐 <b>Language:</b> {admin_language}\n━━━━━━━━━━━━━━\n"
        "📦 <b>Allowed Backups:</b> {backups}\n━━━━━━━━━━━━━━"
    ),
    "enabled": "🟢 Enabled",
    "disabled": "⚪ Disabled",
    "none": "None",
    "language_en": "English",
    "language_fa": "Persian",
    "ask_admin_name": section("👤", "Admin Name", "Enter a display name."),
    "ask_telegram_id": section("🆔", "Telegram ID", "Enter the numeric Telegram user ID."),
    "ask_admin_language": section("🌐", "Admin Language", "Choose the preferred language."),
    "admin_confirmation": (
        "👤 <b>Confirm Admin</b>\nName: <code>{name}</code>\nTelegram ID: "
        "<code>{telegram_id}</code>\nLanguage: {admin_language}\nCreate this admin?"
    ),
    "admin_created_access": section("✅", "Admin Created", "Now assign backup access."),
    "admin_saved": section("✅", "Admin Saved", "The account and backup access are up to date."),
    "select_admin_edit": section("✏️", "Edit Admin", "Choose an administrator."),
    "select_admin_delete": section("🗑", "Delete Admin", "Choose an administrator."),
    "edit_admin_prompt": section("✏️", "Edit Admin", "Choose the field to update."),
    "ask_new_name": section("👤", "New Name", "Enter the administrator's new display name."),
    "ask_new_telegram_id": section("🆔", "New Telegram ID", "Enter the new numeric user ID."),
    "admin_updated": section("✅", "Admin Updated", "The administrator details were saved."),
    "admin_status_changed": section(
        "✅", "Status Updated", "The administrator status was changed."
    ),
    "admin_delete_summary": (
        "⚠️ <b>Delete Admin?</b>\nAdmin: <code>{name}</code>\nTelegram ID: "
        "<code>{telegram_id}</code>\nStatus: {status}\nLanguage: {admin_language}\n"
        "Access will be revoked and the account disabled."
    ),
    "admin_deleted": section("✅", "Admin Deleted", "The account was disabled and access revoked."),
    "select_backup_access": section(
        "📦", "Backup Access", "Select the backups this admin may use."
    ),
    "access_saved": section("✅", "Access Updated", "The access assignments were saved."),
    "no_active_operations": section(
        "✅", "No Active Backups", "There are currently no backup operations running."
    ),
    "active_runs_overview": section(
        "⚙️", "Active Backups", "{count} backup run(s) are in progress."
    ),
    "active_runs_title": section(
        "⚙️", "Active Backup Runs", "Currently active: <code>{count}</code>"
    ),
    "no_run_history": section("📜", "No Run History", "No backup runs have been recorded yet."),
    "run_history_header": section(
        "📜", "Run History", "Page {page} of {pages} · <code>{total}</code> total"
    ),
    "run_history_footer": section("🧭", "Run History", "Use the buttons below to navigate."),
    "size_unknown": "Unknown",
    "run_size_summary": (
        "📦 Original Backup Size: {original_size}\n🗜 Final Backup Size: {final_size}"
    ),
    "run_list_card": (
        "🔎 <b>Run #</b><code>{run_id}</code> · <code>{profile}</code>\n━━━━━━━━━━━━━━\n"
        "📋 <b>Status:</b> {status}\n⚙️ <b>Stage:</b> {stage}\n🕒 <b>Created:</b> {created}"
        "{size_summary}\n━━━━━━━━━━━━━━"
    ),
    "run_detail_card": (
        "🔎 <b>Backup Run Details</b>\n━━━━━━━━━━━━━━\n🆔 <b>Run ID:</b> <code>{run_id}</code>\n"
        "📌 <b>Profile:</b> <code>{profile}</code>\n📋 <b>Status:</b> {status}\n"
        "⚙️ <b>Stage:</b> {stage}\n▶️ <b>Trigger:</b> {trigger}\n{source_details}\n"
        "━━━━━━━━━━━━━━\n"
        "🕒 <b>Created:</b> {created}\n📅 <b>Scheduled:</b> {scheduled}\n"
        "🚀 <b>Started:</b> {started}\n🏁 <b>Finished:</b> {finished}\n"
        "🌍 <b>Timezone:</b> {timezone}\n━━━━━━━━━━━━━━\n"
        "⚠️ <b>Error Code:</b> <code>{error_code}</code>\n"
        "📝 <b>Safe Error:</b> {error_message}\n━━━━━━━━━━━━━━\n"
        "📦 <b>Original Backup Size:</b> {original_size}\n"
        "🗜 <b>Final Backup Size:</b> {final_size}\n"
        "📁 <b>Artifact:</b> {artifact}\n📨 <b>Deliveries:</b> "
        "Pending: <code>{pending}</code> · Succeeded: <code>{succeeded}</code>\n"
        "Failed: <code>{failed}</code> · Skipped: <code>{skipped}</code>\n━━━━━━━━━━━━━━"
    ),
    "run_cancel_requested": section(
        "⛔", "Cancellation Requested", "Run <code>#{run_id}</code> will stop safely."
    ),
    "run_delivery_retry_requested": (
        "🔁 <b>Delivery Retry Queued</b>\nFailed deliveries for run "
        "<code>#{run_id}</code> will be retried."
    ),
    "run_retry_queued": section("✅", "Retry Queued", "New run: <code>#{run_id}</code>"),
    "run_artifact_purge_complete": (
        "✅ <b>Artifact Purged</b>\nThe retained artifact for run "
        "<code>#{run_id}</code> was removed."
    ),
    "run_artifact_purge_confirm": (
        "⚠️ <b>Purge Retained Artifact?</b>\nPermanently remove the verified artifact for run "
        "<code>#{run_id}</code>? Database history will be kept."
    ),
    "run_action_unavailable": section(
        "⚠️", "Action Unavailable", "This run action is no longer available or safe to perform."
    ),
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
    "run_status_queued": "🟡 Queued",
    "run_status_running": "🟡 Running",
    "run_status_delivery_pending": "🟡 Delivery pending",
    "run_status_succeeded": "🟢 Succeeded",
    "run_status_failed": "🔴 Failed",
    "run_status_cancelled": "⚪ Cancelled",
    "run_status_skipped": "⚪ Skipped",
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
    "no_backups": section("📦", "No Backup Profiles", "No backup profiles have been created yet."),
    "backups_title": section("📦", "Backup Profiles", "Manage profiles, runs, and access."),
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
    "ask_edit_database_" + "password": section(
        "🔐", "Database Password", "Enter a replacement, or send <code>-</code> to keep it."
    ),
    "ask_edit_age_" + "passphrase": section(
        "🔐", "Age Passphrase", "Enter a replacement, or send <code>-</code> to keep it."
    ),
    "edit_keep_hint": section(
        "ℹ️", "Editing", "Send <code>-</code> to keep an existing non-secret value."
    ),
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
    "schedule_cron_value": "Cron: {value}",
    "schedule_daily": "Daily",
    "schedule_weekly": "Weekly",
    "schedule_monthly": "Monthly",
    "schedule_cron": ("📅 Cron Schedule"),
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
    "ask_server_name": section("🖥", "Server Name", "Enter a unique name for this server."),
    "ask_server_host": section("🌐", "Server Address", "Enter the hostname or IP address."),
    "ask_server_port": section("🔌", "SSH Port", "Enter the SSH port number."),
    "ask_server_username": section("👤", "SSH Username", "Enter the SSH account name."),
    "ask_server_private_key": (
        "🔐 <b>SSH Private Key</b>\nSend the private key. It will be encrypted and never displayed."
    ),
    "ask_server_" + "passphrase": section(
        "🔑", "Key Passphrase", "Enter the passphrase, or send <code>-</code> if there is none."
    ),
    "ask_server_name_edit": section("🖥", "Server Name", "Enter the replacement name."),
    "server_fingerprint_confirm": (
        "🛡 <b>Trust SSH Host Key?</b>\nConnection succeeded. Verify and trust this fingerprint:\n"
        "<code>{fingerprint}</code>"
    ),
    "server_saved": section("✅", "Server Saved", "SSH host-key verification is pinned."),
    "server_updated": section(
        "✅", "Server Updated", "The new SSH host key is trusted and pinned."
    ),
    "edit_server_prompt": section("✏️", "Edit Remote Server", "Choose what to update."),
    "reconfigure_server": "🔐 Replace Connection Details",
    "server_status_changed": section(
        "✅", "Status Updated", "The remote server status was changed."
    ),
    "server_deleted": section("✅", "Server Deleted", "The remote server was removed."),
    "server_delete_prompt": section(
        "⚠️", "Delete Remote Server?", "Review the server below, then confirm permanent deletion."
    ),
    "server_test_ok": section(
        "✅", "Connection Verified", "SSH connected and the host key matches."
    ),
    "servers_title": section("🖥", "Remote Servers", "Manage trusted backup source servers."),
    "no_servers": section("🖥", "No Remote Servers", "No remote servers have been added yet."),
    "server_card": (
        "🖥 <b>Remote Server Details</b>\n━━━━━━━━━━━━━━\n"
        "📌 <b>Server Name:</b> <code>{name}</code>\n"
        "🌐 <b>Address:</b> <code>{host}:{port}</code>\n👤 <b>User:</b> <code>{username}</code>\n"
        "🔐 <b>Authentication:</b> {authentication}\n"
        "🛡 <b>Fingerprint:</b> <code>{fingerprint}</code>\n"
        "📋 <b>Status:</b> {status}\n━━━━━━━━━━━━━━"
    ),
    "ssh_private_key": "SSH private key",
    "choose_database_engine": section("🗄", "Database Engine", "Choose one database type."),
    "choose_tls": section("🛡", "Database TLS", "Choose the TLS/SSL verification mode."),
    "ask_tls_ca": section("📜", "Trusted CA", "Send the CA certificate in PEM format."),
    "ask_database_host": section("🌐", "Database Host", "Enter the hostname or IP address."),
    "ask_database_port": section("🔌", "Database Port", "Enter the port number."),
    "ask_database_name": section("🗄", "Database Name", "Enter exactly one database name."),
    "ask_database_username": section("👤", "Database Username", "Enter the backup account name."),
    "ask_database_" + "password": section(
        "🔐", "Database Password", "Enter the password. It will never be displayed."
    ),
    "ask_mssql_docker": section("🐳", "SQL Server Runtime", "Is SQL Server running in Docker?"),
    "ask_mssql_backup_path": section(
        "📂",
        "SQL Server Backup Path",
        "Enter the destination path inside SQL Server or its container.",
    ),
    "ask_mssql_host_path": section(
        "📂", "Remote Host Path", "Enter the corresponding mounted path on the remote host."
    ),
    "choose_location": section(
        "📍", "Source Location", "Choose where the files or directories live."
    ),
    "ask_paths": section(
        "📂", "Files & Directories", "Enter one or more absolute paths, one per line."
    ),
    "choose_gitlab_location": "📍 <b>GitLab Location</b>\nWhere is this GitLab instance running?",
    "ask_gitlab_container": "🐳 <b>GitLab Container</b>\nEnter the Docker container name.",
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
        "🛡 <b>Verified Native Acquisition</b>\nThe local helper removes only the exact native "
        "artifact after its staged size and SHA-256 have been verified.\n"
        "GitLab application backups do not include all configuration/secrets. Back up "
        "<code>gitlab.rb</code> and <code>gitlab-secrets.json</code> separately "
        "for complete recovery."
    ),
    "gitlab_source_details_local": (
        "\n🦊 <b>GitLab source</b>\n📍 <b>Location:</b> {location}\n<b>Container:</b> {container}"
    ),
    "gitlab_source_details_remote": (
        "\n🦊 <b>GitLab source</b>\n📍 <b>Location:</b> {location}"
        "\n<b>Container:</b> {container}\n<b>Backup Path:</b> {backup_path}"
    ),
    "choose_schedule": section(
        "⏰", "Backup Schedule", "Choose a schedule. Times use <code>{timezone}</code>."
    ),
    "ask_schedule_interval": section("🔁", "Schedule Interval", "Enter a positive whole number."),
    "ask_schedule_daily": section("🕒", "Daily Time", "Enter the time as <code>HH:MM</code>."),
    "ask_schedule_weekly": section(
        "📅",
        "Weekly Schedule",
        "Enter weekday and time as <code>0 HH:MM</code> (0=Monday, 6=Sunday).",
    ),
    "ask_schedule_monthly": section(
        "📅", "Monthly Schedule", "Enter day and time as <code>1 HH:MM</code> (day 1–28)."
    ),
    "ask_schedule_cron": (
        "📅 <b>Cron Schedule</b>\nTimezone: <code>{timezone}</code>.\nUse 5-f"
        "ield Linux cron in the configured backup timezone:\n<code>minute h"
        "our day-of-month month day-of-week</code>\n\n<code>30 3 * * *</code"
        "> → daily at 03:30\n<code>10 * * * *</code> → hourly at minute 10\n"
        "<code>0 2 1 * *</code> → 1st of each month at 02:00\n\nSend your cr"
        "on expression."
    ),
    "choose_remote_server": "🌐 <b>Remote Server</b>\nChoose the server hosting this source.",
    "choose_assigned_admins": section(
        "👥", "Admin Access", "Select administrators, then choose Save Access."
    ),
    "archive_explanation": (
        "📦 <b>Archive Format</b>\nAn archive groups files into one package. It does not "
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
        "🗜 <b>Compression</b>\nCompression reduces the archive size. Higher ratios usually cost "
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
        "🗜 <b>Compression Preset</b>\nChoose the speed and size balance. Balanced is recommended."
    ),
    "choose_encryption": section("🔐", "Encryption", "Choose how the final backup is protected."),
    "ask_age_recipients": section(
        "🔑", "Age Recipients", "Enter public recipient keys, one per line."
    ),
    "ask_age_" + "passphrase": (
        "🔐 <b>Age Passphrase</b>\nEnter the passphrase. It will be encrypted and never displayed."
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
    "timezone_heading": "Timezone",
    "ask_backup_timezone": section(
        "🕓", "Backup Timezone", "Enter an IANA timezone, for example <code>Asia/Tehran</code>."
    ),
    "status_draft": "🟡 Draft",
    "status_archived": "⚪ Archived",
    "backup_created": section(
        "✅", "Backup Profile Saved", "Draft <code>{name}</code> was created."
    ),
    "backup_setup_expired": (
        "⌛ <b>Setup Expired</b>\nThis session expired. Please restart backup profile creation."
    ),
    "backup_confirm_missing": (
        "⚠️ <b>Incomplete Profile</b>\nMissing field: <code>{field}</code>. "
        "Return to that step and complete it."
    ),
    "backup_confirm_invalid_state": (
        "⚠️ <b>Confirmation Mismatch</b>\nThis confirmation does not match the current setup step. "
        "Review the profile again."
    ),
    "backup_confirm_stale": (
        "⚠️ <b>Outdated Confirmation</b>\nUse the button on the newest confirmation message."
    ),
    "backup_confirm_unavailable": (
        "⚠️ <b>Saving Unavailable</b>\nSecure storage is not configured. Contact the operator."
    ),
    "backup_confirm_validation_failed": (
        "⚠️ <b>Validation Failed</b>\nPlease review the profile: {reason}"
    ),
    "backup_confirm_save_failed": (
        "⚠️ <b>Profile Not Saved</b>\nAn internal error occurred. No setup changes were accepted."
    ),
    "select_backup_edit": section("✏️", "Edit Backup", "Choose a backup profile."),
    "select_backup_delete": section("🗑", "Delete Backup", "Choose a backup profile."),
    "edit_backup_prompt": section("✏️", "Edit Backup", "Choose the field to update."),
    "backup_updated": section("✅", "Backup Updated", "The profile changes were saved."),
    "backup_configuration_updated": section(
        "✅", "Configuration Updated", "Backup <code>{name}</code> was updated atomically."
    ),
    "current_value": "<b>Current value:</b>",
    "existing_credential": (
        "🔐 <b>Credential Stored</b>\nEnter a new value or keep the existing secret."
    ),
    "backup_status_changed": section(
        "✅", "Status Updated", "The backup profile status was changed."
    ),
    "backup_run_queued": section(
        "✅", "Backup Queued", "Run <code>#{run_id}</code> is ready to start."
    ),
    "backup_run_active": section(
        "⚙️", "Backup Already Running", "Stage: {stage}\nStarted: {started}"
    ),
    "backup_backpressure": section(
        "⚠️", "Backups Paused", "Delivery storage has reached its configured safety limit."
    ),
    "backup_delete_summary": (
        "⚠️ <b>Archive Backup?</b>\nName: <code>{name}</code>\nSource: {source}\n"
        "Status: {status}\nAssigned access will be revoked."
    ),
    "backup_deleted": section("✅", "Backup Archived", "Assigned access has been revoked."),
    "select_admin_access": section(
        "👥", "Admin Access", "Select administrators allowed to use this backup."
    ),
    "my_backups_title": section("📦", "My Backups", "Choose one of your assigned backup profiles."),
    "audit_empty": section("🧾", "No Audit Events", "No activity has been recorded yet."),
    "audit_title": section("🧾", "Audit Log", "Recent system activity"),
    "help_text": section("ℹ️", "Help", "Use the menu buttons below to manage your backups."),
    "permission_invalid": section("⛔", "Option Unavailable", "You cannot use this option here."),
    "caption_backup_completed": "📦 <b>Backup Completed</b>",
    "caption_profile": "📌 <b>Profile:</b> {value}",
    "caption_status": "🟢 <b>Status:</b> Completed",
    "caption_started": "⏱ <b>Started:</b> {value}",
    "caption_completed": "🏁 <b>Completed:</b> {value}",
    "caption_schedule": "⏰ <b>Schedule:</b> {value}",
    "caption_next_run": "⏭ <b>Next run:</b> {value}",
    "caption_timezone": "🌍 <b>Timezone:</b> {value}",
    "caption_source": "🌐 <b>Source:</b> {value}",
    "caption_source_database_value": "Database",
    "caption_database_type": "🗄 <b>Database Type:</b> {value}",
    "caption_engine": "⚙️ <b>Engine:</b> {value}",
    "caption_server_name": "🏷 <b>Server name:</b> {value}",
    "caption_server": "🖥 <b>Server:</b> {value}",
    "caption_database": "💾 <b>Database:</b> {value}",
    "caption_path": "📂 <b>Path (File / Directory):</b> {value}",
    "caption_skipped": "⚠️ <b>Skipped:</b> {value}",
    "caption_skipped_symlink_one": "{count} unsafe symlink",
    "caption_skipped_symlink_many": "{count} unsafe symlinks",
    "caption_skipped_special_one": "{count} special file",
    "caption_skipped_special_many": "{count} special files",
    "caption_skipped_unreadable_one": "{count} unreadable entry",
    "caption_skipped_unreadable_many": "{count} unreadable entries",
    "caption_run_id": "🆔 <b>Run ID:</b> {value}",
    "caption_original_size": "📦 <b>Original Backup Size:</b> {value}",
    "caption_final_size": "🗜 <b>Final Backup Size:</b> {value}",
    "caption_part": "📄 <b>Part {number}/{total}</b>",
    "settings_text": (
        "⚙️ <b>Settings</b>\n\n<b>⚙️ Runtime</b>\nEnvironment: {environment} "
        "· Version: {version}\nPython: {python}\n\n<b>🗄 Database</b>\n{backend"
        "}: {database}\nSchema: {schema}\nMigration: {revision}\n\n<b>📡 Telegr"
        "am</b>\nMTProto Bot / Worker: {telegram}\nUpload limit: {limit:,} b"
        "ytes\n\n<b>🌐 Network</b>\nSOCKS5: {proxy} · {endpoint}\nConnection: {"
        "proxy_status}\n\n<b>🕒 Time</b>\nBackup timezone: {timezone}\nUTC: {ut"
        "c}\n\n<b>💾 Storage</b>\n{storage} · Free: {disk:,} MiB\nPending: {pen"
        "ding_count} / {pending:,} MiB\n{directories}\n\n<b>🗂 Backups</b>\nPro"
        "files: {profiles} · Active runs: {running}\nScheduler: {scheduler}"
        "\nFailed: {failed} · Retained artifacts: {retained}\n\n<b>🔐 Security"
        "</b>\nCredential encryption: {encryption}"
    ),
    "health_text": (
        "🩺 <b>System Health</b>\nOverall: {status}\n\n{live} Application\n{dat"
        "abase} Database ({backend})\n{schema} Schema / migrations\n{worker}"
        " Worker\n{scheduler} Scheduler\n{telegram} Telegram Bot / Worker\n{p"
        "roxy} Proxy\n{helper} GitLab Helper\n{storage} Storage\n\n<b>💾 Storag"
        "e</b>\nFree / used: {disk:,} / {used:,} MiB\nPending: {pending_coun"
        "t} / {pending:,} MiB\n\n<b>🗂 Operations</b>\nQueued / running / fail"
        "ed: {queued} / {running} / {failed}\nStale leases: {stale} · Retai"
        "ned artifacts: {retained}\nAvailable tools: {tools}\nMigration: {re"
        "vision}\n\n<b>📂 Runtime directories</b>\n{directories}\n\n🕒 Checked: {"
        "checked}"
    ),
    "active": "Active",
    "inactive": "Inactive",
    "yes": "Yes",
    "no": "No",
    "schedule_interval_mode": ("🕒 Interval Schedule"),
    "choose_schedule_mode": (
        "⏱ <b>Schedule Type</b>\nChoose Interval or Cron. Timezone: <code>{"
        "timezone}</code>.{current}"
    ),
    "current_schedule_mode": ("\n\n<b>Current mode:</b> {mode}\n{schedule}"),
    "available_paths": ("📌 <b>Available paths</b>\n{paths}\nTap a path to copy it."),
    "no_available_paths": ("No approved paths are currently verified as available by the worker."),
    "invalid_cron": (
        "⚠️ <b>Invalid Cron Expression</b>\nSend a valid 5-field Linux cron"
        " expression, for example <code>30 3 * * *</code>."
    ),
    "health_healthy": ("🟢 Healthy"),
    "health_warning": ("🟡 Warning"),
    "health_error": ("🔴 Error"),
    "health_disabled": ("⚪ Disabled / Not configured"),
    "health_unverified": ("🟡 Not verified"),
    "health_unavailable": (
        "🔴 <b>Health Unavailable</b>\nThe database health check failed. Check the service logs."
    ),
    "audit_entry": ("{icon} <b>{name}</b>\n🕒 {timestamp}\n{details}"),
    "audit_target": ("Target: {kind} <code>{target}</code>"),
    "audit_actor": ("Actor: <code>{actor}</code>"),
    "audit_detail": ("{label}: {value}"),
    "audit_unknown": ("Event {event}"),
    "audit_event_application_startup": ("Application Started"),
    "audit_event_proxy_mode": ("Proxy Mode"),
    "audit_event_telegram_connection_state": ("Telegram Connection"),
    "audit_event_backup_timezone_changed": ("Backup Timezone Changed"),
    "audit_event_backup_created": ("Backup Created"),
    "audit_event_backup_edited": ("Backup Updated"),
    "audit_event_backup_deleted": ("Backup Deleted"),
    "audit_event_backup_enabled": ("Backup Enabled"),
    "audit_event_backup_disabled": ("Backup Disabled"),
    "audit_event_admin_created": ("Administrator Created"),
    "audit_event_admin_edited": ("Administrator Updated"),
    "audit_event_admin_deleted": ("Administrator Deleted"),
    "audit_event_admin_enabled": ("Administrator Enabled"),
    "audit_event_admin_disabled": ("Administrator Disabled"),
    "audit_event_remote_server_created": ("Remote Server Created"),
    "audit_event_remote_server_edited": ("Remote Server Updated"),
    "audit_event_remote_server_deleted": ("Remote Server Deleted"),
    "audit_event_remote_server_enabled": ("Remote Server Enabled"),
    "audit_event_remote_server_disabled": ("Remote Server Disabled"),
    "audit_event_backup_draft_created": ("Backup Draft Created"),
    "audit_event_backup_profile_configured": ("Backup Configured"),
    "audit_event_backup_profile_configuration_updated": ("Backup Configuration Updated"),
    "audit_event_backup_access_granted": ("Backup Access Granted"),
    "audit_event_backup_access_revoked": ("Backup Access Revoked"),
    "audit_event_language_changed": ("Language Changed"),
    "audit_event_superadmin_bootstrap": ("Superadmin Initialized"),
    "audit_event_superadmin_restored": ("Superadmin Restored"),
    "audit_event_remote_server_trusted": ("Remote Server Trusted"),
    "audit_event_remote_server_retrusted": ("Remote Server Trust Updated"),
    "audit_event_remote_server_connection_tested": ("Remote Connection Tested"),
    "audit_event_backup_run_queued": ("Backup Run Queued"),
    "audit_event_backup_run_claimed": ("Backup Run Claimed"),
    "audit_event_backup_run_started": ("Backup Run Started"),
    "audit_event_backup_run_completed": ("Backup Run Completed"),
    "audit_event_backup_run_failed": ("Backup Run Failed"),
    "audit_event_backup_run_cancelled": ("Backup Run Cancelled"),
    "audit_event_backup_run_cancel_requested": ("Backup Run Cancellation Requested"),
    "audit_event_backup_run_retry_requested": ("Backup Run Retry Requested"),
    "audit_event_backup_run_recovered": ("Backup Run Recovered"),
    "audit_event_backup_run_delivery_pending": ("Backup Run Delivery Pending"),
    "audit_event_backup_run_delivery_retried": ("Backup Run Delivery Retried"),
    "audit_event_backup_run_artifact_purged": ("Backup Run Artifact Purged"),
    "audit_label_connected": ("Connected"),
    "audit_label_proxy_enabled": ("Proxy"),
    "audit_label_socks5_enabled": ("SOCKS5"),
    "audit_label_mode": ("Mode"),
    "audit_label_status": ("Status"),
    "audit_label_stage": ("Stage"),
    "audit_label_error_code": ("Error code"),
    "audit_label_admin_id": ("Admin ID"),
    "audit_label_timezone": ("Timezone"),
    "audit_label_backup": ("Backup"),
    "audit_label_backup_run": ("Run"),
    "audit_label_admin": ("Admin"),
    "audit_label_user": ("User"),
    "audit_label_remote_server": ("Remote server"),
    "audit_label_system_settings": ("Settings"),
    "health_idle": "⚪ Idle (no scheduled profiles)",
    "audit_label_backup_timezone": "Backup timezone",
    "audit_label_source_type": "Source",
    "audit_label_schedule": "Schedule",
    "audit_label_compression": "Compression",
    "audit_label_encryption": "Encryption",
    "audit_label_assigned_admin_count": "Assigned admins",
    "audit_label_enabled": "Enabled",
    "audit_label_database_credentials_changed": "Database password changed",
    "audit_label_age_unlock_changed": "Age passphrase changed",
    "audit_event_backup_run_succeeded": "Backup Run Succeeded",
    "audit_event_backup_run_retention_cleanup": "Retained Artifact Cleanup",
    "audit_event_authorization_denied": "Authorization Denied",
}
