"""Validate matching real Qt galleries and copy PNG bytes unchanged to docs."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import tarfile
import uuid

ROOT=Path(__file__).resolve().parents[1]
BRANDS=re.compile(r'\b(?:Hasselblad|Leica|Fujifilm|Sony|Canon|Nikon|Panasonic|Olympus|Pentax|Lumix)\b',re.I)
OLD_DISPLAY_BRAND=re.compile(r'\b(?:HassleFree|HasselFree)\b|无扰计划')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def load(directory,language):
    report=json.loads((directory/'capture.json').read_text(encoding='utf-8'))
    assert report['format']=='Project-Walnut-readme-gallery-v1'
    assert report['passed'] and report['language']==language
    assert not report['untranslated_development_only'] and not report.get('development_prefix_only')
    assert not report['source_changed_during_capture']
    assert report['control_qa']['passed'] and report['control_qa']['separate_session']
    assert report['control_qa']['capture_actions']==len(report['control_qa']['captures'])==3
    assert all(item['status']=='complete' and item['bytes']>0 and re.fullmatch('[a-f0-9]{64}',item['sha256']) for item in report['control_qa']['captures'])
    assert report['display_capture_actions']==0 and report['display_library_id']!=report['control_qa']['library_id']
    assert report['display_original_JPEG_bytes_unchanged']
    imports=report['display_photo_imports']
    assert len(imports)==3 and {item['file'] for item in imports}=={'coast.jpg','campus-building.jpg','campus-trees.jpg'}
    for item in imports:
        assert item['media_type']=='image/jpeg' and item['method']=='MediaWorkspace.importPaths / native copyImport'
        assert digest(ROOT/'ui/on-device/demo-images'/item['file'])==item['sha256']
        assert item['neutral_source_sha256']==item['sha256']
    assert report['explicit_private_data_root']
    assert report['viewfinder_sample']['sha256']==digest(ROOT/'ui/on-device/demo-images'/report['viewfinder_sample']['file'])
    assert report['physical_IMU_measurements'] is False
    assert report['level_examples']==[
        {'key':'01b-level-unavailable','state':'unavailable','roll_deg':None,'pitch_deg':None,'physical_IMU':False},
        {'key':'01c-level-demo-aligned','state':'demo','roll_deg':0.,'pitch_deg':0.,'physical_IMU':False},
        {'key':'01d-level-demo-tilted','state':'demo','roll_deg':-6.,'pitch_deg':3.,'physical_IMU':False}]
    assert report['runtime']['qpa']=='offscreen'
    assert report['physical_camera_display_network_or_GPIO_validated'] is False
    assert len(report['frames'])==50
    for name,expected in report['source_sha256'].items():assert digest(ROOT/name)==expected,name
    for name,expected in report['native_source_sha256'].items():assert digest(ROOT/name)==expected,name
    for frame in report['frames']:
        assert re.fullmatch(r'[a-z0-9-]+\.png',frame['file'])
        path=directory/frame['file'];raw=path.read_bytes()
        assert digest(path)==frame['sha256']
        assert raw[:8]==b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II',raw[16:24])==(frame['width'],frame['height'])
        assert not any(BRANDS.search(text) for text in frame['visible_text'])
        assert not any(OLD_DISPLAY_BRAND.search(text) for text in frame['visible_text']),'Old visible project branding: '+frame['key']
        if frame['selected_photo_uuid']:
            assert frame['selected_photo_uuid'] in {item['photo_uuid'] for item in imports}
            assert frame['selected_photo_media_type']=='image/jpeg'
        if language=='en_US':assert not any(re.search('[\u3400-\u9fff]',text) for text in frame['visible_text']),'Untranslated visible English UI: '+frame['key']
    errors=[warning for warning in report['qml_warnings'] if re.search(r'ReferenceError|TypeError|is not a type|Cannot assign|\bError:',warning)]
    assert not errors,errors
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--english',type=Path,required=True)
    parser.add_argument('--chinese',type=Path,required=True)
    parser.add_argument('--destination',type=Path,default=ROOT/'docs/screenshots')
    parser.add_argument('--snippet',type=Path,required=True,help='new Markdown file for the root README editor')
    parser.add_argument('--package',type=Path,help='optional factory application bundle for exact UI-source correspondence')
    parser.add_argument('--package-sha256',help='required expected hash when binding a bundle')
    parser.add_argument('--replace-owned-gallery',action='store_true',help='atomically replace only this tool\'s docs/screenshots, retaining its previous version under build')
    args=parser.parse_args(argv)
    en=load(args.english,'en_US');zh=load(args.chinese,'zh_CN')
    assert en['source_sha256']==zh['source_sha256']
    assert en['native_source_sha256']==zh['native_source_sha256']
    assert en['native_sha256']==zh['native_sha256']
    assert [f['key'] for f in en['frames']]==[f['key'] for f in zh['frames']]
    package_binding=None
    if args.package is not None:
        assert re.fullmatch('[0-9a-f]{64}',args.package_sha256 or '')
        assert digest(args.package)==args.package_sha256
        with tarfile.open(args.package) as archive:
            package_manifest=json.load(archive.extractfile('manifest.json'))
            package_files={item['path']:item for item in package_manifest['files']}
            correspondence={}
            for name,expected in en['source_sha256'].items():
                if not name.startswith('ui/on-device/'):continue
                packaged='opt/hasslefree/'+name
                assert packaged in package_files,packaged
                assert package_files[packaged]['sha256']==expected,packaged
                raw=archive.extractfile('payload/'+packaged).read()
                assert hashlib.sha256(raw).hexdigest()==expected
                correspondence[name]=expected
        package_binding={'package':args.package.name,'sha256':args.package_sha256,
                         'version':package_manifest['version'],'UI_files_exactly_matched':len(correspondence),
                         'UI_source_correspondence':correspondence,
                         'package_ARM64_authority_sha256':package_files['opt/hasslefree/bin/hf-camerad']['sha256'],
                         'gallery_Windows_authority_sha256':en['native_sha256'],
                         'platform_binaries_are_different':True,
                         'ARM64_package_runtime_not_claimed_by_desktop_screenshots':True}
    final_destination=args.destination.absolute()
    replacing=final_destination.exists()
    if replacing:
        assert args.replace_owned_gallery
        assert final_destination==ROOT/'docs/screenshots' and final_destination.resolve()==final_destination
        existing=json.loads((final_destination/'manifest.json').read_text(encoding='utf-8'))
        assert existing['format'] in ('Project-HassleFree-bilingual-UI-gallery-v1','Project-Walnut-bilingual-UI-gallery-v1')
        destination=ROOT/'build'/('readme-gallery-assembly-'+uuid.uuid4().hex)
    else:destination=final_destination
    destination.mkdir(parents=True,exist_ok=False)
    rows=[];entries=[]
    for language,source,report in [('en_US',args.english,en),('zh_CN',args.chinese,zh)]:
        (destination/language).mkdir()
        for frame in report['frames']:
            target=destination/language/frame['file'];shutil.copyfile(source/frame['file'],target)
            assert digest(target)==frame['sha256']
        # Only the canonical manifest is linked from docs; retain all runtime
        # provenance in a separate source-bound receipt without duplicating PNGs.
        (destination/(language+'-capture.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for left,right in zip(en['frames'],zh['frames']):
        assert left['features']==right['features']
        title_en=left['title_en'];title_zh=left['title_zh']
        assert not BRANDS.search(title_en+' '+title_zh)
        file=left['file'];key=left['key']
        rows.extend([f'| **{title_en}** | **{title_zh}** |',
                     f'| ![{title_en} — English desktop preview](docs/screenshots/en_US/{file}) | ![{title_zh} — 中文桌面预览](docs/screenshots/zh_CN/{file}) |'])
        entries.append({'key':key,'title_en':title_en,'title_zh':title_zh,'features':left['features'],
                        'en_US':{'file':'en_US/'+file,'sha256':left['sha256']},
                        'zh_CN':{'file':'zh_CN/'+file,'sha256':right['sha256']},
                        'width':left['width'],'height':left['height']})
    manifest={'format':'Project-Walnut-bilingual-UI-gallery-v1','passed':True,
              'scope':'Actual Windows Qt offscreen rendering of user-supplied sample JPEGs, imported through the real native library; HF-Sim capture QA uses a separate session. Not physical Pi/display/sensor validation.',
              'language_pairs':len(entries),'images':len(entries)*2,
              'same_feature_keys_and_sources':True,'PNG_bytes_unchanged':True,
              'viewfinder_sample':en['viewfinder_sample'],
              'display_source_JPEGs':[{key:item[key] for key in ('file','sha256','bytes','media_type','method')} for item in en['display_photo_imports']],
              'separate_control_QA_captures_per_language':3,'display_session_RAW_captures':0,
              'source_JPEG_bytes_unchanged_after_editing':True,
              'level_examples':en['level_examples'],'physical_IMU_measurements':False,
              'runtime':en['runtime'],'native_sha256':en['native_sha256'],
              'source_sha256':en['source_sha256'],'native_source_sha256':en['native_source_sha256'],
              'physical_camera_display_network_or_GPIO_validated':False,
              'limitations':en['limitations'],'entries':entries,
              'source_capture_receipts':{language:{'file':language+'-capture.json','sha256':digest(destination/(language+'-capture.json'))}
                                         for language in ('en_US','zh_CN')},
              'capture_driver_sha256':digest(ROOT/'tools/capture_readme_gallery.py'),
              'assembly_driver_sha256':digest(Path(__file__))}
    if package_binding is not None:manifest['factory_application_package_binding']=package_binding
    (destination/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    intro=('These are actual Project Walnut desktop Qt renders, in matching English and Simplified Chinese views. '
           'The viewfinder and library show three user-supplied sample JPEG photographs, imported through the real photo workflow; they were not captured by HF-Sim. '
           'Three HF-Sim control captures run in a separate test session and never appear in this display library. '
           'Level DEMO angles are explicit display-test inputs, not IMU measurements. '
           'These views do not establish physical display, sensor or wireless operation. Video Q remains a layout.\n\n'
           '以下为 Project Walnut 的真实桌面 Qt 渲染，中英文功能逐项对应。取景和照片库使用用户提供的三张示例 JPEG，'
           '经真实照片工作流导入、查看和编辑；它们不是 HF-Sim 拍摄结果。三次 HF-Sim 控制验收在独立会话完成，其 RAW 不进入展示照片库。'
           '水平仪 DEMO 角度为显式显示测试输入，不是 IMU 实测。'
           '截图不代表实体屏幕、传感器或无线验证；视频 Q 仍为布局。\n\n')
    table='| English | 简体中文 |\n| --- | --- |\n'+'\n'.join(rows)+'\n'
    args.snippet.parent.mkdir(parents=True,exist_ok=True)
    with args.snippet.open('x',encoding='utf-8') as file:file.write(intro+table)
    local_table=table.replace('(docs/screenshots/','(')
    readme=('# Current UI / 当前界面\n\n'+intro+
            f'{len(entries)} matching views per language; {len(entries)*2} unchanged PNG images. '
            f'每种语言 {len(entries)} 个对应界面，共 {len(entries)*2} 张原始 PNG。\n\n'+
            '[Source-bound manifest / 源码与图像清单](manifest.json)\n\n'+local_table+
            '\nReproduce with `tools/capture_readme_gallery.py --data-root <new-private-neutral-path> --demo-image ui/on-device/demo-images/coast.jpg` once per language, then `tools/assemble_readme_gallery.py`. '
            'The capture driver creates and removes its own new synthetic test data; it never reads existing camera data.\n')
    (destination/'README.md').write_text(readme,encoding='utf-8')
    if replacing:
        backup=ROOT/'build'/('readme-gallery-previous-'+digest(final_destination/'manifest.json')[:12])
        assert not backup.exists() and not backup.is_symlink()
        assert final_destination.resolve().is_relative_to(ROOT.resolve())
        assert destination.resolve().is_relative_to((ROOT/'build').resolve())
        assert backup.parent.resolve()==(ROOT/'build').resolve()
        final_destination.rename(backup)
        try:destination.rename(final_destination)
        except BaseException:
            backup.rename(final_destination)
            raise
        destination=final_destination
    print(json.dumps({'pairs':len(entries),'images':len(entries)*2,'manifest_sha256':digest(destination/'manifest.json'),
                      'destination':str(destination),'snippet':str(args.snippet)},indent=2))


if __name__=='__main__':main()
