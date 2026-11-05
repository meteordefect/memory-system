# Model key

Hindsight needs a model key to read a document and to answer. That key is separate from the bearer key Claude uses. Embeddings run inside the image, so this key is only for the language model.

Pick one. Leave the other unset.

The workshop date is after 30 November 2026. OpenCode Go's DeepSeek agreement, as read on 5 November 2026, ran through 30 November 2026 and renews monthly. Read Go's retention footnote again before the room. If the model name has changed, change `HINDSIGHT_API_LLM_MODEL` to the current one.

## OpenCode Go

This is what `render.yaml` already contains.

One Go subscription per business, about $10 a month. A new Go workspace must allow the Global region, or DeepSeek is blocked.

In Render, `HINDSIGHT_API_LLM_API_KEY` is the Go key. The other three are already set:

```
HINDSIGHT_API_LLM_PROVIDER=openai
HINDSIGHT_API_LLM_BASE_URL=https://opencode.ai/inference/go/openai/v1
HINDSIGHT_API_LLM_MODEL=deepseek-v4.1-flash
```

Go has no embeddings endpoint. That is why the service uses the full Hindsight image, which embeds on the machine.

## Claude Team

Use this only when the business already has Claude Team. A Team Owner links a Console organization and creates an API key. Credits start 7 days after the plan begins ($20 per Standard seat, pooled, cap $500). Pro and Enterprise do not include these credits. The Claude chat login is not this key.

Before the first deploy, edit `render.yaml`. Set the provider and the model, and delete the `HINDSIGHT_API_LLM_BASE_URL` entry so Hindsight uses Anthropic's own endpoint. An empty base URL would still be sent.

```yaml
- key: HINDSIGHT_API_LLM_PROVIDER
  value: anthropic
- key: HINDSIGHT_API_LLM_MODEL
  value: claude-haiku-5-5
```

Hindsight's default Anthropic model is the older `claude-haiku-4-5`, so the model line has to stay. Paste the Console API key when Render asks for `HINDSIGHT_API_LLM_API_KEY`.

If the service is already live, set those two variables on the Render Environment page, delete `HINDSIGHT_API_LLM_BASE_URL`, and save. Render redeploys.
