import argparse,json,uuid
from .client import CameraClient,USBTransport,SocketTransport,HTTPTransport

def main():
    parser=argparse.ArgumentParser(description='DaOS controlled tethering. Never automatically retries an exposure.')
    transport=parser.add_mutually_exclusive_group(required=True)
    transport.add_argument('--adb');transport.add_argument('--socket');transport.add_argument('--https')
    parser.add_argument('--serial',default='DaOS-USB-ADB-DEVELOPMENT');parser.add_argument('--port',type=int,default=5049)
    parser.add_argument('--certificate');parser.add_argument('--pin')
    parser.add_argument('command',choices=['state','photos','capture','query','download'])
    parser.add_argument('--request-id');parser.add_argument('--photo');parser.add_argument('--output')
    args=parser.parse_args()
    if args.adb:link=USBTransport(args.adb,args.serial,args.port)
    elif args.socket:link=SocketTransport(args.socket)
    else:
        if not args.certificate or not args.pin:parser.error('HTTPS requires --certificate and --pin')
        link=HTTPTransport(args.https,args.pin,cafile=args.certificate)
    with CameraClient(link) as camera:
        if args.command=='state':value=camera.state()
        elif args.command=='photos':value=camera.photos()
        elif args.command=='capture':
            if not args.request_id:parser.error('Persist a UUID first, then pass --request-id; query that UUID after interruption')
            camera.acquire();value=camera.capture(args.request_id)
        elif args.command=='query':value=camera.query(args.request_id)
        else:
            if not args.photo or not args.output:parser.error('download requires --photo and --output')
            value=camera.download(args.photo,args.output)
        print(json.dumps(value,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
