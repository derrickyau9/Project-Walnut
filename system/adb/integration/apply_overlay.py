"""Apply a verified USB bundle only to an already-private Pi4 assembly tree."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE.parents[2]/'system/freebsd/image'))
from install import ordinary, relative, sha, json_bytes
from device_tree import parse_fdt, usb_node, string_list
from assemble_image import read_metalog, install_file, write_mtree

def apply_to_prepared(value, summary, board, bundle):
    """Call immediately after prepare_pi(), before makefs and final evidence.

    Mutates only value['work']/{root,efi,root.mtree}; returns USB metadata.
    It also updates summary/board in place and their installed JSON documents.
    """
    bundle=Path(bundle).resolve(strict=True)
    manifest_path=ordinary(bundle/'manifest.json'); manifest=json.loads(manifest_path.read_text())
    receipt=json.loads(ordinary(bundle/'receipt.json').read_text())
    if (manifest.get('format')!='DaOS-USB-ADB-image-overlay-v1'
            or receipt.get('format')!='DaOS-USB-ADB-integration-stage-v1' or receipt.get('status')!='staged'
            or receipt.get('manifest_sha256')!=sha(manifest_path)
            or manifest.get('base_state_sha256')!=value['base_state_sha256']
            or manifest.get('private_keys_staged') is not False): raise ValueError('USB overlay is unbound to this assembled source world')
    if (board.get('firmware_provenance') or {}).get('receipt_sha256')!=manifest.get('firmware_receipt_sha256'):
        raise ValueError('USB DTB derivation must bind the selected official firmware')
    work=Path(value['work']); root=work/'root'; efi=work/'efi'
    if root.is_symlink() or efi.is_symlink() or not root.is_dir() or not efi.is_dir(): raise ValueError('private prepared assembly trees required')
    maps=read_metalog(work/'root.mtree',root)
    boot={item['path']:dict(item) for item in board['boot_payload']}
    for tree in ('root','efi'):
        records=manifest.get(tree,[]); expected={}
        for record in records:
            rel=relative(record['path'])
            if rel in expected or record.get('type')!='file' or record.get('uid')!=0 or record.get('gid')!=0:
                raise ValueError('invalid USB overlay file metadata')
            expected[rel]=record
        actual={p.relative_to(bundle/tree).as_posix() for p in (bundle/tree).rglob('*') if p.is_file() or p.is_symlink()}
        if actual!=set(expected): raise ValueError('USB bundle has missing/unmanifested payload')
        for rel,record in expected.items():
            source=ordinary(bundle/tree/rel); data=source.read_bytes()
            if len(data)!=record['bytes'] or sha(source)!=record['sha256'] or record['mode'] not in {0o555,0o600,0o644,0o755}:
                raise ValueError('USB payload differs from staged manifest: '+rel)
            if rel.endswith('.dtb'):
                nodes,_=parse_fdt(data); props=nodes[usb_node(nodes)]
                if props.get('status')!=b'okay\0' or props.get('dr_mode')!=b'peripheral\0' or 'snps,dwc2' not in string_list(props['compatible']):
                    raise ValueError('USB boot candidate is not device mode')
            if tree=='root': install_file(root,maps,rel,data,record['mode'])
            else:
                target=efi/rel
                for parent in target.parents:
                    if parent==efi.parent: break
                    if parent.is_symlink(): raise ValueError('redirected EFI output')
                if target.is_symlink() or target.exists() and not target.is_file(): raise ValueError('EFI payload conflicts')
                target.parent.mkdir(parents=True,exist_ok=True)
                original=sha(target) if target.exists() else None
                if target.exists(): target.unlink()
                target.write_bytes(data)
                boot[rel]={'path':rel,'bytes':len(data),'sha256':sha(target),'usb_derived':True}
                if original: boot[rel]['derived_from_sha256']=original
    append=manifest['config_append']; suffix=ordinary(bundle/relative(append['path']))
    if sha(suffix)!=append['sha256']: raise ValueError('USB GPU configuration suffix changed')
    config=ordinary(efi/'config.txt'); old=sha(config); content=config.read_bytes().rstrip()+b'\n'+suffix.read_bytes()
    config.unlink(); config.write_bytes(content)
    boot['config.txt']={'path':'config.txt','bytes':len(content),'sha256':sha(config),'derived_from_sha256':old,'usb_device_tree_selected':True}
    usb={'format':manifest['format'],'manifest_sha256':sha(manifest_path),'integration_receipt_sha256':sha(bundle/'receipt.json'),
         'build_receipt_sha256':manifest['usb_build_receipt_sha256'],'identity':manifest['identity'],
         'authorized_public_key_fingerprints':manifest['authorized_public_key_fingerprints'],
         'source_world_usb_template':manifest['source_world_usb_template'],
         'transport_elf_closure':manifest['transport_elf_closure'],
         'native_usb_bulk_transport':True,'network_listener':False,'preauthorized_auth_required':True,
         'service_backgrounded':True,'physical_usb_verified':False,'windows_binding_verified':False}
    board['boot_payload']=sorted(boot.values(),key=lambda item:item['path']); board['usb_adb']=usb
    summary['board']=board; summary['usb_adb']=usb
    install_file(root,maps,'usr/share/DaOS/usb-adb-overlay.json',manifest_path.read_bytes())
    install_file(root,maps,'usr/share/DaOS/payload.json',json_bytes(summary))
    install_file(root,maps,'usr/share/DaOS/raspberrypi-board.json',json_bytes(board))
    write_mtree(root,maps,work/'root.mtree')
    return usb
