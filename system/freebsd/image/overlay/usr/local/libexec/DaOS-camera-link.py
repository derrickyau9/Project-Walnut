#!/usr/local/bin/python3.12
"""Fixed USB stdio bridge to the existing unprivileged UI broker."""
import os,socket,sys,threading

def main():
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(15);connection.connect('/var/DaOS/ui/camera-link.sock');connection.settimeout(None)
        def upload():
            try:
                while True:
                    block=os.read(0,65536)
                    if not block:break
                    connection.sendall(block)
            finally:
                try:connection.shutdown(socket.SHUT_WR)
                except OSError:pass
        threading.Thread(target=upload,daemon=True).start()
        while True:
            block=connection.recv(65536)
            if not block:break
            sys.stdout.buffer.write(block);sys.stdout.buffer.flush()
if __name__=='__main__':main()
