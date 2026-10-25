Nagonu data checkout
====================

Enabled services: MTN NORMAL, MTN EXPRESS and TELECEL. These are all three data
bundle services currently in the database. Other service types keep their own
application/checker flows; an unmapped data service cannot silently fall back to
manual fulfilment.

Both `/checkout` and `/store-checkout/<slug>` use `nagonu_orders.py` and POST to
`https://nagonu.site/api/send_order.php`. Offer IDs come from the saved offer's
`value.id`. Customer stage prices and store prices remain locally configured;
provider package prices are not automatically copied into retail pricing.

`NAGONU_API_KEY` overrides the supplied default key on the server. The key is never
sent to browsers. `fetch_nagonu_packages.py` is a separate read-only package tool.

Checkout behaviour
------------------

- Validate the whole cart against saved offers and service availability.
- Reserve wallet payment and save all order lines in a MongoDB transaction.
  Store payments must pass the existing Paystack verification first.
- Claim each saved line once, then call Nagonu outside the database transaction.
- Send JSON with a 60-second response timeout, matching the provider's example.
  Uncertain responses retain safe HTTP/provider codes and a failure reason for
  admin review; response bodies and credentials are not displayed.
- Success requires HTTP 200, provider status 200, reference_id and order_id.
  Accepted means processing, not delivered.
- Repeated customer request IDs or store payment references reuse saved results.
- Explicit provider rejections (100/101/102/103/555 without order references)
  refund wallet payments once. Store payment refunds require admin review; no
  store profit is credited for rejected or uncertain orders.
- Timeouts, HTTP failures, malformed responses and provider 500 are uncertain.
  They do not trigger retries or automatic refunds. A recipient/package lock
  prevents another checkout while acceptance is unresolved.
- A delivered/completed order permits an immediate new purchase for the same
  recipient and package, even within the 30-minute lock. This also applies to
  existing locks; each line in a multi-item batch is checked independently.
- Multi-item carts retain the result for every line. Any unconfirmed line makes
  checkout return `success: false, order_recorded: true` with HTTP 202 and a receipt.

Review uncertain orders
-----------------------

Use the Nagonu account/support to establish whether the exact order was accepted.
Admin order details and receipts expose the Nagonu status and a reconciliation
form. For acceptance, enter the reference and provider order ID; the application
checks the reference against Nagonu's status endpoint, recipient, network and
bundle size. For rejection, explicitly confirm that Nagonu accepted no order,
and record a review note. Wallet refunds/store profit are then applied once.
An in-flight submission cannot be reconciled within its first two minutes.

The live package catalogue places MTN EXPRESS in regular packages and MTN NORMAL
and TELECEL in bigtime packages. Known accepted references use
`response_regular.php` for MTN EXPRESS and `response_big_time.php` for MTN NORMAL
and TELECEL, including previously saved MTN NORMAL orders. `order_status.py` polls unfinished accepted orders every three minutes, using
per-order MongoDB leases to avoid duplicate polling across web workers. It checks
recipient, network and bundle before applying provider status. Lookup errors
leave local statuses unchanged. Terminal deliveries stop polling; failed delivery
requires refund review, without automatically moving money.

The app starts polling automatically. Set `NAGONU_STATUS_POLLING=false` to disable
the in-app worker when running `python order_status.py` as a dedicated worker.
For one immediate cycle, use `python order_status.py --once`.

Admin Dashboard includes Provider Wallet, fetched server-side from `/api/wallet.php`
and cached for 60 seconds. Failures show the last confirmed balance and its age,
or Unavailable if no successful lookup exists.

Requires MongoDB replica-set transactions (the configured Atlas database supports
them). Do not reset `nagonu_state` to queued or delete recipient locks to retry an
uncertain POST: Nagonu has no documented client idempotency key.

Validation used mocked POSTs, wallet/store checkout tests, and a read-only live
package check matching 70 saved customer/store offer entries. No live bundle
orders were submitted during implementation.
