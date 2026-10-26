"""
mock_slbfe_server.py
--------------------
A tiny local HTTP server that imitates the SLBFE TraRegChk.asp pages so the
automation can be tested end-to-end without agency credentials.

GET  /TraRegChk.asp   -> the real search form (same input names / JS validation)
POST /TraRegChk.asp   -> results page with select[name=idindex] options when the
                         identifier is "on file", otherwise the portal's yellow
                         "Not Found !!!" page (input[name=return2]), exactly like
                         the production server returns.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

SEARCH_FORM = """
<html><head><title>:: Checking Sheet ::</title>
<SCRIPT LANGUAGE="JAVASCRIPT">
function chk() {
  form1.ppno.value = form1.reader.value;
  if (isNaN(Number(form1.ppno.value.substr(1,7))))
    {alert("Enter Correct Number )"); form1.reader.value=""; form1.reader.focus(); return false;}
  if (form1.ppno.value.length < 8)
    {alert("Enter Correct number. "); form1.reader.value=""; form1.reader.focus(); return false;}
}
function upperCase(x){ var str=x.toUpperCase(); form1.ppno.value=str; form1.reader.value=str; }
</SCRIPT></head>
<body onload="sf()">
<form name="form1" method="post" action="TraRegChk.asp" onSubmit="return chk()">
  Passport Number or NIC Number :
  <input name="reader" maxlength="170" onChange="upperCase(this.value)">
  <input type="text" readonly name="ppno">
  <input type="hidden" value="chk" name="action">
  <input type="submit" name="Submit" value="S  E  A  R  C  H">
</form>
</body></html>
"""

# identifier -> list of <option> lines the fake server "has on file"
FAKE_DB = {
    "N8074311": [
        "PP N8074311 NIC 980260925V Training~ Romania Pre departure Orientation Programme 14/10/2026",
        "PP N8074311 NIC 980260925V Registration~ > 27/06/2023 | Qatar | "
        "Specialist Marketing | Source:2860-SKY HIGH SERVI",
    ],
    "N5408869": [
        "PP N5408869 NIC 820453352V Training~ Orientation for other than "
        "Domestic Housekeeping-2 days         31/08/2026",
    ],
    "R1234567": [
        "PP R1234567 NIC 900000000V Registration~ > 10/01/2024 | UAE | "
        "Driver | Source:1100-TEST SOURCE",
    ],
    "B1000001": [
        "PP B1000001 NIC 911111111V Training~ Safety Awareness 05/05/2026",
    ],
    # Pre departure Orientation exists, but the returned PP/NIC belong to
    # someone else -> the checker must flag this for REVIEW.
    "M2000001": [
        "PP N9999999 NIC 111111111V Training~ Romania Pre departure Orientation Programme 14/10/2026",
    ],
}

# Identifier that simulates an ASP session expiry on submit.
EXPIRED_ID = "E2000001"
EXPIRED_PAGE = """
<html><head><title>:::::::::: Message :::::::::</title></head>
<body>
<table><tr><td>Message !!!</td></tr>
<tr><td>Your session has expired. Please login again!!!...
<a href="wrongway.asp?msg=expired">OK</a></td></tr></table>
</body></html>
"""

# Exact look-alike of the live portal's "Not Found !!!" page.
NOT_FOUND_PAGE = """
<html><head><title>:: Checking Sheet ::</title>
<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1"></head>
<body bgcolor="#FFFFFF" text="#000000" onload="sf3()">
<table width="68%" border="1" align="center" bordercolor="#CCCCFF"
       bgcolor="#FEFCF3" cellpadding="0" cellspacing="0">
  <tbody>
  <tr><td bgcolor="#FBC944" height="74">
    <div align="center"><font face="Verdana, Arial" size="4" color="#660000">Not Found !!!</font></div>
  </td></tr>
  <tr><td bgcolor="#FEFCF3"><b><font size="4">Sorry !!!</font></b></td></tr>
  <tr><td bgcolor="#FEFCF3"><font size="4">The number you entered [{id} ] , not found !!!</font></td></tr>
  <tr><td bgcolor="#FEFCF3"><font size="4">Please check again.</font></td></tr>
  <tr><td bgcolor="#FEFCF3" height="56"><div align="center">
    <input type="button" name="return2" onclick="location= 'TraRegChk.asp'" value="R E T U R N ">
  </div></td></tr>
  </tbody>
</table>
</body></html>
"""


def results_page(identifier: str) -> str:
    options = FAKE_DB.get(identifier.upper(), [])
    blacklist = ""
    if identifier.upper() == "B1000001":
        blacklist = ('<p><a href="wrongwayb.asp?msg=!!!!!! Blacklisted  Employee !!! '
                     'B1000001"><font color="#FF0000">!!!!!! Blacklisted Employee !!! '
                     'B1000001</font></a></p>')
    option_html = "".join(
        f'<option value="T{i}">{txt}</option>' for i, txt in enumerate(options)
    )
    return f"""
<html><head><title>:: Checking Sheet ::</title>
<SCRIPT>function sf2(){{document.form2.idindex.focus();}}</SCRIPT></head>
<body onload="sf2()">
<h2>Training &amp; Experience Details</h2>
{blacklist}
<form name="form2" method="post" action="TraRegChkResults.asp">
  <select name="idindex" size="8">{option_html}</select>
</form>
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
        if "TraRegChk.asp" in self.path:
            self._send(SEARCH_FORM)
        else:
            self._send("<html>not found</html>", 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        params = parse_qs(body)
        identifier = (params.get("reader") or params.get("ppno") or [""])[0]
        if identifier.upper() == EXPIRED_ID:
            self._send(EXPIRED_PAGE)
        elif identifier.upper() in FAKE_DB:
            self._send(results_page(identifier))
        else:
            self._send(NOT_FOUND_PAGE.format(id=identifier))


def serve(port: int = 8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock SLBFE server on http://127.0.0.1:{port}/TraRegChk.asp")
    server.serve_forever()


if __name__ == "__main__":
    serve(8765)
