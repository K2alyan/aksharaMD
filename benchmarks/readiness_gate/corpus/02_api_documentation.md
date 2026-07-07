# REST API Reference — DataBridge v4

## Authentication

All requests must include a Bearer token in the Authorization header.
Tokens are issued via the `/auth/token` endpoint and expire after 3600 seconds.
Refresh tokens are valid for 30 days and may be exchanged for a new access token.

## Rate Limits

The default rate limit is 1000 requests per hour per API key.
Burst allowance permits up to 50 requests per second for a maximum of 5 seconds.
Exceeding the rate limit returns HTTP 429 with a Retry-After header.

## Timeout Behavior

The request timeout is 30 seconds for all endpoints.
Long-running operations return a 202 Accepted response with a job ID.
Job status is available at `/jobs/{id}` and polling interval should be at least 5 seconds.

## Pagination

List endpoints return a maximum of 100 records per page.
Use the `cursor` field from the response to fetch the next page.
Pass `limit` (1–100) and `cursor` as query parameters.

## Error Codes

| Code | Meaning                         |
|------|---------------------------------|
| 400  | Bad request — invalid parameters |
| 401  | Unauthorized — invalid token     |
| 403  | Forbidden — insufficient scope   |
| 404  | Resource not found               |
| 429  | Rate limit exceeded              |
| 500  | Internal server error            |

## Webhooks

Webhooks deliver event payloads to your registered endpoint within 10 seconds.
Failed deliveries are retried with exponential backoff for up to 24 hours.
Each payload includes an HMAC-SHA256 signature in the X-DataBridge-Signature header.
