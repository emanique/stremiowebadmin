import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('boot_reconcile',Path(__file__).parents[1]/'scripts/boot-gpu-reconcile.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_restart_when_node_changed():
    info={'State':{'Status':'running'}, 'Config':{'Env':['STREMIOSRV_BT_LISTEN_PORT=47883']}, 'HostConfig':{'Devices':[{'PathOnHost':'/dev/dri/renderD129'}], 'PortBindings':{'47883/tcp':[{}],'47883/udp':[{}]}}}
    assert mod.needs_recreate('/dev/dri/renderD128',47883,info)


def test_no_recreation_when_all_bindings_match():
    info={'State':{'Status':'running'}, 'Config':{'Env':['STREMIOSRV_BT_LISTEN_PORT=47883']}, 'HostConfig':{'Devices':[{'PathOnHost':'/dev/dri/renderD128'}], 'PortBindings':{'47883/tcp':[{}],'47883/udp':[{}]}}}
    assert not mod.needs_recreate('/dev/dri/renderD128',47883,info)


def test_port_change_requires_recreate():
    info={'State':{'Status':'running'}, 'Config':{'Env':['STREMIOSRV_BT_LISTEN_PORT=6881']}, 'HostConfig':{'Devices':[{'PathOnHost':'/dev/dri/renderD128'}], 'PortBindings':{'6881/tcp':[{}],'6881/udp':[{}]}}}
    assert mod.needs_recreate('/dev/dri/renderD128',47883,info)
