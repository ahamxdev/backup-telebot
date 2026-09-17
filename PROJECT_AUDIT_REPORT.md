# گزارش ممیزی کامل و فقط‌خواندنی پروژه `backup-telebot`

تاریخ ممیزی: ۲۰ شهریور ۱۴۰۵ / 11 September 2026  
مبنای ممیزی: فایل‌های واقعاً موجود در سیستم، نه ادعاهای README  
نسخه مشاهده‌شده: `0.1.0`  
دامنه: تمام کد `src`، migrationها، آزمون‌ها، Docker/Compose، تنظیمات، lockfile و دیتابیس runtime موجود  

> این ممیزی هیچ کد برنامه‌ای را تغییر نداده است. فقط همین گزارش ایجاد شده است. تمام cacheها و دیتابیس‌های آزمایشی در `/tmp` ساخته و سپس پاک شدند؛ دیتابیس واقعی `.runtime/data/app.sqlite3` فقط با URI فقط‌خواندنی باز شد.

## خلاصه مدیریتی

این پروژه یک «صفحه کنترل بکاپ از طریق تلگرام» است: یک فرایند Bot برای UI و مدیریت، و یک Worker جدا برای زمان‌بندی، گرفتن بکاپ، فشرده‌سازی/رمزگذاری و ارسال به ادمین‌های مجاز. دیتابیس کنترل پروژه SQLite است؛ PostgreSQL، MySQL/MariaDB و MSSQL فقط **منبع بکاپ** هستند و داده‌های کنترلی در آن‌ها نگهداری نمی‌شود.

پیاده‌سازی فعلی صرفاً اسکلت یا placeholder نیست. مدل داده، migration، ویزارد تلگرام، SSH pinning، موتور فایل محلی/ریموت، snapshot از SQLite، pipeline پایدار، lease، spool تحویل و fan-out تلگرام واقعاً نوشته شده‌اند. ۷۷ آزمون نیز پاس می‌شوند. بااین‌حال، پروژه هنوز برای integration واقعی یا production آماده نیست؛ مهم‌ترین دلایل عبارت‌اند از:

- مسیر MySQL/MariaDB در toolchain فعلی عملاً خراب است: کد symlink به `mariadb-dump` را به‌اشتباه MySQL تشخیص می‌دهد و گزینه ناسازگار `ssl-mode` می‌سازد.
- TLS در MSSQL به `pyOpenSSL` نیاز دارد، اما این dependency در پروژه/lock وجود ندارد؛ حالت `PREFERRED` نیز عملاً TLS را فعال نمی‌کند.
- هیچ آزمون واقعی برای PostgreSQL/MySQL/MSSQL، SSH/SFTP/rsync یا Telegram وجود ندارد.
- cancellation، retry تحویل، cleanup فایل‌های failed و محدودسازی مصرف دیسک چند نقص مهم عملیاتی دارند.
- UI تاریخچه/وضعیت/cancel اجرا ندارد و تنظیمات اصلی profile پس از ساخت قابل ویرایش نیست.

نتیجه کلی: **هسته معماری مناسب و قابل ادامه است، اما وضعیت فعلی pre-integration است.**

## ۱. پروژه چه می‌کند و وضعیت واقعی قابلیت‌ها

جریان اصلی چنین است:

1. SUPERADMIN از تلگرام ادمین، Remote Server و Backup Profile می‌سازد.
2. profile شامل یک منبع، schedule، compression، encryption و مجموعه گیرندگان ADMIN است.
3. اجرای دستی یا scheduler یک `backup_runs` می‌سازد.
4. Worker run را با lease claim می‌کند، منبع را در workspace می‌گیرد، verify می‌کند، transformها را اجرا می‌کند و خروجی را اتمیک به pending-delivery می‌برد.
5. Worker پیش از هر ارسال، مجوز گیرنده را دوباره از دیتابیس می‌خواند؛ تحویل‌های موفق را نگه می‌دارد و فقط گیرنده‌های باقیمانده را retry می‌کند.
6. پس از موفقیت همه گیرنده‌های فعلی (یا revoked شدن آن‌ها)، فایل spool حذف و metadata/checksum در SQLite باقی می‌ماند.

وضعیت واقعی:

| بخش | وضعیت | توضیح |
|---|---:|---|
| Bot و UI دو‌زبانه | ✅ | جریان‌های مدیریت و ساخت profile کامل هستند؛ ۱۸۸ کلید در هر زبان و key setها برابرند. |
| RBAC | ✅ | SUPERADMIN و ADMIN، allowlist و silent-ignore پیاده شده‌اند. |
| مدیریت admin/access | ✅ | CRUD نرم، فعال/غیرفعال و assignment دوطرفه روی یک جدول. |
| Remote Server/SSH | ⚠️ | pinning و secret encryption خوب است؛ timeout/quota و ایمنی نام SFTP نقص دارد. |
| PostgreSQL | ⚠️ | dump/verify واقعی است، ولی image فقط client 15 دارد. |
| MySQL/MariaDB | ❌ | به علت تشخیص اشتباه symlink و `ssl-mode` در محیط فعلی اجرا نمی‌شود. |
| MSSQL | ⚠️ | backup/VERIFYONLY نوشته شده؛ TLS dependency غایب و cancellation ناسالم است. |
| فایل محلی و SQLite | ⚠️ | tar و snapshot واقعی؛ TOCTOU و quota ناقص. |
| فایل ریموت و SQLite | ⚠️ | rsync→SFTP fallback و snapshot واقعی؛ traversal/quota/timeout ناقص. |
| compression | ✅/⚠️ | همه ۶ فرمت + NONE اجرا و smoke-test شده؛ resource limit همه ابزارها را پوشش نمی‌دهد. |
| Age encryption | ✅/🧪 | passphrase با ابزار واقعی smoke-test شده؛ public-key فقط mock شده است. |
| pipeline/lease/recovery | ⚠️ | durable است، ولی cancel و cleanup failed spool نقص دارند. |
| Telegram delivery | 🧪/⚠️ | fan-out/retry/recovery با fake transport آزموده شده؛ Telegram واقعی آزموده نشده و retry بی‌نهایت است. |
| Docker Compose | ⚠️ | hardening خوب و config معتبر؛ credential واقعی/source mount حاضر نیست. |

## ۲. معماری و درخت پروژه

```text
backup-telebot/
├── .env.example                 نمونه ۴۵ متغیر محیطی
├── pyproject.toml               metadata، dependencyها، entry pointها و تنظیم ابزارها
├── uv.lock                      lock کامل dependencyها
├── Dockerfile                   Python 3.13 slim، ابزارهای backup، کاربر non-root
├── compose.yml                  migrate، bot و worker با volumeهای مشترک
├── alembic.ini
├── migrations/
│   ├── env.py                   اجرای async Alembic بر URL تنظیمات
│   └── versions/0001..0006      زنجیره خطی schema
├── src/
│   ├── main.py                  entry point ربات
│   ├── bootstrap/app.py         bootstrap، Telethon handlers، lifecycle ربات
│   ├── cli/main.py              backupctl: version/bootstrap/doctor/healthcheck
│   ├── config/settings.py       Pydantic settings و validation
│   ├── domain/enums.py          تمام enumهای دامنه و stateها
│   ├── database/
│   │   ├── models.py            ۱۲ مدل SQLAlchemy
│   │   └── session.py           engine/session و PRAGMAهای SQLite
│   ├── application/
│   │   ├── bootstrap_service.py ساخت idempotent اولین SUPERADMIN
│   │   ├── admin_service.py     RBAC، admin CRUD، language و assignments
│   │   ├── backup_service.py    ساخت/ویرایش/archive profile و secret persistence
│   │   ├── profile_config.py    DTO و validation منبع/schedule/transform
│   │   ├── remote_server_service.py  probe/confirm/test/edit/delete SSH
│   │   ├── settings_service.py  backup timezone
│   │   ├── validation.py        Telegram ID و نام
│   │   └── services.py          re-export سازگار سرویس‌ها
│   ├── infrastructure/
│   │   ├── repositories.py      User/Backup/Remote/Settings/Access/Audit repository
│   │   └── ssh.py               TOFU confirmation و pin دقیق fingerprint
│   ├── presentation/telegram/
│   │   ├── dispatcher.py        allowlist، RBAC، routing و تمام wizardها
│   │   ├── keyboards.py         reply/inline keyboardها
│   │   └── i18n/{en,fa,service}.py
│   ├── services/
│   │   ├── fsm.py               FSM حافظه‌ای با timeout/invalid limit
│   │   ├── callbacks.py         capability token تصادفی و actor-bound
│   │   └── rate_limit.py        sliding-window حافظه‌ای
│   ├── security/secrets.py      AES-256-GCM با AAD/version/nonce
│   ├── app_logging/setup.py         structlog و redaction مبتنی بر نام key
│   ├── telegram/transport.py    Telethon، SOCKS5 و upload helper قدیمی/جدا
│   └── pipeline/
│       ├── jobs.py              enqueue، one-active، claim/renew/cancel
│       ├── schedule.py          محاسبه schedule و cursor
│       ├── acquisition.py       تمام موتورهای acquisition
│       ├── process.py           subprocess/PTY و timeout/kill
│       ├── transforms.py        compression، Age و SHA-256
│       ├── finalize.py          spool اتمیک، manifest و recovery
│       ├── delivery.py          fan-out و retry هر گیرنده
│       ├── workspace.py         مسیرهای UUID-owned و cleanup محدود
│       └── worker.py            orchestration، concurrency و recovery
└── tests/{unit,integration,security}/
```

### کلاس‌ها و وابستگی‌های مهم

- `TelegramDispatcher` (`dispatcher.py:143`) نقطه ورود همه updateهای Telegram است. به repositoryها، application serviceها، `FSMStore`، `CallbackStore`، `RateLimiter` و `RunService` وابسته است.
- `BackupService` (`backup_service.py:34`) validationهای `profile_config` را به مدل‌های SQLAlchemy تبدیل می‌کند و password/passphrase را با `SecretCipher` رمز می‌کند.
- `RemoteServerService` (`remote_server_service.py:28`) میان UI/repository و `SSHGateway` قرار دارد؛ probe قبل از ذخیره fingerprint انجام می‌شود.
- `RunService` (`jobs.py:24`) مرز durable job است: enqueue، collision، lease و cancel.
- `PipelineRunner` (`worker.py:109`) acquire → compress → encrypt → finalize → deliver را orchestrate می‌کند.
- `DeliveryService` (`delivery.py:27`) وضعیت per-recipient را persist و authorization را پیش از هر attempt re-check می‌کند.
- `SecretCipher` (`secrets.py:9`) از AES-GCM با کلید ۳۲-byte، nonce تصادفی ۱۲-byte، version byte و AAD جدا برای هر نوع secret استفاده می‌کند.
- `Database` (`session.py:27`) engine async و session factory می‌دهد؛ هنگام اتصال SQLite، foreign keys، WAL و busy timeout را فعال می‌کند (`session.py:16-22`).

وابستگی لایه‌ای عمدتاً صحیح است: presentation → application/repositories → models؛ worker → pipeline/application config → repositories/models. استثناهای قابل توجه، import شدن `disk_has_capacity` از worker در dispatcher (`dispatcher.py:47`) و وجود upload pipeline جدا و استفاده‌نشده در `telegram/transport.py:82-171` هستند.

## ۳. Startup و جریان runtime

### `backup-bot`

entry point در `pyproject.toml:25-28` به `main.entrypoint` متصل است. `main.py:8-11` تنظیمات و logging را می‌سازد و `run_bot` را با `asyncio.run` اجرا می‌کند.

`run_bot` (`bootstrap/app.py:14-57`):

1. Telegram credentialها و SUPERADMIN ID را الزام می‌کند.
2. `DATA_DIR` و `TEMP_DIR` را با mode مورد انتظار 0700 می‌سازد و session path را secure می‌کند.
3. SUPERADMIN را فقط اگر هیچ SUPERADMIN فعالی نیست bootstrap می‌کند (`bootstrap_service.py:18-43`).
4. startup/proxy audit event ثبت می‌کند.
5. Telethon client، dispatcher و handlerهای message/callback را می‌سازد.
6. با bot token متصل می‌شود و تا disconnect منتظر می‌ماند.
7. در `finally` client و database engine را می‌بندد.

Migration خودکار داخل bot وجود ندارد؛ اجرای دستی بدون migration به خطای table/revision می‌رسد.

### `backup-worker`

entry point در `worker.py:492-498` است. `run_worker` (`worker.py:448-489`):

1. Telegram credentials، session مستقل worker و master key را لازم دارد.
2. Telethon client را start می‌کند.
3. cleanupهای ثبت‌شده متعلق به runهای terminal را recovery می‌کند.
4. owner یکتا از hostname+UUID می‌سازد.
5. در loop، taskهای تمام‌شده را کنار می‌گذارد، system settings را می‌خواند، capacity را می‌سنجد و scheduler را tick می‌کند.
6. تا `MAX_CONCURRENT_BACKUPS` run claim و task می‌سازد؛ یک semaphore جدا remote transfer را محدود می‌کند.
7. shutdown: taskها cancel/gather، سپس Telegram client و DB dispose می‌شوند.

Worker migration اجرا نمی‌کند. shutdown عادی cleanup دارد، ولی عملیات `to_thread` ذاتاً قابل متوقف‌کردن نیستند (یافته H-08).

### `backupctl`

- `version` (`cli/main.py:20-23`): نسخه package.
- `bootstrap-superadmin` (`cli/main.py:26-48`): ساخت idempotent SUPERADMIN و dispose صحیح DB.
- `doctor` (`cli/main.py:64-110`): config، Telegram، proxy، directoryها، session mode، DB revision، disk، Python و packageها. برخلاف واژه diagnostics، directory می‌سازد (`cli/main.py:84-89`).
- `healthcheck` (`cli/main.py:113-128`): writable بودن data/work/pending و `SELECT 1`/وجود alembic table. اتصال Telegram، revision=head، worker session و سالم بودن worker/bot را بررسی نمی‌کند.

### Docker Compose

- `migrate`: همان image، command=`alembic upgrade head`، بدون restart/healthcheck.
- `bot` و `worker`: فقط پس از موفقیت migrate شروع می‌شوند (`compose.yml:36-54`).
- هر سه service volumeهای `state`, `telegram`, `runtime` را share می‌کنند؛ session file ربات و worker جداست.
- hardening: `read_only`, `init: true`, non-root UID 10001، `no-new-privileges`, حذف همه capabilityها، pids/memory/CPU limit و `/tmp` با tmpfs (`compose.yml:16-28`; `Dockerfile:26-35`). Docker socket mount نشده است.
- image ابزارهای age، gzip/bzip2/xz/zstd/zip/7z، rsync/OpenSSH، MariaDB client و PostgreSQL client 15 را نصب می‌کند (`Dockerfile:12-17`).
- Compose فعلی هیچ source bind mount برای local files یا local MSSQL artifact ندارد؛ بنابراین این profileها بدون override کاربر قابل استفاده نیستند.

### Migration lifecycle

`migrations/env.py:15-17` URL را از `Settings` جایگزین می‌کند و حتی commandهای metadata مانند history هنگام import، `DATA_DIR` را می‌سازند. اتصال migration با `NullPool` ساخته، در context بسته و engine dispose می‌شود (`env.py:39-47`). زنجیره یک head خطی `0006` دارد.

## ۴. جدول کامل تنظیمات و env

تمام fieldها در `config/settings.py:12-56` تعریف شده‌اند. مدل `case_sensitive=True` و `.env` را می‌خواند (`settings.py:13`). «الزام runtime» یعنی ممکن است Pydantic آن را optional بداند ولی entry point بدون آن fail شود.

| متغیر | هدف/محل مصرف | پیش‌فرض | الزام | secret | validation/نکته |
|---|---|---|---|---:|---|
| `APP_ENV` | حالت config؛ فقط validation production | `development` | اختیاری | خیر | فقط development/test/production؛ production credentialها و key را الزام می‌کند (`settings.py:98-108`). |
| `APP_NAME` | نام برنامه | `backup-telebot` | اختیاری | خیر | در runtime استفاده نشده است. |
| `APP_TIMEZONE` | timezone ادعایی برنامه | `UTC` | اختیاری | خیر | IANA validate می‌شود، ولی runtime استفاده نمی‌کند. |
| `DISPLAY_TIMEZONE` | timezone نمایش ادعایی | `Asia/Baku` | اختیاری | خیر | IANA validate می‌شود، ولی UI از `system_settings.backup_timezone` استفاده می‌کند. |
| `MASTER_ENCRYPTION_KEY_B64` | AES-GCM secrets | خالی/`None` | production و worker؛ عملاً ساخت هر profile در UI | بله | base64 و طول دقیق ۳۲ byte هنگام ساخت `SecretCipher` (`secrets.py:14-21`). |
| `DATABASE_URL` | SQLite control DB | `sqlite+aiosqlite:///./.runtime/data/app.sqlite3` | اختیاری | ممکن است | فقط prefix SQLite async مجاز (`settings.py:91-92`). Compose آن را `/data/state/app.sqlite3` می‌کند. |
| `TELEGRAM_API_ID` | Telethon | `None` | bot/worker | خیر | int؛ production non-null. |
| `TELEGRAM_API_HASH` | Telethon | `None` | bot/worker | بله | `SecretStr`. |
| `TELEGRAM_BOT_TOKEN` | start bot | `None` | bot/worker | بله | `SecretStr`. |
| `TELEGRAM_SUPERADMIN_ID` | bootstrap allowlist | `None` | bot/bootstrap | شناسه حساس | positive signed-64-bit (`settings.py:73-78`). |
| `TELEGRAM_SUPERADMIN_LANGUAGE` | زبان اولین bootstrap | `en` | اختیاری | خیر | enum `en/fa`; پس از وجود SUPERADMIN قبلی آن را تغییر نمی‌دهد. |
| `TELEGRAM_SESSION_PATH` | session bot | `.runtime/telegram/telegram.session` | اختیاری | بله | parent 0700/file 0600 در startup؛ UI health فقط این session را چک می‌کند. |
| `TELEGRAM_WORKER_SESSION_PATH` | session worker | `.runtime/telegram/worker.session` | اختیاری | بله | worker آن را به client path تبدیل می‌کند. |
| `TELEGRAM_PROXY_ENABLED` | SOCKS5 | `false` | اختیاری | خیر | در حالت true host+port لازم است. |
| `TELEGRAM_PROXY_HOST` | proxy host | `None` | شرطی | خیر | فقط nonempty بودن ضمن enabled؛ hostname مستقل validate نمی‌شود. |
| `TELEGRAM_PROXY_PORT` | proxy port | `None` | شرطی | خیر | 1..65535. |
| `TELEGRAM_PROXY_USERNAME` | proxy auth | `None` | شرطی | نسبتاً حساس | باید همراه password باشد. |
| `TELEGRAM_PROXY_PASSWORD` | proxy auth | `None` | شرطی | بله | `SecretStr`؛ باید همراه username باشد. |
| `TELEGRAM_MAX_UPLOAD_BYTES` | سقف Telegram artifact | `2,000,000,000` | اختیاری | خیر | `0 < x <= 2GB`; قبل از send چک می‌شود (`delivery.py:54-59`). |
| `TELEGRAM_UPLOAD_TIMEOUT_SECONDS` | timeout هر upload | `7200` | اختیاری | خیر | positive؛ `asyncio.timeout` در delivery. |
| `TELEGRAM_UPLOAD_RETRIES` | retry در هر claim | `5` | اختیاری | خیر | 0..20؛ retry بین claimها نامحدود است. |
| `FSM_TIMEOUT_SECONDS` | expiry wizard/callback | `900` | اختیاری | خیر | حداقل 30؛ cleanup حافظه lazy است. |
| `USER_RATE_LIMIT_PER_MINUTE` | نرخ هر user | `30` | اختیاری | خیر | positive. |
| `GLOBAL_RATE_LIMIT_PER_MINUTE` | نرخ کل | `300` | اختیاری | خیر | positive. |
| `INVALID_INPUT_LIMIT` | لغو flow پس از خطا | `5` | اختیاری | خیر | positive. |
| `LOG_LEVEL` | سطح log | `INFO` | اختیاری | خیر | enum DEBUG..CRITICAL. |
| `LOG_FORMAT` | console/json | `console` | اختیاری | خیر | enum console/json. |
| `DATA_DIR` | ساخت data directory؛ migration/bootstrap/health | `.runtime/data` | اختیاری | خیر | path؛ خود DB از `DATABASE_URL` می‌آید. |
| `TEMP_DIR` | directory ادعایی temp | `.runtime/tmp` | اختیاری | خیر | ساخته/doctor می‌شود؛ `tempfile`های backup از آن استفاده نمی‌کنند. |
| `ALLOWED_SOURCE_ROOTS` | allowlist local path CSV | `/srv/backups` | لازم برای local files | خیر | هر path در create و acquisition دوباره lexical+resolved validate می‌شود. |
| `DENIED_SOURCE_ROOTS` | denylist CSV | code: `/proc,/sys,/dev,/run`; example گسترده‌تر | اختیاری | خیر | runtime dirs به‌طور خودکار پس از resolve افزوده می‌شوند (`settings.py:61-71`). |
| `WORK_ROOT` | raw/staging/final/temp workspace | `.runtime/work` | اختیاری | خیر | UUID-owned path. Compose `/data/work` symlink به volume runtime. |
| `PENDING_DELIVERY_ROOT` | durable spool | `.runtime/pending-delivery` | اختیاری | خیر | باید هم-filesystem با work باشد تا `os.replace` اتمیک باشد. |
| `MAX_CONCURRENT_BACKUPS` | تعداد taskهای worker | `2` | اختیاری | خیر | 1..32. |
| `MAX_CONCURRENT_REMOTE_TRANSFERS` | semaphore remote | `2` | اختیاری | خیر | 1..32. |
| `MAX_BACKUP_RUNTIME_SECONDS` | timeout بعضی subprocessها | `21600` | اختیاری | خیر | حداقل 60؛ timeout کل run نیست. |
| `JOB_LEASE_SECONDS` | lease/heartbeat | `120` | اختیاری | خیر | حداقل 30؛ heartbeat هر یک‌سوم. |
| `WORKER_POLL_SECONDS` | فاصله loop | `2` | اختیاری | خیر | 0.1..60. |
| `MIN_FREE_DISK_BYTES` | backpressure free disk | `5,000,000,000` | اختیاری | خیر | >=0؛ فقط قبل از شروع acquisition. |
| `MAX_PENDING_DELIVERY_BYTES` | سقف مجموع spool ثبت‌شده | `50,000,000,000` | اختیاری | خیر | positive؛ reservation/scan filesystem ندارد. |
| `UPLOAD_PROGRESS_INTERVAL_SECONDS` | throttle progress | `5` | اختیاری | خیر | 1..300؛ callback فعلی هیچ progressی گزارش نمی‌کند. |
| `POSTGRES_CLIENT_ROOT` | انتخاب pg_dump/pg_restore هم-major | `/usr/lib/postgresql` | PostgreSQL | خیر | صرفاً Path؛ executable existence بعد از probe چک می‌شود. |
| `CONTAINER_MEMORY_LIMIT` | substitution در Compose | `2g` | اختیاری | خیر | field تنظیمات Python نیست؛ Docker مقدار را validate می‌کند. |
| `CONTAINER_CPU_LIMIT` | substitution در Compose | `2.0` | اختیاری | خیر | field تنظیمات Python نیست. |

`.env` در filesystem وجود ندارد. `.env.example` credentialها/master key را خالی می‌گذارد؛ بنابراین اجرای فعلی بدون تزریق env، bot/worker عملیاتی نیست.

## ۵. schema دیتابیس و migrationها

مدل‌ها در `database/models.py` هستند. دیتابیس runtime موجود integrity=`ok`، revision=`0006`، دارای یک user و بدون profile/server/run/artifact/delivery بود. فایل DB و WAL/SHM mode 0644 دارند، ولی parent directory mode 0700 است.

### جدول‌ها

| جدول | ستون‌ها و رابطه‌های مهم | constraint/index مهم |
|---|---|---|
| `users` | Telegram ID، نام، role، language، enabled، soft delete، created_by self-FK، last_seen | Telegram ID unique/index و positive؛ role/lang CHECK؛ index enabled/deleted (`models.py:57-85`). |
| `backup_profiles` | UUID/name، source type، enabled/status، schedule cursor/config، compression، Age config، creator، archive؛ 1:1 database/files، 1:N runs/access | UUID/name unique؛ schedule interval/day CHECK؛ next/archive index (`models.py:88-151`). |
| `remote_servers` | host/port/user، auth method، encrypted key/passphrase، pinned fingerprint، enabled/deleted | name unique؛ port/auth CHECK؛ enabled/deleted index (`models.py:154-177`). |
| `database_sources` | PK/FK profile، engine/endpoint/db/user/encrypted password/TLS/CA؛ MSSQL path/docker/artifact server | port و MSSQL path combination CHECK؛ artifact server FK RESTRICT (`models.py:180-213`). |
| `files_sources` | PK/FK profile، LOCAL/REMOTE location، optional remote server | FK CASCADE/RESTRICT؛ constraint سازگاری location/server ندارد (`models.py:216-230`). |
| `backup_paths` | source FK، path، SQLite-candidate | nonempty CHECK و source index (`models.py:233-243`). |
| `system_settings` | singleton id=1، backup timezone، compression threads/memory | `id=1` CHECK؛ range برای thread/memory ندارد (`models.py:246-254`). |
| `admin_backup_access` | composite PK admin/profile، creator و timestamp | هر دو FK CASCADE؛ creator RESTRICT (`models.py:256-268`). |
| `audit_events` | type، actor، target، JSON text details، timestamp | event/time index؛ actor SET NULL (`models.py:271-282`). |
| `backup_runs` | UUID/profile/trigger/slot، status/stage، lease، cancel، timestamps، workspace/spool/final metadata/error | UUID unique؛ schedule slot unique؛ partial unique index برای یک active run (`models.py:285-330`). |
| `backup_artifacts` | run، kind FINAL/MANIFEST، relative path، size/hash | `(run,kind)` unique، run index (`models.py:333-345`). |
| `backup_deliveries` | run/admin، status، attempts، Telegram IDs، delivered/error | `(run,admin)` unique؛ run/status index (`models.py:348-367`). |

### زنجیره migration

- `0001`: users، backup_profiles، user_backup_access و audit_events.
- `0002`: CHECKهای role/source/status قدیمی.
- `0003`: `USER→ADMIN`، language و rename جدول access.
- `0004`: source type جدید، profile config، remote/database/files/path/system tables.
- `0005`: `authentication_method=PRIVATE_KEY`.
- `0006`: schedule cursor، runs، artifacts، deliveries و partial active-run index.

آزمایش fresh upgrade تا head موفق بود و `PRAGMA foreign_key_check` خروجی خالی داشت. `alembic history` و `heads` نیز زنجیره بالا و head واحد 0006 را تأیید کردند.

### ناسازگاری schema/model

`alembic check` اضافی روی همان DB تازه‌ساخته‌شده fail شد و برای ۱۵ ستون enum تغییر `VARCHAR → Enum` گزارش کرد. migrationها برای schedule kind، compression format/preset، encryption mode، database engine/TLS، files location، run trigger/status/stage، artifact kind و delivery status CHECK بسته ندارند؛ تنها بعضی enumها CHECK دستی دارند. در نتیجه write مستقیم/خرابی DB می‌تواند مقدار نامعتبر وارد کند و ORM هنگام load با `LookupError` worker/bot را مختل کند. همچنین `files_sources` و `system_settings` invariantهای application را در DB enforce نمی‌کنند.

## ۶. نقشه کامل UI تلگرام

### دروازه مشترک

`handle` ابتدا private بودن، Telegram ID مثبت، user فعال/حذف‌نشده و rate limit را بررسی می‌کند (`dispatcher.py:159-175`). ناشناس، group، disabled و rate-limited بدون پاسخ‌اند. message در جریان secret قبل از پردازش برای حذف از Telegram تلاش می‌شود (`dispatcher.py:181-191`). `/start` و Home، FSM را پاک می‌کنند؛ Cancel نیز پاک می‌کند (`dispatcher.py:235-253`).

### منوی SUPERADMIN

- Backups | Remote Servers
- Admins
- Audit Log | System Health
- Settings | Language
- Help

### منوی ADMIN

- My Backups: فقط نام profileهای assigned و archive‌نشده
- Language
- Help

ADMIN دکمه اجرا/دانلود/مدیریت ندارد. ارسال artifact توسط Worker خودکار است.

### مدیریت Admin

- list card: نام، Telegram ID، enabled، زبان و backupهای assigned.
- Add: name → Telegram ID → language → confirmation → assignment selector.
- Edit: name، Telegram ID، language، backup access، enable/disable.
- Delete: confirmation → soft delete+disable+revocation.
- selectorها ۸تایی paginate می‌شوند؛ inline actionها token تصادفی، actor-bound و expiring هستند (`callbacks.py:15-43`).

### Remote Servers

- Add/Reconfigure: name → host → port → username → private key → optional key passphrase → اتصال اولیه → نمایش SHA-256 fingerprint → confirmation → encrypted persistence.
- Edit: همه connection detailها با probe/trust مجدد، یا فقط toggle enabled.
- Test: اتصال با pin ذخیره‌شده.
- Delete: soft delete/disable.
- فقط private-key auth موجود است.

### ویزارد Backup Profile

مسیر مشترک: name → description → source → source detail → schedule → compression → encryption → admins → summary بدون secret → confirm.

Database:

- PostgreSQL / MySQL-MariaDB / MSSQL
- host، port، دقیقاً یک database name، username، password
- TLS: Disabled، Preferred، Required، Verify CA، Verify Full؛ برای دو verify، PEM CA گرفته می‌شود.
- MSSQL: سؤال Docker؛ SQL Server/container backup destination؛ اگر Docker، corresponding host-mounted path؛ سپس remote artifact server اختیاری.

Files/Directories:

- Local: چند absolute path؛ create و run در allow/deny roots validate می‌شوند.
- Remote: انتخاب یک enabled remote server و چند absolute remote path.
- پسوندهای `.sqlite`, `.sqlite3`, `.db` به‌عنوان snapshot candidate علامت می‌خورند.

Schedule:

- manual؛ هر N دقیقه/ساعت/روز؛ روزانه HH:MM؛ هفتگی weekday 0..6 + HH:MM؛ ماهانه روز 1..28 + HH:MM.
- timezone از singleton `system_settings` می‌آید، نه `DISPLAY_TIMEZONE`.

Compression:

- NONE، Zstandard، Gzip، XZ، Bzip2، ZIP، 7z.
- برای همه به‌جز NONE: Fast/Balanced/High/Maximum.

Encryption:

- NONE؛ Age public key (چند recipient)؛ Age passphrase.

Manual backup:

- فقط داخل Edit profile → Run Backup Now، فقط برای SUPERADMIN و profile enabled.
- backpressure و one-active collision به user گفته می‌شود (`dispatcher.py:1001-1027`).
- run history/status واقعی و Cancel در UI وجود ندارد. `_show_backups` همیشه عبارت «No active operations» را می‌فرستد، بدون query (`dispatcher.py:1083-1086`).

Editing profile محدود به name، description، admin access و enabled است (`keyboards.py:90-97`). source/credential/TLS/schedule/compression/encryption پس از ساخت قابل ویرایش نیست؛ راه فعلی archive و ساخت profile جدید است.

### پوشش زبان

English و Persian هر دو ۱۸۸ key و مجموعه کلید یکسان دارند؛ آزمون آن را enforce می‌کند (`test_admin_localization_access.py:47`). متن visible اصلی از catalog می‌آید. چند مقدار runtime مانند stage enum، ISO timestamp و errorهای عمومی در صورت نمایش/لاگ localized نیستند. Labelهای name/user-entered با `parse_mode=None` ارسال می‌شوند تا markup injection رخ ندهد (`transport.py:69-71`).

## ۷. authorization و security model

- فقط `Role.SUPERADMIN` و `Role.ADMIN` تعریف شده (`enums.py:4-6`).
- operationهای مدیریتی علاوه بر UI، در service layer با `require_superadmin` re-check می‌شوند (`admin_service.py:24-29`).
- SUPERADMIN با API معمول admin قابل target/delete/disable نیست (`admin_service.py:31-37`).
- unknown/disabled/deleted/group interaction silent-ignore است؛ تلاش ناشناس بدون ذخیره raw ID audit می‌شود.
- ADMIN فقط profileهای join‌شده در `admin_backup_access` را می‌بیند (`repositories.py:101-105`).
- فقط ADMIN می‌تواند recipient باشد؛ SUPERADMIN خودکار artifact نمی‌گیرد (`repositories.py:210-230`).
- در آغاز delivery assignmentها sync می‌شوند و پیش از هر attempt، user role/enabled/deleted و access row دوباره خوانده می‌شود (`delivery.py:60-84`, `102-111`). revoked recipient به SKIPPED می‌رود.
- token inline حاوی DB/Telegram ID نیست و actor-bound است؛ بااین‌حال token پس از resolve مصرف نمی‌شود و تا timeout قابل replay است. service re-check مانع privilege escalation شناخته‌شده می‌شود.

## ۸. Remote Server و SSH

### secret و trust

- private key/passphrase با AES-256-GCM و AADهای جدا ذخیره می‌شوند (`remote_server_service.py:13-14`, `69-70`).
- اولین اتصال عمداً `known_hosts=b""` دارد، fingerprint را capture می‌کند و UI confirmation لازم دارد (`ssh.py:59-74`). این TOFU است؛ کاربر باید fingerprint را out-of-band مقایسه کند.
- اتصال‌های بعدی SHA-256 fingerprint دقیق را در custom client validate می‌کنند و تغییر key را رد می‌کنند (`ssh.py:21-32`, `76-106`).
- connection و SFTP با async context manager بسته می‌شوند.

### انتقال

- remote tree ابتدا rsync با private-key/known_hosts موقت mode 0600، `StrictHostKeyChecking=yes`, `IdentitiesOnly=yes`, `--safe-links`, `--no-specials`, `--no-devices`, `--secluded-args` دارد (`acquisition.py:208-255`).
- هر `PipelineError` از rsync باعث fallback به recursive SFTP می‌شود؛ علت‌هایی غیر از transfer compatibility نیز fallback می‌شوند (`acquisition.py:282-286`).
- material و download tree در `finally` پاک می‌شوند (`acquisition.py:288-290`).
- remote SQLite با Python سیستم remote، Online Backup API و integrity_check به نام تصادفی `/tmp` snapshot می‌شود و remote temp در finally حذف می‌شود (`acquisition.py:156-187`).

نقص‌ها: connect/SFTP timeout صریح ندارد؛ quota/file-count/depth ندارد؛ نام برگشتی SFTP basename-validate نمی‌شود؛ fallback ممکن است خطای امنیتی/ظرفیتی rsync را به مسیر دیگری تبدیل کند.

## ۹. معماری job/worker و stateها

### state machine

Statusها: `QUEUED → RUNNING → DELIVERY_PENDING → SUCCEEDED`؛ failure/cancel/skip terminal هستند (`enums.py:90-97`). Stageها جزئی‌ترند: QUEUED, BACKUP, VERIFY_SOURCE, COMPRESS, ENCRYPT, CHECKSUM, FINALIZE, PENDING_DELIVERY, TELEGRAM_DELIVERY, CLEANUP, COMPLETE (`enums.py:100-111`).

### one-active و schedule collision

- partial unique SQLite index برای هر profile فقط یک status از QUEUED/RUNNING/DELIVERY_PENDING اجازه می‌دهد (`models.py:288-296`; migration `0006:60-66`).
- manual duplicate همان active run را برمی‌گرداند؛ scheduled collision یک SKIPPED record می‌سازد (`jobs.py:60-64`, `98-128`).
- `(profile_id, trigger, scheduled_for)` نیز unique است تا tick تکراری idempotent باشد.

### lease و concurrency

- claim یک `UPDATE ... WHERE id=(subquery) RETURNING` است و owner/expiry را atomically می‌گذارد (`jobs.py:140-172`).
- heartbeat هر lease/3 renew می‌کند؛ loss یا DB exception parent task را cancel می‌کند (`worker.py:399-417`).
- expired QUEUED/RUNNING/DELIVERY_PENDING قابل reclaim است.
- worker task count را با `MAX_CONCURRENT_BACKUPS` و remote transfers را با semaphore جدا محدود می‌کند.

### crash recovery و workspace

- workspace فقط زیر `<WORK_ROOT>/<canonical UUID>` ساخته/حذف می‌شود؛ escape check دارد (`workspace.py:8-57`).
- run مسیر workspace/pending را durable ثبت می‌کند.
- اگر manifest/final قبلاً اتمیک شده باشد، worker ownership/schema/path/size/hash را validate و بدون acquisition دوباره delivery را resume می‌کند (`worker.py:338-397`; `finalize.py:109-139`).
- startup cleanup فقط مسیرهای ثبت‌شده و run-owned را لمس می‌کند (`worker.py:75-106`).

### cancellation

backend `request_cancel` وجود دارد؛ QUEUED را فوری CANCELLED می‌کند و RUNNING فقط flag می‌گذارد (`jobs.py:193-203`). stage boundary آن را می‌بیند (`worker.py:186-193`). UI هیچ مسیر cancel ندارد. نقص stuck-run در H-02 آمده است.

## ۱۰. موتورهای بکاپ

### PostgreSQL

- `psql SHOW server_version_num` با `.pgpass` موقت mode 0600 و `PGSSLMODE` اجرا می‌شود.
- major server استخراج و دقیقاً `/usr/lib/postgresql/<major>/bin/{pg_dump,pg_restore}` انتخاب می‌شود (`acquisition.py:303-346`).
- `pg_dump --format=custom --compress=0` و سپس `pg_restore --list` verify می‌شود (`acquisition.py:347-369`).
- password/CA temp در finally حذف می‌شوند.
- محدودیت: Docker فقط client 15 نصب می‌کند؛ server major دیگر عمداً fail می‌شود. restore واقعی انجام نمی‌شود، فقط archive catalog خوانده می‌شود. password شامل newline در pgpass درست escape نشده است.

### MySQL/MariaDB

- protected option file شامل host/port/user/password/TLS ساخته می‌شود؛ dump با single-transaction, quick, routines, events, triggers, hex-blob و `--databases` است (`acquisition.py:380-433`).
- credential/CA temp در finally پاک می‌شوند.
- verification فقط `size > 0` است؛ parse/restore test ندارد (`acquisition.py:438-440`).
- blocker: انتخاب client با `Path('/usr/bin/mysqldump').exists()` و سپس `dump.name == 'mysqldump'` symlink را resolve نمی‌کند (`acquisition.py:394-407`). در سیستم حاضر `/usr/bin/mysqldump -> mariadb-dump` و `mariadb-dump --help` گزینه `ssl-mode` ندارد؛ اجرای config تولیدی با `unknown variable 'ssl-mode=REQUIRED'` fail شد. این برای DISABLED هم همان option را می‌سازد.

### MSSQL

- اتصال sync `python-tds` به master؛ identifier/literal با bracket/quote escaping ساخته می‌شود، بنابراین SQL injection شناخته‌شده از db/path دیده نشد (`acquisition.py:443-480`).
- `BACKUP DATABASE ... WITH INIT, COPY_ONLY, CHECKSUM, COMPRESSION`؛ اگر compression error بدهد بدون COMPRESSION retry؛ سپس `RESTORE VERIFYONLY ... WITH CHECKSUM` (`acquisition.py:451-505`).
- artifact از local visible path copy یا از pinned remote server با SFTP گرفته می‌شود (`acquisition.py:508-533`).
- database password فقط argument API است، نه shell/argv.
- محدودیت‌ها: operation در thread است؛ cancellation thread/SQL را متوقف نمی‌کند. backup file اصلی روی SQL Server/host پس از copy حذف نمی‌شود. path باید در عمل نام فایل کامل و برای Docker mount متناظر باشد، ولی Compose mountی فراهم نمی‌کند. TLS به `pyOpenSSL` نیاز دارد و dependency غایب است؛ `PREFERRED` بدون CA عملاً TLS را advertise نمی‌کند.

### فایل محلی

- path هنگام ساخت و دوباره هنگام acquisition در allow/deny list validate، سپس `resolve(strict=True)` و type check می‌شود (`acquisition.py:35-46`).
- symlink source و symlink/special داخل tree رد می‌شوند؛ tar PAX با namespace `sources/<index>` ساخته می‌شود (`acquisition.py:58-91`).
- محدودیت: بین `is_symlink/stat` و `archive.add` race وجود دارد؛ تغییر هم‌زمان tree می‌تواند check را دور بزند یا snapshot ناسازگار بدهد. quota/timeout ندارد. Compose پیش‌فرض source mount ندارد.

### فایل ریموت

- pinned SSH؛ برای هر path rsync و سپس SFTP fallback؛ symlink/special reject؛ در پایان local tree tar و tar members verify می‌شوند.
- محدودیت: SFTP filename traversal، transfer timeout ناقص، recursion/file/byte quota ندارد و remote server باید trusted و `/usr/bin/python3` برای SQLite داشته باشد.

### SQLite

- local: اگر پسوند candidate و regular file باشد، `sqlite3.Connection.backup` از URI read-only و `PRAGMA integrity_check` اجرا و snapshot داخل tar گذاشته می‌شود (`acquisition.py:49-71`).
- remote: script base64 ثابت با pathها به‌صورت shell-quoted اجرا و همان backup/integrity انجام می‌شود.
- candidate فقط از پسوند زمان ساخت تعیین می‌شود؛ content sniffing نیست. remote directory با پسوند `.db` نیز اشتباهاً snapshot فرض می‌شود.

## ۱۱. pipeline پردازش

```text
profile/run
  → WORK_ROOT/<uuid>/raw/source.<native>
  → verify source
  → staging/source.<native>.<compression>
  → final/artifact... [.age]
  → SHA-256 + نام timezone-aware
  → atomic os.replace به PENDING_DELIVERY_ROOT/<profile>/<stamp_uuid8>/
  → manifest.json (fsync file + directory)
  → backup_artifacts + DELIVERY_PENDING
  → حذف workspace
  → per-recipient Telegram delivery
  → حذف pending directory فقط در success/all-skipped
```

workspace چهار بخش raw/staging/final/temp دارد، هرچند `workspace.temp` مستقیماً استفاده نمی‌شود. compression اگر MSSQL native compression موفق باشد به NONE override می‌شود (`worker.py:233-240`). checksum بعد از انتقال اتمیک دوباره روی artifact نهایی محاسبه می‌شود. manifest secret ندارد و metadata engine را نگه می‌دارد. source/raw و intermediateها پس از finalize پاک می‌شوند؛ failure عادی قبل از pending نیز workspace را پاک می‌کند.

## ۱۲. compression و encryption

### Compression

mapping preset بسته در `transforms.py:9-16` است. commandها absolute و بدون shell هستند (`transforms.py:50-83`) و هر archive با tool متناظر test می‌شود (`transforms.py:84-94`).

- Zstd: level 1/6/15/19 و thread count.
- Gzip: 1/6/9/9.
- XZ: 1/4/7/9، thread و memlimit.
- Bzip2: 1/5/8/9.
- ZIP: 1/5/8/9.
- 7z: 1/5/7/9، thread و dictionary مشتق از memory.
- NONE: copy ساده.

memory limit فقط XZ و اندازه dictionary 7z را enforce می‌کند؛ Zstd/Gzip/Bzip2/ZIP hard memory cap ندارند. thread setting نیز برای تک‌رشته‌ای‌ها اثری ندارد. Docker container limit آخرین لایه دفاعی است.

### Age

- public-key: `age --recipient ... --output`; recipientها regex و de-duplicate می‌شوند.
- passphrase: secret نه در argv و نه env، بلکه از PTY بدون echo دو بار فرستاده می‌شود (`process.py:22-61`).
- خروجی nonempty و header `age-encryption.org/` verify می‌شود (`transforms.py:97-127`).
- passphrase در DB AES-GCM encrypted است؛ public recipient secret نیست.
- Age passphrase با binary واقعی smoke-test شد؛ public-key فقط command-level mock شده است.

## ۱۳. Telegram delivery

- قبل از ارسال، file existence/size/SHA-256 و limit 2GB چک می‌شود (`delivery.py:46-59`).
- delivery record برای assignmentهای جدید ساخته می‌شود؛ revokedهای ناموفق SKIPPED می‌شوند.
- پیش از **هر attempt** authorization دوباره خوانده می‌شود.
- هر گیرنده attempts/error/status/message ID/file ID مستقل دارد؛ موفق‌ها در restart دوباره ارسال نمی‌شوند.
- در همان process، media اولین upload به گیرنده‌های بعدی reuse می‌شود (`delivery.py:87`, `115-138`). پس از restart، `telegram_file_id` ذخیره‌شده استفاده نمی‌شود و فایل محلی دوباره upload می‌شود.
- exception upload با نام نوع خطا durable می‌شود؛ هر claim حداکثر configured retries با exponential sleep دارد.
- failure یک گیرنده کل artifact را `DELIVERY_PENDING` نگه می‌دارد؛ موفقیت‌های قبلی حفظ می‌شوند.
- پس از همه success/skipped، status SUCCEEDED، spool حذف و run COMPLETE می‌شود (`delivery.py:148-159`).

`LargeFileUploadService` در `telegram/transport.py:82-171` pipeline دیگری برای hash/FloodWait/cancel دارد، اما Worker از آن استفاده نمی‌کند. آزمون‌های `test_upload.py` عمدتاً همین مسیر مرده را می‌سنجند، نه `DeliveryService`. progress callback واقعی فقط timestamp محلی را update می‌کند و هیچ message/log/DB progress نمی‌فرستد (`delivery.py:92-99`).

## ۱۴. دیسک و backpressure

`disk_has_capacity` free space زیر pending root و sum `final_size_bytes` برای هر run با pending path را مقایسه می‌کند (`worker.py:67-72`; `jobs.py:205-211`). وقتی false باشد:

- UI enqueue دستی را رد می‌کند.
- scheduler tick نمی‌شود.
- Worker QUEUED/RUNNING جدید claim نمی‌کند.
- DELIVERY_PENDING همچنان claim می‌شود تا امکان خالی‌شدن spool باشد.

مشکلات:

- پیش از شروع، اندازه خروجی آینده reserve/estimate نمی‌شود؛ source/raw/staging/final ممکن است دیسک را پر کنند.
- filesystem scan وجود ندارد؛ orphanها یا اختلاف DB/filesystem دیده نمی‌شوند.
- free-space حین transfer/transform دوباره check نمی‌شود.
- file-count/depth/individual source limit نیست.
- failed pending artifact ممکن است برای همیشه هم فضا بگیرد و هم در `pending_bytes` بماند.
- خطای disk در subprocess/file operation run را fail می‌کند؛ retry acquisition خودکار وجود ندارد.

## ۱۵. ممیزی error handling و resource lifecycle

### subprocess و PTY

- commandها absolute و `create_subprocess_exec` هستند؛ shell invocation محلی وجود ندارد (`process.py:64-113`).
- subprocessها session/process group جدا دارند؛ timeout/cancel ابتدا TERM و بعد KILL می‌دهد و `wait` می‌کند.
- race: `os.killpg` در مسیر عمومی با `suppress(ProcessLookupError)` محافظت نشده؛ اگر process همان لحظه تمام شود، `wait` ممکن است اجرا نشود و error اصلی عوض شود (`process.py:97-107`).
- Age path kill را suppress و wait می‌کند؛ master/slave PTY در finally بسته می‌شوند. stderr PTY drain نمی‌شود و خروجی بسیار زیاد تا timeout می‌تواند block کند.

### task/lock/semaphore

- heartbeat task همیشه cancel/await می‌شود (`worker.py:160-163`).
- worker child taskها در shutdown cancel و gather می‌شوند.
- FSM/callback/rate limiter هر کدام lock دارند؛ deadlock pattern دیده نشد.
- expired FSM/callback/user buckets cleanup سراسری دوره‌ای ندارند؛ cleanup lazy و memory-only است. restart همه wizard/callbackها را از بین می‌برد که قابل قبول ولی UX-impacting است.

### DB connection

- bot/worker/CLI/migration از context manager و dispose استفاده می‌کنند.
- repositoryها session مالک نیستند و درست آن را نمی‌بندند.
- SQLite WAL و busy timeout برای bot/worker همزمان فعال است.

### SSH/SFTP

- async contextها connection و SFTP را می‌بندند؛ material temp در finally پاک می‌شود.
- connect و عملیات SFTP end-to-end deadline ندارند؛ ممکن است task/lease برای مدت نامحدود زنده بماند.

### فایل/temp/cleanup

- DB credential/CA tempها mode 0600 و finally cleanup دارند.
- SSH key material زیر workspace 0700/0600 و finally cleanup دارد.
- workspace و pending deletion ownership check دارند؛ حذف broad root دیده نشد.
- crash وسط MSSQL/local `to_thread` thread را متوقف نمی‌کند.
- cleanup failed pending ناقص است؛ جزئیات در H-04.

## ۱۶. بررسی امنیتی و Bug/Risk Register

### Critical — ۰

هیچ مسیر اثبات‌شده‌ای برای compromise کامل، اجرای فرمان از راه دور یا حذف گسترده داده با severity بحرانی یافت نشد.

### High — ۱۱

| ID | یافته و evidence | اثر | اصلاح پیشنهادی |
|---|---|---|---|
| H-01 | **MySQL/MariaDB در toolchain فعلی خراب است.** detection در `acquisition.py:394-407` فقط نام symlink را می‌بیند. `/usr/bin/mysqldump` فعلی symlink به `mariadb-dump` است و config تولیدی `ssl-mode` با `unknown variable` رد شد. | همه backupهای این engine پیش از اتصال fail می‌شوند. | binary flavor/version را از `--version` یا resolved path تشخیص دهید؛ option matrix واقعی MySQL/MariaDB و integration container test اضافه کنید. |
| H-02 | **cancel می‌تواند run را دائماً active نگه دارد.** claim فقط `cancel_requested=false` را می‌گیرد (`jobs.py:147-150`)، ولی cancel RUNNING فقط flag می‌زند (`jobs.py:193-203`). Delivery flag را نمی‌خواند. | crash پس از flag یا cancel در DELIVERY_PENDING باعث run غیرقابل reclaim، blockage profile و spool ماندگار می‌شود. | claim/recovery مخصوص cancelled بسازید؛ worker در هر loop/delivery cancel را finalize کند؛ UI cancel و آزمون crash اضافه شود. |
| H-03 | **retry تحویل بی‌نهایت و تقریباً فوری بین claimهاست.** `delivery.py:100-147` فقط batch داخلی را محدود می‌کند؛ Worker هر poll همان DELIVERY_PENDING را دوباره claim می‌کند (`worker.py:474-481`). همه Exceptionها retryable فرض می‌شوند. | request storm، FloodWait/ban، مصرف CPU/network و spool همیشگی برای خطای دائمی. | `next_attempt_at`, سقف کل attempts، jitter/backoff durable و تفکیک permanent/transient/FloodWait اضافه کنید. |
| H-04 | **artifact failed پاک نمی‌شود.** `_fail` برای checksum/missing/upload-limit run را FAILED می‌کند ولی pending path را حذف/قرنطینه نمی‌کند (`worker.py:419-445`)؛ recovery pending را فقط برای SUCCEEDED پاک می‌کند (`worker.py:95-105`). | نشت دیسک و backpressure دائمی بدون remediation UI. | policy صریح quarantine/retention و command/UI امن purge؛ برای terminal failure path را reconcile و accounting را اصلاح کنید. |
| H-05 | **SFTP entry name می‌تواند از local target خارج شود.** `entry.filename` فقط برای `.`/`..` چک و سپس `local / filename` می‌شود (`acquisition.py:124-136`)؛ basename، slash، absolute و `..` parts رد نمی‌شوند. | سرور ریموت خراب/مخرب می‌تواند داخل workspace siblingها بنویسد و pipeline را دستکاری/DoS کند. | filename را strict basename validate، resolved parent را enforce و آزمون malicious SFTP اضافه کنید. |
| H-06 | **quota source وجود ندارد و backpressure فقط پیش از run است.** `disk_has_capacity` فقط pending ثبت‌شده را می‌بیند (`worker.py:67-72`)، download/tar/compress هیچ byte/file/depth limit ندارد. | remote/local source بزرگ یا compression expansion می‌تواند volume را کامل پر و کل bot/DB را مختل کند. | reservation، max source/artifact/file count/depth، streaming free-space checks و separate filesystem quota اضافه کنید. |
| H-07 | **`MAX_BACKUP_RUNTIME_SECONDS` timeout کل run نیست.** فقط بعضی processها از آن استفاده می‌کنند؛ SSH connect، SFTP recursion، local tar/SQLite و کل runner داخل timeout نیست (`acquisition.py:109-141`, `259-290`; `worker.py:195-337`). | job و lease می‌تواند نامحدود بماند؛ shutdown و ظرفیت worker گیر می‌کند. | کل acquisition/build را deadline کنید؛ connect/read timeouts و cancel-aware chunk loop اضافه کنید. |
| H-08 | **عملیات thread قابل cancel نیست.** MSSQL و local acquisition با `asyncio.to_thread` اجرا می‌شوند (`acquisition.py:511`; `worker.py:226-228`). cancellation/lease loss await را لغو می‌کند، نه thread/SQL را. | backup MSSQL ممکن است پس از واگذاری lease ادامه و با run reclaim‌شده روی `WITH INIT` overlap کند؛ cleanup نیز با writer race می‌کند. | MSSQL را process قابل kill یا driver cancel handle اجرا کنید؛ lease fencing token و cooperative cancellation؛ عملیات فایل chunk/cancel-aware. |
| H-09 | **secretها ممکن است نامحدود در FSM حافظه بمانند.** password در `item.data` ذخیره و تا confirmation نگه داشته می‌شود (`dispatcher.py:481-486`, `892-924`)؛ Age passphrase نیز داخل object است (`630-640`). expiry فقط هنگام `get` همان user حذف می‌شود (`fsm.py:84-89`). | wizard رهاشده secret plaintext را تا restart/interaction بعدی در heap نگه می‌دارد؛ crash dump/debug exposure بیشتر می‌شود. | secret را بلافاصله encrypt/opaque handle کنید؛ background expiry، `finally` zero/drop و عدم نگهداری در generic dict. |
| H-10 | **TOCTOU در tree محلی.** symlink/type ابتدا check و سپس path دوباره با `archive.add` باز می‌شود (`acquisition.py:76-91`). | مهاجم محلی دارای write روی source tree می‌تواند بین check/use symlink یا فایل ویژه جایگزین و allowlist را دور بزند/فایل ناخواسته بخواند. | fd-relative traversal با `openat/O_NOFOLLOW`, inode recheck یا snapshot/read-only isolated mount استفاده کنید. |
| H-11 | **TLS امن MSSQL در dependency فعلی قابل استفاده نیست.** code برای REQUIRED/VERIFY یک cafile به `pytds` می‌دهد (`acquisition.py:462-480`)، ولی `pyOpenSSL` در `pyproject.toml:11-23`/`uv.lock` نیست و runtime `OPENSSL_AVAILABLE=False` گزارش کرد. `PREFERRED` بدون cafile `ENCRYPT_NOT_SUP` است. | secure MSSQL profile fail می‌شود؛ Preferred برخلاف انتظار plaintext است. | dependency TLS رسمی python-tds را lock کنید، semantics هر TLSMode را دقیق map و با SQL Server TLS واقعی test کنید؛ تا آن زمان modeهای امن را unavailable اعلام کنید. |

### Medium — ۱۲

| ID | یافته و evidence | اثر | اصلاح پیشنهادی |
|---|---|---|---|
| M-01 | migrationها اکثر enum CHECKها را ندارند و `alembic check` ۱۵ type drift گزارش کرد؛ مدل و schema یکسان نیستند. | داده نامعتبر/LookupError و migration drift. | migration جدید برای constraintها و comparator policy؛ `alembic check` در CI. |
| M-02 | image فقط PostgreSQL client 15 دارد (`Dockerfile:13`)، ولی code exact server-major می‌خواهد (`acquisition.py:342-346`). | PostgreSQL غیر ۱۵ در image پیش‌فرض fail می‌شود. | client majorهای support‌شده یا image matrix/documented compatibility و integration test. |
| M-03 | MySQL verification فقط nonempty است (`acquisition.py:438-440`). | dump ناقص ولی غیرخالی ممکن است موفق ثبت شود. | parser sanity + disposable restore test اختیاری/دوره‌ای. |
| M-04 | MSSQL `.bak` اصلی پس از copy/SFTP حذف نمی‌شود (`acquisition.py:508-533`). | مصرف دیسک server و باقی‌ماندن backup حساس؛ `WITH INIT` overwrite risk. | unique remote filename، cleanup فقط پس از verify و retention policy/finally امن. |
| M-05 | source/credential/TLS/schedule/compression/encryption profile قابل edit نیست (`keyboards.py:90-97`). | اصلاح config نیازمند archive/recreate؛ احتمال downtime/اشتباه assignment. | wizard edit نسخه‌دار و audit diff بدون secret. |
| M-06 | run list/history/status/cancel UI نیست و پیام no-active unconditional است (`dispatcher.py:1083-1086`). | operator دید و کنترل واقعی ندارد؛ backend cancel عملاً inaccessible. | repository/query + status cards + bound cancel callback. |
| M-07 | compression resource settings فقط بخشی از ابزارها را محدود می‌کند (`transforms.py:49-80`) و DB range ندارد (`models.py:246-254`). | خارج Docker یا با مقدار DB خراب، CPU/RAM exhaustion/command failure. | validators/DB CHECK و per-tool hard limit/cgroup requirement. |
| M-08 | media reuse فقط حافظه‌ای است؛ `telegram_file_id` ذخیره ولی در restart مصرف نمی‌شود (`delivery.py:135-138`). progress setting هم no-op و upload helper آزموده‌شده dead path است. | restart باعث upload مجدد بزرگ؛ telemetry گمراه‌کننده و test confidence کاذب. | یک upload abstraction؛ durable InputDocument reuse با access-hash مناسب؛ progress persist/report؛ dead code حذف. |
| M-09 | process termination race، `killpg` بدون suppress دارد (`process.py:97-107`). | در exit هم‌زمان ممکن است zombie/replaced error یا cleanup ناقص رخ دهد. | `suppress(ProcessLookupError)`, همیشه `await wait`, test race. |
| M-10 | master key در development optional است، اما Worker همیشه و UI حتی profile فایل بدون encryption در confirmation آن را الزام می‌کند (`worker.py:123-126`; `dispatcher.py:892-895`). | bot می‌تواند ظاهراً بالا بیاید ولی wizard در انتها fail؛ worker restart loop. | startup capability validation؛ key را برای deployment الزامی یا unencrypted profile را مستقل کنید. |
| M-11 | Compose هیچ local source/MSSQL artifact mount ندارد (`compose.yml:25-28`). | قابلیت local در deployment آماده‌شده قابل test نیست. | override نمونه اجرایی/validation mount و read-only source volume. |
| M-12 | audit completion/failure/delivery/cancel lifecycle کامل ثبت نمی‌شود؛ worker عمدتاً فقط log lease-loss دارد (`worker.py:129-163`, `419-445`). | forensic/operational trace ناقص است. | eventهای structured بدون secret برای transitionها و retention policy. |

### Low — ۷

| ID | یافته و evidence | اثر | اصلاح پیشنهادی |
|---|---|---|---|
| L-01 | `APP_NAME`, `APP_TIMEZONE`, `DISPLAY_TIMEZONE` استفاده runtime ندارند؛ `TEMP_DIR` برای `NamedTemporaryFile` مصرف نمی‌شود. | config گمراه‌کننده. | حذف یا wire واقعی؛ tempها را زیر root محدود قرار دهید. |
| L-02 | healthcheck فقط DB/writable dirs است و head بودن revision، Telegram، worker session و liveness role را نمی‌سنجد (`cli/main.py:113-128`). | container ممکن است healthy ولی delivery خراب باشد. | readiness/liveness جدا و dependency state. |
| L-03 | DB/WAL/SHM runtime mode 0644 هستند، هرچند parent 0700 است. | defense-in-depth ضعیف‌تر در copy/misconfigured parent. | umask/explicit 0600 و تست permission. |
| L-04 | callback token تا expiry replayable است و callback query answer نمی‌شود (`callbacks.py:33-43`; `dispatcher.py:195-220`). | تکرار UX/operation idempotent و spinner Telegram. | token one-shot یا nonce state؛ `event.answer`. |
| L-05 | rate limiter bucketها، expired FSM/callback و audit table cleanup دوره‌ای/retention ندارند. | رشد آهسته حافظه/DB در عمر طولانی. | periodic prune و retention limits. |
| L-06 | remote SQLite candidacy فقط پسوند است و برخلاف local، directory قبل از snapshot تشخیص داده نمی‌شود (`acquisition.py:277-281`). | directory با نام `.db` بی‌دلیل fail می‌شود. | remote lstat regular-file و header/content check. |
| L-07 | escape فایل `.pgpass` newline را پوشش نمی‌دهد (`acquisition.py:306-310`). | password دارای newline authentication را خراب می‌کند/entry اضافه می‌سازد؛ temp private است. | newline/CR را reject یا مطابق libpq کامل escape کنید. |

### جمع‌بندی تهدیدهای درخواستی

- Command injection: subprocessهای محلی shell ندارند؛ SQL quoting MSSQL درست است؛ remote Python command pathها `shlex.quote` دارند. exploit مستقیم پیدا نشد.
- SQL injection: ORM bindها و MSSQL escaping مانع مورد شناخته‌شده‌اند.
- Path traversal/symlink: workspace/pending checks خوب‌اند؛ SFTP basename و local TOCTOU یافته واقعی هستند.
- Arbitrary delete: deleteها به UUID-owned workspace و pending شکل‌دار محدودند؛ broad deletion دیده نشد.
- SSH MITM: اولین تماس TOFU و نیازمند تأیید انسانی است؛ بعد pin دقیق. اگر کاربر fingerprint را مستقل verify نکند، MITM اولین اتصال ممکن است.
- Temp files: credentialها 0600؛ remote temp نام UUID دارد؛ cleanup در finally. crash residual workspace با directory 0700 ممکن است.
- Weak secret storage: AES-GCM صحیح؛ مشکل اصلی lifecycle plaintext FSM و حفاظت master key خارج برنامه است.
- Telegram auth bypass: در تست و بازبینی مسیر شناخته‌شده‌ای پیدا نشد؛ UI و service و delivery چندلایه re-check دارند.
- Resource exhaustion: quota/deadline/retry findings H-03/H-06/H-07 واقعی‌اند.
- Docker privilege: hardening خوب؛ non-root/no caps/no socket/read-only. named volumeها و tmpfs تنها write surfaceهای عمدی‌اند.

## ۱۷. ماتریس آزمون

| قابلیت | محل آزمون | نوع | پوشش/شکاف |
|---|---|---|---|
| SecretCipher/redaction | `unit/test_backup_config.py`, `security/test_redaction.py` | واقعی crypto، unit | tamper/AAD هست؛ key rotation/large/zeroization نیست. |
| config/proxy/FSM | `unit/test_settings_fsm.py` | unit/mock clock | production required و transition؛ memory prune/secret retention نیست. |
| schedule/compression config/MSSQL validation | `unit/test_backup_config.py` | unit | closed values؛ DB CHECK drift نیست. |
| host-key mismatch | `unit/test_backup_config.py` | fake key | فقط validator؛ SSH server واقعی نیست. |
| Docker hardening | دو `test_docker_config.py`/`test_pipeline_safety.py` | text assertion | build/run/health/permissions واقعی نیست. |
| compression command matrix | `unit/test_pipeline_safety.py` | mocked process | argv و presetها. |
| compression binaries | `integration/test_pipeline_tools.py` | real local smoke | همه فرمت‌ها در این اجرا؛ round-trip content مستقل نیست ولی tool `-t` اجرا می‌شود. |
| Age passphrase | همان فایل | real binary smoke | public-key واقعی/decrypt round-trip نیست. |
| workspace/finalize/backpressure | `unit/test_pipeline_safety.py` | filesystem واقعی | isolated cleanup/atomic same-FS؛ crash windows و disk full نیست. |
| generic upload helper | `unit/test_upload.py` | fake Telegram | این helper در Worker استفاده نمی‌شود. |
| allowlist/RBAC/silent/i18n/callback | `security/test_dispatcher.py` | fake events/client + real SQLite | spoof و secret message deletion؛ wizard کامل/Telegram RPC واقعی نیست. |
| bilingual menus/access | `integration/test_admin_localization_access.py` | DB واقعی + fake client | catalog key equality و restart DB؛ translation quality انسانی نیست. |
| admin/backup services | `integration/test_services.py` | SQLite via `create_all` | CRUD/RBAC؛ migrated schema نیست. |
| configured profile/remote CRUD | `integration/test_backup_configuration.py` | fake SSH + SQLite | secrets/config persistence؛ SSH واقعی و engines نیست. |
| migration fresh/legacy | `integration/test_migration.py` | real Alembic temp SQLite | upgrade head و USER→ADMIN؛ downgrade و schema-model diff نیست. |
| enqueue/index/scheduler/lease | `integration/test_pipeline_jobs.py` | SQLite | one-active و reclaim؛ multi-process race/cancel crash نیست. |
| partial delivery/re-auth | `integration/test_delivery_pipeline.py` | fake transport | restart-like retry؛ infinite retry/file-id/FloodWait واقعی نیست. |
| worker end-to-end local | `integration/test_worker_end_to_end.py` | real file/SQLite + fake Telegram | تنها file، NONE compression/encryption. |

شکاف‌های مهم: Telegram sandbox واقعی، SSH/SFTP/rsync server واقعی، PostgreSQL/MySQL/MariaDB/MSSQL containerهای واقعی با TLS و restore، Docker image build/run، cancel در هر stage، crash windowهای finalize/DB commit، disk full، symlink race، malicious SFTP names، concurrent worker processes، FloodWait/permanent RPC و key rotation.

## ۱۸. نتیجه validationهای non-destructive

| فرمان | نتیجه |
|---|---|
| `ruff check .` | ✅ exit 0 — All checks passed |
| `ruff format --check .` | ✅ exit 0 — 66 files already formatted |
| `mypy src` | ✅ exit 0 — no issues in 42 source files |
| `pytest` | ✅ exit 0 — 77 passed in 6.25s؛ Python 3.14.6 |
| `bandit -r src` | ✅ exit 0 — 0 issue؛ یک B108 عمداً nosec روی remote UUID temp |
| `pip-audit` | ✅ exit 0 — no known vulnerabilities؛ package محلی `backup-telebot` قابل audit روی PyPI نبود |
| `alembic history` | ✅ exit 0 — زنجیره خطی 0001..0006 |
| `alembic heads` | ✅ exit 0 — `0006 (head)` |
| `docker compose config --quiet` | ✅ exit 0 |
| fresh `alembic upgrade head` روی SQLite temp | ✅ revision 0006، ۱۲ جدول domain + alembic table، FK check clean |
| `alembic check` اضافی روی DB temp | ❌ type drift برای ۱۵ enum؛ یافته M-01 |

هیچ‌کدام به runtime DB واقعی write نکردند. ابزارها از `.venv` موجود استفاده کردند. `pytest` testهای ابزار را skip نکرد: ۸ مورد compression/Age integration اجرا شدند.

## ۱۹. کد ناتمام، dead و duplicated

- TODO/FIXME/`NotImplemented`/placeholder واقعی در source پیدا نشد.
- `pass`های `Base`, exception classها و `cursor.nextset()` loopها عمدی‌اند، نه stub (`models.py:46-47`; `repositories.py:27-32`; `acquisition.py:489-496`).
- `application/services.py` فقط compatibility re-export است و dead محسوب نمی‌شود.
- `LargeFileUploadService` و `TelegramTransport.deliver_backup` مسیر upload قدیمی/موازی‌اند؛ Worker مستقیماً Telethon client را به `DeliveryService` می‌دهد. testهای upload روی مسیر production نیستند.
- `Workspace.temp` ساخته ولی استفاده نمی‌شود.
- envهای `APP_NAME`, `APP_TIMEZONE`, `DISPLAY_TIMEZONE` فعلاً dead configuration و `TEMP_DIR` تقریباً decorative است.

## Feature Status Matrix

| قابلیت | وضعیت |
|---|---:|
| SQLite control DB + migration chain | ✅ Implemented and verified |
| Bootstrap SUPERADMIN و ADMIN RBAC | ✅ Implemented and verified |
| silent-ignore و service-level authorization | ✅ Implemented and verified |
| admin/profile assignment و recipient re-check | ✅ Implemented and verified |
| UI انگلیسی/فارسی | ✅ Implemented and verified |
| Remote Server CRUD، encrypted key و pinning | 🧪 Implemented but only mock/test validated |
| PostgreSQL dump/list verification | 🧪 Implemented but only mock/code validated؛ client 15 محدود |
| MySQL/MariaDB acquisition | ❌ Missing as a working runtime path (implementation exists but current client option is broken) |
| MSSQL backup/VERIFYONLY/artifact retrieval | ⚠️ Partial/risky |
| Local files tar | ✅ Implemented and verified locally؛ ⚠️ TOCTOU/quota |
| Remote files rsync/SFTP | 🧪 Implemented but only mock/code validated؛ ⚠️ traversal/timeout |
| Local SQLite consistent snapshot | ✅ Implemented and verified indirectly |
| Remote SQLite snapshot | 🧪 Implemented but only code validated |
| ۶ compression format + NONE | ✅ Implemented and verified with local binaries |
| Age passphrase | ✅ Implemented and smoke verified |
| Age public-key | 🧪 Implemented but only mock validated |
| Scheduler همه kindها/timezone cursor | ✅ Implemented and verified |
| one-active run و lease reclaim | ✅ Implemented and verified in SQLite |
| crash recovery بعد از finalize | 🧪 Implemented but only simulated/structural validation |
| cancellation کامل | ⚠️ Partial/risky |
| multi-recipient partial delivery | 🧪 Implemented but only fake Telegram validated |
| durable retry با backoff نهایی | ❌ Missing |
| durable Telegram file reuse | ❌ Missing |
| disk backpressure | ⚠️ Partial/risky |
| run history/status/cancel UI | ❌ Missing |
| edit کامل profile | ❌ Missing |
| Docker security baseline | ✅ Implemented and config verified |
| integration واقعی DB/SSH/Telegram | ❌ Missing |

## Remaining Work

### Must fix before real testing

1. H-01: اصلاح تشخیص MySQL/MariaDB client/options؛ سپس test container واقعی برای هر دو flavor.
2. H-11: افزودن dependency TLS MSSQL و mapping/test واقعی همه TLSModeها.
3. ساخت harness integration برای PostgreSQL 15، MariaDB/MySQL، MSSQL، SSH/SFTP/rsync و Telegram test account.
4. تعیین source mountهای read-only و MSSQL artifact path در Compose override آزمایشی.
5. startup validation واضح برای master key/Telegram credential/tool availability.

### Must fix before production

1. H-02/H-03/H-04: state machine cancellation، durable bounded retry و retention/cleanup failed spool.
2. H-05/H-10: بستن SFTP traversal و local symlink TOCTOU.
3. H-06/H-07/H-08: quota/deadline/cancel/fencing واقعی.
4. migration جدید برای enum/invariant constraintها و CI `alembic check`.
5. تست crash/finalize transaction windows و concurrent multi-worker.
6. secret lifecycle FSM، master-key rotation/backup و permission policy.
7. observability: run/delivery audit، metrics، alert برای pending/failed/disk/lease.

### Recommended improvements

- UI تاریخچه، stage، error safe، cancel/retry/purge و edit کامل profile.
- یکپارچه‌سازی upload abstraction و حذف مسیر dead؛ durable media reuse/progress.
- PostgreSQL compatibility matrix یا client imageهای versioned.
- restore-validation دوره‌ای برای DBها، نه فقط archive/nonempty check.
- health/readiness دقیق و بررسی revision=head/tool versions/Telegram.
- retention برای audit/run metadata و cleanup دوره‌ای stateهای حافظه‌ای.

### Optional future work

- password/key authهای بیشتر SSH تنها در صورت نیاز و با threat model جدید.
- split/upload strategy برای artifact بزرگ‌تر از limit Telegram.
- مقصد تحویل غیرتلگرامی، object storage و immutability/retention.
- schedule policy برای DST/missed runs و calendar day 29-31.
- key rotation versioning و external KMS/secret manager.

## Next Development Plan

1. یک CI baseline ثابت بسازید: همه commandهای پاس‌شده فعلی + `alembic check` + Docker image build.
2. H-01 را با detection/version matrix اصلاح و MariaDB/MySQL TLS integration را سبز کنید.
3. MSSQL TLS dependency و semantics را اصلاح؛ backup/VERIFYONLY/copy/cleanup را روی SQL Server container تست کنید.
4. test stack واقعی PostgreSQL 15، SSH server و rsync/SFTP malicious cases اضافه کنید.
5. run state machine را رسمی کنید: transition table، cancel terminalizer، fencing token و crash tests.
6. delivery queue را با `next_attempt_at`, max attempts، error classification، FloodWait و operator retry/purge کامل کنید.
7. spool reconciler و quota/reservation/periodic disk checks را پیاده کنید.
8. filesystem traversal را fd-safe و SFTP name validation را strict کنید.
9. end-to-end deadline و cooperative cancellation را به همه engineها اضافه کنید؛ MSSQL را از thread غیرقابل‌کنترل خارج کنید.
10. migration invariantها را اضافه و model/schema را همگرا کنید.
11. UI run history/status/cancel و profile reconfiguration را بسازید.
12. secret lifecycle، health/metrics/audit و deployment runbook/restore drill را تکمیل کنید.
13. پس از عبور از test stack واقعی، staging soak test با network loss، disk pressure، worker kill و Telegram rate limiting اجرا کنید.
14. تنها پس از restore موفق و chaos/recovery review، production readiness review جدا انجام دهید.

## حکم نهایی

- آمادگی برای integration واقعی: **خیر**؛ ابتدا blockerهای MySQL و MSSQL TLS و test harness واقعی لازم‌اند.
- آمادگی برای production: **خیر**؛ cancellation/retry/cleanup/quota/path safety نیز باید اصلاح شوند.
- شمار یافته‌ها: **Critical: 0، High: 11، Medium: 12، Low: 7**.
- مهم‌ترین مورد باقی‌مانده: **نبود integration واقعی برای engineها/SSH/Telegram، همراه با خراب بودن قطعی مسیر MySQL/MariaDB در toolchain فعلی**.
