"""Launch the Retail-adapted DaOS native Qt/QML camera shell."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default='var/hf-device', help='dedicated camera journal/output directory for native/reference mode')
    parser.add_argument('--backend', choices=('native', 'reference', 'http'), default='native',
                        help='native authority by default; reference or HTTP attach mode must be selected explicitly')
    parser.add_argument('--endpoint', help='existing loopback camera API origin for --backend http')
    parser.add_argument('--ui-state-dir', help='local request-ID state directory; HTTP default: var/hf-device-ui')
    parser.add_argument('--native-command', help='explicit hf-camerad executable path')
    parser.add_argument('--screenshot', help='save one rendered PNG and exit (software desktop QA only)')
    parser.add_argument('--offscreen', action='store_true', help='use the Qt offscreen platform and software renderer')
    parser.add_argument('--kiosk', action='store_true', help='show the camera UI full screen on the selected Qt display')
    parser.add_argument('--app', choices=('camera', 'photos', 'recipes', 'settings', 'devices', 'diagnostics', 'system'), default='camera',
                        help='open a local DaOS app without submitting a capture')
    parser.add_argument('--interface', choices=('camera', 'console'), default='camera',
                        help='camera-first body UI by default; console selects the separate development workspace')
    parser.add_argument('--width', type=int, default=800, help='preview width, 800 through 3840; camera default 800')
    parser.add_argument('--height', type=int, default=600, help='preview height, 600 through 2160; camera default 600')
    args = parser.parse_args(argv)
    if not 800 <= args.width <= 3840 or not 600 <= args.height <= 2160:
        parser.error('preview dimensions must be 800..3840 by 600..2160')
    if args.backend == 'http' and not args.endpoint:
        parser.error('--backend http requires --endpoint')
    if args.endpoint and args.backend != 'http':
        parser.error('--endpoint is only used with --backend http')
    state_dir = Path(args.ui_state_dir or ('var/hf-device-ui' if args.backend == 'http' else args.data_dir))
    if args.offscreen:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        os.environ.setdefault('QT_QUICK_BACKEND', 'software')
        os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
        if os.name == 'nt':
            fonts = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
            if fonts.is_dir():
                os.environ.setdefault('QT_QPA_FONTDIR', str(fonts))
    from PyQt5.QtCore import QCoreApplication, QEvent, QTimer, QUrl
    from PyQt5.QtGui import QFont, QGuiApplication
    from PyQt5.QtQml import QQmlApplicationEngine
    from PyQt5.QtQuick import QQuickWindow  # Register the typed root-window wrapper.
    from controller import CameraController, make_client_factory
    from shell_model import ShellController
    from apps_controller import CameraAppsController
    app = QGuiApplication(sys.argv[:1])
    app.setFont(QFont('Segoe UI' if os.name == 'nt' else 'Sans Serif', 10))
    app.setApplicationName('DaOS Camera')
    app.setOrganizationName('HassleFree')
    command = [args.native_command] if args.native_command else None
    factory = lambda: make_client_factory(args.backend, args.data_dir, command, endpoint=args.endpoint)()
    controller = CameraController(factory, backend=args.backend,
                                  state_path=state_dir / 'device-ui.json')
    shell = ShellController(state_dir / 'shell-preferences.json', parent=app)
    if args.interface == 'camera' and not (state_dir / 'shell-preferences.json').exists():
        shell.setPreference('theme', 'dark')
    apps = CameraAppsController(controller, state_dir, parent=app)
    shell.navigate(args.app)
    theme = json.loads(Path(__file__).with_name('retail-theme.json').read_text(encoding='utf-8'))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('camera', controller)
    engine.rootContext().setContextProperty('cameraShell', shell)
    engine.rootContext().setContextProperty('cameraApps', apps)
    engine.rootContext().setContextProperty('retailTheme', theme)
    runtime_label = ('DaOS BSD Unix · FreeBSD ' + platform.release() + ' · ' + platform.machine()
                     if platform.system() == 'FreeBSD' else platform.system() + ' UI preview')
    engine.rootContext().setContextProperty('runtimeOSLabel', runtime_label)
    qml_name = 'Console.qml' if args.interface == 'console' else 'Main.qml'
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name(qml_name))))
    if not engine.rootObjects():
        controller.shutdown()
        return 2
    window = engine.rootObjects()[0]
    window.resize(args.width, args.height)
    if args.kiosk:
        screen = window.screen() or app.primaryScreen()
        if screen is not None:
            window.setGeometry(screen.geometry())
        window.showFullScreen()
    app.aboutToQuit.connect(controller.shutdown)
    if args.screenshot:
        pending_grabs = []
        if args.interface == 'console' and args.app == 'camera':
            QTimer.singleShot(200, controller.togglePreview)
        def screenshot():
            destination = Path(args.screenshot).resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            grab = window.contentItem().grabToImage()
            if grab is None:
                app.exit(2)
                return
            pending_grabs.append(grab)
            def saved():
                success = grab.image().save(str(destination))
                app.exit(0 if success else 2)
            grab.ready.connect(saved)
        QTimer.singleShot(2500, screenshot)
        QTimer.singleShot(10000, lambda: app.exit(2))
    try:
        return app.exec_()
    finally:
        controller.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == '__main__':
    raise SystemExit(main())
