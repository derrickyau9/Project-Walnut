"""A local PTY terminal rendered by Qt Quick, with a replaceable pyte parser."""
import errno
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).with_name('vendor')))
import pyte
from PyQt5.QtCore import QCoreApplication,QEvent,QRectF,QSocketNotifier,Qt,QTimer,pyqtProperty,pyqtSignal,pyqtSlot
from PyQt5.QtGui import QColor,QFont,QFontMetricsF,QGuiApplication
from PyQt5.QtQuick import QQuickItem,QQuickPaintedItem


class TerminalSurface(QQuickPaintedItem):
    changed=pyqtSignal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setFlag(QQuickItem.ItemAcceptsInputMethod,True)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setActiveFocusOnTab(True)
        self.font=QFont('monospace');self.font.setStyleHint(QFont.TypeWriter);self.font.setPixelSize(14)
        metric=QFontMetricsF(self.font)
        self.cell_width=max(7,metric.horizontalAdvance('M'));self.cell_height=max(18,metric.height()+2)
        self.screen=pyte.HistoryScreen(80,20,history=1000)
        self.stream=pyte.ByteStream(self.screen)
        self.master=None;self.process=None;self.notifier=None;self.writer=None;self.pending=bytearray()
        self._running=False
        self._message='Tap Open to start a local camera-user shell.'
        self.widthChanged.connect(self.resize_terminal);self.heightChanged.connect(self.resize_terminal)
        self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.check_process)
        # The closure owns only Python resources, and remains safe when the item dies.
        self.destroyed.connect(lambda *_:self.cleanup(False))

    @pyqtProperty(bool,constant=True)
    def terminalInput(self):return True
    @pyqtProperty(bool,notify=changed)
    def running(self):return self._running
    @pyqtProperty(str,notify=changed)
    def message(self):return self._message

    @pyqtSlot()
    def open(self):
        try:self.open_session()
        except Exception as error:
            self.cleanup();self._message='Could not open terminal: '+str(error)[:100];self.changed.emit()

    def open_session(self):
        if self._running:return
        if os.name!='posix':
            self._message='The device terminal requires a POSIX system.';self.changed.emit();return
        import pty,fcntl
        self.cleanup()
        self.screen.reset()
        master,slave=pty.openpty()
        self.master=master
        os.set_blocking(master,False)
        home=os.environ.get('HOME','/var/DaOS/home/camera')
        env={key:os.environ[key] for key in ('HOME','USER','LOGNAME','LANG') if key in os.environ}
        env.update(PATH='/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin',TERM='xterm',
                   SHELL='/bin/sh',PS1='DaOS$ ',LANG='C.UTF-8')
        try:
            self.process=subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('terminal_exec.py'))],
                stdin=slave,stdout=slave,stderr=slave,pass_fds=(slave,),start_new_session=True,
                close_fds=True,cwd=home if Path(home).is_dir() else '/',env=env)
        finally:os.close(slave)
        self.master=master
        self.notifier=QSocketNotifier(master,QSocketNotifier.Read,self);self.notifier.activated.connect(self.read_ready)
        self.writer=QSocketNotifier(master,QSocketNotifier.Write,self);self.writer.setEnabled(False);self.writer.activated.connect(self.flush)
        self._running=True;self._message='camera user · /bin/sh'
        self.resize_terminal();self.timer.start();self.forceActiveFocus();self.changed.emit();self.update()

    def read_ready(self,*_):
        if self.master is None:return
        for _ in range(8):
            try:data=os.read(self.master,8192)
            except BlockingIOError:break
            except OSError:break
            if not data:break
            self.stream.feed(data)
        self.update()
        QGuiApplication.inputMethod().update(Qt.ImSurroundingText|Qt.ImCursorPosition)

    def flush(self,*_):
        if self.master is None:return
        try:
            while self.pending:
                n=os.write(self.master,self.pending)
                del self.pending[:n]
        except BlockingIOError:pass
        except OSError:self.pending.clear()
        if self.writer:self.writer.setEnabled(bool(self.pending))

    def send(self,data):
        if self.master is not None and len(self.pending)+len(data)<=65536:
            self.screen.next_page()
            self.pending.extend(data);self.flush()

    @pyqtSlot(str)
    def sendKey(self,name):
        keys={'interrupt':b'\x03','tab':b'\t','escape':b'\x1b','up':b'\x1b[A','down':b'\x1b[B',
              'left':b'\x1b[D','right':b'\x1b[C','enter':b'\r','backspace':b'\x7f','eof':b'\x04'}
        if name in keys:self.send(keys[name])
        self.forceActiveFocus()

    @pyqtSlot(int)
    def scroll(self,direction):
        if direction<0:self.screen.prev_page()
        else:self.screen.next_page()
        self.update()

    def resize_terminal(self):
        columns=max(20,min(200,int(max(self.width()-12,140)/self.cell_width)))
        rows=max(3,min(80,int(max(self.height()-8,54)/self.cell_height)))
        if (rows,columns)!=(self.screen.lines,self.screen.columns):self.screen.resize(lines=rows,columns=columns)
        if self.master is not None:
            import fcntl,termios
            try:fcntl.ioctl(self.master,termios.TIOCSWINSZ,struct.pack('HHHH',rows,columns,0,0))
            except OSError:pass
        self.update()

    def paint(self,painter):
        try:self.paint_screen(painter)
        except Exception as error:
            painter.fillRect(QRectF(0,0,self.width(),self.height()),QColor('#000000'))
            painter.setPen(QColor('#ffffff'))
            painter.drawText(QRectF(8,8,self.width()-16,60),Qt.AlignLeft|Qt.TextWordWrap,'Terminal drawing error: '+str(error)[:100])

    def paint_screen(self,painter):
        painter.fillRect(QRectF(0,0,self.width(),self.height()),QColor('#000000'));painter.setFont(self.font)
        ascent=QFontMetricsF(self.font).ascent()
        for row in range(self.screen.lines):
            for col,cell in self.screen.buffer[row].items():
                if cell.reverse or cell.bg!='default':
                    painter.fillRect(QRectF(6+col*self.cell_width,4+row*self.cell_height,self.cell_width,self.cell_height),QColor('#dddddd' if cell.reverse else '#333333'))
                if not cell.data or cell.data==' ':continue
                painter.setPen(QColor('#000000' if cell.reverse else '#ffffff'))
                painter.drawText(QRectF(6+col*self.cell_width,4+row*self.cell_height,self.cell_width*2,self.cell_height),Qt.AlignLeft|Qt.AlignTop,cell.data)
        if self.property('activeFocus') and not self.screen.cursor.hidden:
            painter.setPen(QColor('#ffffff'))
            painter.drawRect(QRectF(6+self.screen.cursor.x*self.cell_width,4+self.screen.cursor.y*self.cell_height,self.cell_width,self.cell_height-1))

    def mousePressEvent(self,event):
        self.forceActiveFocus();self.update();event.accept()

    def inputMethodQuery(self,query):
        if query==Qt.ImEnabled:return self._running
        if query==Qt.ImHints:return int(Qt.ImhNoPredictiveText|Qt.ImhNoAutoUppercase)
        if query==Qt.ImSurroundingText:return self.screen.display[self.screen.cursor.y]
        if query==Qt.ImCursorPosition:return len(self.screen.display[self.screen.cursor.y][:self.screen.cursor.x].encode('utf-16-le'))//2
        if query==Qt.ImAnchorPosition:return self.inputMethodQuery(Qt.ImCursorPosition)
        if query==Qt.ImCursorRectangle:return QRectF(6+self.screen.cursor.x*self.cell_width,4+self.screen.cursor.y*self.cell_height,self.cell_width,self.cell_height)
        return None

    def inputMethodEvent(self,event):
        if event.commitString():self.send(event.commitString().encode('utf-8'))
        event.accept()

    def keyPressEvent(self,event):
        keys={Qt.Key_Return:'enter',Qt.Key_Enter:'enter',Qt.Key_Backspace:'backspace',Qt.Key_Left:'left',
              Qt.Key_Right:'right',Qt.Key_Up:'up',Qt.Key_Down:'down',Qt.Key_Tab:'tab',Qt.Key_Escape:'escape'}
        if event.modifiers() & Qt.ControlModifier and Qt.Key_A<=event.key()<=Qt.Key_Z:self.send(bytes([event.key()-Qt.Key_A+1]))
        elif event.key() in keys:self.sendKey(keys[event.key()])
        elif event.text():self.send(event.text().encode('utf-8'))
        event.accept()

    def check_process(self):
        if self.process and self.process.poll() is not None:
            self.read_ready();self._message='Shell exited. Tap Open to start again.';self.cleanup();self.changed.emit()

    def cleanup(self,touch_qt=True):
        if touch_qt:
            self.timer.stop()
            for notifier in (self.notifier,self.writer):
                if notifier:notifier.setEnabled(False);notifier.deleteLater()
        self.notifier=self.writer=None
        if self.master is not None:
            os.close(self.master);self.master=None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.process=None;self._running=False;self.pending.clear()

    @pyqtSlot()
    def close(self):
        self.cleanup();self.changed.emit();self.update()
