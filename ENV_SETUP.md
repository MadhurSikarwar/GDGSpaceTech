# OrbitWatch settings and secrets (`.env`)

Everything OrbitWatch needs to know about this computer, its databases and the outside services lives in one
file: **`.env`** in the project folder. It is listed in `.gitignore` and never pushed to GitHub. `.env.example` is
the template (no real values; `start-website.bat` copies it to `.env` on a fresh computer).

This guide says what every setting is for, whether you must set it, and where its value comes from. It contains
**no real passwords**: the values of this installation are in `pass.txt` (personal, also git-ignored).

Format: one `NAME=value` per line, no quotes, no spaces around `=`. Lines starting with `#` are comments.

---

## 1. Needed before the first start

| Setting | What it is | Where it comes from |
|---|---|---|
| `MYSQL_ROOT_PASSWORD` | Password of MySQL's `root` account. Used only to create the database and its accounts and to apply updates (`ow setup`, `ow prepare`, `ow mysql-setup`); the website itself never logs in as root. | The password you chose when installing MySQL. `start-website.bat` asks for it once and saves it here. |
| `MYSQL_ROOT_USER` | The administrator account used for that. Default `root`. | Only set it if your MySQL administrator is not called `root`. |

Everything else in this section of a working `.env` is created automatically.

## 2. Created by the first-time setup (do not invent them)

`ow setup` / `ow mysql-setup` and the first push notification write these. If you set one before the setup, that
value is used instead of a random one (this demo installation uses one simple password for all of them, see
`pass.txt`). If you change one later, run `ow mysql-setup` (MySQL) so the database account matches again.

| Setting | Account it belongs to | Used by |
|---|---|---|
| `MYSQL_AUTH_PASSWORD` | MySQL `ow_auth` | logins, registration, password reset, rate limits, push devices |
| `MYSQL_VIEWER_PASSWORD` | MySQL `ow_viewer` | visitors and Public Viewers |
| `MYSQL_ANALYST_PASSWORD` | MySQL `ow_analyst` | Analysts |
| `MYSQL_ADMIN_PASSWORD` | MySQL `ow_admin` | Administrators |
| `MYSQL_JOBS_PASSWORD` | MySQL `ow_jobs` | the scheduler (ingest, screening, e-mail, push) |
| `MYSQL_BACKUP_PASSWORD` | MySQL `ow_backup` | `ow backup`, restores |
| `MONGO_ROOT_PASSWORD` | MongoDB `ow_root` | cluster administration, `ow mongo-start/status`, backups |
| `MONGO_JOBS_PASSWORD` | MongoDB `ow_jobs` | writing orbit history |
| `MONGO_ANALYST_PASSWORD` | MongoDB `ow_analyst` | reading orbit history on the website |
| `FLASK_SECRET_KEY` | (not an account) | signs login sessions, trusted-device cookies and login challenges. Changing it logs everybody out. |
| `VAPID_PRIVATE_KEY` | (not an account) | signs push notifications. Changing it means every browser must turn notifications on again. |

**MongoDB note:** a MongoDB password is stored inside the cluster. Editing `.env` alone does not change it: the user
has to be updated in MongoDB as well (`updateUser`), or the cluster rebuilt with `ow mongo-init`.

## 3. Optional outside services

The site runs without any of these; each one adds something.

| Setting(s) | Adds | How to get it |
|---|---|---|
| `SPACETRACK_USERNAME`, `SPACETRACK_PASSWORD` | The debris and rocket bodies CelesTrak does not publish, older orbit history, re-entry training data. | Free account at https://www.space-track.org (agree to the user agreement). |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | E-mail: alert e-mails, password-reset links, failed-login warnings, decision e-mails. Without them mail waits in the outbox. | Gmail: turn on 2-step verification, then create an **app password** (Google Account → Security → App passwords). Use the Gmail address and the 16-letter app password. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` | Another mail provider. Defaults: `smtp.gmail.com`, `587`, `starttls` (`ssl` when the port is 465). | Your provider's SMTP settings. |
| `MAIL_FROM` | Sender address of OrbitWatch's mail. Default: `SMTP_USERNAME`. | |
| `GROQ_API_KEY` | The LLM-driven AI decision agent. Without it the same agent runs its deterministic workflow. | Free key at https://console.groq.com/keys |
| `GROQ_REASONING_MODEL` | Which Groq model the agent uses. Default `openai/gpt-oss-120b`; if Groq retires it, the agent picks an available one. | |

## 4. Website and server (defaults are fine)

| Setting | Default | Meaning |
|---|---|---|
| `APP_BASE_URL` | `https://localhost:8443` | The address used in links inside e-mails and notifications. |
| `ORBITWATCH_HTTPS_PORT` | `8443` | HTTPS port of `ow serve` / `ow service`. |
| `ORBITWATCH_HTTP_PORT` | `8080` | Plain-HTTP port that only redirects to HTTPS. |
| `ORBITWATCH_TLS_CERT`, `ORBITWATCH_TLS_KEY` | (self-signed localhost certificate) | Paths to a real certificate and key, e.g. one your browser trusts (needed for background push on HTTPS). |
| `ORBITWATCH_RUNTIME` | `%USERPROFILE%\orbitwatch-runtime` | Folder with the Python environment, MongoDB, data, logs and backups. |
| `ORBITWATCH_READ_CACHE_S` | `20` | Seconds the dashboard totals are reused (0 turns the cache off). |
| `ORBITWATCH_THREADS` | `48` | Web server threads (every open page's live stream holds one). |
| `ORBITWATCH_KEEPALIVE` | `0` | Reuse browser connections (0 = close after each response; faster on Windows). |
| `ORBITWATCH_LOGIN_POW_BITS` | `16` | Difficulty of the "not a robot" check after failed logins (about a second). |
| `ORBITWATCH_USE_CACHE` | `0` | `1` = reuse downloaded files instead of downloading again (same as `--cached`). |
| `MYSQL_HOST`, `MYSQL_PORT` | `localhost`, `3306` | Where MySQL listens. |
| `MYSQL_BIN_DIR` | `C:\Program Files\MySQL\MySQL Server 8.4\bin` | MySQL's programs (used by backups and restores). |
| `MONGO_HOST`, `MONGO_PORT` | `127.0.0.1`, `27017` | The MongoDB router (`mongos`). |

## 5. Start-up switches (set in the console, not in `.env`)

Typed before `start-website.bat`, e.g. `set ORBITWATCH_NO_BROWSER=1`:

| Switch | Effect |
|---|---|
| `ORBITWATCH_NO_BROWSER=1` | Do not open the browser. |
| `ORBITWATCH_CHECK_ONLY=1` | Check and prepare everything, but do not start the website. |
| `ORBITWATCH_STOP_MONGO=1` | Stop the MongoDB cluster too when the website stops. |
| `MYSQL_SERVICE` | Name of the MySQL Windows service (default `MySQL84`). |

## 6. Settings OrbitWatch ignores

Older `.env` files (from OrbitalGuard) may still hold `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
`SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`, `GROQ_FAST_MODEL`, `SCREENING_THRESHOLD_KM`,
`COARSE_ALTITUDE_BUFFER_KM`, `PROPAGATION_HORIZON_MINUTES`, `PROPAGATION_STEP_MINUTES`, `CELESTRAK_BASE_URL`,
`OFFLINE_MODE`, `ENVIRONMENT`, `PORT` and the `*_SERVICE_PORT` / `FRONTEND_PORT` lines. Nothing reads them; they can
be deleted. (The screening threshold and the other screening settings are edited on Admin → Configuration.)

## 7. Checklist for a new computer

1. Install MySQL Server 8.4 (as a Windows service) and Python 3.12; unpack the MongoDB 8.0 zip into
   `%USERPROFILE%\orbitwatch-runtime\mongodb\`.
2. Double-click `start-website.bat`: it creates `.env` and asks for `MYSQL_ROOT_PASSWORD`; the setup fills in
   section 2 by itself.
3. Optional: add the section 3 settings you want, then restart the website.
