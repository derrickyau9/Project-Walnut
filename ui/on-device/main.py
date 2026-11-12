"""Launch the Retail-adapted DaOS native Qt/QML camera shell."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time

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
    parser.add_argument('--language', choices=('en_US','zh_CN'), help='interface language; saved as a local system preference')
    parser.add_argument('--demo-image', type=Path, help='display a local JPEG sample in the viewfinder; captured RAW still comes from the camera authority')
    attitude=parser.add_mutually_exclusive_group()
    attitude.add_argument('--attitude-file',type=Path,help='optional local fresh attitude JSON; enable the level in Display settings')
    attitude.add_argument('--demo-attitude',nargs=2,type=float,metavar=('ROLL','PITCH'),help='show explicitly labeled DEMO level angles; never IMU measurements')
    parser.add_argument('--screenshot', help='save one rendered PNG and exit (software desktop QA only)')
    parser.add_argument('--offscreen', action='store_true', help='use the Qt offscreen platform and software renderer')
    parser.add_argument('--kiosk', action='store_true', help='show the camera UI full screen on the selected Qt display')
    parser.add_argument('--app', choices=('camera', 'photos', 'recipes', 'settings', 'devices', 'diagnostics', 'system'), default='camera',
                        help='open a local DaOS app without submitting a capture')
    parser.add_argument('--interface', choices=('camera', 'console'), default='camera',
                        help='camera-first body UI by default; console selects the separate development workspace')
    parser.add_argument('--width', type=int, default=800, help='preview width, 800 through 3840; camera default 800')
    parser.add_argument('--height', type=int, default=480, help='preview height, 480 through 2160; camera default 480')
    args = parser.parse_args(argv)
    if not 800 <= args.width <= 3840 or not 480 <= args.height <= 2160:
        parser.error('preview dimensions must be 800..3840 by 480..2160')
    if args.backend == 'http' and not args.endpoint:
        parser.error('--backend http requires --endpoint')
    if args.endpoint and args.backend != 'http':
        parser.error('--endpoint is only used with --backend http')
    if args.demo_image and args.interface != 'camera':
        parser.error('--demo-image requires --interface camera')
    state_dir = Path(args.ui_state_dir or ('var/hf-device-ui' if args.backend == 'http' else args.data_dir))
    cache_dir = state_dir / 'cache'
    cache_dir.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(cache_dir.resolve())
    os.environ.setdefault('QML_DISK_CACHE_PATH',str(cache_dir.resolve()/'qml'))
    os.environ.setdefault('QT_SHADER_CACHE_PATH',str(cache_dir.resolve()/'shaders'))
    os.environ.setdefault('XDG_CACHE_HOME',str(cache_dir.resolve()))
    if args.offscreen:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        os.environ.setdefault('QT_QUICK_BACKEND', 'software')
        os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
        if os.name == 'nt':
            fonts=Path(__file__).with_name('fonts')
            if fonts.is_dir():os.environ.setdefault('QT_QPA_FONTDIR',str(fonts))
    from PyQt5.QtCore import QCoreApplication, QEvent, QTimer, QUrl
    from PyQt5.QtGui import QFont, QFontDatabase, QGuiApplication
    from PyQt5.QtQml import QQmlApplicationEngine, qmlRegisterType
    from PyQt5.QtQuick import QQuickWindow, QQuickView
    from boot_status import BootStatus, marker
    app = QGuiApplication(sys.argv[:1])
    from sample_preview import image_url
    try:
        sample_source=image_url(args.demo_image)
    except ValueError as error:
        parser.error(str(error))
    from level_controller import LevelController
    try:
        level=LevelController(state_dir,args.attitude_file,args.demo_attitude,parent=app)
        if args.demo_attitude is not None:level.setEnabled(True,persist=False)
    except ValueError as error:parser.error(str(error))
    from localization import install_localization, saved_language
    localization=install_localization(app,args.language or saved_language(state_dir))
    ui_font = 'Segoe UI' if os.name == 'nt' else 'Sans Serif'
    for font_path in sorted(Path(__file__).with_name('fonts').glob('*')):
        if font_path.suffix.lower() not in ('.otf','.ttf'): continue
        # The product uses an exclusively sans-serif interface, including CJK.
        if 'serif' in font_path.name.casefold() and 'sans' not in font_path.name.casefold(): continue
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if families: ui_font = families[0]
    app.setFont(QFont(ui_font, 10))
    from ui_palette import apply_monochrome_palette
    apply_monochrome_palette(app)
    app.setApplicationName('DaOS Camera')
    app.setOrganizationName('Project Walnut')
    boot = BootStatus(app)
    splash = None
    if args.kiosk:
        splash = QQuickView()
        localization.bindEngine(splash.engine())
        splash.rootContext().setContextProperty('bootStatus', boot)
        splash.setResizeMode(QQuickView.SizeRootObjectToView)
        splash.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('BootSplash.qml'))))
        splash.frameSwapped.connect(lambda: marker('DAOS_SPLASH_READY_FILE'))
        splash.showFullScreen()
        app.processEvents()
    boot.phase(.72, 'Loading settings')
    from controller import CameraController, make_client_factory
    from shell_model import ShellController
    from apps_controller import CameraAppsController
    from soft_keyboard import SoftKeyboardController
    from system_settings import WifiController
    from terminal_surface import TerminalSurface
    from image_pipeline import FrameStore, FrameImageProvider
    from performance import PerformanceRecorder
    from shooting_workspace import ShootingWorkspace
    from input_router import CameraInputRouter
    from image_processing import ImageProcessingService
    from system_control import SystemController
    from camera_link import CameraLink
    from task_hub import TaskHub
    qmlRegisterType(TerminalSurface, 'DaOS.Terminal', 1, 0, 'TerminalSurface')
    wifi = WifiController(app)
    selected_command=args.native_command
    versioned_native=ROOT/'bin/hf-camerad'
    if platform.system()=='FreeBSD' and (versioned_native.is_file() or (ROOT/'release-ready.json').is_file()) and selected_command in (None,'/usr/local/bin/hf-camerad'):
        selected_command=str(versioned_native)
    command = [selected_command] if selected_command else None
    factory = lambda: make_client_factory(args.backend, args.data_dir, command, endpoint=args.endpoint)()
    frames = FrameStore()
    performance = PerformanceRecorder(state_dir / 'ui-performance.json', app)
    input_router = CameraInputRouter(app)
    app.installEventFilter(performance)
    controller = CameraController(factory, backend=args.backend,
                                  state_path=state_dir / 'device-ui.json', frame_store=frames,
                                  performance=performance)
    controller.changed.connect(lambda:performance.setPreviewActive(controller.previewActive))
    shell = ShellController(state_dir / 'shell-preferences.json', parent=app)
    if args.interface == 'camera' and not (state_dir / 'shell-preferences.json').exists():
        shell.setPreference('theme', 'dark')
    apps = CameraAppsController(controller, state_dir, parent=app)
    workspace = ShootingWorkspace(state_dir, parent=app)
    workspace.attachCamera(controller)
    processing=ImageProcessingService(state_dir,parent=app)
    apps.attachProcessing(processing)
    system_control=SystemController(state_dir,controller,parent=app,language=args.language)
    localization.bindSystemController(system_control)
    system_control.attachCatalog(apps.catalog)
    system_control.attachTasks(apps.tasks)
    system_control.refresh()
    def reload_user_configuration():
        try:
            if not shell.reloadPreferences():raise ValueError('shell preferences reload was rejected')
            if not workspace.reloadFromDisk():raise ValueError('shooting workspace reload was rejected')
            apps.refreshRecipes()
            if apps.media.gallery is not None:
                if hasattr(apps.media.gallery,'reloadStyles'):apps.media.gallery.reloadStyles()
                apps.media.gallery.refresh()
            if not system_control.markReloaded():raise ValueError('configuration recovery still needs review')
        except Exception as error:
            print('DaOS settings restored; restart required: '+str(error),file=sys.stderr)
    if hasattr(system_control,'restored'):system_control.restored.connect(reload_user_configuration)
    if system_control.restartRequired:QTimer.singleShot(0,reload_user_configuration)
    link=CameraLink(controller,state_dir,catalog=apps.catalog,tasks=apps.tasks,
                    client_getter=lambda:getattr(controller,'_client',None),parent=app)
    task_hub=TaskHub(apps.tasks,apps.workflow,link,parent=app)
    apps.attachTaskHub(task_hub)
    link.tasks=task_hub
    app.installEventFilter(system_control)
    app.installEventFilter(link)
    controller.localControlRequested.connect(lambda _origin:link.takeLocalControl())
    link.requestCaptureView.connect(lambda:shell.navigate('camera'))
    try:link.startSocket()
    except (OSError,ValueError) as error:print('DaOS camera-link unavailable: '+str(error),file=sys.stderr)
    system_control.powerScheduled.connect(app.quit)
    def sync_workspace():
        workspace.setCapabilities(controller.capabilities)
        if hasattr(workspace, 'setCurrentSettings'):
            workspace.setCurrentSettings(controller.confirmedSettings)
    controller.changed.connect(sync_workspace)
    sync_workspace()
    shell.navigate(args.app)
    theme = json.loads(Path(__file__).with_name('retail-theme.json').read_text(encoding='utf-8'))
    engine = QQmlApplicationEngine()
    localization.bindEngine(engine)
    engine.addImageProvider('daosframes', FrameImageProvider(frames))
    if apps.media.imageProvider is not None:engine.addImageProvider('daosmedia',apps.media.imageProvider)
    engine.rootContext().setContextProperty('camera', controller)
    engine.rootContext().setContextProperty('samplePreviewSource', sample_source)
    engine.rootContext().setContextProperty('levelControl',level)
    engine.rootContext().setContextProperty('cameraShell', shell)
    engine.rootContext().setContextProperty('cameraApps', apps)
    engine.rootContext().setContextProperty('shootingWorkspace', workspace)
    engine.rootContext().setContextProperty('uiPerformance', performance)
    engine.rootContext().setContextProperty('cameraUIFont', ui_font)
    engine.rootContext().setContextProperty('cameraTitleFont', ui_font)
    engine.rootContext().setContextProperty('cameraInput', input_router)
    engine.rootContext().setContextProperty('imageProcessing',processing)
    engine.rootContext().setContextProperty('systemControl',system_control)
    engine.rootContext().setContextProperty('cameraLink',link)
    engine.rootContext().setContextProperty('wifi', wifi)
    engine.rootContext().setContextProperty('bootStatus', boot)
    keyboard = SoftKeyboardController(app)
    engine.rootContext().setContextProperty('softKeyboard', keyboard)
    engine.rootContext().setContextProperty('retailTheme', theme)
    runtime_system = platform.system()
    runtime_label = ('DaOS BSD Unix · ' + runtime_system + ' ' + platform.release() + ' · ' + platform.machine()
                     if runtime_system in ('FreeBSD', 'NetBSD') else runtime_system + ' UI preview')
    engine.rootContext().setContextProperty('runtimeOSLabel', runtime_label)
    qml_name = 'Console.qml' if args.interface == 'console' else 'Main.qml'
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name(qml_name))))
    if not engine.rootObjects():
        task_hub.close();link.close();system_control.close();apps.close();controller.shutdown();performance.close()
        return 2
    window = engine.rootObjects()[0]
    from concurrent.futures import ThreadPoolExecutor
    from runtime_health import probe_imager
    health_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='daos-startup-health')
    worker_health=health_pool.submit(probe_imager)
    health_reported=[False]
    first_frame = [False]
    camera_frame_reported = [False]
    def finish_boot():
        if first_frame[0] and controller.connectionState == 'ready':
            boot.finish()
            if not health_reported[0] and worker_health.done():
                status=worker_health.result()
                if status.get('ready'):
                    health_reported[0]=True
                    print('DaOS_APP_HEALTH '+json.dumps({'format':'DaOS-camera-app-health-v1','version':ROOT.name if ROOT.name.startswith('camera-experience-') else 'development-004',
                          'pid':os.getpid(),'backend':args.backend,'authority_ready':True,'worker_ready':True}),flush=True)
                    boot_poll.stop()
                else:
                    health_reported[0]=True;boot_poll.stop()
                    print('DaOS_APP_HEALTH_FAILURE '+json.dumps(status),flush=True)
    def ui_frame():
        if not first_frame[0]:
            first_frame[0] = True
            if splash is not None: splash.close()
            boot.phase(.9, 'Starting camera')
            finish_boot()
        elif boot.ready and not camera_frame_reported[0]:
            camera_frame_reported[0]=True
            print('DaOS_BOOT_MARK '+json.dumps({'event':'camera-frame-presented',
                'monotonic_ns':time.monotonic_ns(),'authority_ready':controller.connectionState=='ready'}),flush=True)
    window.frameSwapped.connect(ui_frame)
    window.frameSwapped.connect(performance.frameSwapped)
    controller.changed.connect(finish_boot)
    boot_poll = QTimer(app)
    boot_poll.setInterval(100)
    boot_poll.timeout.connect(finish_boot)
    boot_poll.start()
    # A usable settings/diagnostics UI is preferable to a stuck splash if the
    # separate camera authority is unavailable.
    QTimer.singleShot(15000, lambda: boot.finish() if first_frame[0] else None)
    window.resize(args.width, args.height)
    if args.kiosk:
        screen = window.screen() or app.primaryScreen()
        if screen is not None:
            window.setGeometry(screen.geometry())
        window.showFullScreen()
    def close_camera():
        level.close()
        health_pool.shutdown(wait=True,cancel_futures=True)
        task_hub.close();link.close();system_control.close()
        if hasattr(apps, 'close'): apps.close()
        controller.shutdown()
        performance.close()
    app.aboutToQuit.connect(close_camera)
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
        close_camera()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == '__main__':
    raise SystemExit(main())
