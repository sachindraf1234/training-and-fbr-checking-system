"""
mock_fbr_server.py
------------------
Local imitation of the SLBFE Family Background Report pages:

  GET  /logroot/agency/cmpl/fbr_sms.asp   -> search form (form2, ppno/idno/search,
                                             including the real chk() length alert)
  POST /logroot/agency/cmpl/fbr_sms1.asp  -> result table with the same 6-column
                                             layout as the production page

Dates for VALID / EXPIRES SOON cases are generated relative to "today" so the
expiry logic can be tested deterministically on any day.
"""
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

FORM_PAGE = """
<html><head><title>Complain Registration - SLBFE</title>
<script language="JavaScript">
function chk() {
  x = document.form2;
  if ((x.ppno.value.length > 0) && (x.ppno.value.length < 8))
    { alert("Enter a Correct Passport No."); form2.ppno.focus(); return false; }
}
</script></head>
<body>
<h2>Family Background Report Verification</h2>
<form name="form2" method="post" action="fbr_sms1.asp" onSubmit="return chk()">
  Search by<br>
  Passport Number : <input type="text" name="ppno"><br>
  ID Number : <input type="text" name="idno"><br>
  <input type="hidden" name="action2" value="chk1">
  <input type="submit" name="search" value="Search">
</form>
</body></html>
"""

EXPIRED_PAGE = """
<html><head><title>:::::::::: Message :::::::::</title></head>
<body>
<table><tr><td>Message !!!</td></tr>
<tr><td>Your session has expired. Please login again!!!...
<a href="wrongway.asp?msg=expired">OK</a></td></tr></table>
</body></html>
"""

EXPIRED_ID = "E2000001"

# Imitation of the post-login portal welcome page with its programme menu.
# The checker is expected to click the "Family Background Report Verification"
# link instead of only ever deep-linking to fbr_sms.asp.
WELCOME_PAGE = """
<html><head><title>SLBFE Agency Portal</title></head>
<body>
<h2>Welcome - Common Programmes</h2>
<ul>
  <li><a href="/logroot/agency/cmpl/fbr_sms.asp">Family Background Report Verification</a></li>
  <li><a href="/other.asp">Other Agency Service</a></li>
</ul>
</body></html>
"""


def _iso(d: date) -> str:
    return d.strftime("%d/%m/%Y")


today = date.today()

# key -> (passport, nic, agency_lno, status, sms_date)
FAKE_DB = {
    # recent report -> still VALID
    "N8074311": ("N8074311", "980260925V", "0000", "Recommended", today - timedelta(days=10)),
    "980260925V": ("N8074311", "980260925V", "0000", "Recommended", today - timedelta(days=10)),
    # fixed historical report -> EXPIRED (26/01/2026 + 6m = 26/07/2026)
    "P0262022": ("P0262022", "200477304008", "0000", "Recommended", date(2026, 1, 26)),
    "200477304008": ("P0262022", "200477304008", "0000", "Recommended", date(2026, 1, 26)),
    # report expiring in 5 days -> EXPIRES SOON
    "N11030886": ("N11030886", "820453352V", "2860", "Pending", today - timedelta(days=178)),
    "820453352V": ("N11030886", "820453352V", "2860", "Pending", today - timedelta(days=178)),
    # NIC-only fallback record (passport SHORT would be rejected)
    "922222222V": ("N7654321", "922222222V", "0000", "Recommended", today - timedelta(days=30)),
    # mismatch: searching M2000001 returns a row that belongs to someone else
    "M2000001": ("N9999999", "111111111V", "0000", "Recommended", today - timedelta(days=20)),
}


def results_page(identifier: str) -> str:
    row = FAKE_DB.get(identifier.upper())
    data_rows = ""
    if row:
        pp, nic, lno, status, sms_d = row
        # the real page splits date and time across two lines with a leading "-"
        date_cell = f"{_iso(sms_d)}<br>-15:38:52"
        data_rows = f"""
        <tr>
          <td>1</td><td>{date_cell}</td><td>{pp}</td><td>{nic}</td>
          <td>{lno}</td><td>&nbsp;{status}</td>
        </tr>"""
    return f"""
<html><head><title>Complain Registration - SLBFE</title></head>
<body>
<table border="0" cellpadding="1" cellspacing="1">
  <tr><td colspan="6"><b>Family Background Report Verification</b></td></tr>
  <tr>
    <td></td>
    <td>Date (SMS Received )</td><td>Passport No</td><td>NIC No</td>
    <td>Requested Agency LNO</td><td>Status</td>
  </tr>
  {data_rows}
</table>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body: str, status: int = 200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if "fbr_sms.asp" in self.path:
            self._send(FORM_PAGE)
        elif "an_welcomenew.asp" in self.path:
            self._send(WELCOME_PAGE)
        else:
            self._send("<html>not found</html>", 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        params = parse_qs(self.rfile.read(length).decode("utf-8"))
        ppno = (params.get("ppno") or [""])[0].strip()
        idno = (params.get("idno") or [""])[0].strip()
        ident = ppno or idno
        if ident.upper() == EXPIRED_ID:
            self._send(EXPIRED_PAGE)
        else:
            self._send(results_page(ident))


def serve(port: int = 8766):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock FBR server on http://127.0.0.1:{port}/logroot/agency/cmpl/fbr_sms.asp")
    server.serve_forever()


if __name__ == "__main__":
    serve(8766)
