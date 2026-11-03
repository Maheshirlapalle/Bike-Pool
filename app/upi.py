"""UPI deep link + QR generation for the platform fee.

No money moves through this app: the QR opens the buyer's UPI app with the
amount pre-filled, and an admin confirms receipt manually (mark-paid).
"""

import base64
import io
from urllib.parse import quote

import qrcode


def build_upi_uri(upi_id: str, payee: str, amount_inr: int, note: str) -> str:
    return f"upi://pay?pa={quote(upi_id, safe='@')}&pn={quote(payee)}&am={amount_inr}.00&cu=INR&tn={quote(note)}"


def qr_data_url(content: str) -> str:
    img = qrcode.make(content)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
