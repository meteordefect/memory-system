# Connect Claude

The connector is read-only. It has two tools, `recall` and `reflect`. The sample document is loaded in [load-documents.md](load-documents.md). A Google Drive folder is loaded into the same bank by [connect-drive.md](connect-drive.md). Claude asks the bank.

You need the service URL and the bearer key from the Render Environment page (`HINDSIGHT_API_TENANT_API_KEY`).

```
https://<your-service>.onrender.com/mcp/company/
```

The bank name is the last part of the path: `company`.

## The header

Authentication is a fixed bearer token, not a sign-in.

In the connector dialog, choose **No sign-in**. Ignore **Sign in now** even when it says Detected. Leave the OAuth client section alone. Then add a request header:

| Header | Value |
| --- | --- |
| `Authorization` | `Bearer <the key>` |

The value starts with the word `Bearer`, then one space, then the key. Claude sends the value exactly as you type it. A key with no `Bearer` prefix is rejected.

Request headers are in beta. If the dialog has no Request headers section, this organization cannot attach the key. Leave the server locked. Do not set `HINDSIGHT_API_TENANT_MCP_AUTH_DISABLED`.

## Claude Team or Enterprise

An Owner adds the connector once for the organization.

1. Organization settings, then Connectors.
2. Add, then Custom. If Claude asks for a type, choose Web.
3. Paste the MCP URL above.
4. Choose **No sign-in**.
5. Add the `Authorization` header.
6. Save.

Open a new chat inside the project from the next section and turn the connector on before you ask.

## Claude Pro or Max

1. Customize, then Connectors.
2. Add custom connector.
3. Paste the MCP URL.
4. Choose **No sign-in**.
5. Add the `Authorization` header.
6. Save.

## The instruction

Do this after the connector is saved, and before the test chat. Connecting the brain makes the tool available. It does not make Claude use it.

On the Render service, Environment, add `HINDSIGHT_API_MCP_INSTRUCTIONS` and save. Saving restarts the service. Wait until it says Live again. The name of the variable stays. The sentence is written for that company. This is one example:

```
HINDSIGHT_API_MCP_INSTRUCTIONS=This bank holds the company's documents. A short phrase such as a product name is a search of this bank. If recall returns nothing, say the documents do not say.
```

That line is attached to the `recall` tool. Then create a Claude project named `Company brain` and paste this into the project instructions. This is the rule Claude follows on every message:

```
On every message about this company, call the brain first. Use recall for a fact, a price, a policy, or what a document says. Use reflect when the question asks for a judgement, a recommendation, or a comparison across those facts. Do not call both for the same question. Skip the brain only when the message is clearly not about this company, such as general knowledge, coding help, or a personal question. A short or vague line is still a company question. "Bolt pricing" means recall the bolt price. If the brain has the fact, answer from the brain and do not search the web. If it returns nothing, say the documents do not say, then ask whether to search the web. Do not search the web unless the user says yes. Do not suggest or connect Xero, Salesforce, or any other app.
```

Students add both. A new chat in that project, with the brain switched on, is required after either change. If the chat has a web search switch, turn it off.

## Claude Code

From a terminal, with the same URL and key:

```bash
claude mcp add --transport http company-brain \
  "https://<your-service>.onrender.com/mcp/company/" \
  --header "Authorization: Bearer <the key>"
```

## ChatGPT

ChatGPT can add a remote server, but its screen only offers a sign-in flow or no password. It has no place to put this bearer key. Do not turn the lock off to make ChatGPT connect. A client uses Claude for this service.

## What a good answer looks like

Ask these in a new chat in the `Company brain` project, one at a time. A reply with no `recall` or `reflect` step did not use the brain.

1. "jasmine pricing". `recall`. SGD 42.00 per pack, SKU NL-RICE-20, minimum 4 packs, from the price list. No web search.
2. "bolt pricing". `recall`. The documents do not say. It may ask to search the web. It must not search until you say yes.
3. "A customer wants 2 packs of jasmine rice. Should we accept the order?" `reflect`. No, because the minimum is 4 packs. This step is slower. A line about loading a schema is the tool starting, not an error.
4. "What is our growth forecast this quarter?" The documents do not say, then it asks whether to search the web. It must not search until you say yes, and it must not offer Xero, Salesforce, or any other app.
5. "What did Acme last order?" The documents do not say. It must not invent an order.

The server will refuse a tool that writes. `recall` and `reflect` are the only tools on the connector.
