"""How orders get paid.

For now every order is placed as "unpaid" and Rooi Nooi arranges payment with the
customer, then taps "Mark as paid" in the admin.

Yoco comes later, on its own Yoco account (not the vending machine's). The plan is
the same flow the vending machine uses (vending/payment.py there):

  1. store.api_order() creates the order, then calls payments.start(order)
  2. a YocoClient.start() creates a Yoco checkout for order["total_cents"] and returns
     its redirectUrl; the browser is sent there instead of to the order page
  3. Yoco's webhook (signature-checked) or a status poll marks the order paid

Only this file, store.api_order() and a /webhook/yoco route need to change.
"""


class ManualPayments:
    """No online payment: the order page tells the customer what happens next."""
    name = "manual"
    label = "manual (Yoco not connected yet)"
    online = False

    def start(self, order, order_url):
        """Return the URL to send the customer to after placing the order."""
        return order_url
