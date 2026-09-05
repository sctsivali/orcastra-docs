# Rate Limits

**Each credential has its own budget. Exceeding it answers 429, and backing off is not optional.**

---

## What is counted

| Bucket | Default | Applies to |
|---|---|---|
| Per credential | 300 requests per 60 seconds | Your installation's API key |
| Per IP address | 500 requests per 60 seconds | Everything from one address |
| Per subnet | 1500 requests per 60 seconds | A /24 or /64 |

Deployments can change all three, so treat these as the shipped defaults rather than a guarantee. The per-credential budget is spent after your secret verifies, so a wrong secret costs you nothing and cannot be used to exhaust somebody else's budget.

MCP calls spend the same per-credential budget as REST calls. They are the same credential.

## Handling 429

Back off. A client that retries immediately turns a brief overrun into a sustained one, and the deployment cannot tell the difference between that and an attack.

```python
import time

def call_with_backoff(request, attempts=5):
    delay = 1.0
    for attempt in range(attempts):
        response = request()
        if response.status_code != 429:
            return response
        time.sleep(delay + random.uniform(0, delay))   # jitter, or every client retries together
        delay *= 2
    raise RuntimeError("still rate limited after backing off")
```

## Call the API origin

The single most common cause of unexpected throttling is calling through the dashboard's address rather than the API's. Everything behind the dashboard origin shares one per-IP bucket with every browser user on it, so an add-on that behaved in testing hits a ceiling in production that has nothing to do with its own traffic.

`GET /extensions/self` returns the base your installation should be using.

## Reducing what you spend

Subscribe to webhooks instead of polling. That is what they are for: an add-on that polls the instance list every ten seconds spends its whole budget discovering nothing changed, and an add-on subscribed to `orcastra.instance.created.v1` spends nothing until something does.

Where you must poll, poll `GET /extensions/self`, which is cheap and answers the question you usually have.
