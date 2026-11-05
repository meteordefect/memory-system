# Connect Google Drive

Claude asks the bank. A document reaches Claude after `company-drive` loads it from one Google Drive folder into that bank.

The bank name is `company`. Prove the bank with the sample file first. That is [load-documents.md](load-documents.md).

You need three values from Render:

| Where | Value |
| --- | --- |
| `company-brain` URL | Base URL of the bank. No path on the end. |
| `company-brain` Environment, `HINDSIGHT_API_TENANT_API_KEY` | The bearer key |
| `company-drive` URL | The page where Drive is connected |
| `company-drive` Environment, `BASIC_AUTH_PASSWORD` | The password for that page. The username is `admin` |

Leave `CREDENTIAL_ENCRYPTION_KEY` as Render generated it.

## 1. Make the folder

In Google Drive, create a folder named `Company brain`. For this first connection, put one short document in it.

Open the folder once so you know which Google account owns it. The connection in the next steps signs in as that account.

## 2. Sign in to the Drive service

Open the `company-drive` URL. Sign in as `admin` with `BASIC_AUTH_PASSWORD`.

Open **Settings**. Copy the **Redirect URI**. It looks like:

```
https://company-drive-xxxx.onrender.com/api/oauth/callback
```

Copy the value from the page. The Google client rejects a retyped one that differs by a character.

## 3. Create the Google client

In [Google Cloud](https://console.cloud.google.com/), create a project named `company-brain`.

1. **APIs & Services**, **Library**. Search for **Google Drive API**. Enable it.
2. **APIs & Services**, **OAuth consent screen**. If the page title is **Google Auth Platform**, the same fields are on that page. Set the app name to `Company brain` and the support email to your own. Choose **External**. Leave the app in **Testing**.
3. Add your Gmail address as a test user.
4. Add the scope `https://www.googleapis.com/auth/drive.readonly`.
5. **Credentials**, **Create credentials**, **OAuth client ID**. Application type: **Web application**. Name it `company-drive`. Under **Authorized redirect URIs**, paste the redirect URI from step 2.
6. Copy the client ID and the client secret. They go into the Drive page, not into `.env` and not into a slide.

## 4. Connect the bank

On the Drive page, open **Credentials**, then **New credential**.

Provider: **Hindsight**. Name it `brain`.

| Field | Value |
| --- | --- |
| Base URL | The `company-brain` URL, with no path on the end |
| API key | `HINDSIGHT_API_TENANT_API_KEY` |

Save, then **Test**. The line should say the connection is OK.

## 5. Connect the folder

**New credential** again. Provider: **Google Drive**. Name it `drive`.

Paste the client ID and the client secret. Save. Then **Connect**.

Google says the app is not verified. Choose **Advanced**, then **Go to Company brain (unsafe)**. Sign in as the test user that owns the folder, and allow the read-only Drive access.

**Test** should name that Google account.

## 6. Send the folder to the bank

Open **Tasks**, then **New task**. Name it `Company docs`.

| Field | Value |
| --- | --- |
| Source credential | `drive` |
| Drive | Leave empty. That is My Drive. |
| Folder | Browse, and choose `Company brain` |
| Include sub-folders | On |
| Export Google Docs/Sheets/Slides | On. A Doc is sent as Markdown, a Sheet as CSV, a Slides deck as PDF. |
| Hindsight credential | `brain` |
| Memory bank | `company` |
| Retain strategy | Bank default |
| Schedule | Every 15 min |

Leave the task enabled. Save. Then **Run now**.

Open **Runs**. A finished run lists the document. The next edit to that file is picked up on the following run. A file removed from the folder is removed from the bank on the following run.

The 2 GB brain reads one heavy file at a time. Keep the first document short, the way the sample price list is short, and let that run finish before you drop a pile in.

## 7. Ask Claude

Ask in the `Company brain` project, with the `brain` connector on. A fact that is only in the Drive folder comes back through `recall`. See [connect-claude.md](connect-claude.md).

## If it fails

| What you see | What to do |
| --- | --- |
| Google refuses the redirect | The redirect URI on the client is not the exact value from **Settings**. Paste it again. |
| `Access blocked` or the account is not a test user | The app is in Testing, and this Gmail is not on the test-user list. Add it, then **Connect** again. |
| Drive API has not been used | Enable **Google Drive API** on that Cloud project, wait a minute, and **Connect** again. |
| Hindsight **Test** returns `401` | The API key does not match `HINDSIGHT_API_TENANT_API_KEY` on `company-brain`. |
| The run fails and `company-brain` restarts | The file was too heavy for the 2 GB plan. Use a short document for the first run. |
| `company-drive` logs `not writable` and `/data` | The start script did not run as root. Confirm the service is built from `drive.Dockerfile`. |
