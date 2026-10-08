# LLM configuration and historical provider context

Use `FINRECEIPTS_LLM_PROVIDER=fake` for local tests without an API key. Real providers require separate credentials and may incur charges. This source release does not include credentials, account probe logs or fresh service-availability evidence.

## Configuration

The client reads environment variables:

- `FINRECEIPTS_LLM_PROVIDER`: `fake`, `anthropic` or `openai`
- `FINRECEIPTS_LLM_BASE_URL`: the appropriate compatible API endpoint
- `FINRECEIPTS_LLM_API_KEY_ENV`: **name** of the environment variable holding the key
- `FINRECEIPTS_SMALL_MODEL`, `FINRECEIPTS_LARGE_MODEL`: provider-supported model IDs
- `FINRECEIPTS_MIN_CALL_INTERVAL_S`: pacing between calls

Use `.env.example` as a reference; it contains placeholders. Configure the actual environment used to launch the application, and never commit real keys. Copying the example alone does not authenticate a provider. Keep the local API on loopback and do not expose credentials through logs, reports or public images.

The provider SDKs are used through their corresponding compatible interfaces. Verify current endpoint, model availability, billing and terms directly with the provider before making calls. Official MiniMax API documentation: https://platform.minimaxi.com/docs/api-reference/text-anthropic-api and https://platform.minimax.io/docs/api-reference/text-anthropic-api.

## Historical experiment interpretation

The 2026-10-06–07 saved experiment used MiniMax's mainland Anthropic-compatible API. “Small” was `MiniMax-M3`; “large” was the same model with thinking enabled via the `+think` alias implemented in the client. This did not establish a cheaper model tier. Historical prices, latency and account-specific probes are not current configuration guarantees and are not distributed here. The client has a known limitation concerning replay of thinking blocks across revision turns; do not infer full provider protocol support from one successful call.

## Coding-plan safeguards

The client refuses base URLs containing `/api/coding` by default. A coding-tool subscription must not be assumed to permit this finance/evaluation harness. Review the provider's current terms for the intended use; technical compatibility does not establish permission. The `FINRECEIPTS_ALLOW_CODING_PLAN` override is a technical switch, not legal authorization. Prefer an API product that expressly supports the intended workload.

## Budget planning

Review the configured item count, revision budget, escalation settings, token limits and provider prices before a real run. A cascade or revision loop can call a model more than once per item. The evaluation runner records token accounting and can estimate costs using supplied prices; those estimates are not a billing reconciliation. No paid calls are needed for the fake-model tests.
