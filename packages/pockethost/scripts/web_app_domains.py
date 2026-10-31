"""Domain DNS validation and isolated Certbot storage for web applications only."""
import datetime
import ipaddress
import json
import os
import pathlib
import re
import shutil
import ssl
import subprocess
import urllib.parse
import urllib.request

HOST = re.compile(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z')

def valid_hostname(hostname):
    return isinstance(hostname, str) and bool(HOST.fullmatch(hostname))

def _dns(hostname, kind):
    url = 'https://cloudflare-dns.com/dns-query?' + urllib.parse.urlencode({'name': hostname, 'type': kind})
    request = urllib.request.Request(url, headers={'Accept': 'application/dns-json', 'User-Agent': 'PocketHost-Web-Domain/1'})
    with urllib.request.urlopen(request, timeout=6) as response:
        answer = json.loads(response.read(65536))
    if answer.get('Status') not in [0, 3]:
        raise ValueError('Le résolveur DNS ne peut pas vérifier ce domaine pour le moment.')
    number = {'A': 1, 'AAAA': 28, 'TXT': 16}[kind]
    return [row['data'] for row in answer.get('Answer', []) if row.get('type') == number]

def verify_web_domain_dns(hostname, public_ip, ownership_token):
    if not valid_hostname(hostname):
        raise ValueError('Nom de domaine invalide.')
    address = ipaddress.ip_address(public_ip)
    if not address.is_global:
        raise ValueError('L’adresse publique du serveur web est invalide.')
    if not re.fullmatch('[A-Za-z0-9_-]{24,100}', ownership_token):
        raise ValueError('Code de vérification invalide.')
    apex = os.environ.get('APEX_DOMAIN', '')
    aliases = {value.strip().lower() for value in os.environ.get('WEB_APPS_PLATFORM_ALIASES', '').split(',') if value.strip()}
    label = hostname[:-len(apex)-1] if apex and hostname.endswith('.' + apex) else ''
    blocked = {'app','app2','app3','www','api','admin','ftp','sftp','smtp','mail','mothership','edge','daemon','acme','static','assets','files'}
    platform_alias = hostname in aliases and valid_hostname(apex) and label and '.' not in label and not label.startswith('web-') and label not in blocked and not re.fullmatch(r'app[0-9]*', label) and hostname != os.environ.get('APP_HOST', '')
    if not platform_alias:
        expected = 'pockethost-verification=' + ownership_token
        texts = _dns('_pockethost.' + hostname, 'TXT')
        values = [''.join(re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', value)) for value in texts]
        if expected not in values:
            raise ValueError('Le TXT de vérification est absent ou incorrect. Attendez sa propagation DNS.')
    ipv4 = _dns(hostname, 'A')
    ipv6 = _dns(hostname, 'AAAA')
    actual = {ipaddress.ip_address(value) for value in ipv4 + ipv6}
    if not actual or actual != {address}:
        raise ValueError('Les enregistrements A/AAAA doivent pointer uniquement vers l’adresse du serveur. Désactivez le proxy DNS pendant la validation.')
    return {'verified': True}

def _directory(path, mode=0o750):
    if path.exists() and path.resolve() != path:
        raise ValueError('Répertoire de certificat invalide.')
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    path.chmod(mode)
    return path

def provision_web_domain_certificate(root: pathlib.Path, hostname: str, public_ip: str, email: str) -> dict:
    root = pathlib.Path(root).absolute()
    if root.resolve() != root or not valid_hostname(hostname):
        raise ValueError('Emplacement ou domaine invalide.')
    try:
        valid_ip = ipaddress.ip_address(public_ip).is_global
    except ValueError:
        valid_ip = False
    if not valid_ip:
        raise RuntimeError('Adresse IP publique du serveur non configurée pour le HTTPS.')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise RuntimeError('Adresse de contact des certificats HTTPS non configurée sur ce serveur.')
    if not shutil.which('certbot'):
        raise RuntimeError('Certbot est requis pour activer le HTTPS de ce domaine.')
    base = _directory(root / 'certbot', 0o700)
    webroot = _directory(root / 'acme')
    _directory(webroot / '.well-known' / 'acme-challenge')
    # No standalone server, root permissions, global certificate config or PB restart.
    command = ['certbot', 'certonly', '--webroot', '--webroot-path', str(webroot),
               '--config-dir', str(_directory(base / 'config', 0o700)),
               '--work-dir', str(_directory(base / 'work', 0o700)),
               '--logs-dir', str(_directory(base / 'logs', 0o700)),
               '--non-interactive', '--agree-tos', '--email', email, '--cert-name', hostname,
               '--domains', hostname, '--keep-until-expiring', '--preferred-challenges', 'http-01']
    try:
        result = subprocess.run(command, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Délai de création du certificat dépassé. Vérifiez le DNS et l’accès public au port 80.') from None
    if result.returncode:
        # Do not return subprocess logs, private paths or ACME account information.
        raise RuntimeError('La validation HTTPS a échoué. Vérifiez DNS, port 80 et absence de proxy DNS pendant la première validation.')
    live = base / 'config' / 'live' / hostname
    certificate, key = (live / 'fullchain.pem').read_bytes(), (live / 'privkey.pem').read_bytes()
    # Validate the pair before atomically publishing it to the firewall's SNI reader.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(live / 'fullchain.pem'), str(live / 'privkey.pem'))
    details = ssl._ssl._test_decode_cert(str(live / 'cert.pem'))
    expires = datetime.datetime.strptime(details['notAfter'], '%b %d %H:%M:%S %Y %Z').replace(tzinfo=datetime.timezone.utc).isoformat()
    target = _directory(root / 'tls' / hostname)
    for name, content in [('fullchain.pem', certificate), ('privkey.pem', key)]:
        destination = target / name
        if destination.is_symlink():
            raise ValueError('Fichier de certificat invalide.')
        temp = target / ('.' + name + '.' + str(os.getpid()))
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
        with os.fdopen(fd, 'wb') as output:
            output.write(content)
        temp.chmod(0o640)
        os.replace(temp, destination)
    return {'certificate': True, 'certificateExpiresAt': expires}

if __name__ == '__main__':
    import sys
    data = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else json.load(sys.stdin)
    try:
        value = verify_web_domain_dns(data['hostname'], data['publicIp'], data['ownershipToken'])
        print(json.dumps(value))
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else 'Vérification DNS indisponible. Réessayez après propagation.'
        print(json.dumps({'verified': False, 'error': message}))
