# Connect Claude

The connector is read-only. It has two tools, `recall` and `reflect`. Loading a document is a separate step, in [load-documents.md](load-documents.md).

You need the service URL and the bearer key from the Render Environment page (`HINDSIGHT_API_TENANT_API_KEY`).

```
https://<your-service>.onrender.com/mcp/company/
```

The bank name is the last part of the path: `company`.

## The header

Authentication is a fixed bearer token, not a sign-in.

In the connector dialog, set authentication to none, then add a request header:

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
4. Set authentication to none.
5. Add the `Authorization` header.
6. Save.

Open a new chat and turn the connector on before you ask.

## Claude Pro or Max

1. Customize, then Connectors.
2. Add custom connector.
3. Paste the MCP URL.
4. Set authentication to none.
5. Add the `Authorization` header.
6. Save.

## Claude Code

From a terminal, with the same URL and key:

```bash
claude mcp add --transport http company-brain \
  "https://<your-service>.onrender.com/mcp/company/" \
  --header "Authorization: Bearer <the key>"
```

## ChatGPT

A plan that can already call a remote connector uses this same URL and the same `Authorization` header. The clicks are the product's own. The address does not change.

## What a good answer looks like

Ask something the document says, and something it does not.

- "What is the price of the 20 kg jasmine rice?" The answer should match the file, and it should be able to say which file the memory came from.
- "What did Acme last order?" The documents do not say. The answer should say it does not know. It should not invent an order.

The server will refuse a tool that writes. `recall` and `reflect` are the only tools on the connector.
