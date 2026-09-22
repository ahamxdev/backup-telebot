STRINGS: dict[str, str] = {
    "backups": "💾 بکاپ‌ها",
    "admins": "👥 ادمین‌ها",
    "remote_servers": "🖥 سرورهای ریموت",
    "add_server": "➕ افزودن سرور",
    "edit_server": "✏️ ویرایش سرور",
    "delete_server": "🗑 حذف سرور",
    "refresh_servers": "🔄 بروزرسانی",
    "test_connection": "🔌 تست اتصال",
    "audit": "🧾 گزارش فعالیت",
    "health": "🩺 وضعیت سیستم",
    "settings": "⚙️ تنظیمات",
    "language": "🌐 زبان",
    "help": "ℹ️ راهنما",
    "my_backups": "📦 بکاپ‌های من",
    "back": "⬅️ بازگشت",
    "home": "🏠 خانه",
    "cancel": "❌ لغو",
    "english": "🇬🇧 English",
    "persian": "🇮🇷 فارسی",
    "add_admin": "➕ افزودن ادمین",
    "edit_admin": "✏️ ویرایش ادمین",
    "delete_admin": "🗑 حذف ادمین",
    "refresh_admins": "📋 بروزرسانی ادمین‌ها",
    "add_backup": "➕ افزودن بکاپ",
    "edit_backup": "✏️ ویرایش بکاپ",
    "delete_backup": "🗑 حذف بکاپ",
    "refresh_backups": "📋 بروزرسانی بکاپ‌ها",
    "active_runs": "⚙️ اجراهای فعال",
    "run_history": "📜 تاریخچه اجراها",
    "run_details": "🔎 جزئیات",
    "cancel_run": "⛔ لغو اجرا",
    "retry_run": "🔁 تلاش مجدد",
    "purge_artifact": "🗑 پاک‌سازی فایل",
    "edit": "✏️ ویرایش",
    "delete": "🗑 حذف",
    "backup_access": "📦 دسترسی بکاپ‌ها",
    "admin_access": "👥 دسترسی ادمین‌ها",
    "change_name": "✏️ تغییر نام",
    "change_telegram_id": "🔢 تغییر Telegram ID",
    "change_language": "🌐 تغییر زبان",
    "toggle_enabled": "🔘 فعال / غیرفعال",
    "change_description": "📝 تغییر توضیحات",
    "run_backup_now": "▶️ اجرای بکاپ اکنون",
    "reconfigure_backup": "🛠 ویرایش کامل تنظیمات",
    "save_access": "✅ ذخیره دسترسی",
    "remove_all": "🚫 حذف همه",
    "confirm_delete": "✅ تأیید حذف",
    "confirm": "✅ تأیید",
    "previous_page": "◀️ قبلی",
    "next_page": "بعدی ▶️",
    "welcome": "خوش آمدید.",
    "cancelled": "عملیات لغو شد.",
    "returned": "به خانه بازگشتید.",
    "choose_menu": "لطفاً یکی از گزینه‌های منو را انتخاب کنید.",
    "invalid": "ورودی نامعتبر است؛ دوباره تلاش کنید.",
    "too_many_invalid": "تعداد ورودی نامعتبر زیاد بود؛ عملیات لغو شد.",
    "expired": "زمان عملیات به پایان رسیده است.",
    "no_choices": "موردی برای انتخاب وجود ندارد.",
    "language_prompt": "زبان را انتخاب کنید:",
    "language_saved": "زبان بروزرسانی شد.",
    "admins_title": "مدیریت ادمین‌ها",
    "no_admins": "ادمینی یافت نشد.",
    "admin_card": (
        "👤 جزئیات ادمین\n\n━━━━━━━━━━━━━━\n📌 نام\n<code>{name}</code>\n\n"
        "🆔 شناسه تلگرام\n<code>{telegram_id}</code>\n\n✅ وضعیت\n{status}\n\n"
        "🌐 زبان\n{admin_language}\n\n━━━━━━━━━━━━━━\n📦 بکاپ‌های مجاز\n{backups}\n\n"
        "━━━━━━━━━━━━━━"
    ),
    "enabled": "فعال",
    "disabled": "غیرفعال",
    "none": "هیچ‌کدام",
    "language_en": "انگلیسی",
    "language_fa": "فارسی",
    "ask_admin_name": "نام نمایشی را وارد کنید:",
    "ask_telegram_id": "شناسه عددی تلگرام را وارد کنید:",
    "ask_admin_language": "زبان ترجیحی ادمین را انتخاب کنید:",
    "admin_confirmation": (
        "نام: {name}\nشناسه تلگرام: {telegram_id}\nزبان: {admin_language}\n\nاین ادمین ساخته شود؟"
    ),
    "admin_created_access": "ادمین ساخته شد. دسترسی بکاپ را تعیین کنید:",
    "admin_saved": "ادمین و دسترسی‌ها ذخیره شدند.",
    "select_admin_edit": "ادمین را برای ویرایش انتخاب کنید:",
    "select_admin_delete": "ادمین را برای حذف انتخاب کنید:",
    "edit_admin_prompt": "نوع ویرایش را انتخاب کنید:",
    "ask_new_name": "نام جدید را وارد کنید:",
    "ask_new_telegram_id": "شناسه تلگرام جدید را وارد کنید:",
    "admin_updated": "ادمین ویرایش شد.",
    "admin_status_changed": "وضعیت ادمین تغییر کرد.",
    "admin_delete_summary": (
        "این ادمین حذف و غیرفعال شود؟\n\nادمین: {name}\nشناسه تلگرام: {telegram_id}\n"
        "وضعیت: {status}\nزبان: {admin_language}"
    ),
    "admin_deleted": "ادمین حذف و غیرفعال شد و دسترسی‌ها لغو شدند.",
    "select_backup_access": "بکاپ‌های مجاز را انتخاب کنید:",
    "access_saved": "دسترسی‌ها بروزرسانی شدند.",
    "no_active_operations": "در حال حاضر هیچ عملیات بکاپی در حال اجرا نیست.",
    "active_runs_overview": "در حال حاضر {count} اجرای بکاپ فعال است.",
    "active_runs_title": "اجراهای فعال بکاپ: {count}",
    "no_run_history": "تاریخچه‌ای برای اجرای بکاپ وجود ندارد.",
    "run_history_header": "تاریخچه اجراها — صفحه {page} از {pages} (مجموع {total})",
    "run_history_footer": "پیمایش تاریخچه اجراها",
    "run_list_card": (
        "🔎 اجرا #<code>{run_id}</code> · <code>{profile}</code>\n\n━━━━━━━━━━━━━━\n"
        "✅ وضعیت\n{status}\n\n⚙️ مرحله\n{stage}\n\n🕒 ایجاد\n{created}\n\n━━━━━━━━━━━━━━"
    ),
    "run_detail_card": (
        "🔎 <b>جزئیات اجرای بکاپ</b>\n━━━━━━━━━━━━━━\n🆔 <b>شناسه اجرا:</b> <code>{run_id}</code>\n"
        "📌 <b>پروفایل:</b> <code>{profile}</code>\n✅ <b>وضعیت:</b> {status}\n"
        "⚙️ <b>مرحله:</b> {stage}\n▶️ <b>نوع اجرا:</b> {trigger}\n{source_details}\n"
        "━━━━━━━━━━━━━━\n"
        "🕒 <b>ایجاد:</b> {created}\n📅 <b>زمان‌بندی:</b> {scheduled}\n"
        "🚀 <b>شروع:</b> {started}\n🏁 <b>پایان:</b> {finished}\n"
        "🌍 <b>منطقه زمانی:</b> {timezone}\n━━━━━━━━━━━━━━\n"
        "⚠️ <b>کد خطا:</b> <code>{error_code}</code>\n📝 <b>خطای امن:</b> {error_message}\n"
        "━━━━━━━━━━━━━━\n📦 <b>فایل بکاپ:</b> {artifact}\n📨 <b>تحویل‌ها:</b> "
        "در انتظار: <code>{pending}</code> · موفق: <code>{succeeded}</code>\n"
        "ناموفق: <code>{failed}</code> · ردشده: <code>{skipped}</code>\n━━━━━━━━━━━━━━"
    ),
    "run_cancel_requested": "درخواست لغو اجرای شماره <code>{run_id}</code> ثبت شد.",
    "run_delivery_retry_requested": (
        "تحویل‌های ناموفق اجرای شماره <code>{run_id}</code> با ایمنی در صف قرار گرفت."
    ),
    "run_retry_queued": "اجرای مجدد با شماره <code>{run_id}</code> در صف قرار گرفت.",
    "run_artifact_purge_complete": (
        "فایل نگهداری‌شده و تأییدشده اجرای شماره <code>{run_id}</code> پاک شد."
    ),
    "run_artifact_purge_confirm": (
        "فایل نگهداری‌شده و تأییدشده اجرای شماره <code>{run_id}</code> برای همیشه پاک شود؟ "
        "تاریخچه پایگاه داده حفظ خواهد شد."
    ),
    "run_action_unavailable": "این عملیات دیگر در دسترس یا ایمن نیست.",
    "run_artifact_retained": "نگهداری‌شده ({size:,} بایت)",
    "run_artifact_purged": "پاک‌شده؛ تاریخچه پایگاه داده حفظ شده است",
    "run_artifact_delivered": "تحویل کامل ({size:,} بایت)",
    "run_error_cancelled": "اجرا توسط اپراتور لغو شد.",
    "run_error_timeout": "مهلت تنظیم‌شده بکاپ به پایان رسید.",
    "run_error_delivery_action_required": "تحویل خودکار متوقف شده و به اقدام اپراتور نیاز دارد.",
    "run_error_upload_limit": "اندازه فایل از سقف تنظیم‌شده آپلود بیشتر است.",
    "run_error_artifact_invalid": "فایل نگهداری‌شده موجود نیست یا تأیید نشد.",
    "run_error_generic": "اجرا ناموفق بود؛ جزئیات فقط در گزارش‌های محافظت‌شده موجود است.",
    "run_trigger_manual": "دستی",
    "run_trigger_scheduled": "زمان‌بندی‌شده",
    "run_status_queued": "در صف",
    "run_status_running": "در حال اجرا",
    "run_status_delivery_pending": "در انتظار تحویل",
    "run_status_succeeded": "موفق",
    "run_status_failed": "ناموفق",
    "run_status_cancelled": "لغوشده",
    "run_status_skipped": "ردشده",
    "run_stage_queued": "در صف",
    "run_stage_backup": "تهیه بکاپ",
    "run_stage_verify_source": "بررسی منبع",
    "run_stage_compress": "فشرده‌سازی",
    "run_stage_encrypt": "رمزگذاری",
    "run_stage_checksum": "محاسبه چک‌سام",
    "run_stage_finalize": "نهایی‌سازی",
    "run_stage_pending_delivery": "در انتظار تحویل",
    "run_stage_telegram_delivery": "تحویل تلگرام",
    "run_stage_cleanup": "پاک‌سازی",
    "run_stage_complete": "کامل",
    "no_backups": "بکاپی یافت نشد.",
    "backup_card": (
        "📦 <b>پروفایل بکاپ</b>\n━━━━━━━━━━━━━━\n📌 <b>نام:</b> <code>{name}</code>\n"
        "📁 <b>منبع:</b> {source}\n🖥 <b>سرور ریموت:</b> <code>{server}</code>\n"
        "⏰ <b>زمان‌بندی:</b> {schedule}{next_run}\n🌍 <b>منطقه زمانی:</b> {timezone}\n"
        "{source_details}\n\n{archive_compression}\n\n━━━━━━━━━━━━━━\n"
        "🔐 <b>رمزگذاری:</b> {encryption}\n📋 <b>وضعیت پروفایل:</b> {status}\n"
        "✅ <b>فعال:</b> {enabled}\n━━━━━━━━━━━━━━\n👥 <b>دسترسی:</b> {admins}\n━━━━━━━━━━━━━━"
    ),
    "ask_backup_name": "نام بکاپ را وارد کنید:",
    "ask_edit_backup_name": "نام بکاپ را وارد کنید یا برای حفظ «{current}» علامت «-» بفرستید:",
    "ask_edit_description": ("توضیحات را وارد کنید، برای حفظ «-» و برای پاک‌کردن «!» بفرستید:"),
    "ask_edit_database_" + "password": (
        "گذرواژه جدید پایگاه داده را وارد کنید یا برای حفظ گذرواژه فعلی «-» بفرستید:"
    ),
    "ask_edit_age_" + "passphrase": (
        "گذرواژه جدید Age را وارد کنید یا برای حفظ گذرواژه فعلی «-» بفرستید:"
    ),
    "edit_keep_hint": "در این ویرایش، برای حفظ مقدار غیرمحرمانه فعلی «-» بفرستید.",
    "ask_description": "توضیحات را وارد کنید یا «-» بفرستید:",
    "choose_source": "نوع منبع را انتخاب کنید:",
    "source_database": "🗄 پایگاه داده",
    "source_files_directories": "📁 فایل‌ها و پوشه‌ها",
    "source_gitlab": "🦊 GitLab",
    "database_postgresql": "PostgreSQL",
    "database_mysql": "MySQL / MariaDB",
    "database_mssql": "Microsoft SQL Server",
    "tls_disabled": "TLS غیرفعال",
    "tls_preferred": "TLS ترجیحی",
    "tls_required": "TLS الزامی",
    "tls_verify_ca": "TLS با تأیید CA",
    "tls_verify_full": "TLS با تأیید کامل",
    "location_local": "محلی",
    "location_remote": "سرور ریموت",
    "schedule_manual": "فقط دستی",
    "schedule_once": "یک‌بار",
    "schedule_minutes": "هر N دقیقه",
    "schedule_hours": "هر N ساعت",
    "schedule_days": "هر N روز",
    "schedule_minutes_value": "هر {value} دقیقه",
    "schedule_hours_value": "هر {value} ساعت",
    "schedule_days_value": "هر {value} روز",
    "schedule_daily_value": "روزانه ساعت {value}",
    "schedule_weekly_value": "هفتگی، روز {day} ساعت {time}",
    "schedule_monthly_value": "ماهانه، روز {day} ساعت {time}",
    "schedule_daily": "روزانه",
    "schedule_weekly": "هفتگی",
    "schedule_monthly": "ماهانه",
    "compression_none": "◻️ بدون فشرده‌سازی",
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
    "compression_bzip2_name": "Bzip2 (قدیمی)",
    "compression_lz4_name": "LZ4",
    "compression_deflate_name": "Deflate (داخلی ZIP)",
    "compression_lzma2_name": "LZMA2 (داخلی 7-Zip)",
    "preset_fast": "⚡ سریع",
    "preset_balanced": "⚖️ متعادل ✓",
    "preset_high": "زیاد",
    "preset_maximum": "📦 حداکثر فشرده‌سازی",
    "preset_fast_display": "سریع",
    "preset_balanced_display": "متعادل",
    "preset_high_display": "حداکثر فشرده‌سازی",
    "preset_maximum_display": "حداکثر فشرده‌سازی",
    "preset_not_applicable": "قابل اعمال نیست",
    "encryption_none": "بدون رمزگذاری",
    "encryption_age_public": "کلید عمومی Age — پیشنهادی",
    "encryption_age_" + "passphrase": "گذرواژه Age",
    "ask_server_name": "نام یکتای سرور را وارد کنید:",
    "ask_server_host": "نام میزبان یا IP را وارد کنید:",
    "ask_server_port": "پورت SSH را وارد کنید:",
    "ask_server_username": "نام کاربری SSH را وارد کنید:",
    "ask_server_private_key": (
        "کلید خصوصی SSH را ارسال کنید؛ رمزگذاری می‌شود و دیگر نمایش داده نخواهد شد:"
    ),
    "ask_server_" + "passphrase": "گذرواژه کلید خصوصی را وارد کنید یا «-» بفرستید:",
    "ask_server_name_edit": "نام جایگزین سرور را وارد کنید:",
    "server_fingerprint_confirm": (
        "اتصال موفق بود. به اثرانگشت دقیق کلید میزبان SSH اعتماد می‌کنید؟\n\n{fingerprint}"
    ),
    "server_saved": "سرور ریموت با بررسی ثابت کلید میزبان ذخیره شد.",
    "server_updated": "سرور ریموت پس از اعتماد به اثرانگشت جدید به‌روزرسانی شد.",
    "edit_server_prompt": "نحوه ویرایش سرور ریموت را انتخاب کنید:",
    "reconfigure_server": "🔐 جایگزینی اطلاعات اتصال",
    "server_status_changed": "وضعیت سرور ریموت تغییر کرد.",
    "server_deleted": "سرور ریموت حذف شد.",
    "server_test_ok": "اتصال موفق بود و کلید میزبان مطابقت دارد.",
    "servers_title": "مدیریت سرورهای ریموت",
    "no_servers": "سرور ریموتی یافت نشد.",
    "server_card": (
        "🖥 <b>جزئیات سرور ریموت</b>\n━━━━━━━━━━━━━━\n"
        "📌 <b>نام سرور:</b> <code>{name}</code>\n"
        "🌐 <b>نشانی:</b> <code>{host}:{port}</code>\n👤 <b>کاربر:</b> <code>{username}</code>\n"
        "🔐 <b>احراز هویت:</b> {authentication}\n"
        "🛡 <b>اثرانگشت:</b> <code>{fingerprint}</code>\n✅ <b>وضعیت:</b> {status}\n━━━━━━━━━━━━━━"
    ),
    "ssh_private_key": "کلید خصوصی SSH",
    "choose_database_engine": "یک موتور پایگاه داده انتخاب کنید:",
    "choose_tls": "حالت TLS/SSL پایگاه داده را انتخاب کنید:",
    "ask_tls_ca": "گواهی CA مورد اعتماد را با قالب PEM ارسال کنید:",
    "ask_database_host": "میزبان پایگاه داده را وارد کنید:",
    "ask_database_port": "پورت پایگاه داده را وارد کنید:",
    "ask_database_name": "نام دقیق یک پایگاه داده را وارد کنید:",
    "ask_database_username": "نام کاربری پایگاه داده را وارد کنید:",
    "ask_database_" + "password": "گذرواژه پایگاه داده را وارد کنید؛ دیگر نمایش داده نخواهد شد:",
    "ask_mssql_docker": "آیا SQL Server داخل Docker اجرا می‌شود؟",
    "ask_mssql_backup_path": "مسیر مقصد بکاپ SQL Server/کانتینر را وارد کنید:",
    "ask_mssql_host_path": "مسیر متناظر mount شده روی میزبان ریموت را وارد کنید:",
    "choose_location": "محل منبع فایل/پوشه را انتخاب کنید:",
    "ask_paths": "یک یا چند مسیر مطلق فایل / پوشه، هرکدام در یک خط وارد کنید:",
    "ask_gitlab_container": "نام کانتینر Docker گیت‌لب را وارد کنید (مثال: gitlab_natrixo):",
    "ask_gitlab_host_path": "مسیر تأییدشده پوشه پشتیبان گیت‌لب روی میزبان را وارد کنید:",
    "ask_gitlab_worker_path": "مسیر mount شده و قابل مشاهده برای worker را وارد کنید:",
    "gitlab_exclusive_warning": (
        "⚠️ پوشه پشتیبان گیت‌لب منحصراً توسط این پروفایل مدیریت می‌شود. فایل‌های موجود "
        "پیش از هر پشتیبان جدید حذف می‌شوند.\n\nبرای بازیابی کامل، gitlab.rb و "
        "gitlab-secrets.json را جداگانه پشتیبان‌گیری کنید."
    ),
    "gitlab_source_details": (
        "\n🦊 <b>منبع گیت‌لب</b>\nکانتینر: {container}\nمسیر میزبان: {host_path}\n"
        "مسیر worker: {worker_path}\n\n⚠️ فایل‌های موجود پیش از هر پشتیبان حذف می‌شوند. "
        "از gitlab.rb و gitlab-secrets.json جداگانه پشتیبان بگیرید."
    ),
    "choose_schedule": "زمان‌بندی را انتخاب کنید. زمان‌ها بر اساس منطقه زمانی بکاپ هستند: {timezone}",
    "ask_schedule_interval": "N را به‌صورت عدد صحیح مثبت وارد کنید:",
    "ask_schedule_daily": "زمان را به‌شکل HH:MM وارد کنید:",
    "ask_schedule_weekly": "روز هفته (۰=دوشنبه تا ۶=یکشنبه) و زمان را وارد کنید: 0 HH:MM",
    "ask_schedule_monthly": "روز ماه (۱ تا ۲۸) و زمان را وارد کنید: 1 HH:MM",
    "choose_remote_server": "یک سرور ریموت فعال انتخاب کنید:",
    "choose_assigned_admins": "ادمین‌ها را انتخاب کنید و سپس ذخیره دسترسی را بزنید:",
    "archive_explanation": (
        "📦 قالب آرشیو\n\nآرشیو چند فایل را در یک بسته گروه‌بندی می‌کند و الزاماً حجم را "
        "کم نمی‌کند؛ فشرده‌سازی جداگانه انتخاب می‌شود.\n\n━━━━━━━━━━━━━━\n"
        "🗃 TAR (پیشنهادی)\n• پسوند: .tar\n• حفظ متادیتای Unix\n"
        "• سازگاری: عالی در لینوکس و macOS و گسترده در سایر سیستم‌ها\n"
        "• مناسب برای: سرورها و درخت فایل\n\n━━━━━━━━━━━━━━\n"
        "🗃 ZIP\n• پسوند: .zip\n• قالب میان‌پلتفرمی\n"
        "• سازگاری: داخلی در ویندوز، macOS و میزکارهای لینوکس\n"
        "• مناسب برای: اشتراک آسان\n\n━━━━━━━━━━━━━━\n"
        "🗃 7Z\n• پسوند: .7z\n• قالب مدرن و انعطاف‌پذیر\n"
        "• سازگاری: در بسیاری از سیستم‌ها به 7-Zip یا ابزار سازگار نیاز دارد\n"
        "• مناسب برای: محیط‌های دارای ابزار تخصصی"
    ),
    "compression_explanation": (
        "🗜 فشرده‌سازی\n\nفشرده‌سازی حجم آرشیو را کم می‌کند و مستقل از قالب آرشیو است. "
        "نسبت بیشتر معمولاً زمان و پردازنده بیشتری می‌خواهد.\n\n━━━━━━━━━━━━━━\n"
        "◻️ بدون فشرده‌سازی · بدون پسوند · سریع‌ترین · پردازنده ناچیز\n"
        "🚀 Zstandard · .zst · نسبت زیاد · سریع · پردازنده متوسط\n"
        "🌐 gzip · .gz · نسبت متوسط · سرعت متوسط · پردازنده متوسط\n"
        "📚 bzip2 · .bz2 · نسبت زیاد · کند · پردازنده زیاد\n"
        "💾 XZ (LZMA2) · .xz · نسبت بسیار زیاد · بسیار کند · پردازنده زیاد\n"
        "⚡ LZ4 · .lz4 · نسبت کم · بسیار سریع · پردازنده کم"
    ),
    "compression_lz4_unavailable": (
        "⚡ LZ4 در ورکر فعلی در دسترس نیست. الگوریتم دیگری انتخاب کنید؛ "
        "تنظیمات بکاپ تغییر نکرده است."
    ),
    "choose_compression_preset": (
        "🗜 پیش‌تنظیم فشرده‌سازی\n\nتعادل سرعت و حجم را انتخاب کنید. حالت متعادل پیشنهاد می‌شود."
    ),
    "choose_encryption": "روش رمزگذاری را انتخاب کنید:",
    "ask_age_recipients": "کلیدهای عمومی گیرنده Age را هرکدام در یک خط وارد کنید:",
    "ask_age_" + "passphrase": (
        "گذرواژه Age را وارد کنید؛ رمزگذاری می‌شود و دیگر نمایش داده نخواهد شد:"
    ),
    "profile_confirmation": (
        "📋 <b>تأیید پروفایل بکاپ</b>\n━━━━━━━━━━━━━━\n"
        "📌 <b>نام:</b> <code>{name}</code>\n📁 <b>منبع:</b> {source}\n"
        "🖥 <b>سرور ریموت:</b> <code>{server}</code>\n"
        "⏰ <b>زمان‌بندی:</b> {schedule}\n🌍 <b>منطقه زمانی:</b> {timezone}\n"
        "{source_details}\n\n{archive_compression}\n\n━━━━━━━━━━━━━━\n"
        "🔐 <b>رمزگذاری:</b> {encryption}\n📋 <b>وضعیت پروفایل:</b> {status}\n"
        "✅ <b>فعال:</b> {enabled}\n━━━━━━━━━━━━━━\n"
        "👥 <b>دسترسی:</b> {admins}\n━━━━━━━━━━━━━━\nهیچ مقدار محرمانه‌ای نمایش داده نشده است."
    ),
    "compression_summary": (
        "<b>الگوریتم:</b> {algorithm}\n<b>پیش‌تنظیم:</b> {preset}\n"
        "<b>پسوند:</b> <code>{extension}</code>"
    ),
    "archive_summary": (
        "<b>قالب:</b> {archive}\n<b>پسوند:</b> <code>{extension}</code>\n"
        "<b>خروجی:</b> <code>{output}</code>"
    ),
    "archive_compression_sections": (
        "━━━━━━━━━━━━━━\n🗜 <b>فشرده‌سازی</b>\n{compression}\n\n"
        "━━━━━━━━━━━━━━\n📦 <b>آرشیو</b>\n{archive}"
    ),
    "file_directory_path": "📂 <b>مسیر (فایل / پوشه):</b> <code>{path}</code>",
    "file_source_details": "📍 <b>مکان:</b> {location}\n{paths}",
    "database_source_details": "⚙️ <b>موتور:</b> {engine}",
    "run_source_details": "📁 <b>منبع:</b> {source}\n{details}",
    "source_location_local": "میزبان محلی",
    "source_location_remote": "سرور ریموت",
    "backup_timezone": "🕓 منطقه زمانی بکاپ",
    "ask_backup_timezone": "منطقه زمانی IANA بکاپ را وارد کنید (برای نمونه Asia/Tehran):",
    "status_draft": "پیش‌نویس",
    "status_archived": "آرشیوشده",
    "backup_created": "پیش‌نویس «{name}» ذخیره شد.",
    "backup_setup_expired": ("⚠️ نشست راه‌اندازی منقضی شد. ساخت پروفایل بکاپ را دوباره آغاز کنید."),
    "backup_confirm_missing": "⚠️ فیلد {field} تکمیل نشده است. به آن مرحله بازگردید.",
    "backup_confirm_invalid_state": (
        "⚠️ این تأیید با مرحله فعلی هماهنگ نیست. پروفایل را دوباره بررسی کنید."
    ),
    "backup_confirm_stale": "⚠️ این دکمه تأیید قدیمی است. از جدیدترین پیام تأیید استفاده کنید.",
    "backup_confirm_unavailable": (
        "⚠️ ذخیره پروفایل موقتاً در دسترس نیست؛ ذخیره‌سازی امن پیکربندی نشده است."
    ),
    "backup_confirm_validation_failed": "⚠️ اعتبارسنجی پروفایل ناموفق بود: {reason}",
    "backup_confirm_save_failed": ("⚠️ پروفایل به‌دلیل خطای داخلی ذخیره نشد و تنظیمات پذیرفته نشد."),
    "select_backup_edit": "بکاپ را برای ویرایش انتخاب کنید:",
    "select_backup_delete": "بکاپ را برای حذف انتخاب کنید:",
    "edit_backup_prompt": "نوع ویرایش را انتخاب کنید:",
    "backup_updated": "بکاپ ویرایش شد.",
    "backup_configuration_updated": "تنظیمات بکاپ «{name}» به‌صورت اتمیک بروزرسانی شد.",
    "current_value": "مقدار فعلی:",
    "existing_credential": (
        "مقدار محرمانه موجود است — مقدار جدیدی وارد کنید یا مقدار موجود را نگه دارید."
    ),
    "backup_status_changed": "وضعیت بکاپ تغییر کرد.",
    "backup_run_queued": "بکاپ در صف قرار گرفت. اجرای شماره <code>{run_id}</code>.",
    "backup_run_active": "یک اجرا فعال است: {stage}، شروع {started}.",
    "backup_backpressure": "ایجاد بکاپ جدید متوقف است: فضای تحویل به حد ایمنی رسیده است.",
    "backup_delete_summary": (
        "این بکاپ آرشیو شود؟\n\nنام: {name}\nنوع منبع: {source}\nوضعیت: {status}"
    ),
    "backup_deleted": "بکاپ آرشیو و دسترسی‌ها لغو شدند.",
    "select_admin_access": "ادمین‌های مجاز را انتخاب کنید:",
    "my_backups_title": "بکاپ‌های تخصیص‌یافته شما:",
    "audit_empty": "گزارشی وجود ندارد.",
    "help_text": "برای کار با ربات از دکمه‌های منو استفاده کنید.",
    "permission_invalid": "این گزینه معتبر نیست و در دسترس شما قرار ندارد.",
    "settings_text": (
        "اتصال: MTProto Bot\nSOCKS5: {proxy}\nحد آپلود: {limit:,} بایت\n"
        "منطقه زمانی: {timezone}\nSQLite: فعال"
    ),
    "health_text": (
        "سامانه: {status}\n{live} زنده  {ready} آماده\n"
        "{database} پایگاه کنترل  {schema} طرح {revision}\n"
        "{worker} ورکر  {scheduler} زمان‌بند  {telegram} تلگرام\n"
        "فضای آزاد: {disk:,} م‌ب | در انتظار: {pending:,} م‌ب\n"
        "اجرا صف/فعال/ناموفق: {queued}/{running}/{failed} | اجاره کهنه: {stale}\n"
        "خروجی ناموفق نگه‌داری‌شده: {retained} | ابزارها: {tools}"
    ),
    "active": "فعال",
    "inactive": "غیرفعال",
    "yes": "بله",
    "no": "خیر",
}
