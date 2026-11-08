"""Explicit, auditable SDK separation for the DaOS camera appliance runtime.

No installation is changed. All decisions concern declared package entries in
a NEW collection; shared libraries, dynamic resources and legal texts win over
SDK selectors. A full package/SDK profile remains a separate option.
"""
from __future__ import annotations

import posixpath
import re

POLICY = {
    'id': 'camera-runtime-v1', 'version': 1,
    'scope': 'Camera appliance runtime; not the full installed package/SDK contents.',
    'preserve': ['all shared libraries and their retained aliases', 'Qt QML/plugins/resources',
                 'Python standard-library/package runtime files', 'fonts/translations',
                 'all package license/COPYING/NOTICE/copyright texts'],
    'omit': ['static .a archives', 'declared SDK headers outside protected runtime trees',
             'documentation/man outside legal-text exceptions', 'CMake build metadata',
             'llvm19 compiler/tool executables and declared external *19 wrappers',
             'symlink aliases whose SDK targets are omitted'],
}


def legal_text(path):
    lower=path.lower(); name=posixpath.basename(lower)
    return '/share/licenses/' in '/'+lower or any(word in name for word in ('license','licence','copying','notice','copyright','credits','authors'))


def shared_library(path):
    return re.search(r'\.so(?:\.|$)',posixpath.basename(path)) is not None


def runtime_resource(path):
    parts=path.split('/')
    return (any(part in ('qml','plugins','resources','translations','fonts') for part in parts)
            or any(parts[index]=='lib' and parts[index+1].startswith('python3.') for index in range(len(parts)-1)))


def sdk_reason(item):
    path=item['path']; parts=path.split('/')
    if legal_text(path) or item.get('legal_text_detected') is True or shared_library(path):return None
    if path.startswith('usr/local/llvm19/lib/clang/'):
        return None  # Builtin/JIT resource headers and runtimes are not SDK include/.
    if path.endswith('.a'):return 'sdk-static-archive'
    if runtime_resource(path):return None
    if path.startswith('usr/local/include/') or path.startswith('usr/local/llvm19/include/'):
        return 'sdk-headers'
    if '/share/doc/' in '/'+path or '/share/man/' in '/'+path or path.startswith('usr/local/man/'):
        return 'sdk-documentation'
    if '/cmake/' in '/'+path.lower() or path.lower().endswith('.cmake'):
        return 'sdk-cmake-build-metadata'
    if 'llvm19' in item.get('packages',[]):
        if path.startswith('usr/local/llvm19/bin/'):
            return 'llvm19-sdk-tool'
        if item['type']=='file' and path.startswith('usr/local/bin/') and '/' not in path[len('usr/local/bin/'):] and path.endswith('19'):
            return 'llvm19-sdk-wrapper'
    return None


def link_target(path,target):
    joined=target if target.startswith('/') else posixpath.join(posixpath.dirname('/'+path),target)
    normalized=posixpath.normpath(joined)
    if not normalized.startswith('/') or '\0' in normalized:
        raise ValueError('invalid declared runtime link target')
    return normalized.lstrip('/')


def curate(candidates,profile):
    if profile not in ('camera-runtime-v1','full-package-v1'):
        raise ValueError('unsupported explicit runtime profile')
    all_entries={item['path']:item for item in candidates}
    if len(all_entries)!=len(candidates):raise ValueError('duplicate declared package path')
    reasons={}
    if profile=='camera-runtime-v1':
        for path,item in all_entries.items():
            reason=sdk_reason(item)
            if reason:reasons[path]={'reason':reason}
        for path,item in all_entries.items():
            if path in reasons or item['type']!='link':continue
            current=path; visited=set()
            for _ in range(64):
                if current in visited:raise ValueError('declared package link cycle')
                visited.add(current); entry=all_entries.get(current)
                if entry is None or entry['type']!='link':break
                current=link_target(current,entry['link'])
                if current in reasons:
                    reasons[path]={'reason':'sdk-alias-to-omitted-target','target':current,
                                   'target_reason':reasons[current]['reason']};break
            else:raise ValueError('declared package link depth exceeds bound')
    kept=[dict(item) for path,item in sorted(all_entries.items()) if path not in reasons]
    omitted=[dict(all_entries[path],policy='camera-runtime-v1',**detail) for path,detail in sorted(reasons.items())]
    groups={}
    for item in omitted:
        value=groups.setdefault(item['reason'],{'entries':0,'bytes':0})
        value['entries']+=1;value['bytes']+=item.get('bytes',0)
    stats={'declared_entries':len(candidates),'declared_bytes':sum(item.get('bytes',0) for item in candidates),
           'kept_entries':len(kept),'kept_bytes':sum(item.get('bytes',0) for item in kept),
           'omitted_entries':len(omitted),'omitted_bytes':sum(item.get('bytes',0) for item in omitted),'groups':groups}
    return kept,omitted,stats
