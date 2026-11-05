# Company brain on Render

One service per person, on a Render account you control. It runs [Hindsight](https://hindsight.vectorize.io/) (Vectorize, MIT) with local embeddings, one memory bank named `company`, and a bearer key. Claude gets two tools: `recall` and `reflect`.

You do not write a server in the room. This folder is what you deploy.

## Before you start

- A Render account with a card. The workspace fee is $0. The service is the 2 GB plan (`1c-2g`), about $25 if it runs all month, billed to the second. A 1 GB disk is mounted for the database.
- A model key. OpenCode Go is what the link deploys. See [docs/model-keys.md](docs/model-keys.md) if you are using Claude Team instead, and read Go's DeepSeek footnote again if the date is after 30 November 2026.
- Claude Pro or above. On Claude Team, an Owner adds the connector.
- Python, only for loading a document after the service is up. On Windows, install it from [python.org](https://www.python.org/downloads/) and tick "Add python.exe to PATH".

You do not install Git. You do not create a repository.

Leave the plan on `1c-2g`. Render's free instance sleeps after a few minutes and cannot keep a disk, so the bank would vanish.

The first deploy pulls an image of about 9 GB. Budget 15–25 minutes. The admin site on port 9999 is not started. The public URL is the API on port 8888. The service runs in Singapore.

## 1. Open the deploy link

Your teacher sends one link. It looks like this, with their repository filled in:

```
https://render.com/deploy?repo=https://github.com/meteordefect/memory-system
```

Sign in to Render. Render shows the service from that repository: Singapore, the 2 GB plan, and the disk. Choose **Deploy**. When it asks for `HINDSIGHT_API_LLM_API_KEY`, paste your model key. Render generates the bearer key itself. You do not type that one.

Claude Team: after the service exists, change the provider and model on the Environment page. [docs/model-keys.md](docs/model-keys.md) has the two values. Delete `HINDSIGHT_API_LLM_BASE_URL` there and save.

## 2. Wait until the service is live

Open the `company-brain` service. The first deploy is finished when the status is live and the logs contain `Hindsight is running`.

The URL looks like `https://company-brain-xxxx.onrender.com`. Copy it.

## 3. Copy the bearer key

On the service, open Environment. Reveal `HINDSIGHT_API_TENANT_API_KEY`. That value is the bearer token. It is the only copy. Do not email it or put it in a slide.

Download the class folder: on the GitHub page, Code, then Download ZIP, then unzip it. You do not use Git.

In that folder, copy `.env.example` to `.env`. File Explorer or Finder is enough. On a command line, Windows uses `copy .env.example .env`. Mac uses `cp .env.example .env`.

Put the service URL and the bearer key in `.env`. Leave `RENDER_API_KEY` and `HINDSIGHT_API_LLM_API_KEY` empty. The model key was pasted into Render. It does not go in this file.

## 4. Load a document and ask

Follow [docs/load-documents.md](docs/load-documents.md). The sample file is `sample/price-list.md`. The rice line is there so you can check a real figure. A question about a customer's last order is there so you can see an empty answer.

## 5. Connect Claude

Follow [docs/connect-claude.md](docs/connect-claude.md).

The URL Claude needs is:

```
https://<your-service>.onrender.com/mcp/company/
```

The header value is `Bearer <the key>`, including the word `Bearer` and the space.

## Leave it running

Do not reboot it. There is no daily restart. Render starts the process again if it dies.

When Claude fails, do these in order:

1. Wait a minute and ask the same question again. A slow answer can time out while the service is still fine.
2. Open the service in Render. If the status says **Live**, stop. Do not click Restart, Suspend, or Manual Deploy.
3. If the status says **Failed**, click **Manual Deploy** once, wait until it says Live, and ask again. That is the only restart.

Keep the files you loaded. If a failure leaves the bank empty, load those same files again with `scripts/retain.py`. You do not rebuild the service for that.

## End of the day

1. In Claude, remove the connector.
2. In Render, replace `HINDSIGHT_API_TENANT_API_KEY` with a new random value and save. The old key stops working. A new value:

   ```bash
   python -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
   ```

3. Suspend the service (the service page, then Suspend). Compute billing stops. The 1 GB disk keeps a small charge until the service is deleted. Delete the service if you are not keeping the brain.

Per-person login is a later step. This key is one shared key for the whole service.

## If the deploy fails

Open the deploy logs and match the line.

| Log line | What to do |
| --- | --- |
| `not writable` or `Permission denied` and `pg0` | The start script did not run as root. Confirm the service is this Blueprint, built from the `Dockerfile` in this folder, not a raw pull of the Hindsight image. |
| `API did not become healthy within 300s` | The models were still loading. Set `HINDSIGHT_API_MODEL_INIT_TIMEOUT` to `600` on the Environment page and redeploy. |
| `Killed` | The 2 GB instance ran out of memory. Keep the plan at `1c-2g` and load a short document first. |
| Health URL returns nothing | Wait for `live`. A deploy still pulling the image has no URL yet. |
| `scripts/ask.py` returns `401` | The key in `.env` does not match `HINDSIGHT_API_TENANT_API_KEY`, or a space was clipped off when you pasted it. |
| `python` is not recognized | On Windows, reinstall Python and tick "Add python.exe to PATH", then open a new PowerShell. On a Mac, type `python3` instead of `python`. |
| Claude has no Request headers section | That organization cannot attach a bearer key. Leave the server locked. |

Render probes `/health/live`, which only checks that the process is up. `/health` also checks the database. `python scripts/check.py` calls it. Everything under `/v1/` and `/mcp/` requires the bearer key.

## For the teacher

Students never commit. This folder is published once, as a public repository, with `render.yaml` at the root. The link to send is:

```
https://render.com/deploy?repo=https://github.com/meteordefect/memory-system
```

`autoDeployTrigger` is off, so a later push to that repository does not restart the copies already running on their accounts.
