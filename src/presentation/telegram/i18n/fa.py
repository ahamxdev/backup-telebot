from presentation.telegram.i18n.message import section

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
    "active_runs_footer": section(
        "🧭", "بکاپ‌های فعال", "از کنترل‌های بالا استفاده کنید یا به منوی بکاپ‌ها بازگردید."
    ),
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
    "welcome": section(
        "👋", "خوش آمدید", "بکاپ‌ها، اجراها، سرورها و دسترسی‌ها را از منوی زیر مدیریت کنید."
    ),
    "cancelled": section("❎", "عملیات لغو شد", "هیچ تغییری ذخیره نشد."),
    "returned": section("🏠", "خانه", "یکی از گزینه‌های منوی زیر را انتخاب کنید."),
    "choose_menu": section("🧭", "منوی اصلی", "یکی از گزینه‌های زیر را انتخاب کنید."),
    "invalid": section("⚠️", "ورودی نامعتبر", "مقدار را بررسی و دوباره تلاش کنید."),
    "too_many_invalid": section(
        "⛔", "عملیات لغو شد", "تعداد تلاش‌های نامعتبر زیاد بود. عملیات را دوباره آغاز کنید."
    ),
    "expired": section("⌛", "نشست منقضی شد", "عملیات را دوباره آغاز کنید."),
    "no_choices": section(
        "ℹ️", "موردی برای انتخاب نیست", "در حال حاضر گزینه واجد شرایطی وجود ندارد."
    ),
    "language_prompt": section("🌐", "زبان", "زبان دلخواه خود را انتخاب کنید."),
    "language_saved": section("✅", "زبان بروزرسانی شد", "انتخاب شما ذخیره شد."),
    "admins_title": section("👥", "دسترسی ادمین‌ها", "ادمین‌ها و دسترسی بکاپ آن‌ها را مدیریت کنید."),
    "no_admins": section("👥", "ادمینی وجود ندارد", "هنوز حساب ادمینی ساخته نشده است."),
    "admin_card": (
        "👤 <b>جزئیات ادمین</b>\n━━━━━━━━━━━━━━\n📌 <b>نام:</b> <code>{name}</code>\n"
        "🆔 <b>شناسه تلگرام:</b> <code>{telegram_id}</code>\n📋 <b>وضعیت:</b> {status}\n"
        "🌐 <b>زبان:</b> {admin_language}\n━━━━━━━━━━━━━━\n"
        "📦 <b>بکاپ‌های مجاز:</b> {backups}\n━━━━━━━━━━━━━━"
    ),
    "enabled": "🟢 فعال",
    "disabled": "⚪ غیرفعال",
    "none": "هیچ‌کدام",
    "language_en": "انگلیسی",
    "language_fa": "فارسی",
    "ask_admin_name": section("👤", "نام ادمین", "نام نمایشی را وارد کنید."),
    "ask_telegram_id": section("🆔", "شناسه تلگرام", "شناسه عددی کاربر تلگرام را وارد کنید."),
    "ask_admin_language": section("🌐", "زبان ادمین", "زبان دلخواه ادمین را انتخاب کنید."),
    "admin_confirmation": (
        "👤 <b>تأیید ادمین</b>\nنام: <code>{name}</code>\nشناسه تلگرام: "
        "<code>{telegram_id}</code>\nزبان: {admin_language}\nاین ادمین ساخته شود؟"
    ),
    "admin_created_access": section("✅", "ادمین ساخته شد", "اکنون دسترسی بکاپ را تعیین کنید."),
    "admin_saved": section("✅", "ادمین ذخیره شد", "حساب و دسترسی‌های بکاپ بروزرسانی شدند."),
    "select_admin_edit": section("✏️", "ویرایش ادمین", "یک ادمین را انتخاب کنید."),
    "select_admin_delete": section("🗑", "حذف ادمین", "یک ادمین را انتخاب کنید."),
    "edit_admin_prompt": section("✏️", "ویرایش ادمین", "بخش موردنظر را انتخاب کنید."),
    "ask_new_name": section("👤", "نام جدید", "نام نمایشی جدید ادمین را وارد کنید."),
    "ask_new_telegram_id": section("🆔", "شناسه تلگرام جدید", "شناسه عددی جدید را وارد کنید."),
    "admin_updated": section("✅", "ادمین بروزرسانی شد", "مشخصات ادمین ذخیره شد."),
    "admin_status_changed": section("✅", "وضعیت بروزرسانی شد", "وضعیت ادمین تغییر کرد."),
    "admin_delete_summary": (
        "⚠️ <b>ادمین حذف شود؟</b>\nادمین: <code>{name}</code>\nشناسه تلگرام: "
        "<code>{telegram_id}</code>\nوضعیت: {status}\nزبان: {admin_language}\n"
        "دسترسی‌ها لغو و حساب غیرفعال می‌شود."
    ),
    "admin_deleted": section("✅", "ادمین حذف شد", "حساب غیرفعال و دسترسی‌ها لغو شدند."),
    "select_backup_access": section("📦", "دسترسی بکاپ", "بکاپ‌های مجاز این ادمین را انتخاب کنید."),
    "access_saved": section("✅", "دسترسی بروزرسانی شد", "تخصیص دسترسی‌ها ذخیره شد."),
    "no_active_operations": section(
        "✅", "بکاپ فعالی وجود ندارد", "در حال حاضر هیچ عملیات بکاپی در حال اجرا نیست."
    ),
    "active_runs_overview": section("⚙️", "بکاپ‌های فعال", "در حال حاضر {count} اجرا فعال است."),
    "active_runs_title": section("⚙️", "اجراهای فعال بکاپ", "تعداد فعال: <code>{count}</code>"),
    "no_run_history": section("📜", "تاریخچه‌ای وجود ندارد", "هنوز اجرای بکاپی ثبت نشده است."),
    "run_history_header": section(
        "📜", "تاریخچه اجراها", "صفحه {page} از {pages} · مجموع <code>{total}</code>"
    ),
    "run_history_footer": section("🧭", "تاریخچه اجراها", "با دکمه‌های زیر جابه‌جا شوید."),
    "size_unknown": "نامشخص",
    "run_size_summary": "📦 اندازه اصلی بکاپ: {original_size}\n🗜 اندازه نهایی بکاپ: {final_size}",
    "run_list_card": (
        "🔎 <b>اجرای شماره</b> <code>{run_id}</code> · <code>{profile}</code>\n━━━━━━━━━━━━━━\n"
        "📋 <b>وضعیت:</b> {status}\n⚙️ <b>مرحله:</b> {stage}\n🕒 <b>ایجاد:</b> {created}"
        "{size_summary}\n━━━━━━━━━━━━━━"
    ),
    "run_detail_card": (
        "🔎 <b>جزئیات اجرای بکاپ</b>\n━━━━━━━━━━━━━━\n🆔 <b>شناسه اجرا:</b> <code>{run_id}</code>\n"
        "📌 <b>پروفایل:</b> <code>{profile}</code>\n📋 <b>وضعیت:</b> {status}\n"
        "⚙️ <b>مرحله:</b> {stage}\n▶️ <b>نوع اجرا:</b> {trigger}\n{source_details}\n"
        "━━━━━━━━━━━━━━\n"
        "🕒 <b>ایجاد:</b> {created}\n📅 <b>زمان‌بندی:</b> {scheduled}\n"
        "🚀 <b>شروع:</b> {started}\n🏁 <b>پایان:</b> {finished}\n"
        "🌍 <b>منطقه زمانی:</b> {timezone}\n━━━━━━━━━━━━━━\n"
        "⚠️ <b>کد خطا:</b> <code>{error_code}</code>\n📝 <b>خطای امن:</b> {error_message}\n"
        "━━━━━━━━━━━━━━\n📦 <b>اندازه اصلی بکاپ:</b> {original_size}\n"
        "🗜 <b>اندازه نهایی بکاپ:</b> {final_size}\n"
        "📁 <b>فایل بکاپ:</b> {artifact}\n📨 <b>تحویل‌ها:</b> "
        "در انتظار: <code>{pending}</code> · موفق: <code>{succeeded}</code>\n"
        "ناموفق: <code>{failed}</code> · ردشده: <code>{skipped}</code>\n━━━━━━━━━━━━━━"
    ),
    "run_cancel_requested": section(
        "⛔", "درخواست لغو ثبت شد", "اجرای شماره <code>{run_id}</code> به‌شکل ایمن متوقف می‌شود."
    ),
    "run_delivery_retry_requested": (
        "🔁 <b>تلاش مجدد تحویل در صف است</b>\nتحویل‌های ناموفق اجرای شماره "
        "<code>{run_id}</code> دوباره انجام می‌شوند."
    ),
    "run_retry_queued": section("✅", "تلاش مجدد در صف است", "اجرای جدید: <code>{run_id}</code>"),
    "run_artifact_purge_complete": (
        "✅ <b>فایل پاک شد</b>\nفایل نگهداری‌شده اجرای شماره <code>{run_id}</code> حذف شد."
    ),
    "run_artifact_purge_confirm": (
        "⚠️ <b>فایل نگهداری‌شده پاک شود؟</b>\nفایل تأییدشده اجرای شماره "
        "<code>{run_id}</code> برای همیشه حذف شود؟ تاریخچه پایگاه داده حفظ می‌شود."
    ),
    "run_action_unavailable": section(
        "⚠️", "عملیات در دسترس نیست", "این اقدام دیگر قابل انجام یا ایمن نیست."
    ),
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
    "run_status_queued": "🟡 در صف",
    "run_status_running": "🟡 در حال اجرا",
    "run_status_delivery_pending": "🟡 در انتظار تحویل",
    "run_status_succeeded": "🟢 موفق",
    "run_status_failed": "🔴 ناموفق",
    "run_status_cancelled": "⚪ لغوشده",
    "run_status_skipped": "⚪ ردشده",
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
    "no_backups": section("📦", "پروفایل بکاپی وجود ندارد", "هنوز پروفایل بکاپی ساخته نشده است."),
    "backups_title": section(
        "📦", "پروفایل‌های بکاپ", "پروفایل‌ها، اجراها و دسترسی‌ها را مدیریت کنید."
    ),
    "backup_card": (
        "📦 <b>پروفایل بکاپ</b>\n━━━━━━━━━━━━━━\n📌 <b>نام:</b> <code>{name}</code>\n"
        "📁 <b>منبع:</b> {source}\n🖥 <b>سرور ریموت:</b> <code>{server}</code>\n"
        "⏰ <b>زمان‌بندی:</b> {schedule}{next_run}\n🌍 <b>منطقه زمانی:</b> {timezone}\n"
        "{source_details}\n\n{archive_compression}\n\n━━━━━━━━━━━━━━\n"
        "🔐 <b>رمزگذاری:</b> {encryption}\n📋 <b>وضعیت پروفایل:</b> {status}\n"
        "✅ <b>فعال:</b> {enabled}\n━━━━━━━━━━━━━━\n👥 <b>دسترسی:</b> {admins}\n━━━━━━━━━━━━━━"
    ),
    "ask_backup_name": "📦 <b>نام بکاپ</b>\nنامی برای این پروفایل بکاپ وارد کنید.",
    "ask_edit_backup_name": (
        "📦 <b>نام بکاپ</b>\nنام جدید را وارد کنید یا برای حفظ «{current}» <code>-</code> بفرستید."
    ),
    "ask_edit_description": (
        "📝 <b>توضیحات</b>\nتوضیحات جدید را وارد کنید؛ برای حفظ <code>-</code> و "
        "برای پاک‌کردن <code>!</code> بفرستید."
    ),
    "ask_edit_database_" + "password": section(
        "🔐",
        "گذرواژه پایگاه داده",
        "مقدار جدید را وارد کنید یا برای حفظ فعلی <code>-</code> بفرستید.",
    ),
    "ask_edit_age_" + "passphrase": section(
        "🔐", "گذرواژه Age", "مقدار جدید را وارد کنید یا برای حفظ فعلی <code>-</code> بفرستید."
    ),
    "edit_keep_hint": section(
        "ℹ️", "ویرایش", "برای حفظ مقدار غیرمحرمانه فعلی <code>-</code> بفرستید."
    ),
    "ask_description": (
        "📝 <b>توضیحات</b>\nتوضیحات را وارد کنید یا برای خالی گذاشتن <code>-</code> بفرستید."
    ),
    "choose_source": "🧩 <b>نوع منبع</b>\nآنچه باید پشتیبان‌گیری شود انتخاب کنید.",
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
    "schedule_cron_value": "کرون: {value}",
    "schedule_daily": "روزانه",
    "schedule_weekly": "هفتگی",
    "schedule_monthly": "ماهانه",
    "schedule_cron": ("📅 زمان\u200cبندی کرون"),
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
    "ask_server_name": section("🖥", "نام سرور", "نامی یکتا برای سرور وارد کنید."),
    "ask_server_host": section("🌐", "نشانی سرور", "نام میزبان یا نشانی IP را وارد کنید."),
    "ask_server_port": section("🔌", "پورت SSH", "شماره پورت SSH را وارد کنید."),
    "ask_server_username": section("👤", "نام کاربری SSH", "نام حساب SSH را وارد کنید."),
    "ask_server_private_key": (
        "🔐 <b>کلید خصوصی SSH</b>\nکلید را ارسال کنید؛ رمزگذاری می‌شود و دیگر نمایش داده نمی‌شود."
    ),
    "ask_server_" + "passphrase": section(
        "🔑", "گذرواژه کلید", "گذرواژه را وارد کنید یا اگر ندارد <code>-</code> بفرستید."
    ),
    "ask_server_name_edit": section("🖥", "نام سرور", "نام جایگزین را وارد کنید."),
    "server_fingerprint_confirm": (
        "🛡 <b>اعتماد به کلید میزبان SSH؟</b>\nاتصال موفق بود. این اثرانگشت را بررسی و تأیید کنید:\n"
        "<code>{fingerprint}</code>"
    ),
    "server_saved": section("✅", "سرور ذخیره شد", "بررسی کلید میزبان SSH ثابت شد."),
    "server_updated": section("✅", "سرور بروزرسانی شد", "کلید میزبان جدید تأیید و ثابت شد."),
    "edit_server_prompt": section("✏️", "ویرایش سرور ریموت", "بخش موردنظر را انتخاب کنید."),
    "reconfigure_server": "🔐 جایگزینی اطلاعات اتصال",
    "server_status_changed": section("✅", "وضعیت بروزرسانی شد", "وضعیت سرور ریموت تغییر کرد."),
    "server_deleted": section("✅", "سرور حذف شد", "سرور ریموت حذف شد."),
    "server_delete_prompt": section(
        "⚠️", "سرور ریموت حذف شود؟", "سرور زیر را بررسی و سپس حذف همیشگی را تأیید کنید."
    ),
    "server_test_ok": section("✅", "اتصال تأیید شد", "اتصال SSH برقرار و کلید میزبان منطبق است."),
    "servers_title": section("🖥", "سرورهای ریموت", "سرورهای مورد اعتماد منبع بکاپ را مدیریت کنید."),
    "no_servers": section("🖥", "سرور ریموتی وجود ندارد", "هنوز سرور ریموتی اضافه نشده است."),
    "server_card": (
        "🖥 <b>جزئیات سرور ریموت</b>\n━━━━━━━━━━━━━━\n"
        "📌 <b>نام سرور:</b> <code>{name}</code>\n"
        "🌐 <b>نشانی:</b> <code>{host}:{port}</code>\n👤 <b>کاربر:</b> <code>{username}</code>\n"
        "🔐 <b>احراز هویت:</b> {authentication}\n"
        "🛡 <b>اثرانگشت:</b> <code>{fingerprint}</code>\n📋 <b>وضعیت:</b> {status}\n━━━━━━━━━━━━━━"
    ),
    "ssh_private_key": "کلید خصوصی SSH",
    "choose_database_engine": section("🗄", "موتور پایگاه داده", "یک نوع پایگاه داده انتخاب کنید."),
    "choose_tls": section("🛡", "TLS پایگاه داده", "حالت بررسی TLS/SSL را انتخاب کنید."),
    "ask_tls_ca": section("📜", "مرجع CA مورد اعتماد", "گواهی CA را با قالب PEM ارسال کنید."),
    "ask_database_host": section(
        "🌐", "میزبان پایگاه داده", "نام میزبان یا نشانی IP را وارد کنید."
    ),
    "ask_database_port": section("🔌", "پورت پایگاه داده", "شماره پورت را وارد کنید."),
    "ask_database_name": section("🗄", "نام پایگاه داده", "نام دقیق یک پایگاه داده را وارد کنید."),
    "ask_database_username": section("👤", "نام کاربری پایگاه داده", "نام حساب بکاپ را وارد کنید."),
    "ask_database_" + "password": section(
        "🔐", "گذرواژه پایگاه داده", "گذرواژه را وارد کنید؛ دیگر نمایش داده نمی‌شود."
    ),
    "ask_mssql_docker": section("🐳", "محیط SQL Server", "آیا SQL Server در Docker اجرا می‌شود؟"),
    "ask_mssql_backup_path": section(
        "📂", "مسیر بکاپ SQL Server", "مسیر مقصد داخل SQL Server یا کانتینر آن را وارد کنید."
    ),
    "ask_mssql_host_path": section(
        "📂", "مسیر میزبان ریموت", "مسیر mount متناظر روی میزبان ریموت را وارد کنید."
    ),
    "choose_location": section("📍", "مکان منبع", "محل فایل‌ها یا پوشه‌ها را انتخاب کنید."),
    "ask_paths": section("📂", "فایل‌ها و پوشه‌ها", "مسیرهای مطلق را هرکدام در یک خط وارد کنید."),
    "choose_gitlab_location": "📍 <b>مکان گیت‌لب</b>\nاین نمونه گیت‌لب کجا اجرا می‌شود؟",
    "ask_gitlab_container": "🐳 <b>کانتینر گیت‌لب</b>\nنام کانتینر Docker را وارد کنید.",
    "ask_gitlab_remote_path": ("📂 <b>پوشه بکاپ گیت‌لب</b>\nمسیر پوشه روی سرور ریموت را وارد کنید."),
    "no_remote_servers_available": (
        "⚠️ <b>سرور ریموتی در دسترس نیست</b>\n"
        "هنوز هیچ سرور ریموت فعالی ندارید.\n"
        "ابتدا یک سرور ریموت اضافه کنید یا میزبان محلی را انتخاب کنید."
    ),
    "gitlab_path_information": (
        "📁 <b>پوشهٔ بکاپ GitLab</b>\n"
        "مسیر اصلی تنظیم‌شده در هلپر:\n{native_path}\n"
        "مسیر آماده‌سازی تأییدشده و مشترک با ورکر:\n{staging_path}\n"
        "اپراتور مسیر میزبان را با GITLAB_BACKUP_HOST_PATH تنظیم می‌کند. "
        "نیازی به ورود مسیر نیست. فقط هلپر به پوشهٔ اصلی دسترسی دارد."
    ),
    "gitlab_paths_unconfigured": (
        "📁 <b>پوشهٔ بکاپ GitLab</b>\n"
        "پوشهٔ اصلی تحت کنترل هلپر است؛ نیازی به ورود مسیر نیست. "
        "اطلاعات مسیر هلپر در این استقرار در دسترس نیست. "
        "پیش از استفاده از GitLab محلی، GITLAB_BACKUP_ROOT را مطابق هلپر تنظیم کنید."
    ),
    "gitlab_exclusive_warning": (
        "🛡 <b>دریافت تأییدشده بکاپ بومی</b>\nابزار محلی فقط همان فایل بومی را پس از "
        "تأیید اندازه و SHA-256 نسخه مرحله‌بندی‌شده حذف می‌کند.\nبرای بازیابی کامل، "
        "<code>gitlab.rb</code> و <code>gitlab-secrets.json</code> را جداگانه بکاپ بگیرید."
    ),
    "gitlab_source_details_local": (
        "\n🦊 <b>منبع گیت‌لب</b>\n📍 <b>مکان:</b> {location}\n<b>کانتینر:</b> {container}"
    ),
    "gitlab_source_details_remote": (
        "\n🦊 <b>منبع گیت‌لب</b>\n📍 <b>مکان:</b> {location}"
        "\n<b>کانتینر:</b> {container}\n<b>مسیر پشتیبان:</b> {backup_path}"
    ),
    "choose_schedule": section(
        "⏰",
        "زمان‌بندی بکاپ",
        "زمان‌بندی را انتخاب کنید. زمان‌ها بر اساس <code>{timezone}</code> هستند.",
    ),
    "ask_schedule_interval": section("🔁", "فاصله اجرا", "یک عدد صحیح مثبت وارد کنید."),
    "ask_schedule_daily": section(
        "🕒", "زمان روزانه", "زمان را به‌شکل <code>HH:MM</code> وارد کنید."
    ),
    "ask_schedule_weekly": section(
        "📅",
        "زمان‌بندی هفتگی",
        "روز و زمان را به‌شکل <code>0 HH:MM</code> وارد کنید (۰=دوشنبه، ۶=یکشنبه).",
    ),
    "ask_schedule_monthly": section(
        "📅", "زمان‌بندی ماهانه", "روز و زمان را به‌شکل <code>1 HH:MM</code> وارد کنید (روز ۱ تا ۲۸)."
    ),
    "ask_schedule_cron": (
        "📅 <b>زمان\u200cبندی کرون</b>\nمنطقهٔ زمانی: <code>{timezone}</code>.\nاز"
        " قالب ۵ فیلدی کرون لینوکس در منطقهٔ زمانی پشتیبان\u200cگیری استفاده کن"
        "ید:\n<code>minute hour day-of-month month day-of-week</code>\nدقیقه"
        "، ساعت، روز ماه، ماه، روز هفته\n\n<code>30 3 * * *</code> ← هر روز "
        "ساعت ۰۳:۳۰\n<code>10 * * * *</code> ← هر ساعت در دقیقهٔ ۱۰\n<code>0"
        " 2 1 * *</code> ← روز اول هر ماه ساعت ۰۲:۰۰\n\nعبارت کرون خود را بف"
        "رستید."
    ),
    "choose_remote_server": "🌐 <b>سرور ریموت</b>\nسرور میزبان این منبع را انتخاب کنید.",
    "choose_assigned_admins": section(
        "👥", "دسترسی ادمین‌ها", "ادمین‌ها را انتخاب و سپس ذخیره دسترسی را بزنید."
    ),
    "archive_explanation": (
        "📦 <b>قالب آرشیو</b>\nآرشیو چند فایل را در یک بسته گروه‌بندی می‌کند و الزاماً حجم را "
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
        "🗜 <b>فشرده‌سازی</b>\nفشرده‌سازی حجم آرشیو را کم می‌کند و مستقل از قالب آرشیو است. "
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
        "🗜 <b>پیش‌تنظیم فشرده‌سازی</b>\nتعادل سرعت و حجم را انتخاب کنید. حالت متعادل پیشنهاد می‌شود."
    ),
    "choose_encryption": section("🔐", "رمزگذاری", "روش حفاظت از بکاپ نهایی را انتخاب کنید."),
    "ask_age_recipients": section(
        "🔑", "گیرندگان Age", "کلیدهای عمومی را هرکدام در یک خط وارد کنید."
    ),
    "ask_age_" + "passphrase": (
        "🔐 <b>گذرواژه Age</b>\nگذرواژه را وارد کنید؛ رمزگذاری می‌شود و دیگر نمایش داده نمی‌شود."
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
    "timezone_heading": "🕒 منطقه زمانی بکاپ",
    "ask_backup_timezone": section(
        "🕓",
        "منطقه زمانی بکاپ",
        "منطقه زمانی IANA را وارد کنید؛ برای نمونه <code>Asia/Tehran</code>.",
    ),
    "status_draft": "🟡 پیش‌نویس",
    "status_archived": "⚪ آرشیوشده",
    "backup_created": section(
        "✅", "پروفایل بکاپ ذخیره شد", "پیش‌نویس <code>{name}</code> ساخته شد."
    ),
    "backup_setup_expired": (
        "⌛ <b>راه‌اندازی منقضی شد</b>\nاین نشست پایان یافت؛ ساخت پروفایل را دوباره آغاز کنید."
    ),
    "backup_confirm_missing": (
        "⚠️ <b>پروفایل ناقص است</b>\nفیلد <code>{field}</code> تکمیل نشده؛ به آن مرحله بازگردید."
    ),
    "backup_confirm_invalid_state": (
        "⚠️ <b>تأیید هماهنگ نیست</b>\nاین تأیید با مرحله فعلی هماهنگ نیست؛ "
        "پروفایل را دوباره بررسی کنید."
    ),
    "backup_confirm_stale": "⚠️ <b>تأیید قدیمی است</b>\nاز دکمه جدیدترین پیام تأیید استفاده کنید.",
    "backup_confirm_unavailable": (
        "⚠️ <b>ذخیره در دسترس نیست</b>\nذخیره‌سازی امن پیکربندی نشده؛ با اپراتور تماس بگیرید."
    ),
    "backup_confirm_validation_failed": (
        "⚠️ <b>اعتبارسنجی ناموفق بود</b>\nپروفایل را بررسی کنید: {reason}"
    ),
    "backup_confirm_save_failed": (
        "⚠️ <b>پروفایل ذخیره نشد</b>\nخطای داخلی رخ داد و هیچ تغییری پذیرفته نشد."
    ),
    "select_backup_edit": section("✏️", "ویرایش بکاپ", "یک پروفایل بکاپ انتخاب کنید."),
    "select_backup_delete": section("🗑", "حذف بکاپ", "یک پروفایل بکاپ انتخاب کنید."),
    "edit_backup_prompt": section("✏️", "ویرایش بکاپ", "بخش موردنظر را انتخاب کنید."),
    "backup_updated": section("✅", "بکاپ بروزرسانی شد", "تغییرات پروفایل ذخیره شد."),
    "backup_configuration_updated": section(
        "✅", "تنظیمات بروزرسانی شد", "بکاپ <code>{name}</code> به‌صورت اتمیک بروزرسانی شد."
    ),
    "current_value": "<b>مقدار فعلی:</b>",
    "existing_credential": (
        "🔐 <b>اطلاعات محرمانه ذخیره شده</b>\nمقدار جدید وارد کنید یا مقدار موجود را نگه دارید."
    ),
    "backup_status_changed": section("✅", "وضعیت بروزرسانی شد", "وضعیت پروفایل بکاپ تغییر کرد."),
    "backup_run_queued": section(
        "✅", "بکاپ در صف است", "اجرای شماره <code>{run_id}</code> آماده شروع است."
    ),
    "backup_run_active": section("⚙️", "بکاپ در حال اجرا است", "مرحله: {stage}\nشروع: {started}"),
    "backup_backpressure": section(
        "⚠️", "بکاپ‌ها متوقف شده‌اند", "فضای تحویل به حد ایمنی تنظیم‌شده رسیده است."
    ),
    "backup_delete_summary": (
        "⚠️ <b>بکاپ آرشیو شود؟</b>\nنام: <code>{name}</code>\nمنبع: {source}\n"
        "وضعیت: {status}\nدسترسی‌های تخصیص‌یافته لغو می‌شوند."
    ),
    "backup_deleted": section("✅", "بکاپ آرشیو شد", "دسترسی‌های تخصیص‌یافته لغو شدند."),
    "select_admin_access": section(
        "👥", "دسترسی ادمین‌ها", "ادمین‌های مجاز این بکاپ را انتخاب کنید."
    ),
    "my_backups_title": section("📦", "بکاپ‌های من", "یکی از پروفایل‌های تخصیص‌یافته را انتخاب کنید."),
    "audit_empty": section("🧾", "رویدادی وجود ندارد", "هنوز فعالیتی ثبت نشده است."),
    "audit_title": section("🧾", "گزارش فعالیت", "فعالیت‌های اخیر سامانه"),
    "help_text": section("ℹ️", "راهنما", "برای مدیریت بکاپ‌ها از دکمه‌های منوی زیر استفاده کنید."),
    "permission_invalid": section(
        "⛔", "گزینه در دسترس نیست", "در این بخش اجازه استفاده از این گزینه را ندارید."
    ),
    "caption_backup_completed": "📦 <b>بکاپ کامل شد</b>",
    "caption_profile": "📌 <b>پروفایل:</b> {value}",
    "caption_status": "📋 <b>وضعیت:</b> کامل‌شده",
    "caption_started": "⏱ <b>شروع:</b> {value}",
    "caption_completed": "🏁 <b>پایان:</b> {value}",
    "caption_schedule": "⏰ <b>زمان‌بندی:</b> {value}",
    "caption_next_run": "⏭ <b>اجرای بعدی:</b> {value}",
    "caption_timezone": "🌍 <b>منطقه زمانی:</b> {value}",
    "caption_source": "🌐 <b>منبع:</b> {value}",
    "caption_source_database_value": "پایگاه داده",
    "caption_database_type": "🗄 <b>نوع پایگاه داده:</b> {value}",
    "caption_engine": "⚙️ <b>موتور:</b> {value}",
    "caption_server_name": "🏷 <b>نام سرور:</b> {value}",
    "caption_server": "🖥 <b>سرور:</b> {value}",
    "caption_database": "💾 <b>پایگاه داده:</b> {value}",
    "caption_path": "📂 <b>مسیر (فایل / پوشه):</b> {value}",
    "caption_skipped": "⚠️ <b>ردشده:</b> {value}",
    "caption_skipped_symlink_one": "{count} پیوند نمادین ناامن",
    "caption_skipped_symlink_many": "{count} پیوند نمادین ناامن",
    "caption_skipped_special_one": "{count} فایل ویژه",
    "caption_skipped_special_many": "{count} فایل ویژه",
    "caption_skipped_unreadable_one": "{count} مورد خوانده‌نشده",
    "caption_skipped_unreadable_many": "{count} مورد خوانده‌نشده",
    "caption_run_id": "🆔 <b>شناسه اجرا:</b> {value}",
    "caption_original_size": "📦 <b>اندازه اصلی بکاپ:</b> {value}",
    "caption_final_size": "🗜 <b>اندازه نهایی بکاپ:</b> {value}",
    "caption_part": "📄 <b>بخش {number} از {total}</b>",
    "settings_text": (
        "⚙️ <b>تنظیمات</b>\n\n<b>⚙️ محیط اجرا</b>\nمحیط: {environment} · نسخه"
        ": {version}\nپایتون: {python}\n\n<b>🗄 پایگاه داده</b>\n{backend}: {da"
        "tabase}\nساختار: {schema}\nمهاجرت: {revision}\n\n<b>📡 تلگرام</b>\nبات "
        "/ ورکر MTProto: {telegram}\nحد بارگذاری: {limit:,} بایت\n\n<b>🌐 شبکه"
        "</b>\nSOCKS5: {proxy} · {endpoint}\nاتصال: {proxy_status}\n\n<b>🕒 زما"
        "ن</b>\nمنطقهٔ زمانی پشتیبان\u200cگیری: {timezone}\nزمان UTC: {utc}\n\n<b>💾"
        " فضای ذخیره\u200cسازی</b>\n{storage} · آزاد: {disk:,} MiB\nدر انتظار: {p"
        "ending_count} / {pending:,} MiB\n{directories}\n\n<b>🗂 پشتیبان\u200cها</b"
        ">\nپروفایل\u200cها: {profiles} · اجراهای فعال: {running}\nزمان\u200cبند: {sch"
        "eduler}\nناموفق: {failed} · فایل\u200cهای نگهداری\u200cشده: {retained}\n\n<b>🔐"
        " امنیت</b>\nرمزنگاری اطلاعات محرمانه: {encryption}"
    ),
    "health_text": (
        "🩺 <b>سلامت سیستم</b>\nوضعیت کلی: {status}\n\n{live} برنامه\n{database"
        "} پایگاه داده ({backend})\n{schema} ساختار / مهاجرت\u200cها\n{worker} ور"
        "کر\n{scheduler} زمان\u200cبند\n{telegram} بات / ورکر تلگرام\n{proxy} پراک"
        "سی\n{helper} دستیار GitLab\n{storage} فضای ذخیره\u200cسازی\n\n<b>💾 فضای ذخ"
        "یره\u200cسازی</b>\nآزاد / مصرف\u200cشده: {disk:,} / {used:,} MiB\nدر انتظار: "
        "{pending_count} / {pending:,} MiB\n\n<b>🗂 عملیات</b>\nدر صف / در حال"
        " اجرا / ناموفق: {queued} / {running} / {failed}\nاجاره\u200cهای منقضی: "
        "{stale} · فایل\u200cهای نگهداری\u200cشده: {retained}\nابزارهای در دسترس: {to"
        "ols}\nمهاجرت: {revision}\n\n<b>📂 مسیرهای اجرا</b>\n{directories}\n\n🕒 ز"
        "مان بررسی: {checked}"
    ),
    "active": "فعال",
    "inactive": "غیرفعال",
    "yes": "بله",
    "no": "خیر",
    "schedule_interval_mode": ("🕒 زمان\u200cبندی بازه\u200cای"),
    "choose_schedule_mode": (
        "⏱ <b>نوع زمان\u200cبندی</b>\nبازه\u200cای یا کرون را انتخاب کنید. منطقهٔ زما"
        "نی: <code>{timezone}</code>.{current}"
    ),
    "current_schedule_mode": ("\n\n<b>نوع فعلی:</b> {mode}\n{schedule}"),
    "available_paths": ("📌 <b>مسیرهای در دسترس</b>\n{paths}\nبرای کپی روی مسیر بزنید."),
    "no_available_paths": (
        "در حال حاضر هیچ مسیر تأییدشده\u200cای توسط ورکر در دسترس گزارش نشده است."
    ),
    "invalid_cron": (
        "⚠️ <b>عبارت کرون نامعتبر</b>\nیک عبارت معتبر کرون لینوکس با ۵ فیلد"
        " بفرستید؛ مانند <code>30 3 * * *</code>."
    ),
    "health_healthy": ("🟢 سالم"),
    "health_warning": ("🟡 هشدار"),
    "health_error": ("🔴 خطا"),
    "health_disabled": ("⚪ غیرفعال / پیکربندی نشده"),
    "health_unverified": ("🟡 بررسی نشده"),
    "health_unavailable": (
        "🔴 <b>وضعیت سلامت در دسترس نیست</b>\nبررسی سلامت پایگاه داده ناموفق"
        " بود. گزارش\u200cهای سرویس را بررسی کنید."
    ),
    "audit_entry": ("{icon} <b>{name}</b>\n🕒 {timestamp}\n{details}"),
    "audit_target": ("هدف: {kind} <code>{target}</code>"),
    "audit_actor": ("کاربر: <code>{actor}</code>"),
    "audit_detail": ("{label}: {value}"),
    "audit_unknown": ("رویداد {event}"),
    "audit_event_application_startup": ("راه\u200cاندازی برنامه"),
    "audit_event_proxy_mode": ("حالت پراکسی"),
    "audit_event_telegram_connection_state": ("اتصال تلگرام"),
    "audit_event_backup_timezone_changed": ("تغییر منطقهٔ زمانی پشتیبان\u200cگیری"),
    "audit_event_backup_created": ("پشتیبان ایجاد شد"),
    "audit_event_backup_edited": ("پشتیبان ویرایش شد"),
    "audit_event_backup_deleted": ("پشتیبان حذف شد"),
    "audit_event_backup_enabled": ("پشتیبان فعال شد"),
    "audit_event_backup_disabled": ("پشتیبان غیرفعال شد"),
    "audit_event_admin_created": ("مدیر ایجاد شد"),
    "audit_event_admin_edited": ("مدیر ویرایش شد"),
    "audit_event_admin_deleted": ("مدیر حذف شد"),
    "audit_event_admin_enabled": ("مدیر فعال شد"),
    "audit_event_admin_disabled": ("مدیر غیرفعال شد"),
    "audit_event_remote_server_created": ("سرور راه دور ایجاد شد"),
    "audit_event_remote_server_edited": ("سرور راه دور ویرایش شد"),
    "audit_event_remote_server_deleted": ("سرور راه دور حذف شد"),
    "audit_event_remote_server_enabled": ("سرور راه دور فعال شد"),
    "audit_event_remote_server_disabled": ("سرور راه دور غیرفعال شد"),
    "audit_event_backup_draft_created": ("ایجاد پیش\u200cنویس پشتیبان"),
    "audit_event_backup_profile_configured": ("پیکربندی پشتیبان"),
    "audit_event_backup_profile_configuration_updated": ("ویرایش پیکربندی پشتیبان"),
    "audit_event_backup_access_granted": ("اعطای دسترسی پشتیبان"),
    "audit_event_backup_access_revoked": ("لغو دسترسی پشتیبان"),
    "audit_event_language_changed": ("تغییر زبان"),
    "audit_event_superadmin_bootstrap": ("آماده\u200cسازی مدیر ارشد"),
    "audit_event_superadmin_restored": ("بازیابی مدیر ارشد"),
    "audit_event_remote_server_trusted": ("تأیید سرور راه دور"),
    "audit_event_remote_server_retrusted": ("تغییر تأیید سرور راه دور"),
    "audit_event_remote_server_connection_tested": ("آزمایش اتصال راه دور"),
    "audit_event_backup_run_queued": ("اجرای پشتیبان: در صف"),
    "audit_event_backup_run_claimed": ("اجرای پشتیبان: دریافت شد"),
    "audit_event_backup_run_started": ("اجرای پشتیبان: آغاز شد"),
    "audit_event_backup_run_completed": ("اجرای پشتیبان: تکمیل شد"),
    "audit_event_backup_run_failed": ("اجرای پشتیبان: ناموفق"),
    "audit_event_backup_run_cancelled": ("اجرای پشتیبان: لغو شد"),
    "audit_event_backup_run_cancel_requested": ("اجرای پشتیبان: درخواست لغو"),
    "audit_event_backup_run_retry_requested": ("اجرای پشتیبان: درخواست تلاش مجدد"),
    "audit_event_backup_run_recovered": ("اجرای پشتیبان: بازیابی شد"),
    "audit_event_backup_run_delivery_pending": ("اجرای پشتیبان: در انتظار ارسال"),
    "audit_event_backup_run_delivery_retried": ("اجرای پشتیبان: تلاش مجدد ارسال"),
    "audit_event_backup_run_artifact_purged": ("اجرای پشتیبان: حذف فایل"),
    "audit_label_connected": ("متصل"),
    "audit_label_proxy_enabled": ("پراکسی"),
    "audit_label_socks5_enabled": ("SOCKS5"),
    "audit_label_mode": ("حالت"),
    "audit_label_status": ("وضعیت"),
    "audit_label_stage": ("مرحله"),
    "audit_label_error_code": ("کد خطا"),
    "audit_label_admin_id": ("شناسهٔ مدیر"),
    "audit_label_timezone": ("منطقهٔ زمانی"),
    "audit_label_backup": ("پشتیبان"),
    "audit_label_backup_run": ("اجرا"),
    "audit_label_admin": ("مدیر"),
    "audit_label_user": ("کاربر"),
    "audit_label_remote_server": ("سرور راه دور"),
    "audit_label_system_settings": ("تنظیمات"),
    "health_idle": "⚪ آماده (بدون پروفایل زمان\u200cبندی\u200cشده)",
    "audit_label_backup_timezone": "منطقهٔ زمانی پشتیبان\u200cگیری",
    "audit_label_source_type": "منبع",
    "audit_label_schedule": "زمان\u200cبندی",
    "audit_label_compression": "فشرده\u200cسازی",
    "audit_label_encryption": "رمزنگاری",
    "audit_label_assigned_admin_count": "مدیران مجاز",
    "audit_label_enabled": "فعال",
    "audit_label_database_credentials_changed": "تغییر رمز پایگاه داده",
    "audit_label_age_unlock_changed": "تغییر عبارت عبور Age",
    "audit_event_backup_run_succeeded": "اجرای پشتیبان موفق",
    "audit_event_backup_run_retention_cleanup": "پاک\u200cسازی فایل نگهداری\u200cشده",
    "audit_event_authorization_denied": "دسترسی رد شد",
}
