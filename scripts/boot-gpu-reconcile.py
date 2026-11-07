#!/usr/bin/env python3
"""Repair Stremio after reboot when host DRM render node numbering changes.

Do not touch state or cache. Recreate Core only when its bind-mounted render
device or BT port no longer matches the current host.
"""
from pathlib import Path
import json
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def run(*args, **kwargs):
    return subprocess.run(args,cwd=str(ROOT),check=True,text=True,capture_output=True,
                          timeout=kwargs.pop('timeout',25),**kwargs).stdout

def intel_node():
    for node in Path('/dev/dri').glob('renderD*'):
        vendor=Path('/sys/class/drm')/node.name/'device/vendor'
        if vendor.is_file() and vendor.read_text().strip()=='0x8086':
            return str(node)
    return None

def current_container():
    try:
        return json.loads(run('docker','inspect','stremio-libtorrent-server'))[0]
    except (subprocess.CalledProcessError,IndexError,ValueError):
        return None

def env_port():
    content=(ROOT/'.env').read_text()
    matches=[v for line in content.splitlines()
             if line.startswith('STREMIOSRV_BT_LISTEN_PORT=')
             for v in [line.partition('=')[2]]]
    if len(matches)!=1: raise ValueError('invalid BT port config')
    port=int(matches[0])
    if not 1<=port<=65535: raise ValueError('invalid BT port range')
    return port

def needs_recreate(node,port,info):
    if info is None:return True
    if info.get('State',{}).get('Status') not in ('running','restarting'):return True
    environment=dict(v.split('=',1) for v in info['Config']['Env'] if '=' in v)
    if environment.get('STREMIOSRV_BT_LISTEN_PORT')!=str(port):return True
    mapped={v.get('PathOnHost') for v in (info['HostConfig'].get('Devices') or [])}
    if node and node not in mapped:return True
    if node is None and mapped:return True
    bindings=info['HostConfig'].get('PortBindings') or {}
    return any(not bindings.get(f'{port}/{proto}') for proto in ('tcp','udp'))

def main():
    node=intel_node()
    port=env_port()
    info=current_container()
    if not needs_recreate(node,port,info):
        print('Stremio bindings match host GPU and BT port; no action')
        return 0
    print(f'Reconciling Stremio after boot: Intel render={node}, BT port={port}',flush=True)
    run('sh','start.sh','up','-d','--no-deps','--force-recreate','stremio-libtorrent-server',
        timeout=140)
    info=current_container()
    if needs_recreate(node,port,info):
        raise RuntimeError('Stremio bindings not reconciled')
    print('Stremio boot bindings reconciled',flush=True)
    return 0

if __name__=='__main__':
    sys.exit(main())
