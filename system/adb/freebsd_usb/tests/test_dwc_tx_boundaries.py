"""Compile the exact pinned DWC TX scheduler fragment; no USB hardware needed.

This isolates scheduling arithmetic, not interrupts or complete driver behavior.
The physical 2048/2049/2559/2560 observations are recorded separately by the
parent. Every input length through65537 is checked with and without2KiB writes.
"""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'upstream/sys/dev/usb/controller/dwc_otg.c'
HEADER=ROOT/'upstream/sys/dev/usb/controller/dwc_otg.h'
PIN='aa7cf55389300ddfc9ba8151e6ef019cea626f7178269e40067b9edfe6afb0f5'


def fragment():
    raw=SOURCE.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PIN:raise ValueError('Pinned DWC source changed')
    text=raw.decode()
    start=text.index('\t/* try to optimise by sending more data */',text.index('dwc_otg_data_tx('))
    end=text.index('\tDWC_OTG_WRITE_4(sc, DOTG_DIEPTSIZ',start)
    return text[start:end]


def program():
    return r'''
#include <stdint.h>
#include <stdio.h>
struct td_state { uint32_t max_packet_size,remainder,npkt,short_pkt; };
struct result { uint32_t bytes,packets,capacity; };
static struct result schedule(uint32_t length) {
    struct td_state state={512,length,0,0},*td=&state;
    uint32_t count=0,mpkt=0,max_buffer=2048;
'''+fragment()+r'''
    return (struct result){count,td->npkt,mpkt};
}
static int unsafe(uint32_t length) {
    while (length) {
        struct result r=schedule(length);
        if (r.bytes>2048 || r.packets>4) return 1;
        if (!r.bytes || r.bytes>length) return -1;
        length-=r.bytes;
    }
    return 0;
}
int main(void) {
    uint32_t original_unsafe=0,cap_submissions=0;
    if (unsafe(2048)!=0 || unsafe(2049)!=1 || unsafe(2559)!=1 || unsafe(2560)!=0) return 10;
    for (uint32_t n=0;n<=65537;n++) {
        /* Repeated driver schedules expose a short fifth packet whenever the
         * final remainder is2049..2559 after earlier2048-byte schedules. */
        int expected=(n>2048 && n%2048>=1 && n%2048<=511);
        if (unsafe(n)!=expected) return 11;
        original_unsafe+=expected;
        uint32_t remaining=n;
        do {
            uint32_t submitted=remaining>2048?2048:remaining;
            struct result r=schedule(submitted);
            if (r.bytes>2048 || r.packets>4 || unsafe(submitted)) return 12;
            cap_submissions++;
            remaining-=submitted;
        } while (remaining);
    }
    printf("{\"lengths_checked\":65538,\"original_unsafe_lengths\":%u,\"capped_submissions_checked\":%u,\"four_boundary_predictions_passed\":true,\"cap2048_all_safe\":true}\n",original_unsafe,cap_submissions);
    return 0;
}
'''


class DwcTxBoundaryTests(unittest.TestCase):
    def test_exact_source_fragment_and_fifo_configuration_are_bound(self):
        code=fragment()
        self.assertIn('td->npkt = count / td->max_packet_size;',code)
        self.assertIn('if (td->npkt > mpkt)',code)
        self.assertRegex(HEADER.read_text(),r'#define\s+DWC_OTG_MAX_TXP\s+4')
        self.assertRegex(HEADER.read_text(),r'#define\s+DWC_OTG_MAX_TXN\s+\(0x200 \* DWC_OTG_MAX_TXP\)')

    @unittest.skipUnless(shutil.which('cc') or shutil.which('clang') or shutil.which('gcc'),'C compiler required for exact extracted scheduler')
    def test_compiled_pinned_scheduler_all_lengths_and_2k_submissions(self):
        compiler=shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'dwc-fragment.c';binary=Path(folder)/'dwc-fragment'
            source.write_text(program())
            subprocess.run([compiler,'-std=c99','-O2','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)],check=True,capture_output=True,text=True)
            result=subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=20)
        self.assertIn('"lengths_checked":65538',result.stdout)
        self.assertIn('"cap2048_all_safe":true',result.stdout)
        print(result.stdout.strip())


if __name__=='__main__':unittest.main()
