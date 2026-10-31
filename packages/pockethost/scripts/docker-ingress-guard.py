#!/usr/bin/env python3
"""Block unsolicited public forwarding before Docker's accept rules.

Install as a Docker ExecStartPost hook on application hosts (not IP routers).
Only our own chain is replaced; host services and outbound replies are preserved.
"""
import argparse
import json
import re
import subprocess

CHAIN = 'KOLAB-DOCKER-IN'


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30, **kwargs)


def interfaces():
    names = set()
    for family in ('-4', '-6'):
        for route in json.loads(run(['ip', '-j', family, 'route', 'show', 'default']).stdout):
            name = route.get('dev', '')
            if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', name):
                raise RuntimeError('Invalid public interface')
            names.add(name)
    if not names:
        raise RuntimeError('No public default route; ingress guard was not changed')
    return sorted(names)


def apply(names):
    for binary in ('iptables', 'ip6tables'):
        run([binary, '-w', '5', '-S', 'DOCKER-USER'])
        # ORIGINAL also drops existing inbound connections. REPLY packets for
        # connections initiated by containers remain available to Docker's rules.
        rules = ['*filter', ':' + CHAIN + ' - [0:0]', '-F ' + CHAIN]
        rules += ['-A ' + CHAIN + ' -i ' + name + ' -m conntrack --ctdir ORIGINAL -j DROP' for name in names]
        rules += ['-A ' + CHAIN + ' -j RETURN', 'COMMIT', '']
        run([binary + '-restore', '--wait', '5', '--noflush'], input='\n'.join(rules))
        for parent, target in (('DOCKER-USER', CHAIN), ('FORWARD', 'DOCKER-USER')):
            present = subprocess.run([binary, '-w', '5', '-C', parent, '-j', target], capture_output=True, timeout=10)
            if present.returncode:
                run([binary, '-w', '5', '-I', parent, '1', '-j', target])


def remove():
    for binary in ('iptables', 'ip6tables'):
        while subprocess.run([binary, '-w', '5', '-C', 'DOCKER-USER', '-j', CHAIN], capture_output=True, timeout=10).returncode == 0:
            run([binary, '-w', '5', '-D', 'DOCKER-USER', '-j', CHAIN])
        for option in ('-F', '-X'):
            subprocess.run([binary, '-w', '5', option, CHAIN], capture_output=True, timeout=10, check=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remove', action='store_true')
    args = parser.parse_args()
    if args.remove:
        remove()
    else:
        public = interfaces()
        apply(public)
        print(json.dumps({'publicInterfaces': public, 'chain': CHAIN, 'ipv4': True, 'ipv6': True}))
