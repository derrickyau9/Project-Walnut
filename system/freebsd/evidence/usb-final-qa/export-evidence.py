#!/usr/bin/env python3
"""Organize already acquired QA evidence; do not interact with the VM."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

project = Path('/mnt/f/Project HasselFree')
qa = project / 'system/freebsd/evidence/usb-final-qa'
work = Path('/home/dy09/daos-vm-usb-adb')
offline = work / 'verification/offline-run1-1dea85c9'
logo = work / 'verification/boot-logo-restart-1'
qa_offline = qa / 'offline'
qa_offline.mkdir(exist_ok=True)
for name in ('extraction-receipt.json', 'offline-validation.json', 'gpt.json',
             'authorized-keys-istat.txt'):
    shutil.copy2(offline / name, qa_offline / name)
for name in ('DaOS-usb-adb.log', 'DaOS-camera.log', 'DaOS-Xorg.log'):
    shutil.copy2(offline / 'files/DaOS-state/log' / name, qa_offline / name)
shutil.copy2(offline / 'files/DaOS-photos/361f0ccb-1882-4bdf-a557-b206f4161dad.dng',
             qa_offline / 'original.dng')
shutil.copy2(offline / 'files/DaOS-state/DaOS/ui/apps/exports/photo-361f0ccb-1882-4bdf-a557-b206f4161dad-1793948151794.dng',
             qa_offline / 'export-copy.dng')
if not (qa / 'boot-logo').exists():
    shutil.copytree(logo, qa / 'boot-logo')
for name in ('gui-input-actions.json', 'gui-restart-actions.jsonl'):
    shutil.copy2(work / 'verification' / name, qa / name)
offline_result = json.loads((offline / 'offline-validation.json').read_text())
extraction = json.loads((offline / 'extraction-receipt.json').read_text())
logo_result = json.loads((logo / 'logo-validation.json').read_text())
summary = {
    'format': 'DaOS-final-USB-VM-QA-v1',
    'recorded_utc': datetime.now(timezone.utc).isoformat(),
    'status': 'completed',
    'raw_sha256': 'ed84cce5eb14da514732f88909d0baee8f22266a3f837569185b30f855e1394e',
    'scope': 'Actual QEMU ARM64 GUI operation and a clean stop/restart of its private persistent COW. Physical Raspberry Pi, USB enumeration and camera were not exercised.',
    'assembly_receipt_modified': False,
    'passed': {
        'boot_logo_exact_rgb_pixel_match': True,
        'menu_navigation': True, 'system_page_identity': True,
        'one_synthetic_capture': True, 'playback_and_photo_information': True,
        'export_copy': True, 'export_exact_bytes_match': True,
        'guest_requested_poweroff_qemu_exit_zero': True,
        'application_and_xorg_clean_exit': True,
        'raw_and_cow_unchanged_during_stopped_inspection': True,
        'usb_rc_helper_started_and_waited_for_host': True,
        'usb_helper_clean_stop': True,
        'embedded_usb_payloads_match_release_receipt': True,
        'derived_dtb_matches_three_boot_paths': True,
        'authorized_public_key_fingerprints_and_root_0600': True,
        'same_capture_restored_after_restart': True,
        'camera_page_with_synthetic_preview_left_running': True,
    },
    'not_verified': {
        'physical_raspberry_pi_4_boot': True,
        'physical_usb_enumeration': True,
        'windows_usb_driver_binding': True,
        'physical_camera_capture': True,
        'second_shutdown_and_offline_comparison': True,
    },
    'system': offline_result['guest_identity'],
    'capture': {'id': '361f0ccb-1882-4bdf-a557-b206f4161dad',
                'synthetic': True, 'dimensions': [128, 96],
                'exposure_us': 10000, 'gain': 1.0,
                'bytes': 25662,
                'sha256': '2ecab9bcbb66ebe3d5ee9257a2c4fed90e1838784bf56bf61c054c5746470cd5',
                'first_playback_count': '1/1', 'restored_playback_count': '1/1',
                'restored_identity_screenshot': '16-restart-photo-info.png'},
    'shutdown': {'qemu_exit_code': extraction['clean_qemu_exit_code'],
                 'all_buffers_synced_serial_marker_seen': False,
                 'marker_scope': extraction['marker_scope'],
                 'application': offline_result['gui_clean_stop']},
    'usb': {'checked_payload_files': len(offline_result['checked_payload_files']),
            'embedded_manifest_sha256': offline_result['embedded_usb_manifest_sha256'],
            'qemu_state': 'helper started and waited for configured USB host; stopped status0',
            'physical_usb_verified': False, 'windows_binding_verified': False},
    'logo': {'matched_frame': 'boot-logo/' + logo_result['matched_frame'],
             'exact_rgb_match': logo_result['match'],
             'observation_seconds': logo_result['observation_seconds'],
             'evidence': 'boot-logo/logo-validation.json'},
    'final_running_instance': {
        'work_dir': str(work), 'qemu_name': 'DaOS-BSD-R14',
        'run': '8b2c1d0a15e04e75890e74155855a14f',
        'qemu_pid': 800211, 'visible': True,
        'state_at_last_observation': 'running',
        'last_observation_utc': '2026-11-06T07:05:33.822352+00:00',
        'page': 'Camera', 'preview': 'HF-Sim synthetic',
        'screenshot': '19-final-camera-settled.png'},
    'evidence': {'menu': '03-menu-open.png', 'system': '05-system-second.png',
                 'playback': '08-playback.png', 'photo_info': '09-photo-info.png',
                 'export': '11-export-copy-result.png',
                 'restored_playback': '15-restart-playback.png',
                 'restored_photo_info': '16-restart-photo-info.png',
                 'offline': 'offline/offline-validation.json',
                 'stopped_snapshot_receipt': 'offline/extraction-receipt.json'},
    'note': 'No new test or VM operation was performed during evidence organization. Original assembly-only evidence remains unchanged.'
}
(qa / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(str(qa / 'summary.json'))
