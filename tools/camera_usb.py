"""Windows USB camera tool. Reuses only the dedicated, authorized DaOS ADB server."""
import argparse,importlib.util,json,os
from pathlib import Path
import sys
sys.dont_write_bytecode=True

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
sys.path.insert(0,str(HERE))
from daos.camera_link import CameraClient,USBTransport

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools',type=Path,default=Path(os.environ.get('DAOS_ADB_TOOLS','F:/Project HasselFree/build/DaOS_USB_Development/host-tools')))
    parser.add_argument('--port',type=int,default=5049);parser.add_argument('--serial',default='DaOS-USB-ADB-DEVELOPMENT')
    parser.add_argument('action',choices=['state','photos','capture','query','download'])
    parser.add_argument('--request-id');parser.add_argument('--photo');parser.add_argument('--output')
    args=parser.parse_args();connector=HERE/'connect_usb.py'
    if not connector.exists():connector=HERE.parent/'system/adb/connect_usb.py'
    spec=importlib.util.spec_from_file_location('daos_usb_connect',connector);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    tools=args.tools.resolve(strict=True);adb=tools/'platform-tools'/('adb.exe' if os.name=='nt' else 'adb')
    module.ensure_server(tools,adb,args.port,args.serial)
    with CameraClient(USBTransport(adb,args.serial,args.port)) as camera:
        if args.action=='state':value=camera.state()
        elif args.action=='photos':value=camera.photos()
        elif args.action=='query':value=camera.query(args.request_id)
        elif args.action=='capture':
            if not args.request_id:parser.error('请先保存UUID，再传入--request-id；中断后查询原UUID')
            camera.acquire()
            try:value=camera.capture(args.request_id)
            finally:
                # A CLI process cannot heartbeat its lease after exit. Release
                # without retrying an uncertain exposure or hiding its error.
                try:camera.release()
                except (OSError,RuntimeError):pass
        else:
            if not args.photo or not args.output:parser.error('download需要--photo和新的--output路径')
            value=camera.download(args.photo,args.output)
        print(json.dumps(value,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
