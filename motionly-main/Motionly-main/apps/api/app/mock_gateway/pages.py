"""Server-rendered, accessible checkout pages for the mock payment gateway.

Every page: ``lang`` attribute, a single ``h1``, visible labels tied to inputs
with ``for``/``id``, a visible focus style, high-contrast colours, and the
mandatory banner 'Demo payment — no money is charged'. No full card number is
ever echoed back into the HTML.
"""

from __future__ import annotations

from html import escape

from .engine import DEMO_NOTE

_STYLE = """
:root { color-scheme: light; }
body {
  font-family: system-ui, sans-serif;
  background: #ffffff;
  color: #1a1a1a;
  margin: 0;
  padding: 1.5rem;
}
main { max-width: 32rem; margin: 0 auto; }
.demo-banner {
  background: #0b3d2e;
  color: #ffffff;
  padding: 0.75rem 1rem;
  border-radius: 0.5rem;
  font-weight: 600;
}
h1 { font-size: 1.5rem; }
label { display: block; margin-top: 1rem; font-weight: 600; }
input, select {
  display: block;
  width: 100%;
  padding: 0.6rem;
  margin-top: 0.25rem;
  border: 2px solid #1a1a1a;
  border-radius: 0.375rem;
  font-size: 1rem;
  box-sizing: border-box;
}
button {
  margin-top: 1.25rem;
  padding: 0.7rem 1.25rem;
  background: #0b3d2e;
  color: #ffffff;
  border: 2px solid #0b3d2e;
  border-radius: 0.375rem;
  font-size: 1rem;
  cursor: pointer;
}
:focus-visible { outline: 3px solid #1452cc; outline-offset: 2px; }
.error { color: #8a1c1c; font-weight: 600; margin-top: 1rem; }
fieldset { border: 2px solid #1a1a1a; border-radius: 0.5rem; margin-top: 1rem; }
legend { font-weight: 700; padding: 0 0.5rem; }
""".strip()


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)} — Demo payment</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        "<main>\n"
        f'<p class="demo-banner" role="status">{escape(DEMO_NOTE)}</p>\n'
        f"{body}\n"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )


def checkout_page(order_id: str, amount_paise: int, error: str | None = None) -> str:
    rupees = f"{amount_paise / 100:.2f}"
    error_html = f'<p class="error" role="alert">{escape(error)}</p>' if error else ""
    body = f"""
<h1>Complete your demo payment</h1>
<p>Order <strong>{escape(order_id)}</strong> for <strong>₹{rupees}</strong> (INR).</p>
{error_html}
<fieldset>
  <legend>Pay with UPI</legend>
  <form method="post" action="/pay/checkout/{escape(order_id)}/upi">
    <label for="vpa">UPI ID (VPA)</label>
    <input id="vpa" name="vpa" type="text" autocomplete="off"
           placeholder="name@bank or success@upi" aria-describedby="vpa-help">
    <p id="vpa-help">Use <code>success@upi</code> to approve or
       <code>failure@upi</code> to decline.</p>
    <label for="upi-action">Outcome</label>
    <select id="upi-action" name="action">
      <option value="approve">Approve</option>
      <option value="decline">Decline</option>
      <option value="timeout">Let it time out</option>
    </select>
    <button type="submit">Pay by UPI</button>
  </form>
</fieldset>
<fieldset>
  <legend>Pay with card</legend>
  <form method="post" action="/pay/checkout/{escape(order_id)}/card">
    <label for="card-number">Card number</label>
    <input id="card-number" name="card_number" type="text" inputmode="numeric"
           autocomplete="cc-number" placeholder="4111 1111 1111 1111"
           aria-describedby="card-help">
    <p id="card-help">Success: 4111 1111 1111 1111 · Decline: 4000 0000 0000 0002
       · OTP step: 4000 0000 0000 3220.</p>
    <button type="submit">Pay by card</button>
  </form>
</fieldset>
"""
    return _page("Checkout", body)


def otp_page(order_id: str, error: str | None = None) -> str:
    error_html = f'<p class="error" role="alert">{escape(error)}</p>' if error else ""
    body = f"""
<h1>Enter the one-time password</h1>
<p>We sent a demo OTP for order <strong>{escape(order_id)}</strong>.</p>
{error_html}
<form method="post" action="/pay/checkout/{escape(order_id)}/otp">
  <label for="otp">One-time password</label>
  <input id="otp" name="otp" type="text" inputmode="numeric" autocomplete="one-time-code"
         aria-describedby="otp-help">
  <p id="otp-help">Use <code>123456</code> to approve this demo payment.</p>
  <button type="submit">Confirm payment</button>
</form>
"""
    return _page("One-time password", body)


def demo_landing_page() -> str:
    """A self-service page to start a fresh demo payment (no merchant key).

    Lets a viewer (and the browser suite) begin a demo payment end to end
    through the public /pay/* routes without the server-to-server merchant API.
    """
    body = """
<h1>Start a demo payment</h1>
<p>This creates a throwaway demo order so you can try the UPI and card flows.
   No money is ever charged.</p>
<form method="post" action="/pay/new">
  <label for="amount">Amount (₹, INR)</label>
  <input id="amount" name="amount_rupees" type="number" inputmode="numeric"
         min="1" step="1" value="499" aria-describedby="amount-help">
  <p id="amount-help">The demo order is created in paise (₹1 = 100 paise).</p>
  <button type="submit">Create demo order</button>
</form>
"""
    return _page("Start a demo payment", body)


def result_page(order_id: str, status: str, reason: str | None = None) -> str:
    captured = status == "captured"
    heading = "Payment successful" if captured else "Payment not completed"
    detail = (
        "Your demo payment was approved. No money was charged."
        if captured
        else f"The demo payment did not go through ({escape(reason or status)})."
    )
    body = f"""
<h1>{escape(heading)}</h1>
<p>Order <strong>{escape(order_id)}</strong>: <strong>{escape(status)}</strong>.</p>
<p>{detail}</p>
"""
    return _page("Payment result", body)
