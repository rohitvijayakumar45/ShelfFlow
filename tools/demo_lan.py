"""Offline demo on the hotspot: HTTPS certificates for this laptop's current IP and a phone setup page.

Phones only open the camera (QR scanner) on https pages, so the staff and customer apps are served over
HTTPS with a certificate from a local ShelfFlow demo CA. Each phone installs that CA once (from the setup
page this tool serves over plain http) and then trusts the apps without warnings, with no internet.

  python -m tools.demo_lan            # make/refresh certificates, print the links, serve the setup page
  python -m tools.demo_lan --no-serve # certificates and links only

Then start the apps in LAN mode (vite --mode lan), see docs/demo-offline.md.
"""
import argparse
import http.server
import ipaddress
import shutil
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CERTS = ROOT / 'data/demo-certs'
SETUP_PORT = 5180


def openssl():
    found = shutil.which('openssl')
    for candidate in [found, r'C:\Program Files\Git\usr\bin\openssl.exe', r'C:\Program Files\Git\mingw64\bin\openssl.exe']:
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit('openssl not found: install Git for Windows (it ships openssl) or add openssl to PATH')


def lan_addresses():
    """Private IPv4 addresses of this laptop (hotspot, Wi-Fi, Ethernet)."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('10.255.255.255', 1))  # no packet is sent; picks the default interface
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in found if ipaddress.ip_address(a).is_private and not a.startswith('127.'))


def run(*args):
    subprocess.run([openssl(), *args], check=True, capture_output=True)


def make_certificates(addresses):
    CERTS.mkdir(parents=True, exist_ok=True)
    ca_key, ca = CERTS / 'ca.key', CERTS / 'shelfflow-demo-ca.crt'
    if not ca.exists():
        (CERTS / 'ca.cnf').write_text('[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=ShelfFlow Demo CA\nO=ShelfFlow demo\n'
                                      '[ext]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n'
                                      'subjectKeyIdentifier=hash\n')
        run('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(ca_key), '-out', str(ca), '-days', '3650',
            '-config', str(CERTS / 'ca.cnf'), '-extensions', 'ext')
    names = ['DNS:localhost', 'IP:127.0.0.1'] + [f'IP:{a}' for a in addresses]
    (CERTS / 'server.cnf').write_text('[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=ShelfFlow demo\n'
                                      f'[ext]\nbasicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n'
                                      f'extendedKeyUsage=serverAuth\nsubjectAltName={",".join(names)}\n')
    run('req', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(CERTS / 'server.key'), '-out', str(CERTS / 'server.csr'),
        '-config', str(CERTS / 'server.cnf'))
    # 397 days: the longest validity iOS and Android accept for a server certificate.
    run('x509', '-req', '-in', str(CERTS / 'server.csr'), '-CA', str(ca), '-CAkey', str(ca_key), '-CAcreateserial',
        '-out', str(CERTS / 'server.pem'), '-days', '397', '-extfile', str(CERTS / 'server.cnf'), '-extensions', 'ext')
    return ca


def setup_page(addresses):
    links = ''.join(f'<li>Staff: <a href="https://{a}:5174">https://{a}:5174</a><br>Customer: '
                    f'<a href="https://{a}:5173">https://{a}:5173</a></li>' for a in addresses)
    return f'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ShelfFlow demo setup</title><body style="font-family:system-ui;max-width:36rem;margin:1rem auto;padding:0 1rem;line-height:1.5">
<h1>ShelfFlow demo setup</h1>
<p><b>1.</b> <a href="/shelfflow-demo-ca.crt">Download the ShelfFlow demo certificate</a> and install it (once per phone):</p>
<ul><li><b>Android:</b> Settings &rarr; Security &rarr; More security settings &rarr; Encryption &amp; credentials &rarr;
Install a certificate &rarr; CA certificate &rarr; choose the downloaded file.</li>
<li><b>iPhone:</b> open the download, then Settings &rarr; Profile Downloaded &rarr; Install; then Settings &rarr; General
&rarr; About &rarr; Certificate Trust Settings &rarr; turn on <i>ShelfFlow Demo CA</i>.</li></ul>
<p><b>2.</b> Open the app:</p><ul>{links}</ul>
<p>Without the certificate the browser warns once; choose <i>Advanced &rarr; Proceed</i> (the QR scanner may then
still refuse the camera on some phones).</p></body>'''


def serve(addresses):
    page = setup_page(addresses).encode()
    ca = (CERTS / 'shelfflow-demo-ca.crt').read_bytes()

    class Setup(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/shelfflow-demo-ca.crt':
                body, kind = ca, 'application/x-x509-ca-cert'  # public CA certificate only, never a key
            elif self.path in ('/', '/index.html'):
                body, kind = page, 'text/html; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    print(f'Phone setup page: ' + ', '.join(f'http://{a}:{SETUP_PORT}' for a in addresses) + '  (Ctrl+C to stop)', flush=True)
    http.server.ThreadingHTTPServer(('0.0.0.0', SETUP_PORT), Setup).serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--no-serve', action='store_true', help='do not serve the phone setup page')
    args = parser.parse_args()
    addresses = lan_addresses()
    if not addresses:
        raise SystemExit('No network address: connect this laptop to the hotspot first')
    make_certificates(addresses)
    print('HTTPS certificate issued for', ', '.join(addresses), flush=True)
    for a in addresses:
        print(f'  Staff:    https://{a}:5174\n  Customer: https://{a}:5173', flush=True)
    print('Restart the LAN apps after this so they load the new certificate.', flush=True)
    if not args.no_serve:
        serve(addresses)


if __name__ == '__main__':
    main()
