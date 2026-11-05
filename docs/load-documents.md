# Load a document

Claude's connector cannot write. You load documents with the API, using the same bearer key, from your laptop.

The memory bank is created the first time a document is retained. Its name is `company`. Every later question uses that bank.

The commands below are the same on Windows PowerShell and on a Mac. If `python` is not found, use `python3` on a Mac or `py -3` on Windows. Run them from the unzipped class folder.

## Setup

Copy `.env.example` to `.env` in this folder. Then edit `.env`:

- `HINDSIGHT_URL` is the URL on the Render service page, with no path on the end.
- `HINDSIGHT_API_KEY` is the revealed value of `HINDSIGHT_API_TENANT_API_KEY`.

The scripts read `.env` themselves. You do not run `source .env`. Leave the file on the laptop. It is listed in `.gitignore`.

## Prove the server is up, and locked

```bash
python scripts/check.py
```

The health line returns JSON. The next line should say `recall without a key: 401`. That means a caller with no key gets nothing. Any other code means the lock is off. Stop and check the tenant variables in `render.yaml` before you continue.

## Retain

Text and Markdown go in and wait until the model has read them. PDF, Word, and similar files upload and then the script waits for the background job.

```bash
python scripts/retain.py sample/price-list.md
```

Several files, or a folder. The script loads one file, finishes it, then loads the next. A pile of files at once can run the 2 GB instance out of memory and Render will restart it.

```bash
python scripts/retain.py sample/price-list.md path/to/another.md
python scripts/retain.py path/to/folder
```

Keep the originals. They are how you fill the bank again from the laptop.

Running the script again with the same text file replaces that document. The id is the filename without the extension.

Documents after this sample come from a Google Drive folder. `company-drive` loads that folder into this same bank. See [connect-drive.md](connect-drive.md).

## Ask from the terminal

This uses the API, not Claude. It is how you check the bank before the connector is on.

```bash
python scripts/ask.py "What is the price of the 20 kg jasmine rice?"
python scripts/ask.py --reflect "What is the price of the 20 kg jasmine rice?"
python scripts/ask.py "What did Acme last order?"
```

The rice price in the sample file is SGD 42.00 per pack. Acme is not in the file. Recall should come back empty, or with nothing about an Acme order. Reflect should say it does not know.

Then connect Claude. See [connect-claude.md](connect-claude.md).
