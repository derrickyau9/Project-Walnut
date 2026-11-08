"""Compile display catalogs to native Qt QM resources; no Python translation callbacks."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
UI=ROOT/'ui/on-device'


def build(lrelease=None):
    compiler=lrelease or shutil.which('lrelease') or shutil.which('lrelease-qt5')
    if not compiler:raise RuntimeError('Qt lrelease is required to rebuild interface catalogs')
    contexts=['']+sorted(path.stem for path in UI.glob('*.qml'))
    manifest={'format':'DaOS-native-Qt-display-catalogs-v1','contexts':contexts,'files':{}}
    with tempfile.TemporaryDirectory(prefix='daos-qt-catalog-') as work:
        for language in ('en_US','zh_CN'):
            source=UI/'locales'/(language+'.json')
            entries=json.loads(source.read_text(encoding='utf-8'))
            doc=ET.Element('TS',version='2.1',language=language)
            for context in contexts:
                group=ET.SubElement(doc,'context');ET.SubElement(group,'name').text=context
                for key,value in sorted(entries.items()):
                    message=ET.SubElement(group,'message')
                    ET.SubElement(message,'source').text=key
                    ET.SubElement(message,'translation').text=value
            ts=Path(work)/(language+'.ts')
            ET.ElementTree(doc).write(ts,encoding='utf-8',xml_declaration=True)
            output=UI/'locales'/(language+'.qm')
            subprocess.run([compiler,'-silent',str(ts),'-qm',str(output)],check=True)
            manifest['files'][language]={'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'qm_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'entries':len(entries)}
    (UI/'locales/manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--lrelease');args=p.parse_args()
    result=build(args.lrelease)
    print('Built native Qt catalogs: '+', '.join('%s=%d'%(key,row['entries'])for key,row in result['files'].items()))
